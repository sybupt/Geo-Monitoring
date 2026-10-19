import json
from collections.abc import Sequence

from openai import OpenAI, OpenAIError

from app.models import Business
from app.services.monitor_service import PROVIDERS


CHANNEL_LABELS = {
    "rednote": "小红书",
    "zhihu": "知乎",
    "baidu": "百度",
    "google": "Google",
    "official_site": "企业官网",
    "government": "政府平台",
    "tourism_bureau": "文旅局官网",
    "wechat": "微信公众号",
}

CHANNEL_GUIDANCE = {
    "rednote": "小红书笔记：生活化、易读，有标题、正文、话题标签建议；避免硬广和虚构体验。",
    "zhihu": "知乎回答或文章：围绕用户问题提供有信息量、可信的内容，避免伪装成用户评价。",
    "baidu": "百度内容：适合百度搜索生态的信息稿，结构清晰、关键词自然，不堆砌关键词。",
    "google": "Google 搜索生态内容：提供清晰的网页标题、摘要和正文建议；不声称可以保证排名或收录。",
    "official_site": "企业官网：正式、准确的网页标题和正文，适合官网内容编辑器。",
    "government": "政府平台投稿稿件：客观、规范、可核实，适合提交审核，不得假称政府背书或已获发布。",
    "tourism_bureau": "文旅局官网投稿稿件：突出与文旅相关的真实信息，语气正式，适合投稿审核，不得假称官方发布。",
    "wechat": "微信公众号文章：适合公众号图文排版，提供标题、分段正文和摘要，不编造经历或数据。",
}


def build_assets(
    business: Business,
    channels: Sequence[str],
    brief: dict[str, str],
    provider_id: str,
) -> list[dict[str, str]]:
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise ValueError("不支持的文案生成模型")
    if not provider["key"]:
        raise ValueError(f"{provider['label']} 未配置 API 密钥")
    if not channels or any(channel not in CHANNEL_GUIDANCE for channel in channels):
        raise ValueError("请选择支持的平台")

    enterprise_facts = {
        "name": business.name or "",
        "alias": business.alias or "",
        "region": business.region or "",
        "category": business.category or "",
        "description": business.description or "",
        "selling_points": business.selling_points or "",
        "evidence": business.evidence or "",
        "website": business.website or "",
    }
    requested_channels = [
        {
            "channel": channel,
            "platform": CHANNEL_LABELS[channel],
            "format": CHANNEL_GUIDANCE[channel],
        }
        for channel in channels
    ]
    prompt_data = {
        "enterprise": enterprise_facts,
        "campaign_input": brief,
        "requested_platforms": requested_channels,
    }
    try:
        response = OpenAI(
            api_key=provider["key"],
            base_url=provider["base_url"],
        ).chat.completions.create(
            model=provider["model"],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是中文内容编辑，负责为不同平台起草待人工审核的企业图文素材。"
                        "只使用输入中明确提供的事实；不得编造地址、时间、价格、荣誉、"
                        "效果、用户体验、政府背书或已经发生的发布。输入信息不足时，"
                        "使用中性表达或标出需补充的信息，不要猜测。按每个平台的格式要求"
                        "分别撰写，正文可使用 Markdown。只输出 JSON 对象，格式为 "
                        "{\"assets\":[{\"channel\":\"输入中的平台代码\","
                        "\"title\":\"标题\",\"content\":\"正文\"}]}，不要输出其他文字。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt_data, ensure_ascii=False),
                },
            ],
        )
    except OpenAIError as exc:
        raise RuntimeError(
            f"{provider['label']} 文案生成请求失败，请检查密钥、网络和模型配置"
        ) from exc

    content = response.choices[0].message.content
    if not content:
        raise RuntimeError(f"{provider['label']} 未返回投喂文案")
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(f"{provider['label']} 返回内容不是有效 JSON 对象")
    try:
        payload = json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider['label']} 返回的文案 JSON 无法解析") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("assets"), list):
        raise RuntimeError("文案生成结果必须包含 assets 数组")

    generated = {}
    requested_ids = set(channels)
    for item in payload["assets"]:
        if not isinstance(item, dict):
            continue
        channel = item.get("channel")
        title = item.get("title")
        body = item.get("content")
        if (
            isinstance(channel, str)
            and channel in requested_ids
            and isinstance(title, str)
            and title.strip()
            and isinstance(body, str)
            and body.strip()
        ):
            if channel in generated:
                raise RuntimeError(
                    f"{provider['label']} 为{CHANNEL_LABELS[channel]}返回了重复文案"
                )
            generated[channel] = {
                "channel": channel,
                "title": title.strip(),
                "content": body.strip(),
            }
    missing = [CHANNEL_LABELS[channel] for channel in channels if channel not in generated]
    if missing:
        raise RuntimeError(
            f"{provider['label']} 未生成完整的平台文案：{'、'.join(missing)}"
        )
    return [generated[channel] for channel in channels]
