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

    assets = []
    for channel in channels:
        hint = CHANNEL_HINTS.get(channel, "通用风格")
        prompt = f"""你是 GEO（生成式引擎优化）内容专家。请为「{business_name}」写一篇适合发布在{CHANNEL_LABELS.get(channel, channel)}渠道的文章。

企业信息：
- 名称：{business_name}
- 地域：{region}
- 品类：{category}
- 关键词：{keywords or '无'}
- 活动名称：{activity or '无'}
- 宣传点：{points or '无'}
- 事件时间：{event_time or '无'}
- 事件地点：{event_location or '无'}
- 事件描述：{event_desc or '无'}

写作要求：
1. 风格：{hint}
2. 目标：让内容客观、可信、信息具体，方便 AI 生成引擎（豆包/DeepSeek/Kimi）在回答相关问题时引用
3. 自然地融入企业名称、地域、品类等关键信息
4. 不要虚构事实、不要夸大宣传

请严格按照以下 JSON 格式输出（不要输出其他内容）：
{{"title": "文章标题", "content": "文章正文"}}"""
        try:
            raw = ask_text(provider_id, prompt)
        except Exception as exc:
            raise RuntimeError(f"{CHANNEL_LABELS.get(channel, channel)}素材生成失败：{exc}") from exc
        data = _parse_json(raw)
        assets.append({
            "channel": channel,
            "title": data.get("title", f"{business_name} - {CHANNEL_LABELS.get(channel, channel)}"),
            "content": data.get("content", raw),
        })
    return assets


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
