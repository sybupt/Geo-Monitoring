from app.services.monitor_service import PROVIDERS, ask_text

CHANNEL_LABELS = {
    "wechat_personal": "微信个人号",
    "rednote": "小红书",
    "zhihu": "知乎",
    "baidu": "百度",
    "google": "Google",
    "official_site": "官网",
    "government": "政府网站",
    "tourism_bureau": "文旅局",
    "wechat": "微信公众号",
}

CHANNEL_HINTS = {
    "wechat_personal": "微信个人号/朋友圈文案风格：亲切、简短、口语化、适合熟人传播",
    "rednote": "小红书笔记风格：口语化、带 emoji、分点、突出体验和种草点",
    "zhihu": "知乎长文风格：专业、有逻辑、带分析，适合被 AI 引用为权威来源",
    "baidu": "百度百科/问答风格：客观、结构化、信息密度高",
    "google": "面向搜索引擎的网页风格：标题清晰、段落分明、事实具体",
    "official_site": "企业官网风格：正式、可信、信息完整",
    "government": "政务信息风格：客观、规范、突出官方属性",
    "tourism_bureau": "文旅推介风格：突出地域特色、体验、口碑",
    "wechat": "微信公众号推文风格：标题吸引、排版友好、有号召力",
}


def build_assets(business, channels, brief, provider_id):
    if provider_id not in PROVIDERS:
        raise ValueError(f"未知模型：{provider_id}")
    if not PROVIDERS[provider_id]["key"]:
        raise ValueError(f"请先配置 {PROVIDERS[provider_id]['label']} 的 API 密钥")

    business_name = business.name
    region = business.region or ""
    category = business.category or ""
    keywords = brief.get("business_keywords", "")
    activity = brief.get("activity_name", "")
    points = brief.get("promotional_points", "")
    event_time = brief.get("event_time", "")
    event_location = brief.get("event_location", "")
    event_desc = brief.get("event_description", "")

    facts = [f"企业名称：{business_name}"]
    if region:
        facts.append(f"所在地区：{region}")
    if category:
        facts.append(f"所属品类：{category}")
    if keywords:
        facts.append(f"关键词：{keywords}")
    if points:
        facts.append(f"核心卖点：{points}")
    event_parts = []
    if event_time:
        event_parts.append(f"时间：{event_time}")
    if event_location:
        event_parts.append(f"地点：{event_location}")
    if event_desc:
        event_parts.append(f"描述：{event_desc}")
    if event_parts:
        facts.append("事件：" + "；".join(event_parts))
    core_facts = "\n".join(facts)

    assets = []
    for channel in channels:
        hint = CHANNEL_HINTS.get(channel, "通用风格")
        prompt = f"""你是 GEO（生成式引擎优化）内容专家。请为「{business_name}」写一篇适合发布在{CHANNEL_LABELS.get(channel, channel)}渠道的文章。

核心事实（以下信息在所有渠道文案中必须严格一致，不得篡改、不得虚构、不得省略关键数据）：
{core_facts}

写作要求（全部必须满足，这是"被 AI 引用"的关键）：
1. 【结论句】正文第一句必须是一句话总结「{business_name}」的核心定位/优势，可直接被 AI 摘录
2. 【结构化】正文用小标题、列表分层呈现，信息清晰
3. 【数据】尽量使用核心事实中已有的具体数据，禁止空泛描述
4. 【实体】自然地多次出现「{business_name}」、地域、品类等关键词
5. 【FAQ】正文末尾附 3~5 个「Q：… A：…」问答对，贴合用户会问的问题
6. 风格：{hint}
7. 不虚构事实、不夸大宣传
8. 【事实一致】只使用上面"核心事实"里的信息，不要凭空新增数据
9. 【表述差异】同一核心事实在本渠道的表达要与其它渠道差异化（句式、结构、开头不同），避免逐字重复，确保是独立原创内容
10. 【指纹】在 fingerprints 字段输出 3~8 个"最可能被 AI 直接引用的关键短语"：包括结论句、关键数据（含数字）、企业名/地域/品类等实体

请严格按照以下 JSON 格式输出（不要输出其他内容）：
{{"title": "文章标题", "content": "文章正文", "fingerprints": ["可被AI引用的关键短语1", "关键短语2"]}}"""
        try:
            raw = ask_text(provider_id, prompt)
        except Exception as exc:
            raise RuntimeError(f"{CHANNEL_LABELS.get(channel, channel)}素材生成失败：{exc}") from exc
        data = _parse_json(raw)
        content = data.get("content", raw)
        cite_score = score_citability(content, business_name)
        fingerprints = data.get("fingerprints") or []
        if not isinstance(fingerprints, list):
            fingerprints = [str(fingerprints)] if fingerprints else []
        assets.append({
            "channel": channel,
            "title": data.get("title", f"{business_name} - {CHANNEL_LABELS.get(channel, channel)}"),
            "content": content,
            "citability": cite_score,
            "fingerprints": [fp.strip() for fp in fingerprints if isinstance(fp, str) and fp.strip()],
        })
    return assets


def score_citability(content, business_name):
    if not content:
        return {"score": 0, "reasons": ["无正文内容"]}

    text = (content or "").strip()
    reasons = []
    score = 0

    if business_name and business_name in text:
        score += 20
    else:
        reasons.append("缺少企业主称词")

    if len(text) >= 250:
        score += 20
    else:
        reasons.append("正文过短")

    if any(keyword in text for keyword in ["优势", "特点", "卖点", "服务", "活动", "时间", "地点", "详情"]):
        score += 20
    else:
        reasons.append("缺少清晰卖点")

    if "Q：" in text and "A：" in text:
        score += 20
    else:
        reasons.append("缺少 FAQ 问答")

    if any(marker in text for marker in ["\n##", "\n###", "1.", "2.", "3.", "- "]):
        score += 20
    else:
        reasons.append("缺少结构化段落")

    score = max(0, min(100, score))
    if score >= 80:
        reasons.append("高可引用性")
    elif score >= 60:
        reasons.append("中等可引用性")
    else:
        reasons.append("低可引用性")

    return {"score": score, "reasons": reasons}


def _parse_json(raw):
    import json
    import re
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return {}
