"""五路召回：ItemCF / UserCF / 内容 / 热门 / GEO 质量通道。

第五路（geo）是本项目相对常规推荐系统的增量：当协同信号不足（新品、冷启动）
时，用 AutoGEO 规则算出的内容质量分在类目内做曝光分配。它让"文案写得好"本身
就能带来流量，而不是只能靠历史行为。
"""
from __future__ import annotations

import numpy as np

from .index import Index
from ..config import REC_CONFIG


def _norm(v: np.ndarray) -> np.ndarray:
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-9:
        return np.zeros_like(v)
    return (v - lo) / (hi - lo)


def channel_scores(idx: Index, user_pos: int, mask_seen: bool = True) -> dict[str, np.ndarray]:
    """返回每个召回通道对全部物品的打分（已归一化到 0-1）。

    mask_seen 必须区分两种用途，否则会造成特征穿越：
      - 召回/融合阶段（True）：已交互物品置零，用于过滤候选；
      - 排序特征阶段（False）：保留原始分。因为训练正样本本身就是已交互物品，
        若此处置零，模型会学到「itemcf=0 即正例」的伪规律，线上表现正好反向。
    """
    u = user_pos
    seen = idx.R[u] > 0

    itemcf = idx.item_cf @ idx.R[u]
    usercf = idx.user_cf[u] @ idx.R

    content = idx.item_vec @ idx.user_vec[u]

    hot = idx.pop.copy()

    # GEO 通道：类目偏好 × 类目内质量分位；对已交互物品置零由上层统一过滤
    pref_cats = set(idx.users[u]["preferred"])
    cat_mask = np.array([1.0 if c in pref_cats else 0.35 for c in idx.cat_of], dtype=np.float32)
    geo = (0.62 * idx.gqs_rank_in_cat + 0.38 * (idx.gqs / 100.0)) * cat_mask

    scores = {
        "itemcf": _norm(itemcf),
        "usercf": _norm(usercf),
        "content": _norm(content),
        "hot": _norm(hot),
        "geo": _norm(geo),
    }
    if mask_seen:
        for k in scores:
            scores[k][seen] = 0.0
    return scores


def recall(idx: Index, user_pos: int, k: int | None = None, exclude_seen: bool = True) -> dict[str, list[int]]:
    """各路取 Top-K，返回 {channel: [item_pos,...]}。"""
    k = k or REC_CONFIG.recall_k
    scores = channel_scores(idx, user_pos)
    out: dict[str, list[int]] = {}
    for ch, sc in scores.items():
        top = np.argsort(-sc)[:k]
        out[ch] = [int(i) for i in top if (not exclude_seen or idx.R[user_pos, i] == 0)]
    return out


def fused_candidates(idx: Index, user_pos: int, k: int | None = None) -> tuple[list[int], dict[int, dict[str, float]]]:
    """加权融合多路召回，返回候选集与每路贡献明细（用于前端解释）。"""
    k = k or REC_CONFIG.recall_k
    scores = channel_scores(idx, user_pos)
    contrib: dict[int, dict[str, float]] = {}
    fused: dict[int, float] = {}
    for ch, sc in scores.items():
        w = REC_CONFIG.weights[ch]
        top = np.argsort(-sc)[: max(k, 40)]
        for i in top:
            i = int(i)
            s = float(sc[i])
            if s <= 0:
                continue
            fused[i] = fused.get(i, 0.0) + w * s
            contrib.setdefault(i, {})[ch] = round(w * s, 4)
    ranked = sorted(fused, key=lambda i: fused[i], reverse=True)[:k]
    return ranked, contrib
