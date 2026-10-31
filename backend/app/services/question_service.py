import json
import re
from app.services.monitor_service import PROVIDERS, ask_text


def generate_questions(business_data, provider_id, count):
    """用 AI 生成企业相关的监测问题，返回 [{'text','category'}]"""
    if provider_id not in PROVIDERS:
        raise ValueError(f"未知模型：{provider_id}")
    if not PROVIDERS[provider_id]["key"]:
        raise ValueError(f"请先配置 {PROVIDERS[provider_id]['label']} 的 API 密钥")

    name = business_data.get("name", "")
    region = business_data.get("region", "")
    category = business_data.get("category", "")

    prompt = f"""你是本地生活搜索专家。请站在真实用户的角度，列出用户可能向 AI（如豆包、DeepSeek、Kimi）提问的、关于「{name}」及其所在地区的问题。

企业信息：
- 名称：{name}
- 地域：{region or '未提供'}
- 品类：{category or '未提供'}

要求：
1. 共 {count} 个问题
2. 覆盖：好吃/推荐、干净卫生、适合带娃、本地人推荐、性价比、停车/交通、营业时间、特色菜等角度
3. 每个问题要像真实用户会问的自然语言
4. 适当融入地域名（如"{region}哪里..."）

请严格按照以下 JSON 数组格式输出（不要输出其他内容）：
[{{"text": "问题内容", "category": "问题分类"}}]"""
    try:
        raw = ask_text(provider_id, prompt)
    except Exception as exc:
        raise RuntimeError(f"问题生成失败：{exc}") from exc

    items = _parse_array(raw)
    return [{"text": i.get("text", ""), "category": i.get("category", "")} for i in items][:count]


def _parse_array(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return []
