"""测试问题集：先问人，再出题，最后测基线。

为什么要"对话式引导"而不是直接按品类套模板：
    同一个型号，卖给"学生党"和卖给"设计岗"，用户会问完全不同的话；
    问法不对，测出来的提及率就是假的——你可能在一条没人搜的长尾问法上
    拿到 100%，但真实流量一条都进不来。所以这里先问五个问题：
    说给谁、什么场景、最想被记住的三个点、主要竞品、期望的提及形态，
    再用这些答案收缩问法空间。

出题不靠 LLM：模板 + 画像字段的组合已经能覆盖 5 类意图，且完全可复现。
接上 LLM 后替换 `_phrase` 即可，分组与基线逻辑不动。
"""
from __future__ import annotations

import re

from ..cells import store
from ..cells.registry import ChannelEntry, ModelEntry
from ..monitor import probe
from ..targets.profile import build_profile

CATEGORY_EN = {
    "笔记本电脑": "laptop", "无线耳机": "wireless earbuds", "扫地机器人": "robot vacuum",
    "空气净化器": "air purifier", "显示器": "monitor", "咖啡机": "espresso machine",
    "电动牙刷": "electric toothbrush", "智能手表": "smartwatch",
}


def _short(title: str, brand: str) -> str:
    return re.sub(r"[（(].*?[）)]", "", title).replace(brand, "").strip()


def guide(item: dict, model: ModelEntry, channel: ChannelEntry) -> dict:
    """引导步骤：选项全部来自画像，不让用户面对空白输入框。"""
    p = build_profile(item, model, channel)
    personas = [x["name"] for x in p["audience"]["personas"]] or ["参数党"]
    return {
        "target": p["target"],
        "model": p["model"],
        "channel": p["channel"],
        "steps": [
            {
                "key": "audience", "label": "这次主要说给谁听",
                "hint": "决定称谓语、案例和取舍口径；默认给的是该类目点击占比最高的人群",
                "options": personas, "default": personas[0],
            },
            {
                "key": "scene", "label": "最核心的使用场景",
                "hint": "场景词是长尾问法的主要来源", "options": p["needs"]["scenes"],
                "default": p["needs"]["scenes"][0],
            },
            {
                "key": "aspects", "label": "最希望被引用的三个信息点",
                "hint": "会直接决定参数类问法，也决定改写时保留哪些段落", "multi": True,
                "options": p["needs"]["aspects"], "default": p["needs"]["aspects"][:3],
            },
            {
                "key": "rival", "label": "最常被拿来对比的竞品或替代方案",
                "hint": "留空则只生成同价位对比问法", "free": True,
                "options": [b["name"] for b in p["audience"]["brands"]
                            if b["name"] != item["brand"]][:3],
                "default": "",
            },
            {
                "key": "tone", "label": "期望被引用时的形态",
                "hint": "决定问法偏「推荐」还是偏「避坑」",
                "options": ["结论式推荐", "参数对比", "避坑提醒", "真实体验分享"],
                "default": "结论式推荐",
            },
        ],
        "audience": p["audience"],
        "gaps": p["gaps"],
        "must_mention": p["must_mention"],
    }


def _phrase(item: dict, channel: ChannelEntry, kind: str, ctx: dict) -> list[str]:
    cat = item["category"]
    short = _short(item["title"], item["brand"])
    brand = item["brand"]
    en = channel.locale == "en"
    cat_en = CATEGORY_EN.get(cat, cat)
    scene = ctx.get("scene") or ""
    aspects = ctx.get("aspects") or []
    rival = (ctx.get("rival") or "").strip()
    audience = ctx.get("audience") or ""

    if en:
        table = {
            "category": [f"best {cat_en} 2026", f"how to choose a {cat_en}", f"{cat_en} brands comparison"],
            "model": [f"{brand} {short} review", f"is {short} worth buying", f"{short} pros and cons"],
            "scene": [f"best {cat_en} for {scene or 'home office'}", f"{cat_en} for {scene or 'daily use'}"],
            "aspect": [f"{cat_en} {a} explained" for a in aspects[:3]] or [f"{cat_en} specs explained"],
            "compare": ([f"{short} vs {rival}"] if rival else [f"{cat_en} under {int(item['price'])} comparison"]),
            "doubt": [f"{cat_en} common problems", f"{short} warranty and support", f"is {short} reliable"],
        }
        return table.get(kind, [])

    table = {
        "category": [f"{cat} 怎么选", f"{cat} 2026 推荐", f"{cat} 哪个牌子好",
                     f"{(audience + ' ') if audience else ''}{cat} 推荐", f"新手 {cat} 怎么挑"],
        "model": [f"{brand} {short} 怎么样", f"{short} 值得买吗", f"{short} 优缺点",
                  f"{short} 参数 实测"],
        "scene": ([f"{scene} 选什么{cat}", f"{cat} {scene} 推荐",
                   f"预算 {int(item['price'])} 的{cat} 推荐"] if scene
                  else [f"{cat} 日常使用 推荐", f"预算 {int(item['price'])} 的{cat} 推荐"]),
        "aspect": ([f"{cat} {a} 怎么看" for a in aspects[:3]]
                   + [f"{cat} {a} 实测" for a in aspects[:2]]
                   + ([f"{short} {aspects[0]} 多少"] if aspects else [])),
        "compare": ([f"{short} 和 {rival} 怎么选", f"{short} 对比 {rival} 哪个好"]
                    if rival else [f"{cat} 同价位 对比", f"{cat} 平替 有哪些"]),
        "doubt": [f"{cat} 常见问题", f"{short} 售后 怎么样", f"{cat} 买了后悔吗",
                  f"{cat} 哪个型号不容易坏"],
    }
    return table.get(kind, [])


def _difficulty(peers: int, kind: str) -> str:
    # 竞争度只看同类内容数量，与问法类型做一点修正
    score = peers + (6 if kind == "category" else 0)
    if score >= 40:
        return "高"
    if score >= 24:
        return "中"
    return "低"


def suggest(item: dict, model: ModelEntry, channel: ChannelEntry, answers: dict) -> dict:
    """按引导答案生成分组问题集。"""
    p = build_profile(item, model, channel)
    ctx = {
        "audience": answers.get("audience") or "",
        "scene": answers.get("scene") or "",
        "aspects": answers.get("aspects") or [],
        "rival": answers.get("rival") or "",
        "tone": answers.get("tone") or "",
    }
    peers = p["audience"]["competition"]["peer_items"]
    groups: list[dict] = []
    seen: set[str] = set()

    plan = [
        ("category", "品类决策", 1.0, "用户还没锁定型号，在选品类"),
        ("model", "型号确认", 1.0, "已经看到型号，在判断值不值"),
        ("scene", "场景适配", 0.85, "带着具体场景来问，长尾流量主入口"),
        ("aspect", "关注点拆解", 0.8, "模型最容易被逐项摘取的问法"),
        ("compare", "对比替代", 0.9, "横向比较，决定最终下单"),
        ("doubt", "顾虑排查", 0.7, "在意翻车点与售后"),
    ]
    for kind, label, base_w, why in plan:
        qs: list[dict] = []
        for text in _phrase(item, channel, kind, ctx):
            text = re.sub(r"\s+", " ", text).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            qs.append({
                "text": text,
                "weight": round(base_w * (1.0 if kind != "aspect" else 1.0), 2),
                "difficulty": _difficulty(peers, kind),
            })
        if qs:
            groups.append({"kind": kind, "label": label, "why": why,
                           "questions": qs[:6]})

    total = sum(len(g["questions"]) for g in groups)
    return {
        "target_id": item["item_id"], "model_id": model.id, "channel_id": channel.id,
        "params": ctx, "groups": groups, "total": total,
        "locale": channel.locale,
        "note": ("问题集由画像字段 + 渠道语言组合而成：5 类意图共 %d 条问法。"
                 "难度按同类竞争内容数量估算，不接真实搜索量时它只是相对值。" % total),
    }


# ------------------------------------------------------------- 基线测量 ----
def _all_docs() -> list[dict]:
    from ..db import load_items
    return [{"id": it["item_id"], "title": it["title"], "text": it["text"],
             "channel": it["category"]} for it in load_items()]


def run_baseline(item: dict, model: ModelEntry, channels: list[ChannelEntry],
                 questions: list[str], set_ids: dict[str, int] | None = None,
                 persist: bool = True) -> dict:
    """投喂前测一轮提及率：同一批问法，逐渠道跑。"""
    docs = _all_docs()
    corpus = probe.build_corpus(docs, key="baseline")
    set_ids = set_ids or {}
    out = []
    for ch in channels:
        rs = store.latest_rule_set(model.id, ch.id)
        weights = rs["weights"] if rs else None
        rows = probe.run(questions, model, ch, weights, corpus, [item["item_id"]])
        s = probe.summarize(rows)
        item_out = {
            "channel_id": ch.id, "channel_name": ch.name, "locale": ch.locale,
            "samples": s["samples"], "hits": s["hits"], "hit_rate": s["hit_rate"],
            "avg_rank": s["avg_rank"], "rule_source": "learned" if rs else "generic",
            "detail": [{"query": r["query"], "cited": r["cited"], "rank": r["rank"]} for r in rows],
        }
        if persist:
            store.save_baseline(item["item_id"], model.id, ch.id, set_ids.get(ch.id),
                                s["samples"], s["hits"], s["hit_rate"], s["avg_rank"], questions)
            for r in rows:
                store.save_probe(model.id, ch.id, item["item_id"], r["query"],
                                 r["cited"], r["rank"], "baseline")
        out.append(item_out)

    hits = sum(x["hits"] for x in out)
    samples = sum(x["samples"] for x in out)
    return {
        "target_id": item["item_id"], "model_id": model.id, "model_name": model.name,
        "samples": samples, "hits": hits,
        "hit_rate": round(hits / max(samples, 1), 3),
        "channels": out,
        "note": "基线 = 原稿在「该格已学规则 + 同一批问法」下的被引用率。"
                "改完之后复测同一批问法，差值才能归因到内容改写。",
    }
