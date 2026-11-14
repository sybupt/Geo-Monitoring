"""引擎注册表。

七家引擎里，能真正跑起来的才允许出现在跑批选项里。没接线的（通义/文心/智谱/元宝）
显式标记 ``implemented=False`` 并给出接线要点——宁可显示「未覆盖」，也不要拿伪数据充数。
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from app.probes.base import (
    CITATION_ANSWER_LEVEL,
    CITATION_RETRIEVAL_POOL,
    TIER_L1,
    TIER_L2,
    TIER_L3,
    ProbeAdapter,
    ProbeError,
)
from app.probes.deepseek import DeepSeekAdapter
from app.probes.doubao import DoubaoAdapter
from app.probes.kimi import KimiAdapter
from app.probes.mock import MockAdapter
from app.probes.qwen import QwenAdapter
from app.probes.wenxin import WenxinAdapter
from app.probes.yuanbao import YuanbaoAdapter
from app.probes.zhipu import ZhipuAdapter


class AdapterNotWired(ProbeError):
    """引擎已登记但尚未接线。"""


_ADAPTERS: dict[str, Callable[..., ProbeAdapter]] = {
    "doubao": DoubaoAdapter,
    "kimi": KimiAdapter,
    "deepseek": DeepSeekAdapter,
    "zhipu": ZhipuAdapter,
    "qwen": QwenAdapter,
    "wenxin": WenxinAdapter,
    "yuanbao": YuanbaoAdapter,
    "mock": MockAdapter,
}

# 七家引擎的画像。顺序即前端展示顺序。
ENGINE_SPECS: list[dict[str, Any]] = [
    {
        "engine": "doubao",
        "label": "豆包",
        "vendor": "字节 · 火山方舟",
        "tier": TIER_L1,
        "citation_type": CITATION_ANSWER_LEVEL,
        "search_enabled": True,
        "implemented": True,
        "wire_hint": "POST /api/v3/responses + tools=[{type:web_search}]，引用读 output[].content[].annotations",
    },
    {
        "engine": "kimi",
        "label": "Kimi",
        "vendor": "月之暗面",
        "tier": TIER_L1,
        "citation_type": CITATION_RETRIEVAL_POOL,
        "search_enabled": True,
        "implemented": True,
        "wire_hint": "内置函数 $web_search，两轮 tool call",
    },
    {
        "engine": "deepseek",
        "label": "DeepSeek",
        "vendor": "深度求索",
        "tier": TIER_L2,
        "citation_type": "none",
        "search_enabled": False,
        "implemented": True,
        "wire_hint": "官方 API 不联网，为模型记忆口径；需要检索口径要外接第三方搜索",
    },
    {
        "engine": "qwen",
        "label": "通义千问",
        "vendor": "阿里 · 百炼",
        "tier": TIER_L1,
        "citation_type": CITATION_RETRIEVAL_POOL,
        "search_enabled": True,
        "implemented": True,
        "wire_hint": "必须走 DashScope 原生协议（parameters.enable_search=true），读 output.search_info.search_results；OpenAI 兼容协议拿不到检索面",
    },
    {
        "engine": "wenxin",
        "label": "文心一言",
        "vendor": "百度 · 千帆",
        "tier": TIER_L1,
        "citation_type": CITATION_ANSWER_LEVEL,
        "search_enabled": True,
        "implemented": True,
        "wire_hint": "v2 用 API Key 直连（web_search.enable_trace=true，读 search_info.search_results）；AK/SK 走 OAuth 兜底；仅 ERNIE 4.5 支持搜索增强",
    },
    {
        "engine": "zhipu",
        "label": "智谱清言",
        "vendor": "智谱 AI",
        "tier": TIER_L1,
        "citation_type": CITATION_ANSWER_LEVEL,
        "search_enabled": True,
        "implemented": True,
        "wire_hint": "GLM chat/completions + tools=[{type:web_search}]，正文 ref_N 角标；须用支持工具调用的模型",
    },
    {
        "engine": "yuanbao",
        "label": "腾讯元宝",
        "vendor": "腾讯",
        "tier": TIER_L3,
        "citation_type": CITATION_ANSWER_LEVEL,
        "search_enabled": True,
        "implemented": True,
        "wire_hint": "无公开 API：Playwright 驱动真实浏览器，需先 python scripts/yuanbao_login.py 扫码登录；DOM 会随发版失效，数据标 L3",
    },
]


def get_adapter(engine_id: str, **kwargs: Any) -> ProbeAdapter:
    factory = _ADAPTERS.get(engine_id)
    if factory is None:
        spec = next((s for s in ENGINE_SPECS if s["engine"] == engine_id), None)
        if spec is None:
            raise ProbeError(f"未知引擎：{engine_id}")
        raise AdapterNotWired(
            f"{spec['label']} 尚未接线。接线要点：{spec['wire_hint']}", retryable=False
        )
    return factory(**_accepts_only(factory, kwargs))


def _accepts_only(factory: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """按工厂实际签名过滤参数。

    调度层统一传 ``brand_name`` / ``competitors``（mock 需要，真实引擎不需要），
    以前直接透传会让真实引擎在 __init__ 抛 TypeError——建批次直接 500。
    在这里过滤，比要求每个适配器写 ``**kwargs`` 更安全：签名错了会立刻暴露。
    """
    try:
        params = inspect.signature(factory).parameters
    except (TypeError, ValueError):
        return kwargs
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return kwargs
    return {k: v for k, v in kwargs.items() if k in params}


def implemented_engines() -> list[str]:
    return [s["engine"] for s in ENGINE_SPECS if s["implemented"]]


def engine_catalog(**adapter_kwargs: Any) -> list[dict[str, Any]]:
    """给前端的能力矩阵：谁已接线、谁配了 key、引用语义是什么。"""
    out = []
    for spec in ENGINE_SPECS:
        item = dict(spec)
        if spec["implemented"]:
            try:
                item.update(get_adapter(spec["engine"], **adapter_kwargs).capabilities())
                item.update(spec)  # 注册表里的画像优先
            except Exception as exc:  # noqa: BLE001
                item["configured"] = False
                item["error"] = str(exc)
        else:
            item["configured"] = False
        out.append(item)
    return out
