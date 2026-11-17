"""目标物画像：把一个"要被推荐、要被引用"的东西，投影到某个 模型×渠道 上。

为什么需要这一层：
    同一款产品，在小红书和 Reddit 上能被引用的理由完全不同，在豆包和
    Perplexity 上被摘取的段落也不同。所以"投喂前"必须先明确四件事：
      1) 说给谁听（受众）
      2) 他关心什么（关注点/场景）
      3) 这个模型爱吃哪一口（该格学到的规则）
      4) 这个渠道允许怎么说（硬约束/红线）
    这四件事合起来就是"目标画像"，它同时是问题集生成与改写生成的输入。

所有字段都是确定性推导：受众来自真实点击行为（interactions 表），
价格位置来自同类商品的价格分布，缺口来自 GQS 规则分项，模型偏好来自
该格已学权重（没学过就退回模型先验）。没有一处是随机编的。
"""
from __future__ import annotations

import re
from typing import Any

from ..cells import store
from ..cells.registry import DIM_LABELS, ChannelEntry, ModelEntry
from ..data_gen import CATEGORIES
from ..db import connect, load_items
from ..geo.analyzer import analyze
from ..monitor import probe

# 品类 → 典型使用场景（决定"场景适配"类问题怎么写）
SCENES: dict[str, list[str]] = {
    "笔记本电脑": ["学生上课与图书馆", "居家办公", "经常出差", "剪辑与设计"],
    "无线耳机": ["地铁通勤", "运动健身", "办公室通话", "睡前听歌"],
    "扫地机器人": ["养宠家庭", "有小孩家庭", "大户型", "日常懒人保洁"],
    "空气净化器": ["新装修除甲醛", "过敏体质", "母婴房间", "北方雾霾天"],
    "显示器": ["办公文档处理", "游戏高刷", "设计调色", "外接笔记本"],
    "咖啡机": ["家用早晨一杯", "意式浓缩爱好者", "办公室共用", "新手入门"],
    "电动牙刷": ["牙龈敏感", "正畸期间", "常年喝茶咖啡", "烟渍牙"],
    "智能手表": ["跑步与运动", "健康监测", "通勤消息提醒", "睡眠管理"],
}

# 意图分组：问题集会按这 5 组铺开，覆盖面比"一问一答"稳
INTENTS = [
    ("category", "品类决策", 1.0, "用户还没锁定型号，在选品类"),
    ("model", "型号确认", 1.0, "已经看到型号，在判断值不值"),
    ("scene", "场景适配", 0.8, "带着具体使用场景来问"),
    ("compare", "对比替代", 0.9, "拿竞品或同价位做横向比较"),
    ("doubt", "顾虑排查", 0.7, "在意翻车点、售后、隐性问题"),
]

_SPEC_LINE = re.compile(r"^[\s\-·•*]*(.{2,12}?)[：:]\s*(.+)$")


def _rows(sql: str, args: tuple) -> list[Any]:
    with connect() as conn:
        return conn.execute(sql, args).fetchall()


_ITEMS: list[dict] | None = None


def _all_items() -> list[dict]:
    """整个库只有几百条，缓存一次；列表页与详情页都要反复用。"""
    global _ITEMS
    if _ITEMS is None:
        _ITEMS = load_items()
    return _ITEMS


def _category_items(category: str) -> list[dict]:
    return [it for it in _all_items() if it["category"] == category]


def _audience(item: dict) -> dict:
    """受众 = 真实点击过同类商品的人群结构。"""
    rows = _rows(
        "SELECT u.persona AS p, COUNT(*) AS n FROM interactions i "
        "JOIN users u ON u.user_id=i.user_id JOIN items it ON it.item_id=i.item_id "
        "WHERE it.category=? AND i.event='click' GROUP BY u.persona ORDER BY n DESC",
        (item["category"],),
    )
    total = sum(r["n"] for r in rows) or 1
    personas = [{"name": r["p"], "share": round(r["n"] / total, 3)} for r in rows[:3]]

    brands = _rows(
        "SELECT it.brand AS b, COUNT(*) AS n FROM interactions i "
        "JOIN items it ON it.item_id=i.item_id WHERE it.category=? AND i.event='click' "
        "GROUP BY it.brand ORDER BY n DESC LIMIT 3",
        (item["category"],),
    )
    peers = _category_items(item["category"])
    cheaper = sum(1 for p in peers if p["price"] < item["price"])
    pct = cheaper / max(len(peers), 1)
    if pct >= 0.75:
        band, band_text = "偏高", f"价格高于同类 {round(pct * 100)}% 的型号"
    elif pct <= 0.25:
        band, band_text = "偏低", f"价格低于同类 {round((1 - pct) * 100)}% 的型号"
    else:
        band, band_text = "中位", f"价格落在同类中段（{round(pct * 100)} 分位）"

    top = personas[0]["name"] if personas else "参数党"
    summary = (
        f"{top}是这类商品的主力人群（占点击 {round((personas[0]['share'] if personas else 0) * 100)}%），"
        f"价格带{band}；同类商品 {len(peers)} 个型号在竞争同一批问法。"
    )
    return {
        "personas": personas,
        "brands": [{"name": r["b"], "share": round(r["n"] / total, 3)} for r in brands],
        "price_position": {"band": band, "percentile": round(pct, 3), "text": band_text},
        "competition": {"peer_items": len(peers), "category": item["category"]},
        "summary": summary,
    }


def _specs(item: dict) -> list[dict]:
    """从原稿里抽参数行；抽不到就用品类典型参数占位并标注来源，避免画像凭空捏造。"""
    out: list[dict] = []
    for line in (item.get("text") or "").split("\n"):
        m = _SPEC_LINE.match(line.strip())
        if not m or not re.search(r"\d", m.group(2)):
            continue
        label = m.group(1).strip().lstrip("-·•* ")
        if label in ("来源", "数据来自", "参数版本"):
            continue
        if any(label == s["label"] for s in out):
            continue
        out.append({"label": label, "value": m.group(2).strip()[:28], "from_text": True})
        if len(out) >= 5:
            return out
    meta = CATEGORIES.get(item["category"], {})
    for label, value in (meta.get("params") or [])[:4]:
        if any(label == s["label"] for s in out):
            continue
        out.append({"label": label, "value": value, "from_text": False})
    return out[:5]


def _needs(item: dict) -> dict:
    meta = CATEGORIES.get(item["category"], {})
    aspects = [p[0] for p in (meta.get("params") or [])] + ["价格", "售后", "口碑"]
    seen, uniq = set(), []
    for a in aspects:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    return {
        "aspects": uniq[:8],
        "scenes": SCENES.get(item["category"], ["日常使用", "长期使用"]),
        "specs": _specs(item),
        "terms": list(meta.get("terms") or [])[:3],
    }


def _model_prefs(model: ModelEntry, channel: ChannelEntry) -> list[dict]:
    rs = store.latest_rule_set(model.id, channel.id)
    if rs:
        cards = sorted(rs["cards"], key=lambda c: abs(c["weight"]), reverse=True)[:5]
        return [{"label": c["label"], "weight": c["weight"], "source": "learned"} for c in cards]
    items = sorted(model.prior.items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
    return [{"label": DIM_LABELS.get(k, k), "weight": round(v, 2), "source": "prior"}
            for k, v in items]


def _must_mention(item: dict, needs: dict, channel: ChannelEntry) -> list[str]:
    core = re.sub(r"[（(].*?[）)]", "", item["title"])
    specs = [f"{s['label']} {s['value']}" for s in needs["specs"][:3]]
    out = [
        f"品牌与型号：{item['brand']} {core.replace(item['brand'], '').strip()}",
        f"一句话结论：适合哪类人、为什么值得考虑（30 字内）",
        f"关键参数：{'、'.join(specs) if specs else '需补齐 3 项可核验参数'}",
        f"价格锚点：¥{int(item['price'])}（同类{needs.get('_band', '中位')}区间）",
        "时间锚点：2026-11 参数版本",
    ]
    if channel.max_links > 0:
        out.append("来源：官方参数页 + 第三方实测（可点击核验）")
    else:
        out.append("来源：文中标注出处名称，不放外链")
    return out


def build_profile(item: dict, model: ModelEntry, channel: ChannelEntry,
                  rule: dict | None = None) -> dict:
    """生成「目标物 × 模型 × 渠道」画像。"""
    report = analyze(item.get("text") or "")
    audience = _audience(item)
    needs = _needs(item)
    needs["_band"] = audience["price_position"]["band"]

    gaps = [
        {"dim": r.key, "label": r.label, "score": round(r.score, 2), "hint": r.hint}
        for r in report.rules if r.score < 0.6
    ]
    gaps.sort(key=lambda g: g["score"])

    angle_map = {
        "zhihu": "用「先说结论 + 参数拆解」的问答体，正面回答一个具体决策问题",
        "wechat_mp": "用第一人称长文讲清取舍，短段落 + 小标题，不要导流",
        "xiaohongshu": "用短句分点讲真实体验，弱化硬广，不提外链",
        "own_blog": "用官网技术博客的完整结构：结论、参数、适用边界、FAQ",
        "baijiahao": "资讯体直给结论，标题信息量大，段落短",
        "toutiao": "问题式标题 + 短段落小标题，适配手机端阅读",
        "dianping": "第一人称消费体验，讲环境/口味/服务，禁商家口吻",
        "gov_portal": "正式书面语，突出资质、荣誉与公共价值，零营销词",
        "ota": "平台详情页口径，参数齐全，与备案信息一致",
        "reddit": "以普通用户身份分享经验与踩坑，避免任何推广口吻",
        "medium": "以数据与结论为主线的英文长文，标题直给结论",
    }
    return {
        "target": {
            "item_id": item["item_id"], "title": item["title"], "brand": item["brand"],
            "category": item["category"], "price": item["price"], "gqs": item["gqs"],
            "grade": item["geo"]["grade"] if isinstance(item.get("geo"), dict) else report.grade,
            "text_len": len(item.get("text") or ""), "created_at": item.get("created_at"),
            "is_cold": item.get("popularity", 0) == 0,
        },
        "audience": audience,
        "needs": {k: v for k, v in needs.items() if not k.startswith("_")},
        "intents": [
            {"kind": k, "label": label, "weight": w, "why": why,
             "examples": _intent_examples(item, k, needs)}
            for k, label, w, why in INTENTS
        ],
        "model": {"id": model.id, "name": model.name, "vendor": model.vendor,
                  "locale": model.locale, "note": model.note, "retrieval": model.retrieval,
                  "prefs": _model_prefs(model, channel)},
        "channel": {"id": channel.id, "name": channel.name, "style": channel.style,
                    "min_words": channel.min_words, "max_words": channel.max_words,
                    "max_links": channel.max_links, "title_max": channel.title_max,
                    "constraints": list(channel.constraints)},
        "must_mention": _must_mention(item, needs, channel),
        "gaps": gaps[:4],
        "angle": angle_map.get(channel.id, "按结论 → 参数 → 边界 → 来源的顺序组织"),
        "rule_version": rule["version"] if rule else None,
        "rule_auc": rule["auc"] if rule else None,
    }


def _intent_examples(item: dict, kind: str, needs: dict) -> list[str]:
    cat = item["category"]
    name = re.sub(r"[（(].*?[）)]", "", item["title"]).replace(item["brand"], "").strip()
    aspect = needs["aspects"][0] if needs["aspects"] else "参数"
    scene = needs["scenes"][0] if needs["scenes"] else "日常使用"
    table = {
        "category": [f"{cat} 怎么选", f"{cat} 2026 推荐", f"{cat} 哪个牌子好"],
        "model": [f"{item['brand']} {name} 怎么样", f"{name} 值得买吗", f"{name} 优缺点"],
        "scene": [f"{scene} 用什么{cat}", f"{cat} {scene} 推荐", f"{scene} {cat} 哪个合适"],
        "compare": [f"{name} 和同价位比哪个好", f"{cat} 对比 哪个更值", f"{cat} 同价位推荐"],
        "doubt": [f"{cat} {aspect} 翻车吗", f"{name} 售后怎么样", f"{cat} 买了后悔吗"],
    }
    return table.get(kind, [f"{cat} {kind}"])


# --------------------------------------------------------------- 单元格 ----
def cell_status(target_id: str, model_id: str, channel_id: str) -> dict:
    rs = store.latest_rule_set(model_id, channel_id)
    base = store.latest_baseline(target_id, model_id, channel_id)
    feed = store.feed_map(target_id, model_id).get(channel_id)
    recheck = store.latest_recheck(target_id, model_id, channel_id)
    variants = [v for v in store.list_variants(target_id, 50)
                if v["model_id"] == model_id and v["channel_id"] == channel_id]
    summary = store.probe_summary(model_id, channel_id, target_id).get("after")

    if recheck:
        status, label = "rechecked", "已投喂·已复测"
    elif feed:
        status, label = "fed", "已投喂"
    elif variants:
        status, label = "ready", "已改写未投喂"
    elif base:
        status, label = "baselined", "已测基线"
    else:
        status, label = "empty", "未覆盖"

    after = None
    if recheck:
        after = {"hit_rate": recheck["hit_rate"], "samples": recheck["samples"],
                 "avg_rank": recheck["avg_rank"], "at": recheck["created_at"],
                 "delta": recheck["delta"], "z": recheck["z"], "level": recheck["level"]}
    elif summary:
        after = {"hit_rate": summary["hit_rate"], "samples": summary["samples"],
                 "avg_rank": summary["avg_rank"], "at": None}

    return {
        "status": status, "status_label": label,
        "learned": bool(rs), "rule_version": rs["version"] if rs else None,
        "rule_auc": rs["auc"] if rs else None,
        "baseline": ({"hit_rate": base["hit_rate"], "samples": base["samples"],
                      "avg_rank": base["avg_rank"], "at": base["created_at"]} if base else None),
        "after": after,
        "variants": len(variants),
        "latest_variant": variants[0]["id"] if variants else None,
        "feed": ({"url": feed["url"], "status": feed["status"], "at": feed["created_at"]}
                 if feed else None),
    }


_CORPUS: dict[str, Any] = {}


def _corpus() -> probe.Corpus:
    if "all" not in _CORPUS:
        docs = [{"id": it["item_id"], "title": it["title"], "text": it["text"],
                 "channel": it["category"]} for it in _all_items()]
        _CORPUS["all"] = probe.build_corpus(docs, key="targets")
    return _CORPUS["all"]


def cell_view(item: dict, model: ModelEntry, channel: ChannelEntry,
              query: str | None = None) -> dict:
    """当前时间点，这个格子里"已经有谁在占位"。"""
    rs = store.latest_rule_set(model.id, channel.id)
    weights = rs["weights"] if rs else None
    q = query or f"{item['category']} 怎么选"
    cites = probe.retrieve(q, _corpus(), model, channel, weights, None, top_k=8)
    own_rank = next((n for n, c in enumerate(cites, 1) if c["id"] == item["item_id"]), None)
    others = [c for c in cites if c["id"] != item["item_id"]][:4]
    return {
        "query": q,
        "own_rank": own_rank,
        "own_score": next((c["score"] for c in cites if c["id"] == item["item_id"]), 0.0),
        "competitors": others,
        "rule_source": "learned" if rs else "generic",
    }


def target_card(item: dict) -> dict:
    """推荐流列表里的一行：企业会员 + 推广对象 + 内容质量分。"""
    base = store.baseline_map(item["item_id"])
    feeds = store.list_feeds(item["item_id"], 50)
    html = re.sub(r"^【结论】", "", (item.get("text") or "").strip())
    snippet = re.sub(r"\s+", " ", html)[:96]
    mem = store.get_membership(item["item_id"])
    from ..cells.registry import PLANS
    plan = PLANS.get((mem or {}).get("plan") or "", {})
    promote_type = item.get("promote_type") or "brand"
    return {
        "item_id": item["item_id"], "title": item["title"], "brand": item["brand"],
        "category": item["category"], "price": item["price"], "gqs": item["gqs"],
        "grade": item["geo"]["grade"], "popularity": item["popularity"],
        "intro": snippet,
        "text_len": len(item.get("text") or ""),
        "baseline_cells": len(base),
        "feed_cells": len({f["channel_id"] for f in feeds}),
        "member_name": (mem or {}).get("member_name") or item.get("member_name")
                       or f"{item['brand']}（企业）",
        "promote_type": promote_type,
        "promote_label": "推广品牌" if promote_type == "brand" else "推广产品",
        "promote_name": item.get("promote_name") or item["brand"],
        "plan": (mem or {}).get("plan") or "trial",
        "plan_label": plan.get("label", "试用（免费）"),
        "plan_price": plan.get("price", "免费"),
        "plan_channels": len(plan.get("channels", [])),
        "paid_until": (mem or {}).get("paid_until"),
        "created_at": item.get("created_at"),
    }


def enterprise_items() -> list[dict]:
    """推荐流只展示企业会员（E 开头）；电子商品等样例仅作检索语料。"""
    return [it for it in _all_items() if it["item_id"].startswith("E")]
