"""矩阵工作台的 API：学规则、生成版本、投喂登记、效果检测。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .cells import registry, store
from .db import load_items
from .distribute import generator, policy
from .geo import learn
from .monitor import attribution, probe

router = APIRouter(prefix="/api/mx", tags=["matrix"])


# ------------------------------------------------------------ 基础数据 ----
def _docs() -> list[str]:
    return [it["text"] for it in load_items() if it.get("text")]


def seed_assets(limit: int = 8) -> int:
    """把库里质量分布不同的商品抽成素材池，保证工作台开箱有料可用。"""
    items = sorted(load_items(), key=lambda x: x["gqs"])
    if not items:
        return 0
    picks = []
    step = max(1, len(items) // limit)
    for i in range(0, len(items), step):
        picks.append(items[i])
        if len(picks) >= limit:
            break
    for it in picks:
        store.upsert_asset(it["item_id"], it["title"], it["text"], it.get("brand", ""),
                           {"category": it.get("category", "")})
    return len(picks)


@router.get("/catalog")
def catalog() -> dict:
    data = registry.describe()
    data["cells_total"] = len(registry.cells())
    return data


@router.get("/assets")
def assets() -> dict:
    rows = store.list_assets()
    if not rows:
        seed_assets()
        rows = store.list_assets()
    return {"items": rows}


@router.get("/matrix")
def matrix() -> dict:
    cov = store.coverage()
    cells = []
    for model, channel in registry.cells():
        key = f"{model.id}::{channel.id}"
        info = cov.get(key)
        cells.append({
            "model_id": model.id, "model_name": model.name, "region": model.region,
            "channel_id": channel.id, "channel_name": channel.name,
            "learned": bool(info), "version": info["version"] if info else 0,
            "auc": info["auc"] if info else None,
            "samples": info["samples"] if info else 0,
            "source": info["source"] if info else None,
        })
    return {"cells": cells, "learned_count": sum(1 for c in cells if c["learned"]),
            "total": len(cells)}


# ------------------------------------------------------------ 规则学习 ----
@router.post("/cell/{model_id}/{channel_id}/learn")
def learn_one(model_id: str, channel_id: str) -> dict:
    model = registry.MODEL_BY_ID.get(model_id)
    channel = registry.CHANNEL_BY_ID.get(channel_id)
    if not model or not channel:
        raise HTTPException(404, "模型或渠道不存在")
    if not model.retrieval:
        raise HTTPException(400, "该模型不带检索能力，无法产生引用，规则无从学起")

    result = learn.learn_cell(model, channel, _docs())
    rid = store.save_rule_set(
        model.id, channel.id, result["samples"], result["auc"],
        result["weights"], result["cards"], result["source"], result["notes"],
    )
    result["id"] = rid
    return result


@router.post("/learn_all")
def learn_all(limit_cells: int = Query(0, ge=0, le=36)) -> dict:
    cov = store.coverage()
    done, skipped, errors = [], [], []
    pairs = registry.cells()
    if limit_cells:
        pairs = pairs[:limit_cells]
    for model, channel in pairs:
        if not model.retrieval:
            skipped.append(f"{model.id}::{channel.id}")
            continue
        try:
            res = learn.learn_cell(model, channel, _docs())
            store.save_rule_set(model.id, channel.id, res["samples"], res["auc"],
                                res["weights"], res["cards"], res["source"], res["notes"])
            done.append({"cell": f"{model.id}::{channel.id}", "auc": res["auc"]})
        except Exception as exc:  # 单格失败不影响其他格
            errors.append({"cell": f"{model.id}::{channel.id}", "error": str(exc)})
    return {"learned": len(done), "skipped": len(skipped), "errors": errors,
            "detail": done, "previous_covered": len(cov)}


@router.get("/cell/{model_id}/{channel_id}")
def rule_detail(model_id: str, channel_id: str) -> dict:
    rs = store.latest_rule_set(model_id, channel_id)
    if not rs:
        raise HTTPException(404, "该格还没有学习记录，先点「学规则」")
    model = registry.MODEL_BY_ID.get(model_id)
    channel = registry.CHANNEL_BY_ID.get(channel_id)
    return {
        "model": {"id": model.id, "name": model.name, "note": model.note} if model else None,
        "channel": {"id": channel.id, "name": channel.name, "style": channel.style,
                    "min_words": channel.min_words, "max_words": channel.max_words,
                    "max_links": channel.max_links} if channel else None,
        "rule_set": rs,
    }


# -------------------------------------------------------------- 生成 ----
class DistributeReq(BaseModel):
    asset_id: str
    model_id: str
    channel_id: str


@router.post("/distribute")
def distribute(req: DistributeReq) -> dict:
    asset = store.get_asset(req.asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    model = registry.MODEL_BY_ID.get(req.model_id)
    channel = registry.CHANNEL_BY_ID.get(req.channel_id)
    if not model or not channel:
        raise HTTPException(404, "模型或渠道不存在")

    rs = store.latest_rule_set(req.model_id, req.channel_id)
    weights = rs["weights"] if rs else None
    out = generator.generate(asset, channel, model.name, weights)
    vid = store.save_variant(req.asset_id, req.model_id, req.channel_id,
                             out["body"], out["title"], out["checks"], out["gqs"])
    out["variant_id"] = vid
    out["rule_source"] = "learned" if rs else "generic"
    out["rule_version"] = rs["version"] if rs else None
    return out


@router.get("/policy/{channel_id}")
def channel_policy(channel_id: str) -> dict:
    channel = registry.CHANNEL_BY_ID.get(channel_id)
    if not channel:
        raise HTTPException(404, "渠道不存在")
    return {
        "id": channel.id, "name": channel.name, "style": channel.style,
        "min_words": channel.min_words, "max_words": channel.max_words,
        "max_links": channel.max_links, "title_max": channel.title_max,
        "constraints": list(channel.constraints),
        "forbidden": list(policy.FORBIDDEN_BY_CHANNEL.get(channel.id, ())),
    }


@router.get("/variants")
def variants(asset_id: str | None = None, limit: int = 30) -> dict:
    return {"items": store.list_variants(asset_id, limit)}


# ------------------------------------------------------------ 投喂登记 ----
class PublishReq(BaseModel):
    variant_id: int
    url: str
    status: str = "published"


@router.post("/publications")
def publish(req: PublishReq) -> dict:
    v = store.get_variant(req.variant_id)
    if not v:
        raise HTTPException(404, "版本不存在")
    pid = store.save_publication(req.variant_id, v["channel_id"], req.url, req.status)
    return {"id": pid, "variant_id": req.variant_id, "url": req.url}


@router.get("/publications")
def publications(limit: int = 50) -> dict:
    return {"items": store.list_publications(limit)}


# ------------------------------------------------------------ 效果检测 ----
def _docs_list() -> list[dict]:
    """语料 = 库里全部商品，作为"全网可被检索到"的文档池。"""
    return [{"id": it["item_id"], "title": it["title"], "text": it["text"],
             "channel": it.get("category", "")} for it in load_items()]


class ProbeReq(BaseModel):
    asset_id: str
    model_id: str
    channel_id: str
    queries: list[str] | None = None


@router.post("/probe")
def run_probe(req: ProbeReq) -> dict:
    asset = store.get_asset(req.asset_id)
    if not asset:
        raise HTTPException(404, "素材不存在")
    model = registry.MODEL_BY_ID.get(req.model_id)
    channel = registry.CHANNEL_BY_ID.get(req.channel_id)
    if not model or not channel:
        raise HTTPException(404, "模型或渠道不存在")

    rs = store.latest_rule_set(req.model_id, req.channel_id)
    weights = rs["weights"] if rs else None

    items = {it["item_id"]: it for it in load_items()}
    category = items.get(req.asset_id, {}).get("category", "")

    # after 用"该格最近一次生成的版本"；没生成过就当场生成一版，保证可比
    variants = store.list_variants(req.asset_id, 50)
    picked = next((v for v in variants
                   if v["model_id"] == req.model_id and v["channel_id"] == req.channel_id), None)
    if picked:
        after_text = store.get_variant(picked["id"])["body"]
        variant_id = picked["id"]
    else:
        gen = generator.generate(asset, channel, model.name, weights)
        after_text = gen["body"]
        variant_id = store.save_variant(req.asset_id, req.model_id, req.channel_id,
                                        gen["body"], gen["title"], gen["checks"], gen["gqs"])

    queries = req.queries or probe.build_queries(asset["title"], channel, category)
    result = attribution.compare(queries, model, channel, weights, _docs_list(),
                                 req.asset_id, asset["body"], after_text)

    # 落库，供看板累计（同一批 query 的 before/after 都记）
    for row in result["detail"]:
        store.save_probe(model.id, channel.id, req.asset_id, row["query"],
                         row["before"]["cited"], row["before"]["rank"], "before")
        store.save_probe(model.id, channel.id, req.asset_id, row["query"],
                         row["after"]["cited"], row["after"]["rank"], "after",
                         row["citations"])

    result["queries"] = queries
    result["variant_id"] = variant_id
    result["rule_source"] = "learned" if rs else "generic"
    result["after_chars"] = len(after_text)
    result["before_chars"] = len(asset["body"])
    return result


@router.get("/probe/summary")
def probe_summary(model_id: str, channel_id: str, asset_id: str | None = None) -> dict:
    return store.probe_summary(model_id, channel_id, asset_id)
