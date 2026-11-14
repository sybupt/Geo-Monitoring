"""智谱清言（GLM）适配器。

关键事实（2026-11 核对 docs.bigmodel.cn）：
- 走 ``POST /api/paas/v4/chat/completions``，把联网搜索当工具传：
  ``tools=[{"type": "web_search", "web_search": {"enable": true, "search_result": true, ...}}]``
- 模型必须支持工具调用，否则直接 400；不同 GLM 版本能力不同，用 ``ZHIPU_MODEL`` 覆盖。
- **引用语义是答案级**：正文里带 ``[来源：ref_1]`` 角标，ref_N 与搜索结果条目对应。
  但官方 chat 响应目前只在部分版本回传搜索结果数组，所以本适配器对结果数组做**多路径探测**：
  顶层 ``web_search`` / ``search_result``、``choices[].message.web_search``、
  ``choices[].message.tool_calls[].search_result``。
- 探测不到 URL 时**不伪造引用**：citations 留空、``searched`` 记 true、把正文角标数写进 raw。
  报表层会显示「该引擎未返回可解析的信源」，而不是凭空多出一堆域名。
- ``search_engine`` 影响召回：search_pro 性价比最好；search_pro_sogou 覆盖公众号/知乎，
  本地生活这类中文内容用 sogou 往往更全，按行业可切换。
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.config import ZHIPU_BASE, ZHIPU_KEY, ZHIPU_MODEL
from app.probes.base import (
    CITATION_ANSWER_LEVEL,
    TIER_L1,
    Citation,
    ProbeError,
    ProbeResult,
    call_with_retry,
    http_error_to_probe_error,
)

DEFAULT_MODEL = ZHIPU_MODEL or "glm-4.5-air"
DEFAULT_SEARCH_ENGINE = "search_pro"
DEFAULT_COUNT = 10

# 正文角标：[来源：ref_1] / [ref_1] / 【ref_1】
_REF_PATTERN = re.compile(r"ref[_\-\s]?(\d+)", re.IGNORECASE)


class ZhipuAdapter:
    engine_id = "zhipu"
    label = "智谱清言"
    tier = TIER_L1
    citation_type = CITATION_ANSWER_LEVEL
    search_enabled = True
    implemented = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        search_engine: str | None = None,
    ):
        self.api_key = api_key or ZHIPU_KEY
        self.model = model or DEFAULT_MODEL
        self.search_engine = search_engine or DEFAULT_SEARCH_ENGINE

    def capabilities(self) -> dict[str, Any]:
        return {
            "engine": self.engine_id,
            "label": self.label,
            "tier": self.tier,
            "citation_type": self.citation_type,
            "search_enabled": self.search_enabled,
            "implemented": self.implemented,
            "configured": bool(self.api_key),
            "model": self.model,
            "note": "答案级引用（正文 ref_N 角标）；须用支持工具调用的 GLM 模型",
        }

    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult:
        if not self.api_key:
            raise ProbeError("智谱未配置 API Key（backend/.env 的 ZHIPU_KEY）")
        use_model = model or self.model
        payload = {
            "model": use_model,
            "messages": [{"role": "user", "content": question}],
            "tools": [
                {
                    "type": "web_search",
                    "web_search": {
                        "enable": True,
                        "search_result": True,
                        "search_engine": self.search_engine,
                        "count": DEFAULT_COUNT,
                        "content_size": "high",
                    },
                }
            ],
            "temperature": 0,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        t0 = time.monotonic()

        def _call() -> dict[str, Any]:
            with httpx.Client(timeout=180.0) as client:
                resp = client.post(ZHIPU_BASE, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise http_error_to_probe_error(resp.status_code, resp.text)
            return resp.json()

        data = call_with_retry(_call)
        latency = int((time.monotonic() - t0) * 1000)

        answer, citations, searched, ref_count = _parse(data, self.citation_type)
        if not answer:
            raise ProbeError(
                f"智谱返回空正文（searched={searched}），检查模型是否支持 web_search：{use_model}"
            )

        return ProbeResult(
            engine=self.engine_id,
            tier=self.tier,
            answer=answer,
            model=use_model,
            model_version=str(data.get("model") or use_model),
            citation_type=self.citation_type,
            citations=citations,
            search_queries=[question] if searched else [],
            latency_ms=latency,
            usage=data.get("usage") or {},
            raw={
                "id": data.get("id"),
                "searched": searched,
                "ref_marks_in_text": ref_count,
                "search_engine": self.search_engine,
            },
        )


def _parse(data: dict[str, Any], citation_type: str) -> tuple[str, list[Citation], bool, int]:
    """返回 (正文, 引用, 是否检索, 正文角标数)。

    引用是尽力而为：抓不到 URL 列表时宁可返回空引用，也不能让 source_dist 出现假数据。
    """
    texts: list[str] = []
    for ch in data.get("choices") or []:
        msg = ch.get("message") or {}
        if msg.get("content"):
            texts.append(str(msg["content"]))
    answer = "\n".join(texts).strip()

    raw_items = _collect_result_items(data)
    ref_marks = {m.group(1) for m in _REF_PATTERN.finditer(answer)} if answer else set()

    citations: list[Citation] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw_items):
        url = str(item.get("link") or item.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        refer = str(item.get("refer") or item.get("index") or "")
        ref_no = "".join(ch for ch in refer if ch.isdigit())
        citations.append(
            Citation(
                url=url,
                title=str(item.get("title") or ""),
                site_name=str(item.get("media") or item.get("site_name") or ""),
                publish_time=str(item.get("publish_date") or item.get("publish_time") or ""),
                citation_type=citation_type,
                position=_ref_position(answer, ref_no) if ref_no else idx,
            )
        )

    searched = bool(raw_items) or bool(ref_marks)
    return answer, citations, searched, len(ref_marks)


def _collect_result_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    """多路径捞搜索结果。响应结构随版本变过，任何一条路径命中就够。"""
    items: list[dict[str, Any]] = []

    def _take(value: Any) -> None:
        if isinstance(value, list):
            for v in value:
                if isinstance(v, dict) and (v.get("link") or v.get("url")):
                    items.append(v)

    _take(data.get("web_search"))
    _take(data.get("search_result"))

    for ch in data.get("choices") or []:
        msg = ch.get("message") or {}
        _take(msg.get("web_search"))
        _take(msg.get("search_result"))
        for tool_call in msg.get("tool_calls") or []:
            _take((tool_call.get("web_search") or {}).get("search_results"))
            _take(tool_call.get("search_result"))
    return items


def _ref_position(answer: str, ref_no: str) -> int:
    if not answer or not ref_no:
        return 0
    m = re.search(rf"ref[_\-\s]?{ref_no}(?!\d)", answer, re.IGNORECASE)
    return m.start() if m else 0
