"""规则库读写。规则不是「建议文案」，是带证据量的数据行。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.rules.dsl import Rule, applies_to_channel, confidence_label
from app.rules.models import RuleRow


def _row_to_rule(row: RuleRow) -> Rule:
    return Rule(
        dimension=row.dimension,
        instruction=row.instruction or "",
        engine=row.engine or "shared",
        industry=row.industry or "local_life",
        channel=row.channel or "",
        weight=row.weight or 0.5,
        evidence_n=row.evidence_n or 0,
        lift=row.lift or 0.0,
        source=row.source or "prior",
        scope=row.scope or "engine",
        note=row.note or "",
    )


def upsert(db: Session, rules: list[Rule], *, replace_prior: bool = True) -> dict[str, int]:
    """写入规则。同键覆盖；实测规则可以覆盖同键先验，反之不允许。"""
    created = updated = skipped = 0
    for r in rules:
        row = (
            db.query(RuleRow)
            .filter(
                RuleRow.engine == r.engine,
                RuleRow.industry == r.industry,
                RuleRow.channel == r.channel,
                RuleRow.dimension == r.dimension,
            )
            .first()
        )
        if row is None:
            db.add(
                RuleRow(
                    engine=r.engine,
                    industry=r.industry,
                    channel=r.channel,
                    dimension=r.dimension,
                    instruction=r.instruction,
                    weight=r.weight,
                    evidence_n=r.evidence_n,
                    lift=r.lift,
                    source=r.source,
                    scope=r.scope,
                    note=r.note,
                )
            )
            created += 1
            continue
        # 先验不许覆盖实测：实测才是证据
        if row.source == "measured" and r.source == "prior":
            skipped += 1
            continue
        row.instruction = r.instruction
        row.weight = r.weight
        row.evidence_n = r.evidence_n
        row.lift = r.lift
        row.source = r.source
        row.scope = r.scope
        row.note = r.note
        row.version = (row.version or 1) + 1
        row.updated_at = datetime.utcnow()
        updated += 1
    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


def list_rules(
    db: Session,
    *,
    engine: str | None = None,
    industry: str | None = None,
    channel: str | None = None,
) -> list[dict[str, Any]]:
    q = db.query(RuleRow)
    if engine:
        q = q.filter(RuleRow.engine == engine)
    if industry:
        q = q.filter(RuleRow.industry == industry)
    if channel is not None:
        q = q.filter(RuleRow.channel == channel)
    rows = q.order_by(RuleRow.engine, RuleRow.weight.desc()).all()
    out = []
    for row in rows:
        r = _row_to_rule(row)
        d = r.to_dict()
        d["confidence"] = confidence_label(r)
        d["updated_at"] = row.updated_at.isoformat() if row.updated_at else None
        d["version"] = row.version
        out.append(d)
    return out


def effective_rules(db: Session, *, engine: str, industry: str = "local_life", channel: str = "") -> list[Rule]:
    """某引擎在某渠道下实际生效的规则集合。

    优先级：渠道专属 > 引擎专属 > 跨引擎 shared。同维度只保留权重最高的一条，
    否则同一个动作会被要求两遍，生成器会写重复内容。
    """
    rows = (
        db.query(RuleRow)
        .filter(
            RuleRow.industry == industry,
            RuleRow.engine.in_([engine, "shared"]),
            RuleRow.channel.in_([channel, ""] if channel else [""]),
        )
        .all()
    )
    best: dict[str, Rule] = {}
    prio = {"channel": 3, "engine": 2, "shared": 1}

    def level(r: Rule) -> int:
        if r.channel:
            return prio["channel"]
        return prio["engine"] if r.engine != "shared" else prio["shared"]

    for row in rows:
        r = _row_to_rule(row)
        # 先验规则要按渠道适配性过滤（口播脚本不要求 FAQ）；实测规则是数据结论，一律保留
        if r.source == "prior" and channel and not applies_to_channel(r.dimension, channel):
            continue
        cur = best.get(r.dimension)
        if cur is None or (level(r), r.weight) > (level(cur), cur.weight):
            best[r.dimension] = r
    return sorted(best.values(), key=lambda r: -r.weight)
