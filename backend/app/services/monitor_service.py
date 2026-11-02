import json
import time
import re
from urllib.parse import urlparse
from openai import OpenAI
from app.config import DEEPSEEK_KEY, DEEPSEEK_BASE, DEEPSEEK_MODEL, KIMI_KEY, KIMI_BASE, KIMI_MODEL
from app.database import SessionLocal
from app.models import Business, Citation, FeedingAsset, FeedingPlan, MonitorRun, MonitorResult, Question, QuestionSet

SLEEP_BETWEEN = 20

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

URL_RE = re.compile(r'https?://[^\s\)\]\"\'，。；]+')
MD_LINK_RE = re.compile(r'\[([^\]]*)\]\((https?://[^\)]+)\)')


def extract_citations(text):
    """从回答文本抽取引用来源（URL + 域名 + 位置），返回 list[dict]"""
    if not text:
        return []
    citations = []
    seen = set()
    for m in MD_LINK_RE.finditer(text):
        url = m.group(2).rstrip('.,;')
        if url not in seen:
            seen.add(url)
            citations.append({
                "source_url": url,
                "source_domain": urlparse(url).netloc,
                "position": m.start(),
            })
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip('.,;')
        if url not in seen:
            seen.add(url)
            citations.append({
                "source_url": url,
                "source_domain": urlparse(url).netloc,
                "position": m.start(),
            })
    return citations


def list_providers():
    return [
        {"id": pid, "label": cfg["label"], "configured": bool(cfg["key"])}
        for pid, cfg in PROVIDERS.items()
    ]


def match_fingerprints(db, business_id, result, answer_text):
    """根据投喂素材的指纹识别 AI 回答命中了哪条内容，写入 Citation。"""
    if not answer_text:
        return

    assets = (
        db.query(FeedingAsset)
        .join(FeedingPlan, FeedingAsset.plan_id == FeedingPlan.id)
        .filter(FeedingPlan.business_id == business_id)
        .all()
    )
    answer = (answer_text or "").lower()
    seen_asset_keys = set()

    for asset in assets:
        if not asset.fingerprints:
            continue
        try:
            fps = json.loads(asset.fingerprints)
        except Exception:
            continue
        if not isinstance(fps, list):
            continue

        positions = []
        for fp in fps:
            if not fp or not isinstance(fp, str):
                continue
            target = fp.strip()
            if not target:
                continue
            pos = answer.find(target.lower())
            if pos >= 0:
                positions.append(pos)

        if not positions:
            continue

        asset_key = (asset.id, asset.published_url or asset.channel)
        if asset_key in seen_asset_keys:
            continue
        seen_asset_keys.add(asset_key)

        db.add(Citation(
            result_id=result.id,
            source_url=asset.published_url or asset.channel,
            source_domain=asset.channel,
            position=min(positions),
        ))


def questions_for_business(db, business):
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
            result = MonitorResult(
                run_id=run.id,
                question_id=q["question_id"],
                question_text=q["text"],
                question_set_item_id=q["item_id"],
                mentioned=1 if mentioned else 0,
                snippet=business.alias if mentioned else "",
                score=1.0 if mentioned else 0.0,
                raw_answer=answer,
            )
            db.add(result)
            db.flush()

            for cite in extract_citations(answer):
                db.add(Citation(
                    result_id=result.id,
                    source_url=cite["source_url"],
                    source_domain=cite["source_domain"],
                    position=cite["position"],
                ))

            match_fingerprints(db, run.business_id, result, answer)
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
