"""规则抽取：从「高可见 / 低可见」样本对里学出规则，以及跨引擎合并。

两种来源：
- ``learn_from_samples``：实测学习。输入是真实引擎的回答（带提及/排名标签），
  统计每个文本特征在「被提及样本」与「未被提及样本」中的出现率差，换算成 lift。
  这是唯一能称为「实测模型偏好」的路径。
- ``prior.prior_rules``：先验规则，没有实测数据时用来冷启动。

合并规则（对应 AutoGEO 的 Merger）：跨引擎重叠高的维度收成 shared 规则，
差异大的保留引擎专属——因为「引擎专属规则效果最好」，不能为了整齐把差异抹平。
"""

from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any, Iterable

from app.rules.dsl import DIMENSIONS, Rule
from app.rules.features import extract

HIGH_VIS = 0.6   # 视为「高可见」的阈值
LOW_VIS = 0.3    # 视为「低可见」的阈值


def label_visibility(mentioned: bool, first_rank: int | None, is_recommended: bool = False) -> float:
    """把判定结果压成一个 0~1 的可见度分数。

    首位提及 = 1.0；前三位 = 0.7；提到但排名靠后 = 0.45；被明确推荐但没排名 = 0.6；
    完全没提到 = 0。
    """
    if not mentioned:
        return 0.0
    if first_rank == 1:
        return 1.0
    if first_rank and first_rank <= 3:
        return 0.7
    if is_recommended:
        return 0.6
    return 0.45


def learn_from_samples(
    records: Iterable[dict[str, Any]],
    *,
    engine: str,
    industry: str = "local_life",
    channel: str = "",
    brand: str = "",
    region: str = "",
    min_evidence: int = 6,
    min_lift: float = 0.12,
) -> list[Rule]:
    """records: [{answer, visibility}]，同引擎同行业的一批样本。

    产出规则按 lift 降序；证据不足或提升不明显的维度会被丢掉（Filter 环节）。
    """
    rows = []
    for rec in records:
        text = rec.get("answer") or ""
        if len(text) < 30:
            continue
        feats = extract(text, brand=brand or rec.get("brand", ""), region=region, channel=channel)
        rows.append((feats, float(rec.get("visibility", 0.0))))

    if len(rows) < min_evidence:
        return []

    rules: list[Rule] = []
    for dim in DIMENSIONS:
        present = [v for f, v in rows if f.get(dim)]
        absent = [v for f, v in rows if not f.get(dim)]
        if len(present) < min_evidence or len(absent) < 3:
            continue
        a, b = mean(present), mean(absent)
        lift = (a - b) / max(b, 0.05)
        if lift < min_lift:
            continue
        rules.append(
            Rule(
                dimension=dim,
                instruction=DIMENSIONS[dim]["instruction"],
                engine=engine,
                industry=industry,
                channel=channel,
                weight=round(min(1.0, 0.5 + lift / 2), 2),
                evidence_n=len(present),
                lift=round(lift, 3),
                source="measured",
                scope="engine",
                note=f"实测：满足该特征的 {len(present)} 条样本平均可见度 {a:.2f}，不满足的 {len(absent)} 条为 {b:.2f}",
            )
        )
    return sorted(rules, key=lambda r: -r.lift)


def evaluate(texts: list[str], rules: list[Rule], *, brand: str = "", region: str = "", channel: str = "") -> dict[str, Any]:
    """给一组文本打分：按规则权重加权，看这套规则解释了多少「可引用性」。"""
    if not texts:
        return {"score": 0.0, "coverage": 0.0, "detail": []}
    total_w = sum(r.weight for r in rules) or 1.0
    per_rule: dict[str, int] = defaultdict(int)
    scores = []
    for text in texts:
        feats = extract(text, brand=brand, region=region, channel=channel)
        hit_w = 0.0
        for r in rules:
            if feats.get(r.dimension):
                hit_w += r.weight
                per_rule[r.dimension] += 1
        scores.append(hit_w / total_w)
    detail = [
        {
            "dimension": d,
            "dimension_label": DIMENSIONS.get(d, {}).get("label", d),
            "hits": c,
            "hit_rate": round(c / len(texts), 2),
        }
        for d, c in sorted(per_rule.items(), key=lambda x: -x[1])
    ]
    return {
        "score": round(mean(scores), 3),
        "coverage": round(len(per_rule) / max(len(rules), 1), 2),
        "detail": detail,
    }


def merge_rules(rules: list[Rule], *, same_threshold: float = 0.15) -> list[Rule]:
    """跨引擎合并：同一行业/渠道/维度下，各引擎 lift 接近 → 收成 shared。"""
    buckets: dict[tuple[str, str, str], list[Rule]] = defaultdict(list)
    for r in rules:
        buckets[(r.industry, r.channel, r.dimension)].append(r)

    merged: list[Rule] = []
    for (industry, channel, dim), group in buckets.items():
        measured = [r for r in group if r.source == "measured"]
        if len(measured) < 2:
            merged.extend(group)
            continue
        lifts = [r.lift for r in measured]
        if max(lifts) - min(lifts) <= same_threshold:
            best = max(measured, key=lambda r: r.lift)
            merged.append(
                Rule(
                    dimension=dim,
                    instruction=best.instruction,
                    engine="shared",
                    industry=industry,
                    channel=channel,
                    weight=round(mean([r.weight for r in measured]), 2),
                    evidence_n=sum(r.evidence_n for r in measured),
                    lift=round(mean(lifts), 3),
                    source="measured",
                    scope="shared",
                    note=f"跨 {len(measured)} 个引擎表现一致（lift 差异 ≤ {same_threshold}），合并为通用规则",
                )
            )
        else:
            merged.extend(measured)  # 差异大 → 保留引擎专属
    return merged
