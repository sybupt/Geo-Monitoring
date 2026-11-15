"""规则 → 内容 → 投喂 → 复测 的闭环接口。

和 v2 的探测量接口分开挂：那边管「测」，这边管「学规则、写稿、投喂、再测」。
没有配置任何模型 Key 时，这条链路也能完整跑通（生成不调模型、复测走 mock 模拟）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.content.facts import build_facts
from app.content.generator import generate as generate_content
from app.content.validator import validate as validate_content
from app.database import SessionLocal
from app.industry import local_life
from app.models import Business
from app.rules import extractor, store
from app.rules.dsl import CHANNEL_SPECS, DIMENSIONS, confidence_label
from app.rules.models import ContentRow
from app.rules.prior import best_channels, prior_rules
from app.sim import corpus as sim
from app.v2 import service as v2_service

router = APIRouter(prefix="/api/v2", tags=["rules-content"])


# ── 规则库 ──────────────────────────────────────────────────────


class LearnPayload(BaseModel):
    industry: str = "local_life"
    engines: list[str] = Field(default_factory=lambda: ["doubao", "kimi", "deepseek", "qwen", "zhipu", "wenxin", "yuanbao", "mock"])
    channels: list[str] = Field(default_factory=list)
    mode: str = "prior"          # prior / measured
    batch_id: int | None = None  # mode=measured 时从哪次跑批的样本里学
    min_evidence: int = 6
    min_lift: float = 0.12


@router.post("/rules/learn")
def learn_rules(payload: LearnPayload):
    """学规则。prior = 用已知引擎画像冷启动；measured = 从真实跑批样本里统计。"""
    with SessionLocal() as db:
        if payload.mode == "measured":
            if not payload.batch_id:
                raise HTTPException(400, "measured 模式需要提供 batch_id")
            samples = v2_service.sample_rows(payload.batch_id)
            engines = sorted({s["engine"] for s in samples if s["ok"]})
            if not engines:
                raise HTTPException(400, "该批次没有可用样本")
            per_engine: dict[str, list[dict[str, Any]]] = {}
            for s in samples:
                if not s["ok"]:
                    continue
                vis = extractor.label_visibility(
                    bool(s.get("mentioned")), s.get("first_rank"), bool(s.get("is_recommended"))
                )
                per_engine.setdefault(s["engine"], []).append({"answer": s.get("answer", ""), "visibility": vis})

            all_rules = []
            for eng in engines:
                learned = extractor.learn_from_samples(
                    per_engine[eng],
                    engine=eng,
                    industry=payload.industry,
                    min_evidence=payload.min_evidence,
                    min_lift=payload.min_lift,
                )
                all_rules.extend(learned)
            merged = extractor.merge_rules(all_rules)
            stats = store.upsert(db, merged)
            return {
                "mode": "measured",
                "batch_id": payload.batch_id,
                "engines": engines,
                "rules": [r.to_dict() for r in merged],
                "stats": stats,
                "note": "实测规则来自真实回答的可见度差异；样本少于 20 条时只能算初步结论",
            }

        all_rules = []
        for eng in payload.engines:
            all_rules.extend(prior_rules(eng, payload.industry, payload.channels))
        stats = store.upsert(db, all_rules)
        rules = store.list_rules(db, industry=payload.industry)
        return {
            "mode": "prior",
            "engine_count": len(payload.engines),
            "rules": rules,
            "stats": stats,
            "note": "先验规则来自公开的引擎信源画像，未经实测校准；接上真实 Key 后请用 measured 模式覆盖",
        }


@router.get("/rules")
def get_rules(industry: str = "local_life", engine: str | None = None, channel: str | None = None):
    with SessionLocal() as db:
        rules = store.list_rules(db, engine=engine, industry=industry, channel=channel)
    by_dim: dict[str, int] = {}
    for r in rules:
        by_dim[r["dimension"]] = by_dim.get(r["dimension"], 0) + 1
    return {
        "industry": industry,
        "total": len(rules),
        "by_dimension": [{"dimension": k, "label": DIMENSIONS.get(k, {}).get("label", k), "count": v} for k, v in by_dim.items()],
        "rules": rules,
        "caliber": "prior = 先验（未实测）；measured = 实测。对外只可称 measured 为「模型偏好」",
    }


# ── 内容生成与校验 ───────────────────────────────────────────────


class GeneratePayload(BaseModel):
    business_id: int
    engine: str = "mock"
    channel: str = "zhihu"
    question: str = ""
    industry: str = "local_life"
    extra_facts: dict[str, Any] = Field(default_factory=dict)
    save: bool = True


def _load_facts(db: Session, business_id: int, extra: dict[str, Any]) -> tuple[Business, dict[str, Any]]:
    biz = db.get(Business, business_id)
    if biz is None:
        raise HTTPException(404, "企业不存在")
    return biz, build_facts(biz, extra)


def _ensure_rules(db: Session, *, engine: str, industry: str, channel: str = "") -> None:
    """规则库为空时自动用先验规则冷启动。

    否则用户第一次点「生成」会撞到「规则库为空」，还得先回去点一次「学规则」——
    这一步完全可以自动做，而且先验规则本身就是要被实测覆盖的。
    """
    if store.effective_rules(db, engine=engine, industry=industry, channel=channel):
        return
    channels = [channel] if channel else []
    store.upsert(db, prior_rules(engine, industry, channels))


@router.post("/content/generate")
def generate_one(payload: GeneratePayload):
    with SessionLocal() as db:
        biz, facts = _load_facts(db, payload.business_id, payload.extra_facts)
        _ensure_rules(db, engine=payload.engine, industry=payload.industry, channel=payload.channel)
        rules = store.effective_rules(db, engine=payload.engine, industry=payload.industry, channel=payload.channel)
        out = generate_content(facts, rules, engine=payload.engine, channel=payload.channel, question=payload.question)
        report = validate_content(
            out["content"], facts=facts, rules=rules, channel=payload.channel, brand=facts["name"]
        )
        row_id = None
        if payload.save:
            row = ContentRow(
                business_id=payload.business_id,
                engine=payload.engine,
                channel=payload.channel,
                question=payload.question,
                title=out["title"],
                content=out["content"],
                rule_hits=json.dumps(out["rule_hits"], ensure_ascii=False),
                validation=json.dumps(report, ensure_ascii=False),
                score=report["score"],
                status="passed" if report["pass"] else "rejected",
            )
            db.add(row)
            db.commit()
            row_id = row.id
        return {
            "content_id": row_id,
            "business": {"id": biz.id, "name": biz.name},
            "channel_label": (CHANNEL_SPECS.get(payload.channel) or {}).get("label", payload.channel),
            "engine": payload.engine,
            "title": out["title"],
            "content": out["content"],
            "chars": out["chars"],
            "channel_genre": out["channel_genre"],
            "rule_hits": out["rule_hits"],
            "validation": report,
            "facts_missing": out["facts_missing"],
            "rule_count": len(rules),
        }


class BatchGeneratePayload(BaseModel):
    business_id: int
    engines: list[str] = Field(default_factory=lambda: ["mock"])
    channels: list[str] = Field(default_factory=list)  # 空 = 每引擎取亲和度最高的 2 个渠道
    industry: str = "local_life"
    extra_facts: dict[str, Any] = Field(default_factory=dict)
    question: str = ""


@router.post("/content/generate-batch")
def generate_batch(payload: BatchGeneratePayload):
    """按「引擎 × 它最可能检索的渠道」批量出稿，只返回通过校验的。"""
    results = []
    with SessionLocal() as db:
        biz, facts = _load_facts(db, payload.business_id, payload.extra_facts)
        for eng in payload.engines:
            channels = payload.channels or [c["channel"] for c in best_channels(eng, limit=2)]
            for ch in channels:
                if ch not in CHANNEL_SPECS:
                    continue
                _ensure_rules(db, engine=eng, industry=payload.industry, channel=ch)
                rules = store.effective_rules(db, engine=eng, industry=payload.industry, channel=ch)
                if not rules:
                    continue
                out = generate_content(facts, rules, engine=eng, channel=ch, question=payload.question)
                report = validate_content(out["content"], facts=facts, rules=rules, channel=ch, brand=facts["name"])
                row = ContentRow(
                    business_id=payload.business_id,
                    engine=eng,
                    channel=ch,
                    question=payload.question,
                    title=out["title"],
                    content=out["content"],
                    rule_hits=json.dumps(out["rule_hits"], ensure_ascii=False),
                    validation=json.dumps(report, ensure_ascii=False),
                    score=report["score"],
                    status="passed" if report["pass"] else "rejected",
                )
                db.add(row)
                db.commit()
                results.append(
                    {
                        "content_id": row.id,
                        "engine": eng,
                        "channel": ch,
                        "channel_label": CHANNEL_SPECS[ch]["label"],
                        "title": out["title"],
                        "chars": out["chars"],
                        "score": report["score"],
                        "pass": report["pass"],
                        "issues": report["issues"],
                        "blocking": [i["message"] for i in report["issues"] if i["level"] == "fatal"],
                    }
                )
    passed = [r for r in results if r["pass"]]
    return {
        "business": biz.name,
        "total": len(results),
        "passed": len(passed),
        "rejected": len(results) - len(passed),
        "items": results,
        "facts_missing": facts.get("missing", []),
    }


@router.get("/content")
def list_content(business_id: int, limit: int = 30):
    with SessionLocal() as db:
        rows = (
            db.query(ContentRow)
            .filter(ContentRow.business_id == business_id)
            .order_by(ContentRow.id.desc())
            .limit(limit)
            .all()
        )
        return {
            "total": len(rows),
            "items": [
                {
                    "content_id": r.id,
                    "engine": r.engine,
                    "channel": r.channel,
                    "channel_label": (CHANNEL_SPECS.get(r.channel) or {}).get("label", r.channel),
                    "title": r.title,
                    "chars": len(r.content or ""),
                    "score": r.score,
                    "status": r.status,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }


@router.post("/content/{content_id}/feed")
def feed_content(content_id: int):
    """把通过校验的稿子「投喂」到模拟语料（无 Key 阶段的占位动作）。"""
    with SessionLocal() as db:
        row = db.get(ContentRow, content_id)
        if row is None:
            raise HTTPException(404, "稿件不存在")
        report = json.loads(row.validation or "{}")
        if not report.get("pass"):
            raise HTTPException(400, "校验未通过的稿件不允许投喂，请先修事实或改稿")
        doc = sim.feed(
            db,
            business_id=row.business_id,
            engine=row.engine,
            channel=row.channel,
            title=row.title,
            content=row.content,
            quality=report.get("score", 0.0) or row.score or 0.0,
            content_id=row.id,
        )
        row.status = "fed"
        db.commit()
        return {
            "content_id": row.id,
            "sim_doc_id": doc.id,
            "quality": doc.quality,
            "note": "已写入模拟语料，仅 mock 引擎可读；真实投喂需走渠道发布（v2/publish-tasks）",
        }


@router.delete("/sim/corpus")
def clear_sim(business_id: int | None = None):
    with SessionLocal() as db:
        n = sim.clear(db, business_id)
        return {"deleted": n, "business_id": business_id}


# ── 闭环演示：学规则 → 出稿 → 校验 → 投喂 → 复测 ──────────────────


class LoopPayload(BaseModel):
    business_id: int
    industry: str = "local_life"
    engines: list[str] = Field(default_factory=lambda: ["mock"])
    channels: list[str] = Field(default_factory=list)
    extra_facts: dict[str, Any] = Field(default_factory=dict)
    n_samples: int = 3
    questions: list[str] = Field(default_factory=list)
    reset: bool = True


@router.post("/loop/demo")
def loop_demo(payload: LoopPayload):
    """一键跑完整闭环，返回「投喂前 vs 投喂后」的提及率对比。

    用的是 mock 引擎，所以结论只能说明链路通、逻辑对，不能作为效果证据。
    """
    with SessionLocal() as db:
        biz, facts = _load_facts(db, payload.business_id, payload.extra_facts)
        # 会话关掉后 ORM 对象会 detached，先把要用的字段取出来
        biz_info = {"id": biz.id, "name": biz.name, "region": biz.region or "", "category": biz.category or ""}
        name, region, category = biz_info["name"], biz_info["region"], biz_info["category"]
        if payload.reset:
            sim.clear(db, payload.business_id)

    questions = (
        [{"question_key": f"Q{i}", "text": q, "intent": "custom", "is_control": False} for i, q in enumerate(payload.questions)]
        if payload.questions
        else local_life.generate_questions(region or "本地", categories=[category] if category else None, per_intent=1)
    )

    def run_mock(stage: str, note: str) -> dict[str, Any]:
        with SessionLocal() as db:
            batch = v2_service.create_batch(
                db,
                business_id=payload.business_id,
                engines=["mock"],
                questions=questions,
                n_samples=payload.n_samples,
                stage=stage,
                judge_mode="rule_only",
                competitors=["竞品A", "竞品B"],
                note=note,
            )
            batch_id = batch.id
        v2_service.run_batch(batch_id)
        report = v2_service.report(batch_id)
        engine = (report.get("engines") or {}).get("mock", {})
        rate = engine.get("mention_rate") or {}
        first = engine.get("first_mention_rate") or {}
        return {
            "batch_id": batch_id,
            "mention_rate": rate.get("rate", 0.0),
            "mention_ci": rate.get("ci95"),
            "first_mention_rate": first.get("rate", 0.0),
            "first_mention_ci": first.get("ci95"),
            "avg_rank": engine.get("avg_rank"),
            "samples": engine.get("n", 0),
        }

    before = run_mock("baseline", "闭环演示：投喂前")

    generated: list[dict[str, Any]] = []
    with SessionLocal() as db:
        for eng in payload.engines:
            channels = payload.channels or [c["channel"] for c in best_channels(eng, limit=2)]
            for ch in channels:
                if ch not in CHANNEL_SPECS:
                    continue
                _ensure_rules(db, engine=eng, industry=payload.industry, channel=ch)
                rules = store.effective_rules(db, engine=eng, industry=payload.industry, channel=ch)
                if not rules:
                    continue
                out = generate_content(facts, rules, engine=eng, channel=ch)
                report = validate_content(out["content"], facts=facts, rules=rules, channel=ch, brand=name)
                row = ContentRow(
                    business_id=payload.business_id,
                    engine=eng,
                    channel=ch,
                    title=out["title"],
                    content=out["content"],
                    rule_hits=json.dumps(out["rule_hits"], ensure_ascii=False),
                    validation=json.dumps(report, ensure_ascii=False),
                    score=report["score"],
                    status="passed" if report["pass"] else "rejected",
                )
                db.add(row)
                db.commit()
                fed = False
                if report["pass"]:
                    sim.feed(
                        db,
                        business_id=payload.business_id,
                        engine=eng,
                        channel=ch,
                        title=out["title"],
                        content=out["content"],
                        quality=report["score"],
                        content_id=row.id,
                    )
                    row.status = "fed"
                    db.commit()
                    fed = True
                generated.append(
                    {
                        "content_id": row.id,
                        "engine": eng,
                        "channel": ch,
                        "channel_label": CHANNEL_SPECS[ch]["label"],
                        "title": out["title"],
                        "chars": out["chars"],
                        "score": report["score"],
                        "pass": report["pass"],
                        "fed": fed,
                        "issues": [i["message"] for i in report["issues"]],
                        "rule_dimensions": [h["dimension"] for h in out["rule_hits"] if h["applied"]],
                    }
                )

    after = run_mock("after", "闭环演示：投喂后")
    delta = round(after["mention_rate"] - before["mention_rate"], 3)
    return {
        "business": biz_info,
        "facts_missing": facts.get("missing", []),
        "before": before,
        "after": after,
        "delta": delta,
        "contents": generated,
        "simulation": True,
        "notice": "本轮用 mock 引擎模拟引用，只能说明链路打通与规则生效逻辑，不能作为真实效果证据；接上真实 Key 后请用真实跑批复测",
    }
