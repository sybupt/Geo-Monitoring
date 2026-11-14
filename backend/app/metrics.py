"""指标层：把「提及率」拆成能被人追问的五个数，并给区间。

只报一个「提及率 60%」是撑不住的，客户三个问题就能问倒你：
- 提了但排第几？→ first_mention_rate
- 说的是好话吗？→ sentiment / recommend_rate
- 竞品占多少？→ share_of_voice
- 你凭什么说提升是投喂带来的？→ did()

统计纪律：
- 二项比例一律给 Wilson 区间，不给单点。N=5 时区间很宽，这是事实，不许藏。
- 引用按 citation_type 分组，answer_level 和 retrieval_pool 不许加总。
- 记忆口径（DeepSeek 不联网）和检索口径分开列出，不给跨口径排名。
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

Z = 1.96  # 95%

DOMAIN_RULES: list[tuple[tuple[str, ...], str]] = [
    (("dianping.com", "meituan.com", "m.dianping", "amap.com", "baidu.com/map"), "本地生活平台"),
    (("xiaohongshu.com", "xhslink.com"), "笔记种草"),
    (("douyin.com", "kuaishou.com", "ixigua.com"), "短视频"),
    (("zhihu.com",), "问答社区"),
    (("baike.baidu.com", "wikipedia.org", "baike.sogou.com"), "百科"),
    (("gov.cn", "people.com.cn", "xinhuanet.com", "cctv.com", "chinanews.com"), "官媒/政务"),
    (("weixin.qq.com", "mp.weixin.qq.com"), "公众号"),
    (("sohu.com", "sina.com.cn", "163.com", "qq.com", "ifeng.com"), "综合门户"),
    (("36kr.com", "huxiu.com", "iyiou.com", "tmtpost.com"), "行业媒体"),
]


def wilson_interval(k: int, n: int, z: float = Z) -> tuple[float, float]:
    """二项比例的 Wilson 置信区间。n=0 返回 (0,1)，表示「什么都不知道」。"""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def rate_block(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson_interval(k, n)
    return {
        "count": k,
        "n": n,
        "rate": round(k / n, 4) if n else 0.0,
        "ci95": [round(lo, 4), round(hi, 4)],
        "ci_width": round(hi - lo, 4),
    }


def classify_domain(domain: str) -> str:
    d = (domain or "").lower()
    for keys, label in DOMAIN_RULES:
        if any(k in d for k in keys):
            return label
    return "其他"


def summarize_engine(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """单引擎聚合。samples 里每条是一次采样的判定结果。"""
    rows = [s for s in samples if s.get("ok", True)]
    n = len(rows)
    if n == 0:
        return {"n": 0, "note": "无有效采样"}

    mentioned = [r for r in rows if r.get("mentioned")]
    rec = [r for r in rows if (r.get("sentiment") or 0) > 0]
    neg = [r for r in rows if (r.get("sentiment") or 0) < 0]
    ranks = [r.get("first_rank") for r in mentioned if r.get("first_rank")]

    brand_hits = len(mentioned)
    comp_hits = sum(len(r.get("competitors") or []) for r in rows)

    domain_counter: Counter[str] = Counter()
    type_counter: Counter[str] = Counter()
    citation_types: Counter[str] = Counter()
    for r in rows:
        citation_types[r.get("citation_type") or "none"] += 1
        for c in r.get("citations") or []:
            if c.get("domain"):
                domain_counter[c["domain"]] += 1
                type_counter[classify_domain(c["domain"])] += 1

    first_hits = sum(1 for x in ranks if x == 1)
    rank_coverage = round(len(ranks) / n, 4) if n else 0.0
    warnings: list[str] = []
    if n and rank_coverage < 0.5:
        warnings.append(
            f"仅有 {rank_coverage:.0%} 的采样能判断排名，首位提及率仅供参考，不宜对外汇报"
        )

    return {
        "n": n,
        "engine": rows[0].get("engine"),
        "tier": rows[0].get("tier"),
        "memory_mode": rows[0].get("memory_mode", False),
        "model_versions": sorted({r.get("model_version") or "unknown" for r in rows}),
        "citation_types": dict(citation_types),
        "mention_rate": rate_block(brand_hits, n),
        "first_mention_rate": rate_block(first_hits, n),
        "rank_known": len(ranks),
        "rank_coverage": rank_coverage,
        "warnings": warnings,
        "recommend_rate": rate_block(len(rec), n),
        "negative_rate": rate_block(len(neg), n),
        "avg_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
        "share_of_voice": round(brand_hits / (brand_hits + comp_hits), 4)
        if (brand_hits + comp_hits)
        else 0.0,
        "sov_detail": {"brand": brand_hits, "competitors": comp_hits},
        "top_domains": domain_counter.most_common(10),
        "source_types": type_counter.most_common(),
    }


def did(
    after_t: int, n_t: int, before_t: int, n_t_before: int, after_c: int, n_c: int, before_c: int, n_c_before: int
) -> dict[str, Any]:
    """双重差分：Δ = (处理组后-前) - (对照组后-前)。

    这是唯一能挡掉「模型升级了 / 竞品也在动」这类质疑的结构。没有对照组就别声称因果关系。
    """
    pt_a = after_t / n_t if n_t else 0.0
    pt_b = before_t / n_t_before if n_t_before else 0.0
    pc_a = after_c / n_c if n_c else 0.0
    pc_b = before_c / n_c_before if n_c_before else 0.0
    delta = (pt_a - pt_b) - (pc_a - pc_b)

    var = 0.0
    for p, nn in ((pt_a, n_t), (pt_b, n_t_before), (pc_a, n_c), (pc_b, n_c_before)):
        if nn:
            var += p * (1 - p) / nn
    se = math.sqrt(var)
    return {
        "treated_delta": round(pt_a - pt_b, 4),
        "control_delta": round(pc_a - pc_b, 4),
        "did": round(delta, 4),
        "se": round(se, 4),
        "ci95": [round(delta - Z * se, 4), round(delta + Z * se, 4)],
        "significant": abs(delta) > Z * se if se > 0 else False,
        "note": "置信区间跨 0 时不能声称投喂带来了提升",
    }


def build_report(samples: list[dict[str, Any]], brand: str) -> dict[str, Any]:
    """一次跑批的完整报告：按引擎、按问题、按口径分组。"""
    by_engine: dict[str, list[dict]] = defaultdict(list)
    by_question: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    controls: list[dict] = []

    for s in samples:
        by_engine[s["engine"]].append(s)
        by_question[s["question_key"]][s["engine"]].append(s)
        if s.get("is_control"):
            controls.append(s)

    engines = {eng: summarize_engine(rows) for eng, rows in by_engine.items()}

    questions = []
    for qkey, per_engine in by_question.items():
        first = next(iter(per_engine.values()))[0]
        questions.append(
            {
                "question_key": qkey,
                "question": first["question_text"],
                "intent": first.get("intent"),
                "is_control": bool(first.get("is_control")),
                "engines": {
                    eng: {
                        "mention_rate": summarize_engine(rows)["mention_rate"],
                        "avg_rank": summarize_engine(rows)["avg_rank"],
                    }
                    for eng, rows in per_engine.items()
                },
            }
        )

    memory_mode = [e for e, v in engines.items() if v.get("memory_mode")]
    retrieval_mode = [e for e, v in engines.items() if v.get("n") and not v.get("memory_mode")]

    warnings = [f"{eng}：{w}" for eng, v in engines.items() for w in v.get("warnings", [])]

    return {
        "brand": brand,
        "total_samples": len(samples),
        "engines": engines,
        "questions": sorted(questions, key=lambda x: x["question_key"]),
        "warnings": warnings,
        "control_group": {
            "samples": len(controls),
            "note": "对照组＝同行业但不投喂的问题，用于 DID 归因",
        },
        "caliber_notice": {
            "retrieval_engines": retrieval_mode,
            "memory_engines": memory_mode,
            "warning": (
                "检索口径（有联网）与记忆口径（无联网）不可互相排名；"
                "answer_level 与 retrieval_pool 引用不可加总。"
            ),
        },
    }
