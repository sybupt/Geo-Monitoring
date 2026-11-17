"""离线评测：按时间切分，训练 baseline / +GEO 两个排序器，做同口径对比。

指标口径：
  Recall@K / NDCG@K / MAP@K —— 对全库打分、排除训练期已交互物品后取 Top-K
  AUC                        —— 测试正样本 vs 采样负样本
业务侧还看两个数：Top-10 中冷启动物品占比、推荐商品的平均 GQS。
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime

import numpy as np

from ..config import COLD_SLOT_SCORE, LR_EPOCHS, REC_CONFIG, RANDOM_SEED
from ..db import load_interactions, load_items, load_users, save_metric
from .features import (ALL_FEATURES, BASELINE_FEATURES, COLD_BASE_FEATURES,
                       COLD_GEO_FEATURES, matrix_for)
from .index import build_index
from .ranker import Ranker, auc
from .recall import channel_scores, fused_candidates


def time_split(inter: list[tuple[str, str, int, int]], ratio: float = 0.25) -> tuple[list, dict[str, set[str]]]:
    by_user: dict[str, list[tuple[str, str, int, int]]] = {}
    for row in inter:
        by_user.setdefault(row[0], []).append(row)
    train: list = []
    test: dict[str, set[str]] = {}
    for uid, rows in by_user.items():
        rows.sort(key=lambda r: r[2])
        n_test = max(2, int(len(rows) * ratio))
        if len(rows) - n_test < 5:
            n_test = max(1, len(rows) // 4)
        test[uid] = {r[1] for r in rows[-n_test:]}
        train.extend(rows[:-n_test])
    return train, test


def _pairs(idx, train_inter: list, test: dict[str, set[str]], rng: random.Random, neg_ratio: int = 3):
    train_pos: dict[str, set[str]] = {}
    for uid, iid, _ts, _lab in train_inter:
        train_pos.setdefault(uid, set()).add(iid)
    all_items = set(idx.item_ids)
    pairs, labels = [], []
    for uid, pos in train_pos.items():
        up = idx.user_pos[uid]
        forbidden = pos | test.get(uid, set())
        neg_pool = list(all_items - forbidden)
        for iid in pos:
            pairs.append((up, idx.item_pos[iid]))
            labels.append(1)
            for nid in rng.sample(neg_pool, min(neg_ratio, len(neg_pool))):
                pairs.append((up, idx.item_pos[nid]))
                labels.append(0)
    return pairs, np.array(labels, dtype=np.float64)


def _build_X(idx, pairs, features):
    """按用户分组计算（省重复向量化），但必须按原始行号回填。

    早期版本按用户顺序顺序写入，而标签是按 pairs 原始顺序给的；
    一旦 pairs 不是严格按用户聚集（例如按时间排序返回），特征与标签就整列错位，
    训练出来的权重全是噪声，且这种错误不会报错、只会让指标 quietly 变差。
    """
    grouped: dict[int, list[tuple[int, int]]] = {}
    for n, (up, ip) in enumerate(pairs):
        grouped.setdefault(up, []).append((n, ip))
    rows = np.zeros((len(pairs), len(features)), dtype=np.float64)
    for up, items in grouped.items():
        row_ids = [n for n, _ in items]
        ips = [ip for _, ip in items]
        rows[row_ids] = matrix_for(idx, up, ips, features)
    return rows, list(pairs)


def _rank_pool(idx, up: int, model: Ranker | None, features: list[str], pool: list[int],
               fusion: dict[int, float]) -> list[int]:
    """两阶段：只在召回池内排序。这才是线上真实链路，也保证两种模型口径一致。"""
    if not pool:
        return []
    if model is None or model.w is None:
        return sorted(pool, key=lambda i: fusion.get(i, 0.0), reverse=True)
    X = matrix_for(idx, up, pool, features)
    sc = model.predict(X)
    order = np.argsort(-sc)
    return [pool[int(o)] for o in order]


def _metrics_at_k(ranked: list[int], rel: set[str], idx, k: int) -> tuple[float, float, float]:
    top = ranked[:k]
    hits = [1 if idx.item_ids[i] in rel else 0 for i in top]
    n_rel = min(len(rel), k)
    recall = sum(hits) / len(rel) if rel else 0.0
    dcg = sum(h / math.log2(i + 2) for i, h in enumerate(hits))
    idcg = sum(1 / math.log2(i + 2) for i in range(n_rel))
    ndcg = dcg / idcg if idcg > 0 else 0.0
    ap, cnt = 0.0, 0
    for i, h in enumerate(hits):
        if h:
            cnt += 1
            ap += cnt / (i + 1)
    ap = ap / max(len(rel), 1)
    return recall, ndcg, ap


def run_evaluation(seed: int = RANDOM_SEED, epochs: int = LR_EPOCHS) -> dict:
    rng = random.Random(seed)
    inter = load_interactions()
    train_inter, test = time_split(inter)
    idx = build_index(train_inter)

    pairs, y = _pairs(idx, train_inter, test, rng)
    X_full, _ = _build_X(idx, pairs, ALL_FEATURES)
    X_base, _ = _build_X(idx, pairs, BASELINE_FEATURES)

    r_full = Ranker(features=ALL_FEATURES).fit(X_full, y, epochs=epochs, verbose=True)
    r_base = Ranker(features=BASELINE_FEATURES).fit(X_base, y, epochs=epochs, verbose=True)

    # 测试集 AUC
    test_pairs, test_y = [], []
    for uid, rel in test.items():
        up = idx.user_pos.get(uid)
        if up is None:
            continue
        seen = {idx.item_ids[j] for j in np.nonzero(idx.R[up])[0]}
        pos = [i for i in rel if i not in seen and i in idx.item_pos]
        pool = [i for i in idx.item_ids if i not in seen and i not in rel]
        for iid in pos:
            test_pairs.append((up, idx.item_pos[iid]))
            test_y.append(1)
        for iid in rng.sample(pool, min(len(pool), 5 * max(len(pos), 1))):
            test_pairs.append((up, idx.item_pos[iid]))
            test_y.append(0)
    Xt_full, _ = _build_X(idx, test_pairs, ALL_FEATURES)
    Xt_base, _ = _build_X(idx, test_pairs, BASELINE_FEATURES)
    auc_full = auc(np.array(test_y), r_full.predict(Xt_full))
    auc_base = auc(np.array(test_y), r_base.predict(Xt_base))

    # ---- 冷启动专项：新品零交互，仅用上线后的真实点击做标签 ----
    probe = load_interactions(events=("cold_probe",))
    cold_pairs, cold_y = [], []
    for uid, iid, _ts, label in probe:
        up, ip = idx.user_pos.get(uid), idx.item_pos.get(iid)
        if up is None or ip is None:
            continue
        cold_pairs.append((up, ip))
        cold_y.append(label)
    cold_report: dict = {}
    r_cold_base = r_cold_geo = None
    if cold_pairs and len(set(cold_y)) > 1:
        # 两个冷启动专用排序器：同一批热数据训练，唯一差别是有没有 GEO 内容特征
        Xcb, _ = _build_X(idx, pairs, COLD_BASE_FEATURES)
        Xcg, _ = _build_X(idx, pairs, COLD_GEO_FEATURES)
        r_cold_base = Ranker(features=COLD_BASE_FEATURES).fit(Xcb, y, epochs=epochs)
        r_cold_geo = Ranker(features=COLD_GEO_FEATURES).fit(Xcg, y, epochs=epochs)

        Xc_base, _ = _build_X(idx, cold_pairs, COLD_BASE_FEATURES)
        Xc_geo, _ = _build_X(idx, cold_pairs, COLD_GEO_FEATURES)
        auc_cb = auc(np.array(cold_y), r_cold_base.predict(Xc_base))
        auc_cg = auc(np.array(cold_y), r_cold_geo.predict(Xc_geo))

        # 扶持位命中率：多少用户至少有一个新品的内容分够格进入推荐位
        cold_ips = [i for i in range(len(idx.items)) if idx.cold[i]]
        filled = 0
        for uid in test:
            up = idx.user_pos.get(uid)
            if up is None or not cold_ips:
                continue
            sc = r_cold_geo.predict(matrix_for(idx, up, cold_ips, COLD_GEO_FEATURES))
            if float(sc.max()) >= COLD_SLOT_SCORE:
                filled += 1
        slot_rate = filled / max(len(test), 1)
        cold_report = {
            "samples": len(cold_pairs),
            "click_rate": round(float(np.mean(cold_y)), 3),
            "auc_content_only": round(auc_cb, 4),
            "auc_content_plus_geo": round(auc_cg, 4),
            "lift": round(auc_cg - auc_cb, 4),
            "slot_fill_rate": round(slot_rate, 3),
            "slot_threshold": COLD_SLOT_SCORE,
            "features_content_only": COLD_BASE_FEATURES,
            "features_with_geo": COLD_GEO_FEATURES,
        }
        r_cold_base.save("cold_base")
        r_cold_geo.save("cold_geo")

    k = REC_CONFIG.eval_k
    agg = {"fusion": [], "base": [], "full": []}
    pool_recall: list[float] = []
    cold_share = {"base": [], "full": []}
    gqs_share = {"base": [], "full": []}

    for uid, rel in test.items():
        up = idx.user_pos.get(uid)
        if up is None:
            continue
        # 只用训练期的交互做过滤；测试正样本必须留在池子里，否则召回恒为 0
        seen_mask = idx.R[up] > 0
        cands, contrib = fused_candidates(idx, up, k=REC_CONFIG.recall_k)
        fusion = {i: sum(c.values()) for i, c in contrib.items()}
        pool = [i for i in cands if not seen_mask[i]]
        pool_recall.append(sum(1 for i in pool if idx.item_ids[i] in rel) / max(len(rel), 1))

        for name, model, feats in (
            ("fusion", None, BASELINE_FEATURES),
            ("base", r_base, BASELINE_FEATURES),
            ("full", r_full, ALL_FEATURES),
        ):
            ranked = _rank_pool(idx, up, model, feats, pool, fusion)
            agg[name].append(_metrics_at_k(ranked, rel, idx, k))
            if name in cold_share:
                top = ranked[:k]
                cold_share[name].append(float(np.mean([idx.cold[i] for i in top])) if top else 0.0)
                gqs_share[name].append(float(np.mean([idx.gqs[i] for i in top])) if top else 0.0)

    def summarize(rows: list[tuple[float, float, float]]) -> dict:
        arr = np.array(rows) if rows else np.zeros((1, 3))
        return {
            f"recall@{k}": round(float(arr[:, 0].mean()), 4),
            f"ndcg@{k}": round(float(arr[:, 1].mean()), 4),
            f"map@{k}": round(float(arr[:, 2].mean()), 4),
        }

    items = load_items()
    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": {
            "users": len(load_users()),
            "items": len(items),
            "interactions": len(inter),
            "train": len(train_inter),
            "test_users": len(test),
            "features_base": len(BASELINE_FEATURES),
            "features_full": len(ALL_FEATURES),
        },
        "k": k,
        "candidate_recall": round(float(np.mean(pool_recall)), 4),
        "variants": {
            "fusion_only": {**summarize(agg["fusion"]), "auc": None,
                            "avg_gqs": round(float(np.mean(gqs_share["base"])), 1),
                            "cold_share": round(float(np.mean(cold_share["base"])), 3)},
            "baseline_lr": {**summarize(agg["base"]), "auc": round(auc_base, 4),
                            "avg_gqs": round(float(np.mean(gqs_share["base"])), 1),
                            "cold_share": round(float(np.mean(cold_share["base"])), 3)},
            "geo_lr": {**summarize(agg["full"]), "auc": round(auc_full, 4),
                       "avg_gqs": round(float(np.mean(gqs_share["full"])), 1),
                       "cold_share": round(float(np.mean(cold_share["full"])), 3)},
        },
        "lift": {
            f"ndcg@{k}": round(summarize(agg["full"])[f"ndcg@{k}"] - summarize(agg["base"])[f"ndcg@{k}"], 4),
            f"recall@{k}": round(summarize(agg["full"])[f"recall@{k}"] - summarize(agg["base"])[f"recall@{k}"], 4),
            "auc": round(auc_full - auc_base, 4),
            "avg_gqs": round(float(np.mean(gqs_share["full"]) - np.mean(gqs_share["base"])), 1),
            "cold_share": round(float(np.mean(cold_share["full"]) - np.mean(cold_share["base"])), 3),
        },
        "cold_start": cold_report,
        "weights": {
            "base": r_base.top_weights(10),
            "full": r_full.top_weights(12),
        },
        "history": {"base": r_base.history, "full": r_full.history},
        "train_pairs": int(len(y)),
    }

    r_base.save("base")
    r_full.save("full")
    save_metric("evaluation", report, report["generated_at"])
    return report


if __name__ == "__main__":
    rep = run_evaluation()
    print(json.dumps({k: rep[k] for k in ("dataset", "variants", "lift")}, ensure_ascii=False, indent=2))
