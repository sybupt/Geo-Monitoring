"""内存索引：协同过滤矩阵、内容向量、热度，全部用 numpy 现算。

规模（420 用户 × 280 物品）下没必要上 Faiss/Redis，全量矩阵常驻内存即可，
每次请求只做一次矩阵切片，延迟在毫秒级。
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..db import load_interactions, load_items, load_users
from ..geo.analyzer import content_tokens


@dataclass
class Index:
    items: list[dict]
    users: list[dict]
    item_ids: list[str]
    user_ids: list[str]
    item_pos: dict[str, int]
    user_pos: dict[str, int]
    R: np.ndarray                  # user × item 交互矩阵（0/1）
    item_cf: np.ndarray            # item-item 余弦
    user_cf: np.ndarray            # user-user 余弦
    item_vec: np.ndarray           # item × vocab TF-IDF
    user_vec: np.ndarray           # user × vocab 画像向量
    vocab: dict[str, int]
    idf: np.ndarray
    pop: np.ndarray                # 归一化热度
    cat_of: list[str]
    price: np.ndarray
    gqs: np.ndarray
    text_len: np.ndarray
    cold: np.ndarray
    gqs_rank_in_cat: np.ndarray
    user_hist_gqs: np.ndarray      # 用户历史交互物品的平均 GQS（0-100）
    brand_of: np.ndarray           # 物品 -> 品牌下标
    brand_aff: np.ndarray          # 用户 × 品牌 亲和度（行为 + 声明偏好）
    geo_subs: dict[str, np.ndarray] = field(default_factory=dict)
    train_ts_cut: dict[str, int] = field(default_factory=dict)

    # ---------------- 便捷访问 ----------------
    def item(self, item_id: str) -> dict | None:
        p = self.item_pos.get(item_id)
        return self.items[p] if p is not None else None

    def user(self, user_id: str) -> dict | None:
        p = self.user_pos.get(user_id)
        return self.users[p] if p is not None else None

    def user_items(self, user_id: str) -> list[str]:
        p = self.user_pos.get(user_id)
        if p is None:
            return []
        return [self.item_ids[j] for j in np.nonzero(self.R[p])[0]]

    def profile_text(self, user_id: str) -> str:
        return " ".join(self.items[self.item_pos[i]]["title"] for i in self.user_items(user_id)[:20])


_INDEX: Index | None = None


def _l2_normalize(m: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(m, axis=1, keepdims=True)
    norm[norm == 0] = 1.0
    return m / norm


def build_index(interactions: list[tuple[str, str, int, int]] | None = None) -> Index:
    items = load_items()
    users = load_users()
    inter = load_interactions() if interactions is None else interactions

    item_ids = [it["item_id"] for it in items]
    user_ids = [u["user_id"] for u in users]
    item_pos = {i: k for k, i in enumerate(item_ids)}
    user_pos = {u: k for k, u in enumerate(user_ids)}

    R = np.zeros((len(user_ids), len(item_ids)), dtype=np.float32)
    for uid, iid, _ts, label in inter:
        if label <= 0:
            continue
        ui, ij = user_pos.get(uid), item_pos.get(iid)
        if ui is not None and ij is not None:
            R[ui, ij] = 1.0

    item_cf = _l2_normalize(R.T) @ _l2_normalize(R.T).T
    user_cf = _l2_normalize(R) @ _l2_normalize(R).T
    np.fill_diagonal(item_cf, 0.0)
    np.fill_diagonal(user_cf, 0.0)

    # ---- 内容向量（2-gram TF-IDF）----
    docs = [content_tokens((it["title"] + " " + it["text"])[:1500]) for it in items]
    df: Counter = Counter()
    for d in docs:
        df.update(set(d))
    vocab = {w: k for k, w in enumerate(w for w, c in df.items() if c >= 2)}
    n_docs = len(docs)
    idf = np.zeros(len(vocab), dtype=np.float32)
    for w, k in vocab.items():
        idf[k] = math.log((1 + n_docs) / (1 + df[w])) + 1.0

    item_vec = np.zeros((len(items), len(vocab)), dtype=np.float32)
    for i, d in enumerate(docs):
        for w, c in Counter(d).items():
            k = vocab.get(w)
            if k is not None:
                item_vec[i, k] = (1 + math.log(c)) * idf[k]
    item_vec = _l2_normalize(item_vec)

    user_vec = _l2_normalize(R @ item_vec)

    # 用户对"内容质量"的既有偏好：历史点击物品的平均文案质量
    gqs_vec = np.array([it["gqs"] for it in items], dtype=np.float32)
    seen_cnt = R.sum(axis=1)
    hist_gqs = np.where(seen_cnt > 0, (R @ gqs_vec) / np.maximum(seen_cnt, 1), 0.0).astype(np.float32)

    pop_raw = R.sum(axis=0)
    pop = pop_raw / max(pop_raw.max(), 1.0)

    cat_of = [it["category"] for it in items]
    price = np.array([it["price"] for it in items], dtype=np.float32)
    gqs = np.array([it["gqs"] for it in items], dtype=np.float32)
    text_len = np.array([len(it["text"]) for it in items], dtype=np.float32)
    cold = (pop_raw < 5).astype(np.float32)

    # 类目内 GQS 分位：让「同价位里文案更好」变成可比的信号
    gqs_rank = np.zeros(len(items), dtype=np.float32)
    for cat in set(cat_of):
        idx = [i for i, c in enumerate(cat_of) if c == cat]
        order = sorted(idx, key=lambda i: gqs[i])
        for rank, i in enumerate(order):
            gqs_rank[i] = rank / max(len(order) - 1, 1)

    # 品牌亲和：行为占比 0.6 + 用户声明品牌 0.4（声明偏好属于画像，线上同样可得）
    brand_names = sorted({it["brand"] for it in items})
    bpos = {b: k for k, b in enumerate(brand_names)}
    brand_of = np.array([bpos[it["brand"]] for it in items], dtype=np.int64)
    counts = np.zeros((len(users), len(brand_names)), dtype=np.float32)
    for u in range(len(users)):
        row = R[u]
        for j in np.nonzero(row)[0]:
            counts[u, brand_of[j]] += 1.0
    row_max = counts.max(axis=1, keepdims=True)
    row_max[row_max == 0] = 1.0
    behavior = counts / row_max
    declared = np.zeros_like(behavior)
    for u, usr in enumerate(users):
        for b in usr.get("brands", []):
            if b in bpos:
                declared[u, bpos[b]] = 1.0
    brand_aff = 0.6 * behavior + 0.4 * declared

    geo_subs = {
        key: np.array([it["geo"]["sub_scores"].get(key, 0.0) for it in items], dtype=np.float32)
        for key in ("structured", "quantified", "no_hype", "conclusion_first", "sourced", "balanced")
    }

    return Index(
        items=items, users=users, item_ids=item_ids, user_ids=user_ids,
        item_pos=item_pos, user_pos=user_pos, R=R, item_cf=item_cf, user_cf=user_cf,
        item_vec=item_vec, user_vec=user_vec, vocab=vocab, idf=idf, pop=pop,
        cat_of=cat_of, price=price, gqs=gqs, text_len=text_len, cold=cold,
        gqs_rank_in_cat=gqs_rank, geo_subs=geo_subs,
        brand_of=brand_of, brand_aff=brand_aff, user_hist_gqs=hist_gqs,
    )


def get_index(rebuild: bool = False) -> Index:
    global _INDEX
    if _INDEX is None or rebuild:
        _INDEX = build_index()
    return _INDEX
