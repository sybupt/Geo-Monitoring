from openai import OpenAI
from sqlalchemy import or_

from app.config import (
    DEEPSEEK_BASE,
    DEEPSEEK_KEY,
    DEEPSEEK_MODEL,
    KIMI_BASE,
    KIMI_KEY,
    KIMI_MODEL,
)
from app.database import SessionLocal
from app.models import (
    Business,
    Question,
    QuestionSet,
    MonitorRun,
    MonitorResult,
)


PROVIDERS = {
    "kimi_search": {
        "label": "Kimi 联网搜索",
        "model": KIMI_MODEL,
        "key": KIMI_KEY,
        "base_url": KIMI_BASE,
        "search": True,
    },
    "deepseek": {
        "label": "DeepSeek",
        "model": DEEPSEEK_MODEL,
        "key": DEEPSEEK_KEY,
        "base_url": DEEPSEEK_BASE,
        "search": False,
    },
}


def questions_for_business(db, business: Business):
    query = db.query(Question)
    if business.region:
        query = query.filter(
            or_(
                Question.region == business.region,
                Question.region.is_(None),
                Question.region == "",
            )
        )
    return query.order_by(Question.id.asc()).all()


def list_providers():
    return [
        {
            "id": provider_id,
            "name": config["label"],
            "model": config["model"],
            "configured": bool(config["key"]),
            "web_search": config["search"],
        }
        for provider_id, config in PROVIDERS.items()
    ]


def ask_model(provider_id: str, question: str) -> str:
    config = PROVIDERS[provider_id]
    if not config["key"]:
        raise RuntimeError(f"{config['label']} 未配置 API 密钥")

    client = OpenAI(api_key=config["key"], base_url=config["base_url"])
    kwargs = {
        "model": config["model"],
        "messages": [{"role": "user", "content": question}],
    }
    if config["search"]:
        kwargs["tools"] = [
            {"type": "builtin_function", "function": {"name": "$web_search"}}
        ]

    response = client.chat.completions.create(**kwargs)
    answer = response.choices[0].message.content
    if not answer:
        raise RuntimeError(f"{config['label']} 返回了空回答")
    return answer


def create_monitor_runs(
    business_id: int,
    stage: str,
    provider_ids: list[str],
    question_set_id: int | None = None,
):
    db = SessionLocal()
    try:
        business = db.get(Business, business_id)
        if business is None:
            raise ValueError("企业不存在")
        if question_set_id is not None:
            question_set = db.get(QuestionSet, question_set_id)
            if (
                question_set is None
                or question_set.business_id != business_id
                or question_set.status != "active"
            ):
                raise ValueError("请选择该企业已审核的问题集")
            if not question_set.items:
                raise ValueError("已审核问题集为空")
        elif not questions_for_business(db, business):
            raise ValueError("问题库为空，请先添加监测问题")

        runs = []
        for provider_id in provider_ids:
            config = PROVIDERS[provider_id]
            run = MonitorRun(
                business_id=business_id,
                engine=provider_id,
                question_set_id=question_set_id,
                stage=stage,
                status="running",
            )
            db.add(run)
            runs.append((run, config["label"]))
        db.commit()
        return [
            {"run_id": run.id, "provider": run.engine, "provider_name": label}
            for run, label in runs
        ]
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def run_monitor(run_id: int):
    db = SessionLocal()
    try:
        run = db.get(MonitorRun, run_id)
        if run is None:
            raise ValueError(f"监测批次 {run_id} 不存在")

        business = db.get(Business, run.business_id)
        if business is None:
            raise ValueError(f"企业 {run.business_id} 不存在")
        if run.question_set_id:
            question_set = db.get(QuestionSet, run.question_set_id)
            if question_set is None or question_set.business_id != business.id:
                raise ValueError("监测批次关联的问题集不存在或企业不匹配")
            questions = [
                (item.id, item.text, item.category)
                for item in question_set.items
            ]
        else:
            questions = [
                (None, question.text, question.category)
                for question in questions_for_business(db, business)
            ]
        if not questions:
            raise ValueError("问题库为空，请先添加监测问题")

        aliases = [
            value.strip()
            for value in (business.alias, business.name)
            if value and value.strip()
        ]
        for question_id, question_text, _category in questions:
            answer = ask_model(run.engine, question_text)
            normalized_answer = answer.casefold()
            match = next(
                (alias for alias in aliases if alias.casefold() in normalized_answer),
                None,
            )
            db.add(MonitorResult(
                run_id=run.id,
                question_id=question_id if run.question_set_id is None else None,
                question_set_item_id=(
                    question_id if run.question_set_id is not None else None
                ),
                question_text=question_text,
                mentioned=1 if match else 0,
                snippet=match or "",
                score=1.0 if match else 0.0,
                raw_answer=answer,
            ))
            db.commit()

        run.status = "done"
        run.error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        failed_run = db.get(MonitorRun, run_id)
        if failed_run is not None:
            failed_run.status = "failed"
            failed_run.error = str(exc)[:1000]
            db.commit()
        raise
    finally:
        db.close()
