"""FastAPI 服务层。"""
from __future__ import annotations

from datetime import datetime

import numpy as np
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api_matrix import router as mx_router
from .api_pipeline import router as pipeline_router
from .config import COLD_SLOT_SCORE, GEO_RULE_DESC, GEO_RULE_LABELS, GEO_RULE_WEIGHTS, STATIC_DIR
from .db import (all_metrics, connect, get_item, get_metric, load_items, load_users,
                 migrate, save_rewrite, table_counts)
from .geo.rewriter import rewrite
from .rec.features import COLD_BASE_FEATURES, COLD_GEO_FEATURES, matrix_for
from .rec.ranker import Ranker
from .rec.service import get_service
from .seed_entities import ensure_entities

app = FastAPI(title="知位 NEO · 生态位经营系统", version="3.0.0")
app.include_router(pipeline_router)
app.include_router(mx_router)

# 企业会员目标物与套餐：先补列（items 的会员/推广字段），再幂等灌种子
try:
    with connect() as _conn:
        migrate(_conn)
    ensure_entities()
except Exception as _e:  # 数据未就绪也不阻塞服务
    import logging
    logging.getLogger("uvicorn.error").warning(f"seed_entities failed: {_e}")

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "counts": table_counts(), "time": datetime.now().isoformat(timespec="seconds")}


@app.get("/api/users")
def list_users(limit: int = Query(40, le=500)) -> dict:
    users = load_users()
    svc = get_service()
    out = []
    for u in users[:limit]:
        out.append({
            "user_id": u["user_id"],
            "name": u["name"],
            "persona": u["persona"],
            "price_band": u["price_band"],
            "preferred": u["preferred"],
            "brands": u.get("brands", []),
            "history": len(svc.idx.user_items(u["user_id"])),
        })
    return {"total": len(users), "users": out}


@app.get("/api/users/{user_id}")
def user_profile(user_id: str) -> dict:
    svc = get_service()
    u = svc.idx.user(user_id)
    if u is None:
        raise HTTPException(404, f"未知用户 {user_id}")
    up = svc.idx.user_pos[user_id]
    hist_idx = np.nonzero(svc.idx.R[up])[0]
    history = sorted(
        [
            {"item_id": svc.idx.items[i]["item_id"], "title": svc.idx.items[i]["title"],
             "gqs": svc.idx.items[i]["gqs"], "category": svc.idx.items[i]["category"],
             "price": svc.idx.items[i]["price"]}
            for i in hist_idx
        ],
        key=lambda x: -x["gqs"],
    )
    avg_gqs = float(np.mean([h["gqs"] for h in history])) if history else 0.0
    return {
        "user_id": user_id, "name": u["name"], "persona": u["persona"],
        "price_band": u["price_band"], "preferred": u["preferred"], "brands": u.get("brands", []),
        "history": history, "history_avg_gqs": round(avg_gqs, 1),
    }


@app.get("/api/users/{user_id}/recommend")
def recommend(user_id: str, k: int = Query(10, le=30), variant: str = Query("full")) -> dict:
    svc = get_service()
    try:
        result = svc.recommend(user_id, k=k, variant=variant)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc

    # 新品扶持位：零交互物品只能靠内容质量竞争，给一个明确的曝光位并标注来源
    cold_ranker = Ranker.load("cold_geo")
    if cold_ranker is not None:
        idx = svc.idx
        up = idx.user_pos[user_id]
        cold_ips = [i for i in range(len(idx.items)) if idx.pop[i] == 0 and idx.R[up, i] == 0]
        if cold_ips:
            sc = cold_ranker.predict(matrix_for(idx, up, cold_ips, COLD_GEO_FEATURES))
            best = int(np.argmax(sc))
            if float(sc[best]) >= COLD_SLOT_SCORE:
                ip = cold_ips[best]
                it = idx.items[ip]
                slot = {
                    "rank": 3,
                    "item_id": it["item_id"], "title": it["title"], "category": it["category"],
                    "brand": it["brand"], "price": it["price"],
                    "score": round(float(sc[best]), 4), "gqs": it["gqs"],
                    "score_kind": "content",
                    "grade": it["geo"]["grade"], "grade_label": it["geo"]["grade_label"],
                    "recall_sources": [{"channel": "geo", "label": "内容质量", "weight": 1.0}],
                    "reasons": [
                        f"新品（0 次交互），文案质量 {it['gqs']:.0f} 分",
                        f"内容排序分 {float(sc[best]):.2f}，超过扶持阈值 {COLD_SLOT_SCORE}",
                    ],
                    "snippet": it["text"][:110].replace("\n", " "),
                    "slot": "新品扶持位",
                }
                items = result["items"]
                if slot["item_id"] not in {x["item_id"] for x in items}:
                    # 按其内容分落到对应位置，但最多落到末位：既不破坏排序单调性，
                    # 又保证新品一定拿到一次曝光
                    higher = sum(1 for x in items if x["score"] > slot["score"])
                    pos = min(higher, len(items) - 1)
                    items.insert(pos, slot)
                    items = items[:k]
                    for n, x in enumerate(items, start=1):
                        x["rank"] = n
                    result["items"] = items
                    result["cold_slot"] = slot["item_id"]
    return result


@app.get("/api/items/{item_id}")
def item_detail(item_id: str) -> dict:
    svc = get_service()
    try:
        detail = svc.item_detail(item_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    detail["similar"] = svc.similar_items(item_id)
    cold_ranker = Ranker.load("cold_geo")
    if cold_ranker is not None and svc.idx.pop[svc.idx.item_pos[item_id]] == 0:
        detail["is_cold"] = True
    return detail


@app.post("/api/items/{item_id}/rewrite")
def rewrite_item(item_id: str, use_llm: bool = Query(True)) -> dict:
    item = get_item(item_id)
    if item is None:
        raise HTTPException(404, f"未知商品 {item_id}")
    result = rewrite(item["text"], use_llm=use_llm)
    payload = result.to_dict()
    payload["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_rewrite(item_id, payload)
    return payload


@app.get("/api/explain")
def explain(user_id: str, item_id: str, variant: str = "full") -> dict:
    svc = get_service()
    try:
        return svc.explain(user_id, item_id, variant)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/compare")
def compare(user_id: str, k: int = Query(10, le=20)) -> dict:
    """同屏对比 baseline 与 +GEO 两套排序结果，用于看差异。"""
    svc = get_service()
    full = svc.recommend(user_id, k=k, variant="full")
    base = svc.recommend(user_id, k=k, variant="base")
    base_ids = [x["item_id"] for x in base["items"]]
    for x in full["items"]:
        x["in_baseline"] = x["item_id"] in base_ids
    return {"full": full, "base": base}


@app.get("/api/metrics")
def metrics() -> dict:
    data = all_metrics()
    if "evaluation" not in data:
        raise HTTPException(404, "尚未运行离线评测，请执行 python -m app.rec.evaluate")
    return {"evaluation": data["evaluation"]["payload"], "generated_at": data["evaluation"]["created_at"]}


@app.get("/api/rules")
def rules() -> dict:
    items = load_items()
    out = []
    for key, weight in GEO_RULE_WEIGHTS.items():
        vals = [it["geo"]["sub_scores"].get(key, 0.0) for it in items]
        out.append({
            "key": key,
            "label": GEO_RULE_LABELS[key],
            "weight": weight,
            "desc": GEO_RULE_DESC[key],
            "avg_score": round(float(np.mean(vals)), 3),
            "pass_rate": round(float(np.mean([1 if v >= 0.75 else 0 for v in vals])), 3),
            "fail_rate": round(float(np.mean([1 if v < 0.5 else 0 for v in vals])), 3),
        })
    out.sort(key=lambda r: -r["weight"])
    grades = {}
    for it in items:
        g = it["geo"]["grade"]
        grades[g] = grades.get(g, 0) + 1
    return {
        "rules": out,
        "grade_dist": grades,
        "items": len(items),
        "avg_gqs": round(float(np.mean([it["gqs"] for it in items])), 1),
        "low_gqs_items": sum(1 for it in items if it["gqs"] < 55),
    }


@app.get("/api/items")
def search_items(q: str = "", limit: int = Query(20, le=100), sort: str = "gqs") -> dict:
    items = load_items()
    if q:
        ql = q.lower()
        items = [i for i in items if ql in i["title"].lower() or ql in i["category"].lower()]
    key = {"gqs": lambda i: -i["gqs"], "gqs_asc": lambda i: i["gqs"], "price": lambda i: i["price"]}.get(sort, lambda i: -i["gqs"])
    items = sorted(items, key=key)[:limit]
    return {
        "items": [
            {"item_id": i["item_id"], "title": i["title"], "category": i["category"],
             "price": i["price"], "gqs": i["gqs"], "grade": i["geo"]["grade"],
             "popularity": i["popularity"]}
            for i in items
        ]
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
