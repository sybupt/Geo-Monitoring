"""推荐服务：召回 → 特征 → 排序 → 生成可解释理由。"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..config import REC_CONFIG
from ..db import get_item, get_rewrite
from .features import ALL_FEATURES, BASELINE_FEATURES, FEATURE_LABELS, GEO_FEATURES, explain_row, matrix_for
from .index import get_index
from .ranker import Ranker
from .recall import fused_candidates

CHANNEL_LABELS = {
    "itemcf": "相似物品",
    "usercf": "相似用户",
    "content": "内容相关",
    "hot": "热门",
    "geo": "内容质量",
}


class RecommendService:
    def __init__(self) -> None:
        self.idx = get_index()
        self.ranker_full = Ranker.load("full")
        self.ranker_base = Ranker.load("base")

    # ------------------------------------------------------------------
    def _picker(self, variant: str) -> tuple[Ranker | None, list[str]]:
        if variant == "base":
            return self.ranker_base, BASELINE_FEATURES
        return self.ranker_full, ALL_FEATURES

    def _reasons(self, idx, user_pos: int, item_pos: int, contrib: dict[str, float]) -> list[str]:
        it = idx.items[item_pos]
        u = idx.users[user_pos]
        reasons: list[str] = []
        top_ch = sorted(contrib.items(), key=lambda kv: kv[1], reverse=True)[:2]
        for ch, _ in top_ch:
            if ch == "itemcf":
                reasons.append("与你点击过的同类商品高度相似")
            elif ch == "usercf":
                reasons.append("和你看过同一批商品的用户也在看")
            elif ch == "content":
                reasons.append("商品描述的语义与你的历史偏好接近")
            elif ch == "hot":
                reasons.append("近 30 天全站热度靠前")
            elif ch == "geo":
                reasons.append(f"文案质量 {it['gqs']:.0f} 分，类目内前 {max(1, int((1 - idx.gqs_rank_in_cat[item_pos]) * 100))}%")
        if idx.cat_of[item_pos] == u["preferred"][0]:
            reasons.append(f"命中你的首选类目「{it['category']}」")
        elif idx.cat_of[item_pos] == u["preferred"][1]:
            reasons.append(f"命中你的次选类目「{it['category']}」")
        grade = it["geo"]["grade"]
        if grade in ("A", "B") and len(reasons) < 3:
            weak = [r["label"] for r in it["geo"]["rules"] if r["score"] < 0.6][:1]
            if weak:
                reasons.append(f"内容质量 {grade} 级，仍有「{weak[0]}」可优化")
        return reasons[:3]

    # ------------------------------------------------------------------
    def recommend(self, user_id: str, k: int = 10, variant: str = "full") -> dict:
        idx = self.idx
        user_pos = idx.user_pos.get(user_id)
        if user_pos is None:
            raise KeyError(f"未知用户 {user_id}")
        ranker, features = self._picker(variant)
        cand, contrib = fused_candidates(idx, user_pos)

        if ranker is not None and cand:
            X = matrix_for(idx, user_pos, cand, features)
            scores = ranker.predict(X)
        else:
            scores = np.array([sum(contrib.get(i, {}).values()) for i in cand], dtype=np.float64)

        order = np.argsort(-scores)[:k]
        out = []
        for rank, oi in enumerate(order, start=1):
            i = cand[int(oi)]
            it = idx.items[i]
            out.append({
                "rank": rank,
                "item_id": it["item_id"],
                "title": it["title"],
                "category": it["category"],
                "brand": it["brand"],
                "price": it["price"],
                "score": round(float(scores[oi]), 4),
                "gqs": it["gqs"],
                "grade": it["geo"]["grade"],
                "grade_label": it["geo"]["grade_label"],
                "recall_sources": [
                    {"channel": ch, "label": CHANNEL_LABELS[ch], "weight": w}
                    for ch, w in sorted(contrib.get(i, {}).items(), key=lambda kv: -kv[1])
                ],
                "reasons": self._reasons(idx, user_pos, i, contrib.get(i, {})),
                "snippet": it["text"][:110].replace("\n", " "),
            })

        return {
            "user": {
                "user_id": user_id,
                "name": idx.users[user_pos]["name"],
                "persona": idx.users[user_pos]["persona"],
                "price_band": idx.users[user_pos]["price_band"],
                "preferred": idx.users[user_pos]["preferred"],
                "history": len(idx.user_items(user_id)),
            },
            "variant": variant,
            "features": features,
            "items": out,
        }

    # ------------------------------------------------------------------
    def item_detail(self, item_id: str) -> dict:
        idx = self.idx
        it = idx.item(item_id)
        if it is None:
            raise KeyError(f"未知商品 {item_id}")
        i = idx.item_pos[item_id]
        return {
            **{k: it[k] for k in ("item_id", "title", "category", "brand", "price", "text", "gqs", "popularity", "created_at")},
            "geo": it["geo"],
            "text_len": int(idx.text_len[i]),
            "cold": bool(idx.cold[i]),
            "rewrite": get_rewrite(item_id),
        }

    def explain(self, user_id: str, item_id: str, variant: str = "full") -> dict:
        idx = self.idx
        up, ip = idx.user_pos[user_id], idx.item_pos[item_id]
        ranker, features = self._picker(variant)
        weights = ranker.w if ranker is not None and ranker.w is not None else np.ones(len(features)) / len(features)
        rows = explain_row(idx, up, ip, np.asarray(weights), features)
        return {
            "user_id": user_id,
            "item_id": item_id,
            "variant": variant,
            "rows": rows,
            "geo_share": round(sum(abs(r["contribution"]) for r in rows if r["group"] == "geo") /
                               max(sum(abs(r["contribution"]) for r in rows), 1e-9), 3),
        }

    def similar_items(self, item_id: str, k: int = 6) -> list[dict]:
        idx = self.idx
        i = idx.item_pos[item_id]
        sims = idx.item_cf[i]
        order = np.argsort(-sims)[:k]
        return [
            {"item_id": idx.items[j]["item_id"], "title": idx.items[j]["title"],
             "similarity": round(float(sims[j]), 3), "gqs": idx.items[j]["gqs"]}
            for j in order if sims[j] > 0
        ]


@lru_cache(maxsize=1)
def get_service() -> RecommendService:
    return RecommendService()
