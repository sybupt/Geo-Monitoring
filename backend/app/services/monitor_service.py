import time
from openai import OpenAI
from app.config import DEEPSEEK_KEY, DEEPSEEK_BASE, DEEPSEEK_MODEL, KIMI_KEY, KIMI_BASE, KIMI_MODEL
from app.database import SessionLocal
from app.models import Business, MonitorRun, MonitorResult, Question, QuestionSet

SLEEP_BETWEEN = 20   # Kimi 免费档 3 RPM，20 秒一次最稳

PROVIDERS = {
    "kimi_search": {
        "label": "Kimi",
        "key": KIMI_KEY,
        "base_url": KIMI_BASE,
        "model": KIMI_MODEL,
        "web_search": True,
    },
    "deepseek": {
        "label": "DeepSeek",
        "key": DEEPSEEK_KEY,
        "base_url": DEEPSEEK_BASE,
        "model": DEEPSEEK_MODEL,
        "web_search": False,
    },
}


def list_providers():
    return [
        {"id": pid, "label": cfg["label"], "configured": bool(cfg["key"])}
        for pid, cfg in PROVIDERS.items()
    ]


def questions_for_business(db, business):
    """企业监测问题（优先 active 问题集，否则旧 questions 表）"""
    if business is None:
        return []
    active = (
        db.query(QuestionSet)
        .filter(QuestionSet.business_id == business.id, QuestionSet.status == "active")
        .order_by(QuestionSet.version.desc())
        .first()
    )
    if active:
        return [item.text for item in active.items]
    return [q.text for q in db.query(Question).all()]


def create_monitor_runs(business_id, stage, provider_ids, question_set_id=None):
    db = SessionLocal()
    try:
        if db.get(Business, business_id) is None:
            raise ValueError("企业不存在")
        runs = []
        for pid in provider_ids:
            run = MonitorRun(
                business_id=business_id,
                engine=pid,
                status="pending",
                stage=stage,
                question_set_id=question_set_id,
            )
            db.add(run)
            runs.append(run)
        db.commit()
        for run in runs:
            db.refresh(run)
        return [{"run_id": run.id} for run in runs]
    finally:
        db.close()


def ask_text(provider_id, prompt):
    """通用：用某个模型问一段话，返回文本（供监测/投喂/问题生成共用）"""
    provider = PROVIDERS[provider_id]
    if not provider["key"]:
        raise RuntimeError(f"{provider['label']} 未配置 API 密钥")
    client = OpenAI(api_key=provider["key"], base_url=provider["base_url"])
    if provider.get("web_search"):
        return _ask_with_search(client, provider, prompt)
    return _ask_plain(client, provider, prompt)


def _ask_plain(client, provider, prompt):
    for attempt in range(3):
        try:
            resp = client.chat.completions.create(
                model=provider["model"],
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(25)
                continue
            raise


def _ask_with_search(client, provider, prompt):
    tools = [{"type": "builtin_function", "function": {"name": "$web_search"}}]
    for attempt in range(3):
        try:
            resp1 = client.chat.completions.create(
                model=provider["model"],
                messages=[{"role": "user", "content": prompt}],
                tools=tools,
            )
            msg1 = resp1.choices[0].message
            if not msg1.tool_calls:
                return msg1.content or ""
            messages = [{"role": "user", "content": prompt}]
            messages.append({
                "role": "assistant",
                "content": msg1.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg1.tool_calls
                ],
            })
            for tc in msg1.tool_calls:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tc.function.arguments,
                })
            resp2 = client.chat.completions.create(
                model=provider["model"],
                messages=messages,
                tools=tools,
            )
            return resp2.choices[0].message.content or ""
        except Exception as e:
            if "429" in str(e) and attempt < 2:
                time.sleep(25)
                continue
            raise


def run_monitor(run_id):
    """对某个监测批次（MonitorRun）执行监测（供 FastAPI 后台任务调用）"""
    db = SessionLocal()
    try:
        run = db.get(MonitorRun, run_id)
        if run is None:
            return
        provider = PROVIDERS.get(run.engine)
        if provider is None:
            run.status = "failed"
            run.error = f"未知模型：{run.engine}"
            db.commit()
            return
        if not provider["key"]:
            run.status = "failed"
            run.error = f"{provider['label']} 未配置 API 密钥"
            db.commit()
            return

        business = db.get(Business, run.business_id)
        questions = _questions_for_run(db, run, business)
        if not questions:
            run.status = "failed"
            run.error = "没有可监测的问题"
            db.commit()
            return

        run.status = "running"
        db.commit()

        for q in questions:
            answer = ask_text(run.engine, q["text"])
            mentioned = bool(business.alias and business.alias in answer)
            db.add(MonitorResult(
                run_id=run.id,
                question_id=q["question_id"],
                question_text=q["text"],
                question_set_item_id=q["item_id"],
                mentioned=1 if mentioned else 0,
                snippet=business.alias if mentioned else "",
                score=1.0 if mentioned else 0.0,
                raw_answer=answer,
            ))
            db.commit()
            time.sleep(SLEEP_BETWEEN)

        run.status = "done"
        db.commit()
    except Exception as e:
        db.rollback()
        if run is not None:
            run.status = "failed"
            run.error = str(e)
            db.commit()
    finally:
        db.close()


def _questions_for_run(db, run, business):
    if run.question_set_id:
        qs = db.get(QuestionSet, run.question_set_id)
        if qs:
            return [{"text": i.text, "question_id": None, "item_id": i.id} for i in qs.items]
    active = (
        db.query(QuestionSet)
        .filter(QuestionSet.business_id == business.id, QuestionSet.status == "active")
        .order_by(QuestionSet.version.desc())
        .first()
    )
    if active:
        return [{"text": i.text, "question_id": None, "item_id": i.id} for i in active.items]
    return [{"text": q.text, "question_id": q.id, "item_id": None} for q in db.query(Question).all()]
