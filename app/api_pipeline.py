"""全链路 API：目标物 → 测试问题集 → 基线 → 规则学习 → 改写打分 → 投喂 → 看板。

顺序就是前端的六个页签，每个环节的产物都是下一环节的输入：

    targets  ──画像──▶  questions  ──问法──▶  rules
       ▲                                        │
       └──────────── board  ◀── feed  ◀── optimize

所有写操作的产物都落库（question_sets / baselines / cell_rule_sets /
variants / feeds / probes），刷新页面不会丢进度。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .cells import registry, store
from .db import get_item
from .distribute import generator, policy, scoring
from .feed import plan as feed_plan
from .geo import learn
from .monitor import attribution, probe
from .questions import chat as chat_engine
from .questions import dimensions as dims_mod
from .questions import generator as qgen
from .targets import profile

router = APIRouter(prefix="/api", tags=["pipeline"])


# ----------------------------------------------------------------- 工具 ----
def _item(item_id: str) -> dict:
    it = get_item(item_id)
    if not it:
        raise HTTPException(404, f"目标物不存在：{item_id}")
    return it


def _model(model_id: str) -> registry.ModelEntry:
    m = registry.MODEL_BY_ID.get(model_id)
    if not m:
        raise HTTPException(404, f"未知模型：{model_id}")
    return m


def _channel(channel_id: str) -> registry.ChannelEntry:
    c = registry.CHANNEL_BY_ID.get(channel_id)
    if not c:
        raise HTTPException(404, f"未知渠道：{channel_id}")
    return c


def _asset(item: dict) -> dict:
    """items 与 assets 两套结构的桥：生成器只认 asset 形状。"""
    return {"asset_id": item["item_id"], "title": item["title"], "body": item["text"],
            "brand": item["brand"], "facts": {"category": item["category"]}}


def _docs_all() -> list[dict]:
    return [{"id": it["item_id"], "title": it["title"], "text": it["text"],
             "channel": it["category"]} for it in profile._all_items()]


G_DEFAULT_MODEL = "deepseek_web"


def _weights(model_id: str, channel_id: str) -> tuple[dict | None, dict | None]:
    rs = store.latest_rule_set(model_id, channel_id)
    return (rs["weights"] if rs else None), rs


def _queries_for(target_id: str, model_id: str, channel_id: str) -> list[str]:
    """问法来源：目标物的维度问题集（全模型通用）→ 旧的格级问题集 → 自动生成。"""
    qs = store.dimension_questions(target_id)
    if qs:
        return qs
    s = store.latest_question_set(target_id, model_id, channel_id)
    return s["questions"] if s else []


def _membership(target_id: str) -> dict:
    """目标物的会员信息 + 套餐渠道门控视图。"""
    mem = store.get_membership(target_id) or {"plan": "trial", "paid_until": None}
    plan_id = mem["plan"] if mem["plan"] in registry.PLANS else "trial"
    plan = registry.PLANS[plan_id]
    allowed = set(plan["channels"])
    item = get_item(target_id)
    rank = registry.PLANS[plan_id]["rank"]
    locked = []
    for pid in registry.PLAN_ORDER:
        p = registry.PLANS[pid]
        if p["rank"] > rank:
            locked.append({
                "plan": pid, "plan_label": p["label"], "price": p["price"],
                "channels": [{"id": c.id, "name": c.name}
                             for c in registry.CHANNELS if c.id in p["channels"]
                             and c.id not in allowed],
            })
    return {
        "plan": plan_id, "plan_label": plan["label"], "price": plan["price"],
        "paid_until": mem.get("paid_until"),
        "member_name": mem.get("member_name") or (item or {}).get("member_name", ""),
        "promote_type": (item or {}).get("promote_type", "brand"),
        "promote_name": (item or {}).get("promote_name", ""),
        "channels": [{"id": c.id, "name": c.name} for c in registry.plan_channels(plan_id)],
        "locked_plans": locked,
        "item": (profile.target_card(item) if item else None),
    }


# ------------------------------------------------------------- 1. 目标物 ----
@router.get("/targets")
def targets(q: str = "", sort: str = Query("gqs_asc"), limit: int = Query(24, le=200),
            offset: int = 0, category: str = "") -> dict:
    # 推荐流 = 企业会员列表：电子商品等样例数据只作检索语料，不进列表
    items = profile.enterprise_items()
    if category:
        items = [i for i in items if i["category"] == category]
    if q:
        ql = q.lower()
        items = [i for i in items
                 if ql in i["title"].lower() or ql in i["category"].lower() or ql in i["brand"].lower()]
    keys = {
        "gqs_asc": lambda i: i["gqs"],
        "gqs": lambda i: -i["gqs"],
        "price": lambda i: -i["price"],
        "new": lambda i: i.get("created_at") or "",
        "hot": lambda i: -i.get("popularity", 0),
    }
    items = sorted(items, key=keys.get(sort, keys["gqs_asc"]))
    page = items[offset: offset + limit]
    return {"total": len(items), "items": [profile.target_card(i) for i in page],
            "categories": sorted({i["category"] for i in profile.enterprise_items()})}


@router.get("/targets/{item_id}")
def target_detail(item_id: str, model_id: str = Query("deepseek_web")) -> dict:
    item = _item(item_id)
    model = _model(model_id)
    geo = item["geo"]
    return {
        "card": profile.target_card(item),
        "text": item["text"],
        "geo": {"gqs": geo["gqs"], "grade": geo["grade"], "grade_label": geo["grade_label"],
                "rules": geo["rules"], "top_fixes": geo["top_fixes"],
                "sub_scores": geo["sub_scores"]},
        "model_id": model.id, "model_name": model.name,
        "models": [{"id": m.id, "name": m.name, "vendor": m.vendor} for m in registry.MODELS],
        "membership": _membership(item["item_id"]),
        "industry": dims_mod.industry_label(item),
        "channels": [
            {"channel_id": ch.id, "channel_name": ch.name, "locale": ch.locale,
             "style": ch.style, "min_words": ch.min_words, "max_words": ch.max_words,
             **profile.cell_status(item_id, model.id, ch.id)}
            for ch in registry.CHANNELS
        ],
        "progress": store.progress(item_id, model.id),
    }


@router.get("/targets/{item_id}/cell")
def target_cell(item_id: str, model_id: str = Query("deepseek_web"),
                channel_id: str = Query("zhihu")) -> dict:
    item = _item(item_id)
    model = _model(model_id)
    channel = _channel(channel_id)
    rs = store.latest_rule_set(model.id, channel.id)
    prof = profile.build_profile(item, model, channel, rs)
    view = profile.cell_view(item, model, channel)
    return {
        "profile": prof, "view": view,
        "status": profile.cell_status(item_id, model.id, channel.id),
        "policy": {"constraints": list(channel.constraints),
                   "forbidden": list(policy.FORBIDDEN_BY_CHANNEL.get(channel.id, ()))},
    }


@router.get("/targets/{item_id}/progress")
def target_progress(item_id: str, model_id: str = Query("deepseek_web")) -> dict:
    _item(item_id)
    return store.progress(item_id, model_id)


# ---------------------------------------------------------- 会员管理 ----
@router.get("/membership/plans")
def membership_plans() -> dict:
    return {
        "plans": [{"id": k, "label": p["label"], "price": p["price"],
                   "channels": p["channels"], "desc": p["desc"]}
                  for k, p in registry.PLANS.items()],
        "channels": [{"id": c.id, "name": c.name, "locale": c.locale}
                     for c in registry.CHANNELS],
    }


@router.get("/membership/{target_id}")
def membership_get(target_id: str) -> dict:
    _item(target_id)
    return _membership(target_id)


class MembershipReq(BaseModel):
    plan: str
    paid_until: str | None = None
    member_name: str | None = None


@router.post("/membership/{target_id}")
def membership_set(target_id: str, req: MembershipReq) -> dict:
    _item(target_id)
    if req.plan not in registry.PLANS:
        raise HTTPException(400, f"未知套餐：{req.plan}，可选 {list(registry.PLANS)}")
    old = store.get_membership(target_id) or {}
    paid_until = req.paid_until
    if paid_until is None:
        if req.plan == "trial":
            paid_until = None                      # 试用无到期日
        else:
            # 升级/新开通：沿用已有到期日，没有就按一年计
            paid_until = old.get("paid_until") or (
                datetime.now().replace(year=datetime.now().year + 1).strftime("%Y-%m-%d"))
    store.set_membership(target_id, req.plan, paid_until, member_name=req.member_name)
    return _membership(target_id)


class RenewReq(BaseModel):
    months: int = 12


@router.post("/membership/{target_id}/renew")
def membership_renew(target_id: str, req: RenewReq) -> dict:
    """续费：从 max(今天, 现有到期日) 往后加月数，试用套餐不产生到期日。"""
    _item(target_id)
    mem = store.get_membership(target_id) or {"plan": "trial", "paid_until": None}
    if mem["plan"] == "trial":
        raise HTTPException(400, "试用会员无需续费，请先升级套餐")
    today = datetime.now().date()
    base = today
    if mem.get("paid_until"):
        try:
            cur = datetime.strptime(mem["paid_until"][:10], "%Y-%m-%d").date()
            base = max(today, cur)
        except ValueError:
            base = today
    month = base.month + req.months
    year = base.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(base.day, 28)
    paid_until = f"{year:04d}-{month:02d}-{day:02d}"
    store.set_membership(target_id, mem["plan"], paid_until)
    return _membership(target_id)


@router.get("/memberships")
def memberships_list() -> dict:
    """会员管理：企业名、推广对象、套餐、年费、到期与汇总。"""
    rows = store.list_memberships()
    today = datetime.now().date()
    items, by_plan = [], {k: 0 for k in registry.PLAN_ORDER}
    arr = 0
    expiring = 0
    for r in rows:
        plan = registry.PLANS.get(r["plan"], registry.PLANS["trial"])
        until = (r.get("paid_until") or "")[:10]
        days_left = None
        if until:
            try:
                days_left = (datetime.strptime(until, "%Y-%m-%d").date() - today).days
            except ValueError:
                days_left = None
        if r["plan"] != "trial":
            arr += 40000 if r["plan"] == "premium" else 20000
            if days_left is not None and days_left <= 30:
                expiring += 1
        by_plan[r["plan"]] = by_plan.get(r["plan"], 0) + 1
        items.append({
            "target_id": r["target_id"],
            "member_name": r["member_name"],
            "title": r["title"],
            "brand": r["brand"],
            "category": r["category"],
            "promote_type": r.get("promote_type") or "brand",
            "promote_name": r.get("promote_name") or r["brand"],
            "plan": r["plan"],
            "plan_label": plan["label"],
            "price": plan["price"],
            "channels": len(plan["channels"]),
            "paid_until": until or None,
            "days_left": days_left,
            "gqs": r.get("gqs"),
            "updated_at": r.get("updated_at"),
        })
    return {
        "items": items,
        "summary": {
            "members": len(items),
            "paid_members": by_plan.get("standard", 0) + by_plan.get("premium", 0),
            "arr": arr,
            "expiring_30d": expiring,
            "by_plan": by_plan,
        },
        "plans": [{"id": k, "label": p["label"], "price": p["price"],
                   "channels": p["channels"], "desc": p["desc"],
                   "members": by_plan.get(k, 0)}
                  for k, p in registry.PLANS.items()],
    }


# --------------------------------------------------------- 2. 测试问题集 ----
@router.get("/questions/guide")
def questions_guide(target_id: str, model_id: str = Query("deepseek_web"),
                    channel_id: str = Query("zhihu")) -> dict:
    return qgen.guide(_item(target_id), _model(model_id), _channel(channel_id))


class SuggestReq(BaseModel):
    target_id: str
    model_id: str
    channel_id: str
    answers: dict = {}


@router.post("/questions/suggest")
def questions_suggest(req: SuggestReq) -> dict:
    return qgen.suggest(_item(req.target_id), _model(req.model_id),
                        _channel(req.channel_id), req.answers)


class SaveSetReq(SuggestReq):
    pass


@router.post("/questions/sets")
def questions_save(req: SaveSetReq) -> dict:
    item, model, channel = _item(req.target_id), _model(req.model_id), _channel(req.channel_id)
    data = qgen.suggest(item, model, channel, req.answers)
    sid = store.save_question_set(item["item_id"], model.id, channel.id,
                                  data["params"], data["groups"], data["total"])
    data["id"] = sid
    data["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


class SetAllReq(BaseModel):
    target_id: str
    model_id: str
    answers: dict = {}


@router.post("/questions/sets_all")
def questions_save_all(req: SetAllReq) -> dict:
    """一次为全部渠道生成问题集：英文渠道自动切英文问法。"""
    item, model = _item(req.target_id), _model(req.model_id)
    out = []
    for ch in registry.CHANNELS:
        data = qgen.suggest(item, model, ch, req.answers)
        sid = store.save_question_set(item["item_id"], model.id, ch.id,
                                      data["params"], data["groups"], data["total"])
        out.append({"channel_id": ch.id, "channel_name": ch.name, "set_id": sid,
                    "total": data["total"]})
    return {"sets": out, "channels": len(out)}


@router.get("/questions/sets")
def questions_list(target_id: str | None = None, limit: int = 20) -> dict:
    return {"items": store.list_question_sets(target_id, limit)}


@router.get("/questions/set/{set_id}")
def questions_get(set_id: int) -> dict:
    s = store.get_question_set(set_id)
    if not s:
        raise HTTPException(404, "问题集不存在")
    return s


class BaselineReq(BaseModel):
    target_id: str
    model_id: str
    channel_ids: list[str] | None = None
    auto: bool = True
    answers: dict = {}


@router.post("/questions/baseline")
def questions_baseline(req: BaselineReq) -> dict:
    item, model = _item(req.target_id), _model(req.model_id)
    channels = ([_channel(c) for c in req.channel_ids] if req.channel_ids
                else list(registry.CHANNELS))

    set_ids: dict[str, int] = {}
    questions_by_channel: dict[str, list[str]] = {}
    auto_made = []
    for ch in channels:
        s = store.latest_question_set(item["item_id"], model.id, ch.id)
        if not s and req.auto:
            data = qgen.suggest(item, model, ch, req.answers)
            sid = store.save_question_set(item["item_id"], model.id, ch.id,
                                          data["params"], data["groups"], data["total"])
            s = store.get_question_set(sid)
            auto_made.append(ch.id)
        if s:
            set_ids[ch.id] = s["id"]
            questions_by_channel[ch.id] = s["questions"]
    missing = [ch.id for ch in channels if ch.id not in questions_by_channel]
    if missing:
        raise HTTPException(400, f"这些渠道还没有问题集：{', '.join(missing)}")

    # 逐渠道用各自的问法跑：中英渠道问法本来就不同，不能混用
    outs = []
    for ch in channels:
        part = qgen.run_baseline(item, model, [ch], questions_by_channel[ch.id],
                                 set_ids={ch.id: set_ids[ch.id]}, persist=True)
        outs.extend(part["channels"])

    hits = sum(c["hits"] for c in outs)
    samples = sum(c["samples"] for c in outs)
    return {
        "target_id": item["item_id"], "model_id": model.id, "model_name": model.name,
        "channels": outs, "hits": hits, "samples": samples,
        "hit_rate": round(hits / max(samples, 1), 3),
        "auto_sets": auto_made,
        "note": "基线 = 原稿在「该格已学规则 + 该渠道这批问法」下的被引用率。"
                "投喂之后用同一批问法复测，差值才能归因到内容。",
    }


@router.get("/questions/baselines")
def questions_baselines(target_id: str, model_id: str | None = None) -> dict:
    return {"items": store.baseline_map(target_id, model_id)}


# ---------------------------------------------- 维度问题集（不绑模型渠道）----
class DimSetsReq(BaseModel):
    target_id: str


@router.post("/questions/dimension_sets")
def questions_dimension_sets(req: DimSetsReq) -> dict:
    """按目标物行业生成多维度问题集（参数党/口味/价格党/避坑/干净……）。

    维度集不绑定模型与渠道——同一批问法拿去测全部模型的基线。
    """
    item = _item(req.target_id)
    pack = dims_mod.pack_for(item)
    sets = dims_mod.build_sets(item)
    saved = []
    for s in sets:
        sid = store.save_question_set(item["item_id"], "", "", {}, s["groups"],
                                      s["total"], dimension=s["dimension"])
        saved.append({"set_id": sid, "dimension": s["dimension"], "label": s["label"],
                      "desc": s["desc"], "total": s["total"]})
    return {
        "target_id": item["item_id"], "industry": pack["label"],
        "sets": saved, "total_sets": len(saved),
        "total_questions": sum(s["total"] for s in saved),
    }


@router.get("/questions/dimension_sets")
def questions_dimension_list(target_id: str) -> dict:
    item = _item(target_id)
    sets = store.latest_dimension_sets(item["item_id"])
    pack = dims_mod.pack_for(item)
    return {
        "target_id": item["item_id"], "industry": pack["label"],
        "sets": [{"set_id": s["id"], "dimension": s["dimension"],
                  "label": (s["groups"][0]["label"] if s["groups"] else s["dimension"]),
                  "total": s["total"], "created_at": s["created_at"],
                  "questions": s["questions"],
                  "groups": s["groups"]} for s in sets],
    }


class ChatReq(BaseModel):
    target_id: str
    message: str
    history: list[dict] = []


@router.post("/questions/chat")
def questions_chat(req: ChatReq) -> dict:
    """对话式出题：解析一段自然语言（打字或语音转写），按要求生成问题集。

    解析引擎：配置了 GEOREC_LLM_API_KEY 走大模型理解，否则规则引擎兜底。
    生成结果并入目标物的维度问题集（同维度追加去重），供基线与规则学习复用。
    """
    item = _item(req.target_id)
    parsed = chat_engine.parse_message(req.message, item, req.history)
    if parsed.get("needs_clarify"):
        return {"reply": parsed["reply"], "engine": parsed.get("engine", "rule"),
                "sets": [], "added": 0}

    # 并入维度问题集：已有该维度 → 追加去重；没有 → 模板打底
    existing = {s["dimension"]: s for s in store.latest_dimension_sets(item["item_id"])}
    pack = dims_mod.pack_for(item)
    tpl_by_dim = {s["dimension"]: s for s in dims_mod.build_sets(item)}
    saved, added_total = [], 0
    for d in parsed["dims"]:
        old = existing.get(d["dimension"])
        if old:
            merged = list(old["questions"])
            label = ("自定义（对话累积）" if d["dimension"] == "custom"
                     else (old["groups"][0]["label"] if old["groups"] else d["label"]))
        else:
            tpl = tpl_by_dim.get(d["dimension"])
            merged = ([q["text"] for q in tpl["groups"][0]["questions"]]
                      if tpl else [])
            label = d["label"]
        new_qs = [q for q in d["questions"] if q not in merged]
        merged += new_qs
        added_total += len(new_qs)
        groups = [{
            "kind": d["dimension"], "label": label, "why": d.get("desc", ""),
            "questions": [{"text": q, "weight": 1.0, "difficulty": "中"} for q in merged],
        }]
        sid = store.save_question_set(item["item_id"], "", "", {}, groups,
                                      len(merged), dimension=d["dimension"])
        saved.append({"set_id": sid, "dimension": d["dimension"], "label": label,
                      "new": len(new_qs), "total": len(merged),
                      "new_questions": new_qs})
    reply = parsed["reply"]
    if added_total == 0:
        reply += "（这些问法在现有问题集里已经存在，没有重复追加）"
    return {
        "reply": reply, "engine": parsed.get("engine", "rule"),
        "industry": pack["label"], "sets": saved, "added": added_total,
    }


class BaselineAllReq(BaseModel):
    target_id: str
    model_ids: list[str] | None = None   # 默认全部模型


@router.post("/questions/baseline_all")
def questions_baseline_all(req: BaselineAllReq) -> dict:
    """维度问题集 × 全部模型 的提及率基线。

    不区分渠道——问法在模型里的表现与投放渠道无关，渠道差异在规则学习
    （模型×渠道矩阵）里体现。模型先验权重让每个模型的引用概率不同。
    """
    item = _item(req.target_id)
    models = ([_model(m) for m in req.model_ids] if req.model_ids
              else list(registry.MODELS))
    sets = store.latest_dimension_sets(item["item_id"])
    if not sets:
        raise HTTPException(400, "还没有维度问题集，先点「生成维度问题集」")
    docs = _docs_all()
    corpus = probe.build_corpus(docs, key="baseline_all")
    probe_ch = registry.CHANNEL_BY_ID["own_blog"]  # 探针用无硬约束的虚拟渠道口径

    matrix, pref = {}, {}
    for m in models:
        weights = dict(m.prior)  # 模型先验 → 引用概率因模型而异
        per_dim = []
        for s in sets:
            rows = probe.run(s["questions"], m, probe_ch, weights, corpus,
                             [item["item_id"]])
            stat = probe.summarize(rows)
            store.save_baseline(item["item_id"], m.id, "", s["id"],
                                stat["samples"], stat["hits"], stat["hit_rate"],
                                stat["avg_rank"], s["questions"], dimension=s["dimension"])
            for r in rows:
                store.save_probe(m.id, "", item["item_id"], r["query"],
                                 r["cited"], r["rank"], "baseline")
            matrix[f"{m.id}::{s['dimension']}"] = {
                "model_id": m.id, "dimension": s["dimension"],
                "samples": stat["samples"], "hits": stat["hits"],
                "hit_rate": stat["hit_rate"], "avg_rank": stat["avg_rank"],
            }
            per_dim.append((s["dimension"], stat["hit_rate"]))
        # 模型偏好：哪个维度更容易被引用，改写时就往哪个维度发力
        per_dim.sort(key=lambda x: -x[1])
        pref[m.id] = {
            "model_name": m.name,
            "overall": round(sum(x[1] for x in per_dim) / max(len(per_dim), 1), 3),
            "best_dims": per_dim[:3],
            "worst_dims": per_dim[-2:],
            "note": m.note,
        }
    hits = sum(v["hits"] for v in matrix.values())
    samples = sum(v["samples"] for v in matrix.values())
    return {
        "target_id": item["item_id"],
        "models": [{"id": m.id, "name": m.name} for m in models],
        "dimensions": [s["dimension"] for s in sets],
        "matrix": matrix, "preference": pref,
        "hit_rate": round(hits / max(samples, 1), 3),
        "note": "基线矩阵 = 每个维度的问题集在每个模型下的被引用率；"
                "模型偏好取 top3 维度，供第 3 步规则学习与第 4 步改写参照。",
    }


@router.get("/questions/baseline_matrix")
def questions_baseline_matrix(target_id: str) -> dict:
    item = _item(target_id)
    m = store.baseline_matrix(item["item_id"])
    dims = sorted({k.split("::")[1] for k in m})
    models = sorted({k.split("::")[0] for k in m})
    return {"target_id": item["item_id"], "matrix": m,
            "dimensions": dims, "models": models}


# ------------------------------------------------------------ 3. 规则学习 ----
def _target_queries(target_id: str | None) -> list[str]:
    """问法来源：目标物维度问题集（全模型通用，推荐）。"""
    if target_id:
        return store.dimension_questions(target_id)
    return []


class LearnReq(BaseModel):
    model_id: str
    channel_id: str
    target_id: str | None = None


@router.post("/rules/learn")
def rules_learn(req: LearnReq) -> dict:
    model, channel = _model(req.model_id), _channel(req.channel_id)
    qs = _target_queries(req.target_id)
    if not qs:
        s = store.latest_question_set_any(model.id, channel.id)
        if s:
            qs = s["questions"]

    docs = [it["text"] for it in profile._all_items() if it.get("text")]
    res = learn.learn_cell(model, channel, docs, queries=qs or None)
    rid = store.save_rule_set(model.id, channel.id, res["samples"], res["auc"],
                              res["weights"], res["cards"], res["source"], res["notes"])
    res.update({"id": rid, "used_questions": len(qs)})
    return res


class LearnAllReq(BaseModel):
    target_id: str | None = None


@router.post("/rules/learn_all")
def rules_learn_all(req: LearnAllReq) -> dict:
    """按目标物维度问题集（全模型通用）学习 模型×渠道 全部格子。"""
    docs = [it["text"] for it in profile._all_items() if it.get("text")]
    qs = _target_queries(req.target_id)
    if not qs:
        s = store.latest_question_set_any("", "")
        qs = s["questions"] if s else []
    done, skipped, errors = [], [], []
    for model, channel in registry.cells():
        if not model.retrieval:
            skipped.append(f"{model.id}::{channel.id}")
            continue
        try:
            res = learn.learn_cell(model, channel, docs, queries=qs or None)
            store.save_rule_set(model.id, channel.id, res["samples"], res["auc"],
                                res["weights"], res["cards"], res["source"], res["notes"])
            done.append({"cell": f"{model.id}::{channel.id}", "auc": res["auc"],
                         "samples": res["samples"], "questions": len(qs)})
        except Exception as exc:  # 单格失败不影响其他格
            errors.append({"cell": f"{model.id}::{channel.id}", "error": str(exc)})
    return {"learned": len(done), "skipped": len(skipped), "errors": errors,
            "used_questions": len(qs), "detail": done}


@router.get("/rules/cell")
def rules_cell(model_id: str, channel_id: str) -> dict:
    model, channel = _model(model_id), _channel(channel_id)
    rs = store.latest_rule_set(model.id, channel.id)
    if not rs:
        raise HTTPException(404, "该格还没学过规则")
    return {
        "model": {"id": model.id, "name": model.name, "vendor": model.vendor,
                  "note": model.note, "retrieval": model.retrieval},
        "channel": {"id": channel.id, "name": channel.name, "style": channel.style,
                    "min_words": channel.min_words, "max_words": channel.max_words,
                    "max_links": channel.max_links,
                    "constraints": list(channel.constraints)},
        "rule_set": rs,
        "models": [{"id": m.id, "name": m.name} for m in registry.MODELS],
    }


# -------------------------------------------------------- 4. 体检与改写 ----
class OptimizeReq(BaseModel):
    target_id: str
    model_ids: list[str] | None = None   # 多模型：想提升谁家的提及率
    channel_ids: list[str] | None = None  # 默认按会员套餐；越权渠道会被拦截


@router.post("/optimize")
def optimize(req: OptimizeReq) -> dict:
    item = _item(req.target_id)
    mem = _membership(item["item_id"])
    allowed = {c["id"] for c in mem["channels"]}
    plan_id = mem["plan"]

    models = ([_model(m) for m in req.model_ids] if req.model_ids
              else [_model(G_DEFAULT_MODEL)])
    channel_ids = req.channel_ids or list(allowed)
    out, rejected = [], []
    for cid in channel_ids:
        if cid not in allowed:
            rejected.append({"channel_id": cid, "reason": f"会员套餐（{mem['plan_label']}）未包含该渠道，需升级"})
            continue
        ch = _channel(cid)
        for model in models:
            weights, rs = _weights(model.id, ch.id)
            qs = _queries_for(item["item_id"], model.id, ch.id)
            gen = generator.generate(_asset(item), ch, model.name, weights)
            before = scoring.score(item["text"], ch, weights, qs)
            after = scoring.score(gen["body"], ch, weights, qs)
            vid = store.save_variant(item["item_id"], model.id, ch.id, gen["body"],
                                     gen["title"], gen["checks"], after["score"],
                                     status="draft")
            out.append({
                "channel_id": ch.id, "channel_name": ch.name, "locale": ch.locale,
                "style": ch.style, "model_id": model.id, "model_name": model.name,
                "variant_id": vid, "status": "draft",
                "title": gen["title"], "body": gen["body"],
                "checks": gen["checks"], "gqs": gen["gqs"], "grade": gen["grade"],
                "needs_translation": gen["needs_translation"],
                "applied_rules": gen["applied_rules"],
                "before": before, "after": after, "delta": scoring.delta(before, after),
                "questions": len(qs),
                "rule_source": "learned" if rs else "generic",
                "rule_version": rs["version"] if rs else None,
                "policy": {"constraints": list(ch.constraints),
                           "forbidden": list(policy.FORBIDDEN_BY_CHANNEL.get(ch.id, ())),
                           "min_words": ch.min_words, "max_words": ch.max_words,
                           "max_links": ch.max_links, "title_max": ch.title_max},
            })
    avg = sum(x["delta"]["score"] for x in out) / max(len(out), 1)
    return {
        "target": profile.target_card(item),
        "membership": mem,
        "models": [{"id": m.id, "name": m.name} for m in models],
        "items": out, "rejected": rejected, "avg_delta": round(avg, 1),
        "weights_parts": scoring.PARTS,
        "note": "适配分 = 0.45×GQS + 0.35×该格规则适配 + 0.20×问法覆盖；"
                "生成稿为「待审核」状态，可在卡片里再次编辑，审核通过后才能进入投喂。",
    }


class VariantEditReq(BaseModel):
    title: str | None = None
    body: str


@router.post("/optimize/variant/{variant_id}")
def optimize_variant_edit(variant_id: int, req: VariantEditReq) -> dict:
    """人工再次编辑：重算分数，状态回到待审核。"""
    v = store.get_variant(variant_id)
    if not v:
        raise HTTPException(404, "改写稿不存在")
    ch = _channel(v["channel_id"])
    weights, _ = _weights(v["model_id"], ch.id)
    qs = _queries_for(v["asset_id"], v["model_id"], ch.id)
    checks = policy.check(req.body, req.title or v["title"] or "", ch)
    after = scoring.score(req.body, ch, weights, qs)
    d = store.update_variant(variant_id, req.title or v["title"] or "", req.body,
                             checks, after["score"])
    return {
        "variant": d, "after": after,
        "checks": checks,
        "note": "已重算适配分；编辑后状态回到待审核，审核通过才能投喂。",
    }


@router.post("/optimize/variant/{variant_id}/approve")
def optimize_variant_approve(variant_id: int) -> dict:
    v = store.get_variant(variant_id)
    if not v:
        raise HTTPException(404, "改写稿不存在")
    if not v["checks"].get("passed"):
        raise HTTPException(400, "存在阻断性硬约束问题，先修复再审核")
    d = store.approve_variant(variant_id)
    return {"variant": d, "note": "审核通过，可进入「引导投喂」"}


# ------------------------------------------------------------ 5. 引导投喂 ----
@router.get("/feed/plan")
def feed_plan_get(target_id: str, model_id: str | None = Query(None)) -> dict:
    item = _item(target_id)
    mem = _membership(item["item_id"])
    allowed_ids = {c["id"] for c in mem["channels"]}
    channels = [c for c in registry.CHANNELS if c.id in allowed_ids]
    data = feed_plan.build(item, mem["plan_label"], channels)

    # 每个渠道 → 各模型的审核通过版本（第 4 步可选多模型，逐条登记）
    variants = [v for v in store.list_variants(item["item_id"], 120)
                if v["status"] == "approved"]
    for row in data["items"]:
        cid = row["channel_id"]
        row["approved"] = [{
            "variant_id": v["id"], "model_id": v["model_id"],
            "model_name": (registry.MODEL_BY_ID[v["model_id"]].name
                           if v["model_id"] in registry.MODEL_BY_ID else v["model_id"]),
            "title": v["title"], "score": v["score"], "at": v["updated_at"] or v["created_at"],
        } for v in variants if v["channel_id"] == cid]
        # 兼容旧字段：取第一个已审核版本
        if row["approved"]:
            a = row["approved"][0]
            row["variant"] = {"id": a["variant_id"], "title": a["title"],
                              "score": a["score"], "at": a["at"]}
        else:
            row["variant"] = None
        row["locked"] = False
        # 渠道投喂状态
        if row["feed"]:
            s = row["feed"].get("status") or "published"
            row["status"] = s
            row["status_label"] = "已投喂·已复测" if s == "rechecked" else "已登记投喂"
        elif row["approved"]:
            row["status"], row["status_label"] = "ready", "待投喂"
        else:
            row["status"], row["status_label"] = "empty", "待改写·待审核"

    # 套餐外的渠道不返回：投喂页只保留本会员真正能投的渠道，界面更干净
    return {**data, "membership": mem,
            "note": "投喂只允许「审核通过」的改写稿；登记时填写链接与投放时长，"
                    "每条记录都会标注针对的目标模型。"}


class RecordReq(BaseModel):
    target_id: str
    model_id: str
    channel_id: str
    variant_id: int | None = None
    url: str = ""
    status: str = "published"
    note: str = ""
    duration_days: int | None = None


@router.post("/feed/record")
def feed_record(req: RecordReq) -> dict:
    item = _item(req.target_id)
    _model(req.model_id)
    channel = _channel(req.channel_id)
    mem = _membership(item["item_id"])
    if channel.id not in {c["id"] for c in mem["channels"]}:
        raise HTTPException(400, f"会员套餐（{mem['plan_label']}）不包含该渠道，无法投喂")

    vid = req.variant_id
    if not vid:
        v = store.latest_variant(item["item_id"], req.model_id, channel.id,
                                 status="approved")
        vid = v["id"] if v else None
    else:
        v = store.get_variant(vid)
        if not v or v["status"] != "approved":
            raise HTTPException(400, "改写稿未审核通过，先去「内容体检与改写」审核")
    if not vid:
        raise HTTPException(400, "该渠道还没有审核通过的改写稿")

    fid = store.save_feed(item["item_id"], req.model_id, channel.id, vid,
                          req.url.strip(), req.status, req.note,
                          duration_days=req.duration_days)
    return {"id": fid, "variant_id": vid, "url": req.url, "status": req.status,
            "duration_days": req.duration_days}


class RecheckReq(BaseModel):
    target_id: str
    model_id: str
    channel_id: str


@router.post("/feed/recheck")
def feed_recheck(req: RecheckReq) -> dict:
    """投喂后复测：同一批问法、同一把权重，只把目标文档换成投喂版本。

    before 用原稿语料、after 用投喂稿语料，自洽可比（不依赖基线快照）。
    """
    item, model, channel = _item(req.target_id), _model(req.model_id), _channel(req.channel_id)
    variant = store.latest_variant(item["item_id"], model.id, channel.id,
                                   status="approved")
    if not variant:
        raise HTTPException(400, "该渠道没有审核通过的投喂版本，先完成改写审核与投喂登记")
    qs = _queries_for(item["item_id"], model.id, channel.id)
    if not qs:
        qs = probe.build_queries(item["title"], channel, item["category"])

    weights, rs = _weights(model.id, channel.id)
    docs_before = [{**d, "text": item["text"]} if d["id"] == item["item_id"] else d
                   for d in _docs_all()]
    docs_after = [{**d, "text": variant["body"]} if d["id"] == item["item_id"] else d
                  for d in _docs_all()]
    rows_before = probe.run(qs, model, channel, weights,
                            probe.build_corpus(docs_before, key="recheck_before"),
                            [item["item_id"]])
    rows_after = probe.run(qs, model, channel, weights,
                           probe.build_corpus(docs_after, key="recheck_after"),
                           [item["item_id"]])
    before = probe.summarize(rows_before)
    after = probe.summarize(rows_after)

    z = attribution.two_prop_z(before["hits"], before["samples"],
                               after["hits"], after["samples"])
    delta = round(after["hit_rate"] - before["hit_rate"], 3)

    if abs(z) >= 1.96:
        verdict, level = ("投喂后提及率提升显著（|z| ≥ 1.96）" if delta > 0
                          else "投喂后提及率下降显著，需要回滚或重做"), "significant"
    elif after["samples"] < 20:
        verdict, level = f"样本不足（{after['samples']} 条问法），结论仅供参考", "insufficient"
    else:
        verdict, level = "变化不显著，可能还没被检索收录，建议 3-7 天后复测", "not_significant"

    for r in rows_after:
        store.save_probe(model.id, channel.id, item["item_id"], r["query"],
                         r["cited"], r["rank"], "after", r["citations"])
    store.save_recheck(item["item_id"], model.id, channel.id, variant["id"],
                       after["samples"], after["hits"], after["hit_rate"],
                       after["avg_rank"], delta, z, level,
                       before_samples=before["samples"], before_hits=before["hits"],
                       before_hit_rate=before["hit_rate"])

    return {
        "before": {**before, "at": ""},
        "after": {**after, "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
        "delta": delta, "z": z, "verdict": verdict, "level": level,
        "variant_id": variant["id"], "rule_source": "learned" if rs else "generic",
        "detail": [{"query": r["query"], "cited": r["cited"], "rank": r["rank"],
                    "citations": r["citations"][:3]} for r in rows_after],
        "note": "before = 同一问法下原稿的被引用率，after = 投喂稿的被引用率；"
                "z 为两比例检验，|z|≥1.96 才算显著。",
    }


# ------------------------------------------------------------ 6. 效果看板 ----
def _days_between(t1: str | None, t2: str | None) -> int | None:
    """t2 − t1 的天数（"YYYY-MM-DD HH:MM:SS"），解析失败返回 None。"""
    if not t1 or not t2:
        return None
    try:
        d1 = datetime.strptime(t1, "%Y-%m-%d %H:%M:%S")
        d2 = datetime.strptime(t2, "%Y-%m-%d %H:%M:%S")
        return (d2 - d1).days
    except Exception:
        return None


@router.get("/board")
def board(target_id: str, model_id: str | None = Query(None)) -> dict:
    item = _item(target_id)
    model = _model(model_id) if model_id else _model(G_DEFAULT_MODEL)
    # 模型级维度基线均值（第 2 步测的，全渠道共用）
    dim_base = [v for k, v in store.baseline_matrix(item["item_id"]).items()
                if k.startswith(f"{model.id}::")]
    model_base_avg = (round(sum(b["hit_rate"] for b in dim_base) / len(dim_base), 3)
                      if dim_base else None)
    # 渠道投喂批次时间（该渠道首次登记投喂的时间）：多渠道通常同一批次投喂，
    # 中间的「登记修改」仍归同一批次，复测间隔一律从批次起点起算。
    feeds = store.list_feeds(item["item_id"], 60)
    batch_at: dict[str, str] = {}
    for f in feeds:
        ch0 = f["channel_id"]
        if ch0 not in batch_at or f["created_at"] < batch_at[ch0]:
            batch_at[ch0] = f["created_at"]
    # 每个格 (model, channel) 的最新一次登记：复测保存时锚定了当时的登记 id，
    # 登记一旦被修改（新记录 id 变化），锚定旧记录的复测全部失效（去冗余）。
    feed_latest: dict[tuple[str, str], tuple[int, str]] = {}
    for f in feeds:  # list_feeds 按 id 倒序，每个格第一次出现即最新记录
        key = (f["model_id"], f["channel_id"])
        if key not in feed_latest:
            feed_latest[key] = (f["id"], f["created_at"])

    def cell_rechecks(target_id: str, mid: str, ch_id: str) -> tuple[list[dict], int]:
        """返回（修改后仍然有效的复测序列·升序, 因登记修改而失效的复测数）。"""
        rcs = store.list_rechecks(target_id, mid, ch_id)
        fid, ftime = feed_latest.get((mid, ch_id), (None, None))
        valid = []
        for rc in rcs:
            if fid is None:
                valid.append(rc)          # 该格从未登记过：没有失效依据
            elif rc.get("feed_id") is not None:
                if rc["feed_id"] == fid:  # 锚点仍是最新登记 → 有效
                    valid.append(rc)
            elif rc["created_at"] >= ftime:  # 老数据无锚点，退化为时间比较
                valid.append(rc)
        return valid, len(rcs) - len(valid)

    rows = []
    for ch in registry.CHANNELS:
        st = profile.cell_status(item["item_id"], model.id, ch.id)
        runs = store.probe_runs(model.id, ch.id, item["item_id"])
        rcs, void_n = cell_rechecks(item["item_id"], model.id, ch.id)
        baseline = st["baseline"]
        if rcs and rcs[0].get("before_hit_rate") is not None:
            baseline = {"hit_rate": rcs[0]["before_hit_rate"], "samples": rcs[0]["before_samples"],
                        "hits": rcs[0]["before_hits"], "avg_rank": None, "at": None,
                        "source": "recheck_before"}
        elif not baseline and model_base_avg is not None:
            baseline = {"hit_rate": model_base_avg, "samples": sum(b["samples"] for b in dim_base),
                        "hits": None, "avg_rank": None, "at": None, "source": "dim_baseline"}
        # 复测序列：仅保留最新登记之后的有效复测，按真实点击「复测」的时间排序；
        # days = 距该渠道投喂批次起点的真实天数（不是写死的 15/30）
        # 基线取第一次有效复测的 before 测量（登记修改后，旧基线不再适用于新内容）。
        base_rate = baseline["hit_rate"] if baseline else None
        base_samples = baseline["samples"] if baseline else 0
        base_hits = baseline.get("hits") if baseline else None
        if base_hits is None and base_rate is not None and base_samples:
            base_hits = base_rate * base_samples
        recheck_rows = []
        for i, rc in enumerate(rcs, 1):
            recheck_rows.append({
                "no": i, "hit_rate": rc["hit_rate"], "samples": rc["samples"],
                "delta": (round(rc["hit_rate"] - base_rate, 3) if base_rate is not None else None),
                "z": (attribution.two_prop_z(base_hits or 0, base_samples,
                                             rc["hits"], rc["samples"])
                      if base_rate is not None else None),
                "level": rc["level"], "at": rc["created_at"],
                "days": _days_between(batch_at.get(ch.id), rc["created_at"]),
            })
        after = None
        if recheck_rows:
            last = rcs[-1]
            after = {"hit_rate": last["hit_rate"], "samples": last["samples"],
                     "at": last["created_at"], "avg_rank": last["avg_rank"]}
        else:
            after_runs = [r for r in runs if r["phase"] == "after"]
            if after_runs:
                last = after_runs[-1]
                after = {"hit_rate": last["hit_rate"], "samples": last["samples"],
                         "at": last["date"], "avg_rank": last["avg_rank"]}
        z = (attribution.two_prop_z((baseline.get("hits") if baseline else 0) or
                                    (baseline["hit_rate"] * baseline["samples"] if baseline else 0),
                                    baseline["samples"] if baseline else 0,
                                    after["hit_rate"] * after["samples"], after["samples"])
             if baseline and after else None)
        rows.append({
            "channel_id": ch.id, "channel_name": ch.name, "locale": ch.locale,
            "status": st["status"], "status_label": st["status_label"],
            "baseline": baseline, "after": after,
            "delta": (round(after["hit_rate"] - baseline["hit_rate"], 3) if baseline and after else None),
            "z": z,
            "rechecks": recheck_rows, "voided_rechecks": void_n,
            "learned": st["learned"], "rule_version": st["rule_version"],
            "variants": st["variants"], "feed": st["feed"],
            "trend": runs,
        })

    both = [r for r in rows if r["baseline"] and r["after"]]
    avg_delta = round(sum(r["delta"] for r in both) / len(both), 3) if both else None
    overall = {
        "baseline_avg": round(sum(r["baseline"]["hit_rate"] for r in both) / len(both), 3) if both else None,
        "after_avg": round(sum(r["after"]["hit_rate"] for r in both) / len(both), 3) if both else None,
        "avg_delta": avg_delta,
        "channels_with_data": len(both),
        "fed": sum(1 for r in rows if r["feed"]),
        "recheck_channels": sum(1 for r in rows if r["rechecks"]),
        "recheck_total": sum(len(r["rechecks"]) for r in rows),
        "recheck_voided": sum(r["voided_rechecks"] for r in rows),
        "significant": sum(1 for r in rows if r["z"] is not None and abs(r["z"]) >= 1.96),
    }

    def _feed_rechecks(f: dict) -> dict:
        """这条登记记录关联的复测：只有该格「最新一次登记」的复测有效；
        更早的登记记录一旦被修改，其关联复测全部失效（voided）。"""
        key = (f["model_id"], f["channel_id"])
        fid = feed_latest.get(key, (None, None))[0]
        if fid is None or f["id"] != fid:
            return {"rechecks": [], "voided": True, "voided_count": 0}
        if f["model_id"] == model.id:
            row = next((r for r in rows if r["channel_id"] == f["channel_id"]), None)
            if row:
                return {"rechecks": row["rechecks"], "voided": False,
                        "voided_count": row["voided_rechecks"]}
        valid, void_n = cell_rechecks(f["target_id"], f["model_id"], f["channel_id"])
        base_t = batch_at.get(f["channel_id"])
        out = [{"no": i, "hit_rate": rc["hit_rate"], "samples": rc["samples"],
                "delta": rc["delta"], "z": rc["z"], "at": rc["created_at"],
                "days": _days_between(base_t, rc["created_at"])}
               for i, rc in enumerate(valid, 1)]
        return {"rechecks": out[:2], "voided": False, "voided_count": void_n}

    # 投喂记录按格去重（批次口径）：同一渠道（每模型）只保留最新一次登记，
    # 「登记修改」直接覆盖旧记录的展示位；旧记录仍留在库里可追溯，只是不再显示。
    seen = set()
    feeds_out = []
    for f in feeds:  # list_feeds 按 id 倒序，首次出现即该格最新登记
        key = (f["model_id"], f["channel_id"])
        if key in seen:
            continue
        seen.add(key)
        feeds_out.append({**f, **_feed_rechecks(f)})
    return {"target": profile.target_card(item), "model_id": model.id, "model_name": model.name,
            "membership": _membership(item["item_id"]),
            "rows": rows, "overall": overall, "feeds": feeds_out,
            "progress": store.progress(item["item_id"], model.id),
            "note": "before 是复测前的原稿引用率（或第 2 步模型维度基线），after 是最近一次有效复测；"
                    "同一渠道多次登记只显示最新一条（登记修改即覆盖），登记修改前的复测全部失效、不参与统计；"
                    "「投喂后 N 天」为复测与该渠道投喂批次起点（首次登记）的真实间隔。"}


@router.get("/board/overview")
def board_overview() -> dict:
    cov = store.coverage()
    items = profile._all_items()
    feeds = store.list_feeds(None, 200)
    both = 0
    total = len(cov)
    per_cell = []
    for model, channel in registry.cells():
        info = cov.get(f"{model.id}::{channel.id}")
        if not info:
            continue
        per_cell.append({"model_id": model.id, "model_name": model.name,
                         "channel_id": channel.id, "channel_name": channel.name,
                         "version": info["version"], "auc": info["auc"],
                         "samples": info["samples"], "at": info["trained_at"]})
    feed_cells = {(f["target_id"], f["model_id"], f["channel_id"]) for f in feeds}
    return {
        "targets": len(items), "learned_cells": total, "cells_total": len(registry.cells()),
        "baseline_targets": store.baseline_targets(),
        "variants": len(store.list_variants(None, 500)),
        "feeds": len(feed_cells), "feed_channels": len({c[2] for c in feed_cells}),
        "avg_auc": round(sum(c["auc"] or 0 for c in per_cell) / max(len(per_cell), 1), 3),
        "cells": per_cell,
    }


@router.get("/catalog")
def catalog() -> dict:
    d = registry.describe()
    d["cells_total"] = len(registry.cells())
    d["parts"] = scoring.PARTS
    return d
