"""跑批服务：多采样 + 每引擎限流 + 断点续跑 + 分口径落库。

并发模型（刻意保守）：
- 引擎之间并行（3 个引擎 ≈ 3 倍速），**引擎内部串行**，靠令牌桶压住速率；
- 写库全局串行（SQLite 写锁），避免 ``database is locked`` 把整批跑挂；
- 每条采样立刻落库，进程被杀也不丢已完成的部分，重跑自动跳过。

一个刻意的取舍：宁可慢，也不要为了快而并发打爆账号。账号被封，这个产品就没法交付了。
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.config import PROBE_RATE_PER_MIN
from app.database import SessionLocal, engine as db_engine
from app.industry import local_life
from app.judge import judge as run_judge
from app.metrics import build_report
from app.models import Business
from app.probes import TokenBucket, get_adapter
from app.v2.models import ProbeBatch, ProbeSample

_WRITE_LOCK = threading.Lock()
_BUCKETS: dict[str, TokenBucket] = {}
_BUCKET_LOCK = threading.Lock()
_WAL_READY = False

DEFAULT_N_SAMPLES = 5
PARALLEL_PER_ENGINE = 1


def ensure_sqlite_pragmas() -> None:
    """SQLite 开 WAL：读不阻塞写，跑批时前端还能正常查进度。"""
    global _WAL_READY
    if _WAL_READY or not str(db_engine.url).startswith("sqlite"):
        _WAL_READY = True
        return
    with db_engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=8000")
    _WAL_READY = True


def _bucket(engine_id: str) -> TokenBucket:
    with _BUCKET_LOCK:
        if engine_id not in _BUCKETS:
            rate = PROBE_RATE_PER_MIN.get(engine_id, 20)
            _BUCKETS[engine_id] = TokenBucket(per_minute=rate, burst=1)
        return _BUCKETS[engine_id]


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def business_profile(db, business_id: int) -> dict[str, Any]:
    biz = db.get(Business, business_id)
    if biz is None:
        raise ValueError("企业不存在")
    aliases = [a.strip() for a in (biz.alias or "").replace("、", ",").replace("|", ",").split(",") if a.strip()]
    if biz.name and biz.name not in aliases:
        aliases.insert(0, biz.name)
    return {
        "id": biz.id,
        "name": biz.name or "",
        "aliases": aliases,
        "city": biz.region or "",
        "category": biz.category or "",
    }


def create_batch(
    db,
    *,
    business_id: int,
    engines: list[str],
    questions: list[dict[str, Any]],
    n_samples: int = DEFAULT_N_SAMPLES,
    stage: str = "baseline",
    judge_mode: str = "per_sample",
    competitors: list[str] | None = None,
    note: str = "",
) -> ProbeBatch:
    if not engines:
        raise ValueError("至少选择一个引擎")
    if not questions:
        raise ValueError("问题集为空")
    if n_samples < 1 or n_samples > 20:
        raise ValueError("采样次数建议 3–20 次")

    # 提前试建 adapter：没接线、没配好的引擎在这里就挡掉，不要跑到一半才失败。
    # 跑批一跑十几分钟，最后返回一堆 "no api key" 的失败样本，用户只会以为系统坏了。
    # 缺什么由每个引擎自己说明（文心缺 Key、元宝缺登录态，文案不一样）。
    missing: list[str] = []
    for eng in engines:
        adapter = get_adapter(eng, brand_name="probe", competitors=competitors or [])
        caps = adapter.capabilities()
        if not caps.get("configured"):
            hint = caps.get("setup_hint")
            missing.append(f"{adapter.label}（{hint}）" if hint else adapter.label)
    if missing:
        raise ValueError("以下引擎暂不可跑批：" + "；".join(missing) + "。配好后重启服务，或取消勾选这些引擎。")

    batch = ProbeBatch(
        business_id=business_id,
        stage=stage,
        engines=_dumps(engines),
        competitors=_dumps(competitors or []),
        n_samples=n_samples,
        judge_mode=judge_mode,
        status="pending",
        total_tasks=len(engines) * len(questions) * n_samples,
        note=note or f"{len(questions)} 题 × {n_samples} 次",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    with _WRITE_LOCK, SessionLocal() as wdb:
        for eng in engines:
            for q in questions:
                for idx in range(n_samples):
                    wdb.add(
                        ProbeSample(
                            batch_id=batch.id,
                            business_id=business_id,
                            engine=eng,
                            question_key=q["question_key"],
                            question_text=q["text"],
                            intent=q.get("intent", ""),
                            is_control=1 if q.get("is_control") else 0,
                            sample_idx=idx,
                            model="",
                            model_version="pending",
                            ok=0,
                            error="pending",
                        )
                    )
        wdb.commit()
    return batch


def _persist(sample_id: int, fields: dict[str, Any]) -> None:
    with _WRITE_LOCK, SessionLocal() as db:
        sample = db.get(ProbeSample, sample_id)
        if sample is None:
            return
        for k, v in fields.items():
            setattr(sample, k, v)
        db.commit()


def _bump(batch_id: int, *, done: int = 0, failed: int = 0) -> None:
    with _WRITE_LOCK, SessionLocal() as db:
        batch = db.get(ProbeBatch, batch_id)
        if batch is None:
            return
        batch.done_tasks = (batch.done_tasks or 0) + done
        batch.failed_tasks = (batch.failed_tasks or 0) + failed
        db.commit()


def _run_engine(
    batch_id: int,
    engine_id: str,
    profile: dict[str, Any],
    competitors: list[str],
    tasks: list[tuple[int, str, str, str, str, int]],
    judge_mode: str,
) -> None:
    adapter = get_adapter(
        engine_id,
        brand_name=profile["name"],
        competitors=competitors or ["竞品A", "竞品B"],
    )
    bucket = _bucket(engine_id)

    for sample_id, qkey, qtext, intent, is_control, idx in tasks:
        bucket.acquire()
        try:
            result = adapter.probe(qtext, sample_idx=idx)
        except Exception as exc:  # noqa: BLE001 - 单条失败不能拖垮整批
            _persist(
                sample_id,
                {"ok": 0, "error": str(exc)[:500], "model_version": "unknown", "tier": getattr(adapter, "tier", "")},
            )
            _bump(batch_id, done=1, failed=1)
            continue

        verdict = run_judge(
            qtext,
            result.answer,
            profile["name"],
            aliases=profile["aliases"],
            competitors=competitors,
            use_llm=(judge_mode == "per_sample"),
        )

        _persist(
            sample_id,
            {
                "ok": 1,
                "error": "",
                "tier": result.tier,
                "citation_type": result.citation_type,
                "memory_mode": 1 if getattr(adapter, "memory_mode", False) else 0,
                "model": result.model,
                "model_version": result.model_version,
                "answer": result.answer,
                "latency_ms": result.latency_ms,
                "mentioned": 1 if verdict.mentioned else 0,
                "is_recommended": 1 if verdict.is_recommended else 0,
                "first_rank": verdict.first_rank,
                "sentiment": verdict.sentiment,
                "judge_method": verdict.method,
                "judge_reason": verdict.reason,
                "competitors": _dumps(verdict.competitors),
                "search_queries": _dumps(result.search_queries),
                "citations": _dumps([c.to_dict() for c in result.citations]),
                "usage": _dumps(result.usage),
            },
        )
        _bump(batch_id, done=1)


def run_batch(batch_id: int) -> None:
    """后台跑批入口。幂等：已完成（ok=1）的采样会被跳过，支持断点续跑。"""
    ensure_sqlite_pragmas()
    with SessionLocal() as db:
        batch = db.get(ProbeBatch, batch_id)
        if batch is None:
            return
        profile = business_profile(db, batch.business_id)
        engines = _loads(batch.engines, [])
        competitors = _loads(batch.competitors, [])
        judge_mode = batch.judge_mode or "per_sample"
        batch.status = "running"
        batch.started_at = batch.started_at or datetime.utcnow()
        db.commit()

    with SessionLocal() as db:
        rows = (
            db.query(ProbeSample)
            .filter(ProbeSample.batch_id == batch_id, ProbeSample.ok == 0, ProbeSample.error == "pending")
            .order_by(ProbeSample.id)
            .all()
        )
        grouped: dict[str, list[tuple[int, str, str, str, str, int]]] = {}
        for r in rows:
            grouped.setdefault(r.engine, []).append(
                (r.id, r.question_key, r.question_text, r.intent or "", "1" if r.is_control else "0", r.sample_idx)
            )

    try:
        with ThreadPoolExecutor(max_workers=max(1, len(grouped)) * PARALLEL_PER_ENGINE) as pool:
            futures = [
                pool.submit(
                    _run_engine, batch_id, eng, profile, competitors, tasks, judge_mode
                )
                for eng, tasks in grouped.items()
            ]
            for f in futures:
                f.result()

        with _WRITE_LOCK, SessionLocal() as db:
            batch = db.get(ProbeBatch, batch_id)
            batch.finished_at = datetime.utcnow()
            pending = (
                db.query(ProbeSample)
                .filter(ProbeSample.batch_id == batch_id, ProbeSample.error == "pending")
                .count()
            )
            failed = (
                db.query(ProbeSample)
                .filter(ProbeSample.batch_id == batch_id, ProbeSample.ok == 0)
                .count()
            )
            total = db.query(ProbeSample).filter(ProbeSample.batch_id == batch_id).count()
            if pending:
                batch.status = "partial"
            elif failed == total:
                batch.status = "failed"
            elif failed:
                batch.status = "partial"
            else:
                batch.status = "done"
            db.commit()
    except Exception as exc:  # noqa: BLE001
        with _WRITE_LOCK, SessionLocal() as db:
            batch = db.get(ProbeBatch, batch_id)
            if batch:
                batch.status = "failed"
                batch.error = str(exc)[:1000]
                batch.finished_at = datetime.utcnow()
                db.commit()


def sample_rows(batch_id: int) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = (
            db.query(ProbeSample)
            .filter(ProbeSample.batch_id == batch_id)
            .order_by(ProbeSample.question_key, ProbeSample.engine, ProbeSample.sample_idx)
            .all()
        )
        return [
            {
                "id": r.id,
                "engine": r.engine,
                "question_key": r.question_key,
                "question_text": r.question_text,
                "intent": r.intent,
                "is_control": bool(r.is_control),
                "sample_idx": r.sample_idx,
                "tier": r.tier,
                "citation_type": r.citation_type,
                "memory_mode": bool(r.memory_mode),
                "model": r.model,
                "model_version": r.model_version,
                "ok": bool(r.ok),
                "error": r.error,
                "answer": r.answer,
                "latency_ms": r.latency_ms,
                "mentioned": bool(r.mentioned),
                "is_recommended": bool(r.is_recommended),
                "first_rank": r.first_rank,
                "sentiment": r.sentiment or 0,
                "judge_method": r.judge_method,
                "judge_reason": r.judge_reason,
                "competitors": _loads(r.competitors, []),
                "search_queries": _loads(r.search_queries, []),
                "citations": _loads(r.citations, []),
            }
            for r in rows
        ]


def report(batch_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        batch = db.get(ProbeBatch, batch_id)
        if batch is None:
            raise ValueError("批次不存在")
        profile = business_profile(db, batch.business_id)
        meta = {
            "batch_id": batch.id,
            "business": profile,
            "stage": batch.stage,
            "status": batch.status,
            "n_samples": batch.n_samples,
            "engines": _loads(batch.engines, []),
            "total_tasks": batch.total_tasks,
            "done_tasks": batch.done_tasks,
            "failed_tasks": batch.failed_tasks,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
        }
    samples = [s for s in sample_rows(batch_id) if s["ok"]]
    body = build_report(samples, meta["business"]["name"])
    return {**meta, **body}


def available_questions(business_id: int, city: str, categories: list[str], control: list[str]) -> list[dict]:
    with SessionLocal() as db:
        profile = business_profile(db, business_id)
    city = city or profile["city"] or "本地"
    return local_life.generate_questions(
        city, categories=categories or None, control_categories=control or None
    )


def did_from_batches(before_id: int, after_id: int) -> dict[str, Any]:
    """用两次跑批（基线 / 投喂后）算 DID，自动区分处理组与对照组。"""
    before = [s for s in sample_rows(before_id) if s["ok"]]
    after = [s for s in sample_rows(after_id) if s["ok"]]
    engines = sorted({s["engine"] for s in before} & {s["engine"] for s in after})
    out: dict[str, Any] = {"engines": {}, "note": "处理组=非对照组问题；对照组=同城不同品类"}
    from app.metrics import did as calc_did

    for eng in engines:
        b = [s for s in before if s["engine"] == eng]
        a = [s for s in after if s["engine"] == eng]
        t_b = [s for s in b if not s["is_control"]]
        c_b = [s for s in b if s["is_control"]]
        t_a = [s for s in a if not s["is_control"]]
        c_a = [s for s in a if s["is_control"]]
        if not (t_b and t_a and c_b and c_a):
            out["engines"][eng] = {"note": "缺少对照组或处理组样本，无法归因"}
            continue
        out["engines"][eng] = calc_did(
            sum(1 for s in t_a if s["mentioned"]),
            len(t_a),
            sum(1 for s in t_b if s["mentioned"]),
            len(t_b),
            sum(1 for s in c_a if s["mentioned"]),
            len(c_a),
            sum(1 for s in c_b if s["mentioned"]),
            len(c_b),
        )
    return out
