"""投喂适配分：改写前后用同一把尺子量。

只用 GQS 不够——GQS 衡量"这篇稿子本身写得好不好"，不衡量"在这条渠道、
这个模型、这批问法下能不能被引用"。所以把三件事合成一个 0-100 分：

    score = 100 × (0.45 × GQS/100 + 0.35 × 规则适配 + 0.20 × 问法覆盖)

  · GQS      内容硬质量（10 条确定性规则检查器）
  · 规则适配  该格学到的权重对这篇稿子的打分（逻辑回归输出的概率）
  · 问法覆盖  测试问题集里的问法词被覆盖的比例（检索端能否排上来）

三项都是 0-1，权重写死并对外展示，避免"黑箱涨分"。
"""
from __future__ import annotations

import math

from ..cells.registry import ChannelEntry
from ..geo import fit
from ..geo.analyzer import analyze
from ..geo.learn import features

PARTS = {"gqs": 0.45, "rule_fit": 0.35, "coverage": 0.20}


def rule_fit(text: str, channel: ChannelEntry, weights: dict[str, float] | None) -> float:
    if not weights:
        return 0.5
    x = features(text, channel)
    z = sum(w * x.get(d, 0.0) for d, w in weights.items()) / max(1, len(weights)) * 3
    return 1 / (1 + math.exp(-z))


def score(text: str, channel: ChannelEntry, weights: dict[str, float] | None,
          queries: list[str], report=None) -> dict:
    report = report or analyze(text)
    gqs = report.gqs
    rf = rule_fit(text, channel, weights)
    cov = fit.coverage_mean(text, queries)
    total = 100 * (PARTS["gqs"] * gqs / 100 + PARTS["rule_fit"] * rf + PARTS["coverage"] * cov)
    return {
        "gqs": round(gqs, 1),
        "grade": report.grade,
        "rule_fit": round(rf, 3),
        "coverage": round(cov, 3),
        "score": round(total, 1),
        "chars": len(text),
    }


def delta(before: dict, after: dict) -> dict:
    return {
        "score": round(after["score"] - before["score"], 1),
        "gqs": round(after["gqs"] - before["gqs"], 1),
        "rule_fit": round(after["rule_fit"] - before["rule_fit"], 3),
        "coverage": round(after["coverage"] - before["coverage"], 3),
        "chars": after["chars"] - before["chars"],
    }
