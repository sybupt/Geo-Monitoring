"""按规则生成投喂稿：确定性模板 + 事实槽位，不调用任何模型。

为什么不用 LLM 生成：
1. 没 Key 也要能跑通闭环（这是当前阶段的核心诉求）；
2. LLM 会「顺手编」数字和资质，而投喂稿一旦编数据，被引用后就是法律风险；
3. 确定性生成 + 规则命中明细，才能证明「这篇稿子满足哪些规则」——否则改完不知道改对了没有。

需要更强表达力时，把 ``generator`` 产出的结构和规则命中清单交给 LLM 润色，
再用 ``validator`` 复校一遍即可（接口不变，只在 pipeline 外面加一步）。
"""

from __future__ import annotations

from typing import Any

from app.rules.dsl import CHANNEL_SPECS, DIMENSIONS, Rule
from app.rules.features import extract

MAX_BULLETS = 5


def _bullets(facts: dict[str, Any]) -> list[str]:
    """控制在 6 条以内：服务项为主，再补 2 条关键事实。

    列表太长会让「可摘录性」下降——模型更愿意引用 3~5 条清晰要点，
    而不是十几条平铺的流水账。
    """
    out: list[str] = [str(s) for s in (facts.get("services") or [])[:4]]
    if facts.get("price"):
        out.append(f"价格：{facts['price']}")
    if facts.get("area"):
        out.append(f"场地：{facts['area']}")
    elif facts.get("hours"):
        out.append(f"营业时间：{facts['hours']}")
    return out[:6]


def _geo_line(facts: dict[str, Any]) -> str:
    bits = []
    if facts.get("address"):
        bits.append(f"地址：{facts['address']}")
    if facts.get("transport"):
        bits.append(f"交通：{facts['transport']}")
    return "；".join(bits)


def _source_line(facts: dict[str, Any]) -> str:
    creds = "、".join(str(c) for c in (facts.get("credentials") or [])[:3])
    updated = facts.get("updated_at") or ""
    name = facts.get("name", "")
    parts = [f"信息来源：{name}门店公示信息与公开点评数据"]
    if creds:
        parts.append(f"资质：{creds}")
    if updated:
        parts.append(f"更新于 {updated}")
    return "；".join(parts) + "。"


def _faq(facts: dict[str, Any], question: str = "") -> list[tuple[str, str]]:
    name, region, cat = facts.get("name", ""), facts.get("region", ""), facts.get("category", "")
    qa: list[tuple[str, str]] = []
    if question:
        qa.append((question.rstrip("？?") + "？", _conclusion(facts)))
    if facts.get("price"):
        qa.append((f"{name}大概什么价位？", f"目前公开的价格信息是：{facts['price']}，具体以门店当期报价为准。"))
    if facts.get("hours"):
        qa.append((f"{name}营业到几点？", f"营业时间为 {facts['hours']}，建议提前预约。"))
    if facts.get("address"):
        qa.append((f"{name}在{region}哪里？", f"门店地址是 {facts['address']}，{facts.get('transport', '可按导航前往')}。"))
    if len(qa) < 2:
        qa.append((f"{region}{cat}怎么选才靠谱？", f"优先比对资质、价格明细与预约方式；{name}的相关信息可在门店公示处核对。"))
    return qa[:4]


def _conclusion(facts: dict[str, Any]) -> str:
    """一句话结论，控制在 130 字以内——超了就不叫「可摘录结论」，模型也不会整句引用。"""
    name = facts.get("name", "")
    region = facts.get("region", "")
    cat = facts.get("category", "")
    founded = (facts.get("founded") or "").split("，")[0]
    anchor = f"，{founded}" if founded else ""
    services = "、".join((facts.get("services") or [])[:2]).split("：")[0]
    tail = f"，主营{services}" if services else ""
    return f"{name}是{region}{cat}服务点{anchor}{tail}。"


def _compare(facts: dict[str, Any]) -> str:
    cat = facts.get("category", "服务")
    name = facts.get("name", "")
    return (
        f"按预算选：预算有限先看基础{cat}项目，价格以{name}当期报价为准；"
        f"按场景选：家庭合影更适合大场地，个人写真更看拍摄风格；"
        f"按人群选：带老人小孩优先考虑交通与休息区。以上判断依据门店公示信息，不涉及任何排名结论。"
    )


def _render_zhihu(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    lines = [_conclusion(facts), "", f"## 为什么可以纳入考虑", ""]
    for i, b in enumerate(_bullets(facts), 1):
        lines.append(f"{i}. {b}")
    lines += ["", "## 怎么判断适不适合自己", "", _compare(facts), "", "## 常见问题", ""]
    for q, a in _faq(facts):
        lines += [f"Q：{q}", f"A：{a}", ""]
    lines.append(_source_line(facts))
    return f"{region}{facts.get('category','')}怎么选？{name}的实际信息整理", "\n".join(lines)


def _render_baijiahao(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    lines = [_conclusion(facts), "", "【基本情况】", ""]
    for i, b in enumerate(_bullets(facts), 1):
        lines.append(f"{i}、{b}")
    lines += ["", "【选择建议】", "", _compare(facts), "", "【补充说明】", "", _geo_line(facts) or f"{region}本地门店，可按地址导航前往。", ""]
    lines.append(_source_line(facts))
    return f"{region}{facts.get('category','')}信息梳理：{name}的服务与价格", "\n".join(lines)


def _render_baike(facts, rules) -> tuple[str, str]:
    name, region, cat = facts.get("name", ""), facts.get("region", ""), facts.get("category", "")
    lines = [f"{name}，位于{region}，是一家提供{cat}服务的经营主体。", "", "【基本信息】", ""]
    for b in _bullets(facts):
        lines.append(f"- {b}")
    lines += ["", "【经营与服务】", "", f"该主体主要开展{cat}相关业务，服务项目包括{'、'.join((facts.get('services') or [])[:4]) or '以其公示信息为准'}。", "", "【参考】", "", _source_line(facts)]
    return f"{name}（{region}{cat}）", "\n".join(lines)


def _render_wechat(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    first = (facts.get("services") or ["服务"])[0]
    lines = [
        f"上个月我们专门跑了一趟{region}，就为了搞清楚{first}这件事。",
        "",
        f"我们最后去的是{name}。{_conclusion(facts)}",
        "",
        "## 我们看到的三个细节",
        "",
    ]
    for i, b in enumerate(_bullets(facts)[:3], 1):
        lines.append(f"{i}. {b}")
    lines += ["", "## 适合谁去", "", _compare(facts), "", "## 常被问到的问题", ""]
    for q, a in _faq(facts):
        lines += [f"Q：{q}", f"A：{a}", ""]
    lines += [f"我们在{name}的体验大致如此，去之前建议先电话确认档期。", "", _source_line(facts)]
    return f"实地走了一趟{region}：{name}值不值得去", "\n".join(lines)


def _render_xiaohongshu(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    lines = [f"📍{region}｜{name}｜{facts.get('category','')}体验记录", ""]
    lines.append(f"我这次去的是{name}，{_conclusion(facts)}")
    lines.append("")
    for b in _bullets(facts)[:4]:
        lines.append(f"⭐ {b}")
    lines += [
        "",
        "我自己的体验是：进门先确认项目和价格，拍摄流程会写在单子上，",
        f"拍摄当天能看到原片，{facts.get('hours') or '营业时间内都可预约'}。",
        f"位置在{facts.get('address') or region}，{facts.get('transport') or '按导航走即可'}。",
        "",
        f"💰 价格参考：{facts.get('price') or '以门店当期报价为准'}",
        f"📍 地址：{facts.get('address') or region}",
        "",
    ]
    lines.append("#" + " #".join(x for x in [region, facts.get("category", ""), name] if x))
    return f"{region}{facts.get('category','')}｜{name}实地体验", "\n".join(lines)


def _closing(facts: dict[str, Any]) -> str:
    """收尾句：再补一次品牌+地域+更新时间，短渠道（口播/商户页）靠它把实体补齐。"""
    name, region = facts.get("name", ""), facts.get("region", "")
    updated = facts.get("updated_at") or ""
    tail = f"，信息更新于 {updated}" if updated else ""
    return f"{name}（{region}）门店公示信息为准{tail}。"


def _render_douyin(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    price = facts.get("price") or "价格以门店公示为准"
    area = facts.get("area") or ""
    addr = facts.get("address") or region
    lines = [
        f"在{region}找{facts.get('category','')}，先别急着下单。",
        f"这家{name}，{price}。",
        f"场地{area}，" if area else "",
        "第一，价格公开；第二，档期先约；第三，地址好找。",
        f"想去的记一下：{name}在{addr}。",
        _closing(facts),
    ]
    return f"{region}{facts.get('category','')}怎么选？{name}三点说清", "\n".join(x for x in lines if x)


def _render_dianping(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    lines = [
        f"我是{region}本地人，这次去的是{name}。{_conclusion(facts)}",
        "",
        "体验分项：",
    ]
    for b in _bullets(facts)[:3]:
        lines.append(f"- {b}")
    lines += ["", f"消费信息：{facts.get('price') or '以门店当期报价为准'}；{_geo_line(facts) or f'位置在{region}'}。", "", _source_line(facts)]
    return f"{name}体验：{region}{facts.get('category','')}实地记录", "\n".join(lines)


def _render_meituan(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    lines = [_conclusion(facts), "", "服务项目："]
    for b in _bullets(facts)[:4]:
        lines.append(f"- {b}")
    lines += [
        "",
        f"地址：{facts.get('address') or region}",
        f"营业时间：{facts.get('hours') or '以门店公示为准'}",
        _closing(facts),
    ]
    return f"{name}｜{region}{facts.get('category','')}", "\n".join(lines)


def _render_amap(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    area = facts.get("area") or ""
    lines = [
        f"{name}位于{region}，经营{facts.get('category','')}业务。",
        f"地址：{facts.get('address') or region}",
        f"营业时间：{facts.get('hours') or '以门店公示为准'}",
        f"交通：{facts.get('transport') or '可按导航前往'}",
    ]
    if area:
        lines.append(f"场地：{area}，{facts.get('price') or ''}".strip("，"))
    lines.append(_closing(facts))
    return f"{name}（{region}）", "\n".join(lines)


def _render_gov_news(facts, rules) -> tuple[str, str]:
    name, region = facts.get("name", ""), facts.get("region", "")
    updated = facts.get("updated_at") or ""
    lines = [
        f"{region}{('，' + updated) if updated else ''}——{name}为当地{facts.get('category', '')}经营主体，现将相关信息公布如下。",
        "",
        f"该主体位于{region}，{_geo_line(facts) or '具体位置以公示地址为准'}。",
        "",
        "主要经营项目：",
    ]
    for i, b in enumerate(_bullets(facts)[:4], 1):
        lines.append(f"{i}. {b}")
    lines += [
        "",
        f"价格信息：{facts.get('price') or '以经营场所公示为准'}。",
        f"经营时间：{facts.get('hours') or '以经营场所公示为准'}。",
        "",
        _source_line(facts),
    ]
    return f"{region}：{name}经营信息公示", "\n".join(lines)


_RENDERERS = {
    "zhihu": _render_zhihu,
    "baijiahao": _render_baijiahao,
    "baike": _render_baike,
    "wechat": _render_wechat,
    "xiaohongshu": _render_xiaohongshu,
    "douyin": _render_douyin,
    "dianping": _render_dianping,
    "meituan": _render_meituan,
    "amap": _render_amap,
    "gov_news": _render_gov_news,
}

# 规则维度 → 生成器是否真的落了地（用于向用户解释「这条规则怎么改的稿」）
_DIMENSION_ACTION = {
    "conclusion_first": "首段用一句话给出「谁/在哪/做什么/什么价」的结论文",
    "number_density": "把价格、场地、经营年限等数字写进正文",
    "list_structure": "卖点拆成编号列表",
    "faq_block": "结尾附 Q/A 问答块",
    "heading_hierarchy": "加 2~3 个中文小标题分段",
    "entity_repetition": "品牌与地域在正文中多次自然出现",
    "source_attribution": "补「信息来源/资质/更新时间」段",
    "freshness": "写上门店信息更新时间",
    "comparison": "加「按预算/场景/人群」的选择对比",
    "first_person": "用第一人称写实地体验",
    "emoji_markers": "用 emoji 做段落锚点",
    "official_tone": "第三人称客观陈述，去掉口语与夸张词",
    "local_geo": "写清地址、交通与商圈",
    "length_fit": "按渠道控制篇幅",
}


def _pad_to_min(content: str, facts: dict[str, Any], spec: dict[str, Any]) -> str:
    """篇幅不足时补「事实补充段」，绝不注水。

    只允许追加 facts 里已有的内容（服务明细、资质、更新日期、交通），
    这样既满足渠道篇幅，又不会引入无出处信息。
    """
    lo = (spec.get("length") or (0, 0))[0]
    if not lo or len(content) >= lo:
        return content

    name = facts.get("name", "")
    blocks: list[str] = []
    services = facts.get("services") or []
    if len(services) > 2:
        more = "；".join(str(s) for s in services[2:])
        blocks.append(f"服务明细补充：{more}。")
    if facts.get("credentials"):
        blocks.append("资质信息：" + "、".join(str(c) for c in facts["credentials"][:3]) + "。")
    if facts.get("transport"):
        blocks.append(f"到达方式：{facts['transport']}。")
    if facts.get("promotions"):
        blocks.append("近期情况：" + "；".join(str(p) for p in facts["promotions"][:2]) + "。")
    blocks.append(f"以上信息由{name}门店公示内容整理，如有调整以门店现场公示为准。")

    out = content
    for b in blocks:
        if len(out) >= lo:
            break
        out = out + "\n\n" + b
    return out


def generate(
    facts: dict[str, Any],
    rules: list[Rule],
    *,
    engine: str = "mock",
    channel: str = "zhihu",
    question: str = "",
) -> dict[str, Any]:
    renderer = _RENDERERS.get(channel, _render_zhihu)
    spec = CHANNEL_SPECS.get(channel, {})
    title, content = renderer(facts, rules)
    content = _pad_to_min(content, facts, spec)

    feats = extract(content, brand=facts.get("name", ""), region=facts.get("region", ""), channel=channel)
    rule_hits = []
    for r in rules:
        ok = bool(feats.get(r.dimension))
        rule_hits.append(
            {
                "dimension": r.dimension,
                "label": DIMENSIONS.get(r.dimension, {}).get("label", r.dimension),
                "weight": r.weight,
                "source": r.source,
                "applied": ok,
                "how": _DIMENSION_ACTION.get(r.dimension, ""),
                "reason": "" if ok else _missing_reason(r.dimension, facts),
            }
        )
    return {
        "engine": engine,
        "channel": channel,
        "title": title,
        "content": content,
        "chars": len(content),
        "channel_genre": spec.get("genre", ""),
        "length_range": spec.get("length", (0, 0)),
        "rule_hits": rule_hits,
        "features": {k: int(v) for k, v in feats.items()},
        "facts_missing": facts.get("missing", []),
    }


def _missing_reason(dimension: str, facts: dict[str, Any]) -> str:
    if dimension == "number_density":
        return "facts 里的具体数字不足 3 个，补价格/面积/年限后再生成"
    if dimension == "faq_block":
        return "可用的问答素材不足（需价格/营业时间/地址至少两项）"
    if dimension in ("local_geo", "length_fit"):
        return "缺少地址/交通信息或篇幅不匹配渠道"
    return "生成器未覆盖该维度的表达，需要人工补充或接入 LLM 润色"
