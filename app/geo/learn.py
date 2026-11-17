"""按「模型 × 渠道」学习规则权重。

与项目里原有的固定规则不同，这里的权重是从**引用结果**反推出来的：

    文档特征 X  ──（谁被引用）──▶  标签 y  ──逻辑回归──▶  该格的权重 w

拿到 w 之后不做黑箱使用，而是翻译成「规则卡」（哪个维度重要、阈值多少、
应该怎么改），既保留可解释性，也能直接喂给改写器。

【重要】当前没有真实模型 API，标签由 simulator 生成。模拟器用的是**非线性
+ 交互项 + 噪声**，学习器是线性逻辑回归，因此学出来的 AUC 明显小于 1——
这说明权重是"从样本里拟合出来的"，不是把先验原样抄回来。但它只能证明
"流程可跑通"，不能证明真实世界规律。source 字段会如实标注 simulated。
"""
from __future__ import annotations

import hashlib
import math
import random

import numpy as np

from ..cells.registry import ALL_DIMS, DIM_LABELS, ChannelEntry, ModelEntry
from ..geo import fit
from ..geo.analyzer import analyze
from ..geo.rules import TABLE_PATTERN
from ..rec.ranker import Ranker, auc

# 各维度的可执行建议：权重高说明模型爱这一口味，权重为负说明会扣分
ADVICE: dict[str, str] = {
    "conclusion_first": "首段直接给结论与适用人群，别铺垫背景",
    "structured": "用二级标题拆分主题，每节一个论点",
    "quantified": "补具体数值（尺寸/续航/价格/时间），避免形容词",
    "sourced": "标注可核验来源（官方参数页、检测报告、日期）",
    "atomic": "一段只讲一件事，长句拆短",
    "no_hype": "删掉极限词与促销话术（最强、全网最低、闭眼入）",
    "self_contained": "让正文脱离上下文也能读懂，不要写“详见图片/点击链接”",
    "term_defined": "专业名词首次出现时给一句定义",
    "timeliness": "标出内容的时间锚点（参数版本、更新时间）",
    "balanced": "写出适用边界与代价，只夸不贬会被判推广",
    "length_fit": "篇幅贴合该渠道的常规阅读长度",
    "has_table": "用表格并列参数，便于被逐项摘取",
    "has_faq": "补一段问答，覆盖长尾问法",
    "freshness": "提高内容新鲜度，陈旧内容难被检索命中",
    "authority": "抬高来源权威度（官方/第三方实测/白皮书）",
    "link_count": "控制外链数量，过多会被降权",
    "depth": "提高信息密度，减少空话段落",
}


def _norm(v: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo)))


def _freshness(text: str) -> float:
    import re
    years = [int(y) for y in re.findall(r"(20\d{2})\s*[年\-/]", text)]
    if not years:
        return 0.35
    latest = max(years)
    return _norm(latest, 2022, 2026)


def _authority(text: str) -> float:
    strong = ("官方参数", "白皮书", "检测报告", "国标", "GB/T", "实验室", "第三方实测", "抽样")
    hits = sum(1 for w in strong if w in text)
    return min(1.0, 0.25 + 0.2 * hits)


def _depth(text: str) -> float:
    """信息量用"绝对数字个数"而非"每段数字数"。

    早期版本用 数字/段落数，结果与被引用概率负相关——因为表格型长清单
    段落极少，这个比值虚高，与 length_fit 共线，逻辑回归把符号翻成了负的。
    换成对数绝对量后两者解耦。
    """
    import re
    nums = len(re.findall(r"\d+(?:\.\d+)?", text))
    return min(1.0, math.log1p(nums) / math.log1p(45))


_SUB_CACHE: dict[str, dict[str, float]] = {}


def _sub_scores(text: str) -> dict[str, float]:
    """10 个规则检查器纯正则，按内容哈希缓存。

    36 格全量学习要对同一批文档重复跑 36 次规则检查，不缓存要 10 秒以上，
    页面点一次「全部学习」就像卡死。
    """
    key = hashlib.md5(text.encode("utf-8")).hexdigest()
    cached = _SUB_CACHE.get(key)
    if cached is None:
        cached = analyze(text).sub_scores
        _SUB_CACHE[key] = cached
    return cached


def features(text: str, channel: ChannelEntry) -> dict[str, float]:
    """把一篇文档映射到学习特征空间（10 个规则分 + 7 个客观特征）。"""
    chars = len(text)
    # 篇幅适配：落在渠道区间的中段最高，越界快速衰减
    mid = (channel.min_words + channel.max_words) / 2
    span = max(1, (channel.max_words - channel.min_words) / 2)
    length_fit = max(0.0, 1.0 - abs(chars - mid) / span)
    link_count = min(1.0, text.count("http") / 5.0)
    feats = dict(_sub_scores(text))
    feats.update({
        "length_fit": length_fit,
        "has_table": 1.0 if TABLE_PATTERN.search(text) else 0.0,
        "has_faq": 1.0 if ("常见问题" in text or "FAQ" in text.upper() or "Q：" in text) else 0.0,
        "freshness": _freshness(text),
        "authority": _authority(text),
        "link_count": link_count,
        "depth": _depth(text),
    })
    return {k: float(feats.get(k, 0.0)) for k in ALL_DIMS}


# ------------------------------------------------------------ 模拟器 ----
def simulate_prob(model: ModelEntry, channel: ChannelEntry, x: dict[str, float]) -> float:
    """按模型先验 + 渠道偏置给出被盗用的概率。

    刻意加入交互项与非线性，使线性学习器无法完美拟合。
    """
    z = -2.55 + channel.bias * 1.3
    for dim, w in model.prior.items():
        if dim in x:
            z += w * x[dim]
    # 交互项：检索型入口更看重"新且权威"，中文入口更看重"结论+定义"
    if model.retrieval:
        z += 0.8 * x.get("freshness", 0) * x.get("authority", 0)
    if model.locale == "zh":
        z += 0.7 * x.get("conclusion_first", 0) * x.get("term_defined", 0)
    # 结构红利递减：表格已经很规整时，再多收益变小
    z += 0.6 * math.sqrt(max(0.0, x.get("has_table", 0))) * x.get("depth", 0)
    # 渠道硬约束不满足直接压分（如小红书放外链）
    if channel.max_links == 0 and x.get("link_count", 0) > 0.2:
        z -= 0.9
    return 1 / (1 + math.exp(-z))


def _rng_for(model_id: str, channel_id: str) -> random.Random:
    seed = int(hashlib.md5(f"{model_id}::{channel_id}".encode()).hexdigest()[:8], 16)
    return random.Random(seed)


def simulate_labels(model: ModelEntry, channel: ChannelEntry, X: np.ndarray,
                    rng: random.Random) -> np.ndarray:
    y = np.zeros(len(X), dtype=np.float64)
    for i, row in enumerate(X):
        x = {d: float(row[k]) for k, d in enumerate(ALL_DIMS)}
        p = simulate_prob(model, channel, x)
        # 噪声：模拟检索波动、query 差异
        p = min(0.98, max(0.02, p + rng.gauss(0, 0.06)))
        y[i] = 1.0 if rng.random() < p else 0.0
    return y


# ------------------------------------------------------------ 学习入口 ----
def _cards(weights: dict[str, float], corr: dict[str, float] | None = None,
           top_n: int = 8) -> list[dict]:
    """规则卡同时给出多变量权重与单变量相关。

    规则维度天然共线（好文案往往多项都高），逻辑回归会出现符号翻转。
    符号与单变量相关一致时判为稳定；不一致时明确标记 unstable，
    前端会提示"此维度受共线性影响，按相关方向解读"，而不是假装没这回事。
    """
    corr = corr or {}
    ranked = sorted(weights.items(), key=lambda kv: abs(kv[1]), reverse=True)[:top_n]
    cards: list[dict] = []
    for dim, w in ranked:
        c = corr.get(dim)
        stable = True if c is None else (c == 0 or (c > 0) == (w > 0))
        cards.append({
            "dim": dim,
            "label": DIM_LABELS.get(dim, dim),
            "weight": round(w, 3),
            "corr": round(c, 3) if c is not None else None,
            "stable": stable,
            "direction": "正向" if w >= 0 else "负向",
            "advice": ADVICE.get(dim, "—"),
            "threshold": 0.7 if w > 0 else 0.3,
        })
    return cards


def learn_cell(model: ModelEntry, channel: ChannelEntry, docs: list[str],
               queries: list[str] | None = None, sample: int = 140,
               epochs: int = 400) -> dict:
    """对一格做规则学习，返回权重、规则卡与拟合质量。

    传入 queries（测试问题集）时，问题集决定的是**谁进样本**，而不是谁天生得分高：
    先算每篇文档对整批问法的平均相关度，只保留能答到这批问法的那一半文档，
    再在样本内学习"什么样的文档会被引用"。这样学出来的权重是"针对这批用户问法
    的引用偏好"，同时不会把"问法覆盖率"这种特征集里没有的东西混进标签——
    早期版本用它做概率乘子，AUC 从 0.77 掉到 0.55，就是因为标签里混入了
    特征无法解释的成分。
    """
    rng = _rng_for(model.id, channel.id)
    picked = docs if len(docs) <= sample else rng.sample(docs, sample)
    qs = [q for q in (queries or []) if q][:12]

    if qs and len(picked) >= 40:
        rel = [sum(fit.relevance(t, q) for q in qs) / len(qs) for t in picked]
        pair = sorted(zip(rel, picked), key=lambda x: -x[0])
        keep = max(40, len(pair) // 2)
        picked = [t for _, t in pair[:keep]]

    rows = [features(t, channel) for t in picked]
    X = np.array([[r[d] for d in ALL_DIMS] for r in rows], dtype=np.float64)
    y = simulate_labels(model, channel, X, rng)

    model_lr = Ranker(features=list(ALL_DIMS)).fit(X, y, epochs=epochs)
    pred = model_lr.predict(X)
    score = auc(y, pred)
    weights = {d: float(w) for d, w in zip(ALL_DIMS, model_lr.w)}
    corr: dict[str, float] = {}
    for k, d in enumerate(ALL_DIMS):
        col = X[:, k]
        corr[d] = float(np.corrcoef(col, y)[0, 1]) if col.std() > 1e-9 else 0.0

    notes = ("标签由模拟器生成（模型先验 + 渠道偏置 + 交互项 + 噪声）"
             if not qs else
             f"样本 = {len(picked)} 篇候选文档（已按 {len(qs)} 条测试问法的相关度筛选），"
             "标签按「模型先验 + 交互项 + 噪声」生成")
    return {
        "model_id": model.id,
        "channel_id": channel.id,
        "samples": int(len(y)),
        "docs": len(picked),
        "queries_used": qs,
        "positive_rate": round(float(y.mean()), 3),
        "auc": round(float(score), 4),
        "weights": weights,
        "cards": _cards(weights, corr),
        "source": "simulated",
        "notes": notes + "；仅证明学习流程可跑通，不代表真实引用规律",
    }


def top_dims(weights: dict[str, float], n: int = 5) -> list[tuple[str, float]]:
    return sorted(weights.items(), key=lambda kv: abs(kv[1]), reverse=True)[:n]
