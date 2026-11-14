"""v2 API：多采样监测 / 归因 / 渠道占位。

口径约定（前端展示必须遵守）：
- ``tier`` 为 MOCK 的数据不许进正式报告；
- ``memory_mode=True`` 的引擎单独分区展示，不与检索型引擎同图排名；
- 所有比例都带 ``ci95``，前端不要只显示点估计。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.database import SessionLocal
from app.industry import local_life
from app.metrics import wilson_interval
from app.models import Business
from app.probes import AdapterNotWired, ProbeError, engine_catalog
from app.v2 import service
from app.v2.models import ChannelAccount, JudgeAudit, ProbeBatch, ProbeSample, PublishTask

router = APIRouter(prefix="/api/v2", tags=["v2"])


# ── 引擎能力 ────────────────────────────────────────────────────
@router.get("/engines")
def engines():
    """七个引擎的能力矩阵：谁已接线、谁配了 key、引用语义、数据等级。"""
    catalog = engine_catalog()
    return {
        "engines": catalog,
        "implemented": [e["engine"] for e in catalog if e["implemented"]],
        "coverage": {
            "total": len(catalog),
            "implemented": sum(1 for e in catalog if e["implemented"]),
            "configured": sum(1 for e in catalog if e.get("configured")),
        },
    }


# ── 问题矩阵 ────────────────────────────────────────────────────
class QuestionPreview(BaseModel):
    business_id: int
    city: str = ""
    categories: list[str] = Field(default_factory=list)
    control_categories: list[str] = Field(default_factory=list)


@router.post("/questions/preview")
def preview_questions(payload: QuestionPreview):
    try:
        questions = service.available_questions(
            payload.business_id, payload.city, payload.categories, payload.control_categories
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    counts: dict[str, int] = {}
    for q in questions:
        counts[q["intent_label"]] = counts.get(q["intent_label"], 0) + 1
    return {
        "questions": questions,
        "total": len(questions),
        "treated": sum(1 for q in questions if not q["is_control"]),
        "control": sum(1 for q in questions if q["is_control"]),
        "by_intent": counts,
    }


# ── 跑批 ────────────────────────────────────────────────────────
class BatchCreate(BaseModel):
    business_id: int = Field(gt=0)
    engines: list[str] = Field(min_length=1)
    n_samples: int = Field(default=5, ge=1, le=20)
    stage: Literal["baseline", "after", "adhoc"] = "baseline"
    judge_mode: Literal["per_sample", "rule_only"] = "per_sample"
    competitors: list[str] = Field(default_factory=list)
    note: str = ""
    # 二选一：直接给问题，或用本地生活矩阵生成
    questions: list[dict[str, Any]] = Field(default_factory=list)
    city: str = ""
    categories: list[str] = Field(default_factory=list)
    control_categories: list[str] = Field(default_factory=list)


@router.post("/batches", status_code=202)
def create_batch(payload: BatchCreate, background: BackgroundTasks):
    questions = payload.questions
    if not questions:
        questions = service.available_questions(
            payload.business_id, payload.city, payload.categories, payload.control_categories
        )
    if not questions:
        raise HTTPException(400, "问题集为空：请给 questions，或给 city + categories")

    with SessionLocal() as db:
        if db.get(Business, payload.business_id) is None:
            raise HTTPException(404, "企业不存在")
        try:
            batch = service.create_batch(
                db,
                business_id=payload.business_id,
                engines=payload.engines,
                questions=questions,
                n_samples=payload.n_samples,
                stage=payload.stage,
                judge_mode=payload.judge_mode,
                competitors=payload.competitors,
                note=payload.note,
            )
        except AdapterNotWired as exc:
            raise HTTPException(400, str(exc)) from exc
        except ProbeError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    background.add_task(service.run_batch, batch.id)
    return {
        "batch_id": batch.id,
        "total_tasks": batch.total_tasks,
        "questions": len(questions),
        "n_samples": payload.n_samples,
        "engines": payload.engines,
        "estimate": _estimate(batch.total_tasks, payload.engines),
    }


def _estimate(tasks: int, engines: list[str]) -> str:
    """粗估跑批耗时：每引擎串行，引擎间并行。够用就行，别做成假精确。"""
    from app.config import PROBE_RATE_PER_MIN

    slowest = min(PROBE_RATE_PER_MIN.get(e, 20) for e in engines) if engines else 20
    minutes = tasks / max(1, len(engines)) / max(1, slowest)
    return f"约 {max(1, round(minutes))} 分钟（按当前限流保守估算）"


@router.get("/batches")
def list_batches(business_id: int, limit: int = Query(default=20, le=100)):
    with SessionLocal() as db:
        rows = (
            db.query(ProbeBatch)
            .filter(ProbeBatch.business_id == business_id)
            .order_by(ProbeBatch.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "batch_id": r.id,
                "stage": r.stage,
                "status": r.status,
                "engines": service._loads(r.engines, []),
                "n_samples": r.n_samples,
                "progress": f"{r.done_tasks or 0}/{r.total_tasks or 0}",
                "failed": r.failed_tasks or 0,
                "note": r.note,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: int):
    try:
        return service.report(batch_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/batches/{batch_id}/report")
def batch_report(batch_id: int):
    try:
        data = service.report(batch_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    warning = list(data.get("warnings") or [])
    if data.get("status") not in ("done", "partial"):
        warning.append("批次尚未跑完，当前数字只反映已完成部分")
    if any(e.get("tier") == "MOCK" for e in data.get("engines", {}).values()):
        warning.append("包含 MOCK 引擎数据，不可作为效果证明")
    data["warnings"] = warning
    return data


@router.get("/batches/{batch_id}/samples")
def batch_samples(batch_id: int, engine: str | None = None, limit: int = Query(default=200, le=2000)):
    rows = service.sample_rows(batch_id)
    if engine:
        rows = [r for r in rows if r["engine"] == engine]
    return {"total": len(rows), "samples": rows[:limit]}


@router.post("/batches/{batch_id}/resume", status_code=202)
def resume_batch(batch_id: int, background: BackgroundTasks):
    with SessionLocal() as db:
        batch = db.get(ProbeBatch, batch_id)
        if batch is None:
            raise HTTPException(404, "批次不存在")
        left = (
            db.query(ProbeSample)
            .filter(ProbeSample.batch_id == batch_id, ProbeSample.error == "pending")
            .count()
        )
        batch.status = "pending"
        db.commit()
    background.add_task(service.run_batch, batch_id)
    return {"batch_id": batch_id, "remaining": left}


@router.get("/did")
def did(before: int, after: int):
    """两次跑批的 DID 归因。before/after 都要包含对照组样本，否则只返回提示。"""
    try:
        return service.did_from_batches(before, after)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


# ── 渠道与发布 ──────────────────────────────────────────────────
@router.get("/channels")
def channels(city: str = ""):
    return {"channels": local_life.channels_for(city)}


class AccountBind(BaseModel):
    business_id: int
    channel_key: str
    account_name: str = ""
    bind_mode: Literal["api", "rpa", "guide"] = "guide"
    credential_ref: str = ""
    note: str = ""


@router.post("/channel-accounts", status_code=201)
def bind_account(payload: AccountBind):
    if local_life.channel(payload.channel_key) is None:
        raise HTTPException(400, f"未知渠道：{payload.channel_key}")
    with SessionLocal() as db:
        acc = ChannelAccount(
            business_id=payload.business_id,
            channel_key=payload.channel_key,
            account_name=payload.account_name,
            bind_mode=payload.bind_mode,
            credential_ref=payload.credential_ref,
            note=payload.note,
        )
        db.add(acc)
        db.commit()
        db.refresh(acc)
        return {"id": acc.id, "status": acc.status}


@router.get("/channel-accounts")
def list_accounts(business_id: int):
    with SessionLocal() as db:
        rows = (
            db.query(ChannelAccount)
            .filter(ChannelAccount.business_id == business_id)
            .order_by(ChannelAccount.id.desc())
            .all()
        )
        return [
            {
                "id": r.id,
                "channel_key": r.channel_key,
                "channel_label": (local_life.channel(r.channel_key) or {}).get("label", r.channel_key),
                "account_name": r.account_name,
                "bind_mode": r.bind_mode,
                "status": r.status,
                "bound_at": r.bound_at.isoformat() if r.bound_at else None,
            }
            for r in rows
        ]


class PublishCreate(BaseModel):
    business_id: int
    channel_key: str
    title: str = ""
    content: str = ""
    topics: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    asset_id: int | None = None
    account_id: int | None = None


@router.post("/publish-tasks", status_code=201)
def create_publish_task(payload: PublishCreate):
    ch = local_life.channel(payload.channel_key)
    if ch is None:
        raise HTTPException(400, f"未知渠道：{payload.channel_key}")
    mode = ch["mode"]
    status = "awaiting_user" if mode == "guide" else "ready"
    with SessionLocal() as db:
        task = PublishTask(
            business_id=payload.business_id,
            asset_id=payload.asset_id,
            channel_key=payload.channel_key,
            account_id=payload.account_id,
            mode=mode,
            status=status,
            guide_url=ch.get("guide_url", ""),
            payload=service._dumps(
                {
                    "title": payload.title,
                    "content": payload.content,
                    "topics": payload.topics,
                    "images": payload.images,
                    "copy_text": _copy_text(payload.title, payload.content, payload.topics),
                }
            ),
            note=ch.get("note", ""),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return _task_dict(task, ch)


def _copy_text(title: str, content: str, topics: list[str]) -> str:
    tags = " ".join(f"#{t}" for t in topics if t)
    return f"{title}\n\n{content}\n\n{tags}".strip()


def _task_dict(task: PublishTask, ch: dict | None = None) -> dict:
    data = service._loads(task.payload, {})
    return {
        "id": task.id,
        "channel_key": task.channel_key,
        "channel_label": (ch or local_life.channel(task.channel_key) or {}).get("label", task.channel_key),
        "mode": task.mode,
        "status": task.status,
        "guide_url": task.guide_url,
        "title": data.get("title", ""),
        "copy_text": data.get("copy_text", ""),
        "published_url": task.published_url,
        "indexed": bool(task.indexed),
    }


@router.get("/publish-tasks")
def list_publish_tasks(business_id: int, status: str | None = None):
    with SessionLocal() as db:
        q = db.query(PublishTask).filter(PublishTask.business_id == business_id)
        if status:
            q = q.filter(PublishTask.status == status)
        rows = q.order_by(PublishTask.id.desc()).limit(100).all()
        return [_task_dict(r) for r in rows]


class MarkPublished(BaseModel):
    published_url: str
    note: str = ""


@router.post("/publish-tasks/{task_id}/published")
def mark_published(task_id: int, payload: MarkPublished):
    """用户去平台发完后回填链接。**发出去 ≠ 被收录 ≠ 被 AI 引用**，这里只记第一层。"""
    with SessionLocal() as db:
        task = db.get(PublishTask, task_id)
        if task is None:
            raise HTTPException(404, "任务不存在")
        task.status = "published"
        task.published_url = payload.published_url
        task.published_at = datetime.utcnow()
        task.note = payload.note or task.note
        db.commit()
        return _task_dict(task)


@router.post("/publish-tasks/{task_id}/check")
def check_published(task_id: int):
    """收录回查（第一层）：确认链接是否还活着。第二层「是否被引擎引用」要靠复测批次。"""
    with SessionLocal() as db:
        task = db.get(PublishTask, task_id)
        if task is None:
            raise HTTPException(404, "任务不存在")
        if not task.published_url:
            raise HTTPException(400, "还没有发布链接")
        alive, status_code, note = _probe_url(task.published_url)
        task.indexed = 1 if alive else 0
        task.indexed_checked_at = datetime.utcnow()
        task.note = note
        db.commit()
        return {"id": task.id, "alive": alive, "status_code": status_code, "note": note}


def _probe_url(url: str) -> tuple[bool, int, str]:
    try:
        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0 GEO-Monitor"})
        ok = resp.status_code < 400 and len(resp.text) > 200
        return ok, resp.status_code, "页面可访问" if ok else "页面异常（可能是删除/审核/需要登录）"
    except Exception as exc:  # noqa: BLE001
        return False, 0, f"访问失败：{exc}"


# ── 判定质量抽检 ────────────────────────────────────────────────
class AuditCreate(BaseModel):
    sample_id: int
    human_mentioned: int = Field(ge=0, le=1)
    human_sentiment: int = Field(default=0, ge=-1, le=1)
    human_rank: int | None = None
    note: str = ""


@router.post("/judge-audits", status_code=201)
def create_audit(payload: AuditCreate):
    with SessionLocal() as db:
        sample = db.get(ProbeSample, payload.sample_id)
        if sample is None:
            raise HTTPException(404, "样本不存在")
        agree = 1 if int(sample.mentioned or 0) == payload.human_mentioned else 0
        audit = JudgeAudit(
            sample_id=payload.sample_id,
            human_mentioned=payload.human_mentioned,
            human_sentiment=payload.human_sentiment,
            human_rank=payload.human_rank,
            agree=agree,
            note=payload.note,
        )
        db.add(audit)
        db.commit()
        return {"id": audit.id, "agree": bool(agree), "machine": bool(sample.mentioned)}


@router.get("/judge-quality")
def judge_quality(batch_id: int):
    """抽检一致率。低于 90% 就别把报告给客户，先去修判定 prompt。"""
    with SessionLocal() as db:
        total = (
            db.query(func.count(JudgeAudit.id))
            .join(ProbeSample, JudgeAudit.sample_id == ProbeSample.id)
            .filter(ProbeSample.batch_id == batch_id)
            .scalar()
        ) or 0
        agrees = (
            db.query(func.count(JudgeAudit.id))
            .join(ProbeSample, JudgeAudit.sample_id == ProbeSample.id)
            .filter(ProbeSample.batch_id == batch_id, JudgeAudit.agree == 1)
            .scalar()
        ) or 0
    lo, hi = wilson_interval(agrees, total) if total else (0.0, 1.0)
    return {
        "audited": total,
        "agree": agrees,
        "agreement_rate": round(agrees / total, 4) if total else None,
        "ci95": [round(lo, 4), round(hi, 4)],
        "pass": bool(total >= 20 and agrees / total >= 0.9),
        "note": "建议每批抽检 ≥20 条；一致率 <90% 先修判定再出报告",
    }
