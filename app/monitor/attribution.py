"""投喂前后归因。

只看"投喂后被引用了吗"没有意义——可能本来就会被引用。正确做法是同一批 query
下比较**原稿**与**改写稿**：before 用素材原文，after 用按该格规则生成的版本。
两份内容走同一个模拟检索器，唯一变量就是内容本身，这样差值才能归因到改写。

（真实场景里 before 也可以是"投喂前该页面的线上快照"，机制完全一致。）
"""
from __future__ import annotations

import math

from ..cells.registry import ChannelEntry, ModelEntry
from . import probe


def _swap(docs: list[dict], doc_id: str, text: str) -> list[dict]:
    out = []
    for d in docs:
        out.append({**d, "text": text} if d["id"] == doc_id else d)
    return out


def _two_prop_z(h1: int, n1: int, h2: int, n2: int) -> float:
    if n1 == 0 or n2 == 0:
        return 0.0
    p1, p2 = h1 / n1, h2 / n2
    p = (h1 + h2) / (n1 + n2)
    se = math.sqrt(max(p * (1 - p), 1e-9) * (1 / n1 + 1 / n2))
    return (p2 - p1) / se if se else 0.0


def two_prop_z(h1: int, n1: int, h2: int, n2: int) -> float:
    """对外暴露的两比例 z 检验，看板与探针共用同一套口径。"""
    return round(_two_prop_z(h1, n1, h2, n2), 2)


def compare(queries: list[str], model: ModelEntry, channel: ChannelEntry,
            weights: dict[str, float] | None, docs: list[dict],
            asset_id: str, before_text: str, after_text: str) -> dict:
    corpus_before = probe.build_corpus(_swap(docs, asset_id, before_text), key="cmp_before")
    corpus_after = probe.build_corpus(_swap(docs, asset_id, after_text), key="cmp_after")

    before_rows = probe.run(queries, model, channel, weights, corpus_before, [asset_id])
    after_rows = probe.run(queries, model, channel, weights, corpus_after, [asset_id])
    before = probe.summarize(before_rows)
    after = probe.summarize(after_rows)

    if before["hit_rate"] == 0 and after["hit_rate"] == 0:
        verdict, level = "两版都未被引用，说明问题不在文案，而在该类目缺少可被检索的入口", "no_signal"
    elif after["samples"] < 20:
        verdict, level = "样本不足（每轮仅 %d 条 query），不足以判断效果" % after["samples"], "insufficient"
    elif abs(_two_prop_z(before["hits"], before["samples"],
                         after["hits"], after["samples"])) >= 1.96:
        verdict, level = "改写后引用率变化显著（|z| ≥ 1.96）", "significant"
    else:
        verdict, level = "改写后引用率变化不显著，暂不能归因于本次改写", "not_significant"

    detail = []
    for b, a in zip(before_rows, after_rows):
        detail.append({
            "query": b["query"],
            "before": {"cited": b["cited"], "rank": b["rank"]},
            "after": {"cited": a["cited"], "rank": a["rank"]},
            "citations": a["citations"],
        })

    return {
        "before": before,
        "after": after,
        "delta_hit_rate": round(after["hit_rate"] - before["hit_rate"], 3),
        "z": round(_two_prop_z(before["hits"], before["samples"],
                               after["hits"], after["samples"]), 2),
        "verdict": verdict,
        "level": level,
        "detail": detail,
        "note": "before = 素材原稿，after = 按该格规则生成的版本；两者走同一检索器，"
                "唯一变量是内容。接真实 API 后把模拟检索替换为模型返回的引用即可。",
    }
