"""特征工程：把召回分、用户-物品匹配度、AutoGEO 内容质量拼成排序模型输入。

特征分成两组，这正好是 A/B 的边界：
  BASELINE —— 常规推荐系统能拿到的信号（协同、内容相似、热度、价格匹配）
  GEO      —— 只有接了 AutoGEO 规则引擎才存在的信号（整体质量分、类目内质量分位、分项规则命中）
"""
from __future__ import annotations

import math

import numpy as np

from .index import Index
from .recall import channel_scores

BASELINE_FEATURES = [
    "itemcf", "usercf", "content", "hot",
    "cat_pref", "brand_pref", "price_fit",
]
# 文案信息量也归入 GEO 组：baseline 系统不解析正文长度，接规则引擎后才有这一维
GEO_FEATURES = [
    "gqs", "gqs_rank_in_cat", "text_len", "geo_conclusion", "geo_structured",
    "geo_quantified", "geo_no_hype", "geo_affinity",
]
ALL_FEATURES = BASELINE_FEATURES + GEO_FEATURES

# 冷启动物品没有协同信号（itemcf/usercf/hot 恒为 0），只能靠"内容理解"排序。
# 这两组特征就是冷启动通路的对照组，训练集仍是热物品，不含任何新品标签。
COLD_BASE_FEATURES = ["content", "cart_pref_placeholder"]
COLD_BASE_FEATURES = ["content", "brand_pref", "price_fit", "cat_pref"]
COLD_GEO_FEATURES = COLD_BASE_FEATURES + [
    "gqs", "gqs_rank_in_cat", "text_len",
    "geo_conclusion", "geo_structured", "geo_quantified", "geo_no_hype", "geo_affinity",
]

FEATURE_LABELS = {
    "itemcf": "协同过滤（ItemCF）",
    "usercf": "相似用户（UserCF）",
    "content": "内容相似度",
    "hot": "全局热度",
    "cat_pref": "类目偏好",
    "brand_pref": "品牌偏好",
    "price_fit": "价格匹配",

    "text_len": "正文信息量",
    "gqs": "GEO 内容质量分",
    "gqs_rank_in_cat": "类目内质量分位",
    "geo_conclusion": "规则·结论前置",
    "geo_structured": "规则·结构化",
    "geo_quantified": "规则·量化细节",
    "geo_no_hype": "规则·去推广用语",
    "geo_affinity": "用户质量偏好 × 内容质量",
}

BAND_CENTER = {"<1000": 500, "1000-3000": 1800, "3000-6000": 4200, ">6000": 8000}


def price_fit(price: np.ndarray, band: str) -> np.ndarray:
    ideal = BAND_CENTER.get(band, 1800)
    return np.exp(-np.abs(np.log(np.maximum(price, 1.0) / ideal)))


def cat_pref(idx: Index, user_pos: int) -> np.ndarray:
    pref = idx.users[user_pos]["preferred"]
    out = np.zeros(len(idx.items), dtype=np.float32)
    for i, c in enumerate(idx.cat_of):
        if c == pref[0]:
            out[i] = 1.0
        elif c == pref[1]:
            out[i] = 0.55
        else:
            out[i] = 0.15
    return out


def user_activity(idx: Index) -> np.ndarray:
    acts = np.array([u["activity"] for u in idx.users], dtype=np.float32)
    lo, hi = acts.min(), acts.max()
    return (acts - lo) / max(hi - lo, 1e-6)


def vectorize_user(idx: Index, user_pos: int) -> dict[str, np.ndarray]:
    """一次性算出该用户对全部物品的特征列（向量化，避免逐物品循环）。"""
    sc = channel_scores(idx, user_pos, mask_seen=False)
    feats: dict[str, np.ndarray] = {
        "itemcf": sc["itemcf"],
        "usercf": sc["usercf"],
        "content": sc["content"],
        "hot": sc["hot"],
        "cat_pref": cat_pref(idx, user_pos),
        "brand_pref": idx.brand_aff[user_pos][idx.brand_of],
        "price_fit": price_fit(idx.price, idx.users[user_pos]["price_band"]),
        "text_len": np.clip(idx.text_len / 1200.0, 0, 1),
        "gqs": idx.gqs / 100.0,
        "gqs_rank_in_cat": idx.gqs_rank_in_cat,
        "geo_conclusion": idx.geo_subs["conclusion_first"],
        "geo_structured": idx.geo_subs["structured"],
        "geo_quantified": idx.geo_subs["quantified"],
        "geo_no_hype": idx.geo_subs["no_hype"],
        # 交互项：对"本来就偏好高质量文案"的用户，内容质量分的边际价值更大
        "geo_affinity": (idx.gqs / 100.0) * (idx.user_hist_gqs[user_pos] / 100.0),
    }
    return feats


def matrix_for(idx: Index, user_pos: int, item_positions: list[int], features: list[str]) -> np.ndarray:
    feats = vectorize_user(idx, user_pos)
    cols = [feats[f][item_positions] for f in features]
    return np.stack(cols, axis=1).astype(np.float64)


def explain_row(idx: Index, user_pos: int, item_pos: int, weights: np.ndarray, features: list[str]) -> list[dict]:
    feats = vectorize_user(idx, user_pos)
    rows = []
    for name, w in zip(features, weights):
        v = float(feats[name][item_pos])
        rows.append({
            "feature": name,
            "label": FEATURE_LABELS.get(name, name),
            "value": round(v, 3),
            "weight": round(float(w), 3),
            "contribution": round(float(w) * v, 4),
            "group": "geo" if name in GEO_FEATURES else "base",
        })
    rows.sort(key=lambda r: abs(r["contribution"]), reverse=True)
    return rows
