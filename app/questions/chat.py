"""对话式出题：把一段自然语言（语音转写或打字）解析成「维度问题集」。

两种引擎，自动降级：

    1. LLM 引擎（可选）：配置了 GEOREC_LLM_API_KEY 时，把对话历史交给
       大模型，让它输出结构化 JSON（维度 + 自定义关注点 + 补充问法）。
    2. 规则引擎（默认，无 Key 可跑）：关键词命中维度 + 引号/「重点问」
       抽取自定义关注点，再用行业模板生成问法。

设计约束：
    - 产出与「按行业生成维度问题集」同一形状（groups/questions），
      直接并入库里的维度问题集，基线与规则学习链路无需改动。
    - 多轮：用户说「再来几个」「补充一下」时，从对话历史里取上一条
      用户消息的维度继续生成。
    - 不绑模型与渠道：问法是全模型通用的。
"""
from __future__ import annotations

import json
import re

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

# 关键词 → 维度 key（覆盖所有行业包的维度，按行业过滤后使用）
_DIM_HINTS: dict[str, tuple[str, ...]] = {
    "price": ("价格", "多少钱", "贵不", "便宜", "性价比", "价位", "预算", "收费",
              "人均", "报价", "省钱", "打几折", "划算", "值不值"),
    "avoid": ("避坑", "踩雷", "翻车", "差评", "套路", "陷阱", "假货", "上当",
              "被坑", "防坑", "雷区", "缺点", "后悔"),
    "taste": ("口味", "好吃", "口感", "味道", "香气", "回甘", "风味", "鲜不", "好不好吃"),
    "clean": ("干净", "卫生", "农残", "食材", "消毒", "后厨", "布草", "检测",
              "食品安全", "干不干净"),
    "param": ("参数", "配置", "规格", "性能", "设施", "硬件", "产地", "等级",
              "工艺", "响应速度", "客房", "隔音", " breakfast "),
    "auth": ("资质", "案例", "靠谱", "备案", "证书", "信誉", "规模", "正规"),
    "compare": ("对比", "比较", "哪个好", "排行", "排名", "竞品", "同行",
                "横向", "和.*比", "vs"),
    "location": ("位置", "交通", "地段", "地铁", "周边", "离.*近", "方便吗"),
    "env": ("环境", "排队", "服务态度", "包间", "装修", "停车", "氛围"),
    "scene": ("请客", "聚餐", "约会", "团建", "送礼", "送人", "场景", "新手",
              "家用", "办公", "亲子", "商务", "学生党"),
}

# 「全部维度」的说法
_ALL_WORDS = ("全部", "所有维度", "都要", "都来", "全面", "各个维度", "每种")

# 自定义关注点的通用问法模板（{asp}=关注词）
_CUSTOM_TPLS = [
    "{name} {asp}怎么样",
    "{name} 在{asp}方面表现如何",
    "选{cat} 最该关注{asp}吗",
    "{cat} 的{asp}一般怎么看",
    "{name} {asp}做得好不好",
    "{asp}对选{cat}影响大吗",
]

# 多轮延续词：本轮没说新维度时，沿用上一条用户消息的维度
_FOLLOW_WORDS = ("再来", "补充", "继续", "再加", "多来", "还有", "也出", "接着")


# ------------------------------------------------------------- 规则解析 ----
def _match_dims(message: str, pack_dims: list[dict]) -> list[dict]:
    """按行业包内维度做关键词命中，保持包内顺序。"""
    hits = []
    for d in pack_dims:
        words = _DIM_HINTS.get(d["key"], ())
        words += tuple(d["aspects"]) + (d["label"], d["label"].rstrip("党"))
        if any(w in message for w in words if len(w) <= 6) or any(
                re.search(w, message) for w in words if len(w) > 6):
            hits.append(d)
    return hits


def _extract_aspects(message: str) -> list[str]:
    """抽取自定义关注点：引号内容优先，其次「重点问/看/关注 X」。"""
    aspects: list[str] = []
    for pat in (r"[「『\"“']([^」』\"”']{2,12})[」』\"”']",
                r"重点(?:问|看|关注|聊|出题|考察)[一下是]{0,2}([\u4e00-\u9fa5A-Za-z0-9]{2,12})",
                r"(?:关于|针对|围绕)([\u4e00-\u9fa5A-Za-z0-9]{2,12})(?:方面|的问题|出题)"):
        aspects += re.findall(pat, message)
    stop = ("问题", "问法", "维度", "内容", "方面", "一些", "几个")
    return [a.strip() for a in aspects
            if a.strip() and not any(a.strip().endswith(s) for s in stop)][:4]


def _gen_custom(item: dict, asp: str) -> list[str]:
    from .dimensions import _fill
    out, seen = [], set()
    for tpl in _CUSTOM_TPLS:
        q = _fill(tpl, item, asp)
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def rule_parse(message: str, item: dict, history: list[dict]) -> dict:
    from .dimensions import _fill, dims_for
    pack_dims = dims_for(item)
    dims = _match_dims(message, pack_dims)
    aspects = _extract_aspects(message)

    # 多轮延续：本轮没提新维度/关注点时，沿用上一条用户消息
    if not dims and not aspects:
        prev = next((h["content"] for h in reversed(history)
                     if h.get("role") == "user"), "")
        if prev and any(w in message for w in _FOLLOW_WORDS):
            dims = _match_dims(prev, pack_dims)
            if not dims:
                if any(w in prev for w in _ALL_WORDS):
                    dims = list(pack_dims)

    if any(w in message for w in _ALL_WORDS):
        dims = list(pack_dims)

    if not dims and not aspects:
        labels = "、".join(f"{d['label']}" for d in pack_dims)
        return {
            "engine": "rule", "needs_clarify": True,
            "reply": (f"我没有从这句话里听出想测哪些维度。当前目标物"
                      f"《{item['title']}》（{item['category']}）可以出这些维度："
                      f"{labels}。比如你可以说「重点出价格和避坑的问题」，"
                      f"或者「出几条关于售后的」——售后这种自定义关注点我也会生成。"),
        }

    dim_out = []
    for d in dims:
        qs, seen = [], set()
        for tpl in d["templates"]:
            q = _fill(tpl, item, d["aspects"][0] if d["aspects"] else "")
            if q and q not in seen:
                seen.add(q)
                qs.append(q)
        dim_out.append({"dimension": d["key"], "label": d["label"],
                        "desc": d["desc"], "questions": qs})
    for asp in aspects:
        dim_out.append({"dimension": "custom", "label": f"自定义·{asp}",
                        "desc": f"来自对话要求的关注点：{asp}",
                        "questions": _gen_custom(item, asp)})

    names = "、".join(s["label"] for s in dim_out)
    n = sum(len(s["questions"]) for s in dim_out)
    return {
        "engine": "rule", "needs_clarify": False, "dims": dim_out,
        "reply": (f"好的，已按你的要求为《{item['title']}》生成 {names} "
                  f"共 {n} 条问法，并入了维度问题集——可以去测全模型基线了。"
                  + ("另外提醒：这句话里提到了其他目标物，本次仍按当前目标物出题。"
                     if _other_item_mentioned(message, item) else "")),
    }


def _other_item_mentioned(message: str, item: dict) -> bool:
    from ..db import load_items
    for it in load_items():
        if it["item_id"] != item["item_id"] and it["title"] and it["title"] in message:
            return True
    return False


# ------------------------------------------------------------- LLM 解析 ----
LLM_SYSTEM = """你是 GEO 投喂系统的出题助手。用户会用一段口语描述出题要求，
你要解析要求并生成「测试问题集」——用于检测某个目标物（产品/企业/餐厅/酒店等）
是否会被大模型在回答中引用。

可用的维度 key（尽量从中选）：
{dim_keys}

目标物信息：{item_card}

输出严格 JSON（不要多余文字）：
{{"reply": "一句话确认你理解的要求",
  "dimensions": ["命中的维度key"],
  "custom_aspects": ["用户提出但不在维度里的自定义关注点"],
  "extra_questions": {{"维度key或custom": ["按用户要求写的具体问法", ...]}}}}

要求：
- 问法要像真实用户会问的口语化短句，带目标物名字或类目；
- 用户没提到的维度不要硬凑；用户说"全部维度"才全部选上；
- extra_questions 每个维度 3-6 条。"""


def llm_parse(message: str, item: dict, history: list[dict]) -> dict | None:
    """LLM 解析；任何失败返回 None 走规则兜底。"""
    if not LLM_API_KEY:
        return None
    import requests
    from .dimensions import dims_for
    keys = []
    for d in dims_for(item):
        keys.append(f"- {d['key']}（{d['label']}：{d['desc']}）")
    card = (f"名称={item['title']}，品牌={item['brand']}，类目={item['category']}，"
            f"价格={item.get('price')}元")
    msgs = [{"role": "system",
             "content": LLM_SYSTEM.format(dim_keys="\n".join(keys), item_card=card)}]
    for h in history[-8:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:500]})
    msgs.append({"role": "user", "content": message})
    try:
        resp = requests.post(
            f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
            json={"model": LLM_MODEL, "temperature": 0.3, "messages": msgs},
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {LLM_API_KEY}"},
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
        data = json.loads(text)
    except Exception as exc:  # 网络失败 / JSON 不合法 → 规则兜底
        print(f"[chat] LLM 解析失败，回退规则引擎：{exc}")
        return None

    from .dimensions import _fill, dims_for
    by_key = {d["key"]: d for d in dims_for(item)}
    dim_out = []
    for key in data.get("dimensions", []):
        d = by_key.get(key)
        if not d:
            continue
        qs, seen = [], set()
        for tpl in d["templates"]:
            q = _fill(tpl, item, d["aspects"][0] if d["aspects"] else "")
            if q and q not in seen:
                seen.add(q)
                qs.append(q)
        extra = [q for q in data.get("extra_questions", {}).get(key, []) if q]
        dim_out.append({"dimension": d["key"], "label": d["label"], "desc": d["desc"],
                        "questions": dedup(qs + extra)})
    for asp in data.get("custom_aspects", [])[:4]:
        if not isinstance(asp, str) or not asp.strip():
            continue
        asp = asp.strip()[:12]
        extra = [q for q in data.get("extra_questions", {}).get("custom", [])
                 if q and asp in q] or _gen_custom(item, asp)
        dim_out.append({"dimension": "custom", "label": f"自定义·{asp}",
                        "desc": f"来自对话要求的关注点：{asp}",
                        "questions": dedup(extra)})
    if not dim_out:
        return None
    names = "、".join(s["label"] for s in dim_out)
    n = sum(len(s["questions"]) for s in dim_out)
    return {"engine": "llm", "needs_clarify": False, "dims": dim_out,
            "reply": str(data.get("reply") or
                         f"已按你的要求生成 {names} 共 {n} 条问法，并入了维度问题集。")}


def dedup(qs: list) -> list[str]:
    seen, out = set(), []
    for q in qs:
        q = str(q).strip()
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


# ----------------------------------------------------------------- 入口 ----
def parse_message(message: str, item: dict, history: list[dict]) -> dict:
    message = (message or "").strip()
    if not message:
        return {"engine": "rule", "needs_clarify": True,
                "reply": "请说出你的出题要求，例如「重点出价格和避坑的问题」。"}
    return llm_parse(message, item, history) or rule_parse(message, item, history)
