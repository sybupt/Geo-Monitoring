"""文本特征提取：全部用确定性规则（正则/计数），不调模型。

为什么不用 LLM 判特征：如果「规则有没有被满足」也要靠模型判断，那规则库就成了
第二个黑箱，改完内容到底是不是因为规则起作用，谁也说不清。这里宁可粗糙但可复现。
"""

from __future__ import annotations

import re
from typing import Any

_NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|元|块|年|个月|天|小时|分钟|平米|平方米|㎡|次|人|位|家|分|折|公里|km|米)")
_LIST_LINE = re.compile(r"^\s*(?:\d+[.、)）]|[-*•·]|[①②③④⑤⑥])\s*\S", re.MULTILINE)
_HEADING = re.compile(r"^\s*(?:#{2,4}\s+\S|【[^】]{2,12}】|[一二三四五六七八九十]、|\d+[.、]\s*\S{2,20}$)", re.MULTILINE)
_FAQ_Q = re.compile(r"^\s*(?:Q|问)\s*[:：]\s*\S", re.MULTILINE | re.IGNORECASE)
_FAQ_A = re.compile(r"^\s*(?:A|答)\s*[:：]\s*\S", re.MULTILINE | re.IGNORECASE)
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF]")
_SOURCE_WORDS = ("来源", "据", "公示", "资质", "许可证", "执业", "门店信息", "公开点评", "实地", "官方", "备案")
_FRESH = re.compile(r"20\d{2}\s*年|今年|本月|上月|近期|当季|近\s*\d+\s*个?月|最新")
_COMPARE = re.compile(r"相比|对比|区别|适合|如果|预算|按.{0,6}分|场景")
_GEO = re.compile(r"地铁|公交|驾车|停车|公里|步行|商圈|路口|大道|广场|附近|对面|楼层|\d+\s*号")
# 极限词：只匹配「绝对化宣称」的用法。「第一，价格公开」这种序数词不算，
# 否则每一篇分点稿都会被误判成违反广告法。
_EXTREME = re.compile(
    r"最好|最佳|最优|最便宜|最专业|最靠谱|最低价|最强|第一家|第一名|第一品牌|第一选择|第一推荐|"
    r"唯一|独家|顶级|国家级|世界级|绝对|100%|百分百|史上|无人能及|行业领先"
)
_FIRST_PERSON = re.compile(r"我们|我们|我(?!们)|咱")


def metrics(text: str, *, brand: str = "", region: str = "") -> dict[str, Any]:
    """可解释的原始计数，用于在前端展示「为什么判定这条规则被满足」。"""
    text = text or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    first_block = lines[0] if lines else ""
    # 结论句 = 首段里第一个句号之前的文字；超过 130 字就不是「一句话结论」而是铺垫
    first_sentence = first_block.split("。")[0] + "。" if "。" in first_block else first_block
    return {
        "chars": len(text),
        "number_hits": len(_NUM.findall(text)),
        "list_items": len(_LIST_LINE.findall(text)),
        "headings": len(_HEADING.findall(text)),
        "faq_pairs": min(len(_FAQ_Q.findall(text)), len(_FAQ_A.findall(text))),
        "emoji": len(_EMOJI.findall(text)),
        "brand_mentions": text.count(brand) if brand else 0,
        "region_mentions": text.count(region) if region else 0,
        "first_person": len(_FIRST_PERSON.findall(text)),
        "extreme_words": _EXTREME.findall(text)[:5],
        "first_block_chars": len(first_block),
        "first_sentence_chars": len(first_sentence),
        "first_sentence_has_entity": bool((brand and brand in first_sentence) or (region and region in first_sentence)),
        "source_hit": any(w in text for w in _SOURCE_WORDS),
        "fresh_hit": bool(_FRESH.search(text)),
        "compare_hit": len(_COMPARE.findall(text)) >= 2,
        "geo_hit": bool(_GEO.search(text)) or bool(region and region in text),
    }


def extract(
    text: str,
    *,
    brand: str = "",
    region: str = "",
    channel: str = "",
    length_range: tuple[int, int] | None = None,
) -> dict[str, float]:
    """返回 {维度: 0/1}。1 表示该特征被满足。"""
    m = metrics(text, brand=brand, region=region)
    feats: dict[str, float] = {
        "conclusion_first": 1.0 if (m["first_sentence_chars"] <= 130 and m["first_sentence_has_entity"]) else 0.0,
        "number_density": 1.0 if m["number_hits"] >= 3 else 0.0,
        "list_structure": 1.0 if m["list_items"] >= 3 else 0.0,
        "faq_block": 1.0 if m["faq_pairs"] >= 2 else 0.0,
        "heading_hierarchy": 1.0 if m["headings"] >= 2 else 0.0,
        "entity_repetition": 1.0 if (m["brand_mentions"] >= 3 and (not region or m["region_mentions"] >= 2)) else 0.0,
        "source_attribution": 1.0 if m["source_hit"] else 0.0,
        "freshness": 1.0 if m["fresh_hit"] else 0.0,
        "comparison": 1.0 if m["compare_hit"] else 0.0,
        "first_person": 1.0 if m["first_person"] >= 3 else 0.0,
        "emoji_markers": 1.0 if m["emoji"] >= 2 else 0.0,
        "official_tone": 1.0 if (m["first_person"] == 0 and not m["extreme_words"]) else 0.0,
        "local_geo": 1.0 if m["geo_hit"] else 0.0,
    }
    if length_range:
        lo, hi = length_range
        feats["length_fit"] = 1.0 if lo <= m["chars"] <= hi else 0.0
    elif channel:
        from app.rules.dsl import CHANNEL_SPECS

        spec = CHANNEL_SPECS.get(channel)
        if spec:
            lo, hi = spec["length"]
            feats["length_fit"] = 1.0 if lo <= m["chars"] <= hi else 0.0
    return feats


def satisfied_rules(text: str, rules: list[Any], *, brand: str = "", region: str = "", channel: str = "") -> list[dict[str, Any]]:
    """逐条检查规则是否被正文满足，输出可用于展示的明细。"""
    from app.rules.dsl import CHANNEL_SPECS

    feats = extract(
        text,
        brand=brand,
        region=region,
        channel=channel,
        length_range=(CHANNEL_SPECS.get(channel) or {}).get("length"),
    )
    out = []
    for r in rules:
        dim = getattr(r, "dimension", None) or r.get("dimension")
        weight = getattr(r, "weight", None) or r.get("weight", 0.5)
        out.append(
            {
                "dimension": dim,
                "dimension_label": getattr(r, "dimension", None) and "" or "",
                "weight": weight,
                "satisfied": bool(feats.get(dim, 0.0)),
            }
        )
    return out
