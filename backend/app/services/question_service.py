import json

from openai import OpenAI, OpenAIError

from app.services.monitor_service import PROVIDERS


def generate_questions(business: dict, provider_id: str, count: int) -> list[dict]:
    provider = PROVIDERS.get(provider_id)
    if provider is None:
        raise ValueError("不支持的问题生成模型")
    if not provider["key"]:
        raise ValueError(f"{provider['label']} 未配置 API 密钥")

    client = OpenAI(api_key=provider["key"], base_url=provider["base_url"])
    try:
        response = client.chat.completions.create(
            model=provider["model"],
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是搜索意图研究员。仅根据提供的企业资料生成真实用户可能输入的"
                        "搜索问题，不要在问题中预设或夸大企业优势。输出 JSON 数组，"
                        "每项格式为 {\"question\": \"...\", \"category\": \"...\"}，不要输出其他文字。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "enterprise": business,
                            "required_count": count,
                            "requirements": [
                                "覆盖了解、比较、推荐、具体服务等不同搜索意图",
                                "自然地包含地区或行业信息，避免重复和品牌强行植入",
                                "每个问题可独立用于模型回答监测",
                            ],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
    except OpenAIError as exc:
        raise RuntimeError(
            f"{provider['label']} 问题生成请求失败，请检查密钥、网络和模型配置"
        ) from exc
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError(f"{provider['label']} 未返回问题候选")
    start, end = content.find("["), content.rfind("]")
    if start < 0 or end < start:
        raise RuntimeError(f"{provider['label']} 返回内容不是有效 JSON 数组")
    try:
        values = json.loads(content[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider['label']} 返回的问题 JSON 无法解析") from exc
    if not isinstance(values, list):
        raise RuntimeError("问题生成结果必须是 JSON 数组")

    candidates = []
    seen = set()
    for value in values:
        if isinstance(value, str):
            text, category = value.strip(), ""
        elif isinstance(value, dict):
            text = str(value.get("question", "")).strip()
            category = str(value.get("category", "")).strip()
        else:
            continue
        key = text.casefold()
        if text and key not in seen:
            candidates.append({"text": text, "category": category or None})
            seen.add(key)
        if len(candidates) >= count:
            break
    if not candidates:
        raise RuntimeError("模型没有生成可用的问题候选")
    return candidates
