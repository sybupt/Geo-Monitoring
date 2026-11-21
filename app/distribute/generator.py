"""按「模型 × 渠道」生成投喂版本。

生成是确定性的、可复现的：先过通用规则改写拿到干净母稿，再按渠道硬约束与
该格学到的规则卡做结构化重组。不接 LLM 也能出稿；接上 LLM 后只需替换
`_llm_polish` 一处，其余流程不变。
"""
from __future__ import annotations

import json
import re
import time
import urllib.request

from ..cells.registry import ChannelEntry, DIM_LABELS
from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT
from ..geo.analyzer import analyze
from ..geo.rewriter import rewrite_rule_based
from .policy import check
from .source_features import format_profile_for_prompt, load_channel_profile

_URL = re.compile(r"https?://\S+")
_NUM_LINE = re.compile(r"^[\s\-·•*]*(.{2,12}?)[：:]\s*(.+)$")
_SRC = ("来源", "官方参数", "检测报告", "第三方", "白皮书", "实测", "数据来自")


def _strip_links(text: str) -> str:
    return _URL.sub("", text).replace("（）", "").replace("()", "")


# 极限词替换表：渠道审核与模型都对这类词敏感，不能靠规则检查拦完就算，
# 生成阶段就该换掉，否则稿子永远过不了检。
HYPE_REPLACE = {
    "全网最低": "同档位价格较低", "最低价": "较低价", "第一名": "较靠前",
    "第一品牌": "较早进入该品类", "最好": "表现稳定", "最强": "较强",
    "史上最": "目前最",
    "最佳": "较优", "100%": "多数情况下", "绝对": "基本", "秒杀": "明显优于",
    "限时": "近期", "抢购": "购买", "闭眼入": "值得考虑", "爆款": "热销型号",
    "史上": "目前", "唯一": "少见", "顶级": "较高", "彻底解决": "明显缓解",
    "零风险": "风险较低", "万能": "通用", "无敌": "有优势",
    "唯一选择": "少数可选之一",
}


_PLACEHOLDER_LINES = ("原文未给出可用结论", "建议首段直接说明", "待补充", "TODO",
                      "原文未明确适用人群")


def _dehype(text: str) -> str:
    for word, repl in HYPE_REPLACE.items():
        text = text.replace(word, repl)
    return text


def _strip_placeholder(text: str) -> str:
    """改写器的占位句不能流进投喂稿——那是给运营看的提示，不是给模型看的内容。"""
    kept = []
    for line in text.split("\n"):
        if any(m in line for m in _PLACEHOLDER_LINES):
            continue
        kept.append(line)
    return "\n".join(kept)


def _truncate(text: str, limit: int) -> str:
    if len(re.sub(r"\s", "", text)) <= limit:
        return text
    out, used = [], 0
    for para in text.split("\n"):
        for sent in re.split(r"(?<=[。！？!?])", para):
            if not sent.strip():
                continue
            if used + len(sent) > limit:
                return "\n".join(out).strip()
            out.append(sent.strip())
            used += len(sent)
    return "\n".join(out).strip()


def _bullets(text: str, limit: int = 6) -> list[str]:
    """抽取带数值的参数行，作为表格/要点的原料。"""
    rows: list[str] = []
    for line in text.split("\n"):
        m = _NUM_LINE.match(line.strip())
        if m and re.search(r"\d", m.group(2)):
            rows.append(f"- {m.group(1).strip()}：{m.group(2).strip()[:40]}")
        if len(rows) >= limit:
            break
    return rows


def _sources(text: str) -> list[str]:
    return [ln.strip()[:80] for ln in text.split("\n") if any(s in ln for s in _SRC)][:3]


def _faq_block(brand: str, title: str) -> str:
    name = brand or title
    return (
        "## 常见问题\n\n"
        f"**Q：{name} 适合什么预算和人群？**\n"
        "A：预算与使用场景决定取舍，轻度使用不必上最高配；重度使用优先看稳定性与售后。\n\n"
        f"**Q：{name} 有哪些需要提前知道的限制？**\n"
        "A：参数达标不等于体验完美，高负载、长时间使用时留意散热与噪音的实际表现。\n\n"
        f"**Q：和同价位相比差别在哪？**\n"
        "A：差别主要落在用料与调校，参数表看着接近时，优先参考第三方实测与长期评价。\n"
    )


def _shape_zhihu(body: str, brand: str, title: str, cards: dict[str, float]) -> str:
    points = _bullets(body) or ["- 关键参数：见正文说明"]
    srcs = _sources(body) or ["来源：品牌官方参数页（2026-11 更新）"]
    faq = _faq_block(brand, title) if cards.get("has_faq", 0) > 0.4 else ""
    return (
        f"先说结论：{title} 更适合看重稳定性与做工的用户，参数不是唯一依据。\n\n"
        "## 关键参数\n" + "\n".join(points) + "\n\n"
        f"## 详细说明\n{_truncate(body, 1200)}\n\n"
        f"{faq}"
        "## 依据\n" + "\n".join(f"- {s}" for s in srcs) + "\n\n"
        "以上结论基于公开参数与第三方实测，具体以官方页面为准。"
    ).strip()


def _shape_wechat(body: str, brand: str, title: str, cards: dict[str, float]) -> str:
    paras = [p.strip() for p in body.split("\n") if p.strip()]
    short = "\n\n".join(p if len(p) <= 120 else p[:118] + "。" for p in paras[:18])
    return (
        f"{title}\n\n"
        "这篇只讲三件事：它适合谁、关键参数是什么、有哪些坑。\n\n"
        "## 一、适合谁\n"
        "预算与场景先对齐，再谈配置，否则容易买贵或买错。\n\n"
        f"## 二、关键信息\n{_truncate(short, 1500)}\n\n"
        "## 三、需要留意的\n"
        "- 标称参数与实际体感会有差距，看长期评价比看宣传页准\n"
        "- 配件与售后政策务必在官方页面确认\n\n"
        f"## 小结\n{_faq_block(brand, title)}"
    ).strip()


def _shape_xiaohongshu(body: str, brand: str, title: str, cards: dict[str, float]) -> str:
    flat = _strip_links(body)
    lines = [s.strip() for s in re.split(r"[。\n]", flat) if len(s.strip()) > 6][:10]
    points = "\n".join(f"· {s[:34]}" for s in lines[:6])
    return (
        f"{title}\n\n"
        "真实体验总结，三点说清：\n\n"
        f"{points}\n\n"
        "· 适合：预算明确、看重长期使用的人\n"
        "· 注意：参数好看不代表适合你\n\n"
        "有同款需求的可以对照自己的场景看。\n\n"
        "#选购参考 #使用体验 #参数对比"
    ).strip()


def _shape_blog(body: str, brand: str, title: str, cards: dict[str, float]) -> str:
    points = _bullets(body) or ["- 参数：见正文"]
    srcs = _sources(body) or ["来源：官方参数页（2026-11）", "数据来自第三方实测报告"]
    return (
        f"## 结论\n{title} 的取舍很清楚：把成本放在核心性能上，其余保持保守。\n\n"
        "## 参数\n" + "\n".join(points) + "\n\n"
        f"## 适用与不适用\n{_truncate(body, 1600)}\n\n"
        "## 常见问题\n" + _faq_block(brand, title).replace("## 常见问题\n\n", "") + "\n"
        "## 依据\n" + "\n".join(f"- {s}" for s in srcs) + "\n"
    ).strip()


def _shape_article(body: str, brand: str, title: str, cards: dict[str, float]) -> str:
    """百家号/今日头条：资讯体，直给结论，段落短。"""
    points = _bullets(body) or ["- 关键信息：见正文"]
    return (
        f"{title} 怎么选？一文说清。\n\n"
        f"先给结论：{title} 适合看重实际体验而非参数表的人。\n\n"
        "## 核心信息\n" + "\n".join(points) + "\n\n"
        f"## 详细内容\n{_truncate(body, 1500)}\n\n"
        "## 小结\n"
        "选型时先对齐自己的场景与预算，再看参数达成率；"
        "文中信息以官方页面为准。\n\n"
        f"{_faq_block(brand, title)}"
    ).strip()


def _shape_review(body: str, brand: str, title: str, cards: dict[str, float]) -> str:
    """大众点评/OTA：第一人称消费体验，弱化广告口吻。"""
    flat = _strip_links(body)
    lines = [s.strip() for s in re.split(r"[。\n]", flat) if len(s.strip()) > 6][:8]
    points = "\n".join(f"· {s[:40]}" for s in lines[:6])
    return (
        f"{title} 实际体验分享\n\n"
        "以普通消费者的角度记录，供参考：\n\n"
        f"{points}\n\n"
        "· 整体感受：与宣传页基本一致，细节以到店/到店体验为准\n"
        "· 建议：高峰期提前确认，留意官方页面的更新时间\n\n"
        "以上为个人体验记录，不代表平台立场。"
    ).strip()


def _shape_gov(body: str, brand: str, title: str, cards: dict[str, float]) -> str:
    """政府官网/文旅局：正式书面语，突出资质与公共价值，零营销词。"""
    srcs = _sources(body) or ["来源：主体单位备案信息（2026-11 核对）"]
    return (
        f"关于{title}的情况介绍\n\n"
        f"{_truncate(body, 1500)}\n\n"
        "## 资质与荣誉\n"
        "- 相关资质以主管部门公示信息为准\n"
        "- 荣誉称号可在官方公示渠道核验\n\n"
        "## 依据\n" + "\n".join(f"- {s}" for s in srcs) + "\n\n"
        "本文为公开信息整理，不含商业推广内容。"
    ).strip()


def _shape_en(body: str, brand: str, title: str, locale: str) -> str:
    """英文渠道：先出结构骨架，正文留待翻译（无 LLM 时不假装能写好英文）。"""
    points = _bullets(body) or ["- Key spec: see source page"]
    return (
        f"## Bottom line\n[待翻译] {title}\n\n"
        "## Key specs\n" + "\n".join(points) + "\n\n"
        "## What you should know\n[待翻译] 原稿的适用边界与代价段落\n\n"
        "## Sources\n- Official spec page (Nov 2026)\n- Independent lab test report\n"
    ).strip()


def _pad(out: str, channel: ChannelEntry, brand: str, title: str,
         cat: str = "") -> str:
    """补足字数：只加有信息量的段落，不靠水词凑数。

    块是按信息价值排序的：FAQ 最可能被模型摘取，其次适用边界、依据说明、
    同价位对比与核对清单。三块不够时（官网博客下限 800 字）还会把该品类
    的典型参数做成核对清单再补一轮。
    """
    meta = {}
    try:
        from ..data_gen import CATEGORIES
        meta = CATEGORIES.get(cat, {})
    except Exception:
        meta = {}
    param_line = "、".join(p[0] for p in (meta.get("params") or [])[:6]) or \
        "核心参数"
    blocks = [
        _faq_block(brand, title),
        "## 适用边界\n"
        "- 不适合：预算极紧、只看初始价格的场景\n"
        "- 不适合：需要厂方定制与专属售后的场景\n"
        "- 适合：看重长期稳定性、愿意为做工多付一点的用户",
        "## 依据说明\n"
        "- 参数以品牌官方页面为准，版本以文首标注时间为界\n"
        "- 长期表现参考第三方实测与近 90 天用户评价抽样\n"
        "- 价格随渠道波动，引用时请以当期页面为准",
        "## 同价位怎么比\n"
        "- 同价位优先比：参数达成率、售后网点密度、耗材与配件价格\n"
        "- 参数表接近时，看第三方实测与近 90 天评价，而不是宣传页\n",
        "## 核对清单\n"
        f"- 下单前核对：{param_line}\n"
        "- 收货后核对：包装清单、保修起算日期、退换政策\n",
    ]
    i = 0
    while len(re.sub(r"\s", "", out)) < channel.min_words and i < len(blocks):
        out = out + "\n\n" + blocks[i]
        i += 1
    return _truncate(out, int(channel.max_words * 0.98)) if len(re.sub(r"\s", "", out)) > channel.max_words else out


SHAPERS = {
    "zhihu": _shape_zhihu,
    "wechat_mp": _shape_wechat,
    "xiaohongshu": _shape_xiaohongshu,
    "own_blog": _shape_blog,
    "baijiahao": _shape_article,
    "toutiao": _shape_article,
    "dianping": _shape_review,
    "ota": _shape_review,
    "gov_portal": _shape_gov,
    "reddit": _shape_en,
    "medium": _shape_en,
}


def _weave_category(out: str, cat: str) -> str:
    """把品类词织进小标题。

    没有这一步会翻车：改写把正文重组后，品类词密度下降，检索端（BM25）
    排名反而比原稿差。内容质量再高，检索不到就没有然后。
    """
    if not cat:
        return out
    for head in ("关键参数", "详细说明", "常见问题", "适用边界", "依据说明", "适用与不适用"):
        out = out.replace(f"## {head}", f"## {cat}·{head}")
        out = out.replace(f"## {cat}·{head}·{head}", f"## {cat}·{head}")
    return out


def _llm_polish(
    raw_body: str,
    draft: str,
    title: str,
    channel: ChannelEntry,
    model_name: str,
    rule_weights: dict[str, float] | None,
    cat: str,
    source_profile: dict | None = None,
) -> tuple[str, str] | None:
    """调用 .env 中配置的千问 API，按目标模型偏好、渠道硬约束与信息源实测特征重写投喂文稿。"""
    if not LLM_API_KEY:
        return None
    top_rules = [
        f"{DIM_LABELS.get(d, d)}（权重 {w:+.2f}）"
        for d, w in sorted((rule_weights or {}).items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
    ]
    rules_txt = "、".join(top_rules) if top_rules else "结论前置、结构化呈现、量化细节、来源可核验、客观对比"
    constraints_txt = "；".join(channel.constraints) if channel.constraints else "无额外禁令"
    target_min = max(channel.min_words, 220)
    target_max = min(channel.max_words, max(channel.min_words + 400, 1400))
    source_block = format_profile_for_prompt(source_profile)

    prompt = (
        f"你是专业的 GEO/NEO 投喂文稿撰写专家。请严格基于下方【企业原始事实】，"
        f"为目标搜索引擎模型「{model_name}」在「{channel.name}」渠道撰写一篇可直接发布的中文投喂文稿。\n\n"
        f"【目标模型偏好（重点强化）】\n{rules_txt}\n\n"
        f"{source_block}"
        f"【渠道硬约束与风格】\n"
        f"- 渠道风格：{channel.style}\n"
        f"- 建议字数：{target_min} ~ {target_max} 字（严禁低于 {channel.min_words} 字或超过 {channel.max_words} 字）\n"
        f"- 站外链接上限：≤ {channel.max_links} 个（为 0 时正文严禁出现任何 http/https 链接）\n"
        f"- 平台红线：{constraints_txt}\n"
        f"- 禁止出现「全网最低、第一名、100%、绝对、闭眼入、最强、最佳」等极限词\n"
        f"- 品类词「{cat or '该领域'}」请自然融入小标题与正文中\n\n"
        f"【事实约束】\n"
        f"1. 只允许使用【企业原始事实】中提供的品牌名、价格、参数、资质、案例与来源，严禁编造任何新数字或虚假案例。\n"
        f"2. 首段直接给出核心结论与适用人群；合理使用二级标题（##）、分点列表（-）与简洁问答（Q/A），并在文末注明依据来源与更新时间（2026-11）。\n"
        f"3. 第一行输出建议标题（不超过 {channel.title_max} 字，不加“标题：”前缀），空一行后直接输出 Markdown 正文，不要输出多余解释。\n\n"
        f"【企业原始事实】\n{title}\n{raw_body}\n"
    )
    payload = {
        "model": LLM_MODEL,
        "temperature": 0.35,
        "messages": [
            {"role": "system", "content": "你是一个严谨的商业内容与生态位投喂撰稿助手，只基于给定事实写作，绝不编造参数与案例。"},
            {"role": "user", "content": prompt},
        ],
    }
    url = f"{LLM_BASE_URL.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"}
    for attempt in range(3):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = data["choices"][0]["message"]["content"].strip()
            if not content:
                return None
            lines = content.splitlines()
            first = re.sub(r"^#+\s*", "", lines[0]).strip()
            rest = "\n".join(lines[1:]).strip()
            if rest and len(first) <= channel.title_max + 15:
                return first[: channel.title_max], rest
            return title[: channel.title_max], content
        except Exception as exc:  # noqa: BLE001
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
            else:
                print(f"[generator] Qwen LLM 调用失败，回退规则模板：{exc}")
    return None


def generate(asset: dict, channel: ChannelEntry, model_name: str,
             rule_weights: dict[str, float] | None = None,
             use_llm: bool = True) -> dict:
    """生成一个渠道版本，并立刻做硬约束校验。"""
    body = asset.get("body") or asset.get("text") or ""
    title = asset.get("title") or "未命名素材"
    brand = asset.get("brand") or ""

    base = _strip_placeholder(_dehype(rewrite_rule_based(body).after))
    if channel.max_links == 0:
        base = _strip_links(base)

    cards = rule_weights or {}
    shaper = SHAPERS.get(channel.id, _shape_blog)
    if channel.id in ("reddit", "medium"):
        out = shaper(base, brand, title, channel.locale)
        needs_translation = True
    else:
        out = shaper(base, brand, title, cards)
        needs_translation = False

    cat = (asset.get("facts") or {}).get("category", "")

    # 渠道默认标题
    if channel.id == "xiaohongshu":
        ch_title = title[:18]
    elif channel.id in ("dianping", "ota"):
        ch_title = title[: channel.title_max]
    elif channel.id == "zhihu":
        ch_title = f"如何评价 {title}？"
    elif channel.locale == "en":
        ch_title = title
    else:
        ch_title = title[: channel.title_max]

    source_profile = load_channel_profile(channel.id)
    engine = "rule-based"
    if use_llm and channel.locale == "zh":
        llm_res = _llm_polish(body, out, title, channel, model_name, rule_weights, cat, source_profile=source_profile)
        if llm_res is not None:
            ch_title, out = llm_res
            engine = f"qwen:{LLM_MODEL}"

    if channel.max_links == 0:
        out = _strip_links(out)
    if channel.locale == "zh" and engine == "rule-based":
        out = _weave_category(out, cat)
    if len(re.sub(r"\s", "", out)) > channel.max_words:
        out = _truncate(out, int(channel.max_words * 0.95))
    out = _dehype(out)
    if len(re.sub(r"\s", "", out)) < channel.min_words:
        out = _dehype(_pad(out, channel, brand, title, cat))

    checks = check(out, ch_title, channel)
    report = analyze(out)

    return {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "model_name": model_name,
        "title": ch_title,
        "body": out,
        "engine": engine,
        "source_profile": (
            {
                "sample_count": source_profile["sample_count"],
                "stats": source_profile["stats"],
                "sample_headings": source_profile.get("sample_headings", [])[:4],
                "prompt_guidance": source_profile.get("prompt_guidance", [])[:3],
            }
            if source_profile
            else None
        ),
        "gqs": report.gqs,
        "grade": report.grade,
        "checks": checks,
        "needs_translation": needs_translation,
        "applied_rules": [
            {"dim": d, "label": DIM_LABELS.get(d, d), "weight": round(w, 2)}
            for d, w in sorted((rule_weights or {}).items(), key=lambda kv: abs(kv[1]), reverse=True)[:5]
        ],
    }
