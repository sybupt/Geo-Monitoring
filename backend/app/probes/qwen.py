"""通义千问（DashScope 原生协议）适配器。

关键事实（2026-11 核对阿里云百炼文档）：
- **必须走 DashScope 原生协议**：``POST /api/v1/services/aigc/text-generation/generation``。
  OpenAI 兼容端点即使传了 ``enable_search``，``search_info`` 也不保证透传——
  用兼容协议采集引用，结果会稳定为空，然后被误判成「AI 没引用任何来源」。
- 请求体结构是 ``input.messages`` + ``parameters``，与 OpenAI 的 ``messages`` 不同。
- 取引用必须三件套：``enable_search=true`` + ``search_options.enable_source=true``
  + ``search_options.enable_citation=true``；再配 ``citation_format="[ref_<number>]"``
  让正文出现角标，才能做位置关联。
- ``forced_search=true`` 很关键：不强制的话模型可能判断「不需要搜」，
  测出来的 0 提及是**没检索**，不是**没被推荐**，两者在报表里必须能分开。
- 引用语义是**检索候选面**（``output.search_info.search_results``）：
  能知道「检索到了哪些页面」，不能严格证明「答案采纳了哪条」。
  所以 citation_type 固定为 retrieval_pool，指标层不许和豆包的答案级引用加总。
"""

from __future__ import annotations

import re
import time
from typing import Any

import httpx

from app.config import QWEN_BASE, QWEN_KEY, QWEN_MODEL
from app.probes.base import (
    CITATION_RETRIEVAL_POOL,
    TIER_L1,
    Citation,
    ProbeError,
    ProbeResult,
    call_with_retry,
    http_error_to_probe_error,
)

DEFAULT_MODEL = QWEN_MODEL or "qwen-plus"
CITATION_FORMAT = "[ref_<number>]"

_REF_PATTERN = re.compile(r"ref_(\d+)", re.IGNORECASE)


class QwenAdapter:
    engine_id = "qwen"
    label = "通义千问"
    tier = TIER_L1
    citation_type = CITATION_RETRIEVAL_POOL
    search_enabled = True
    implemented = True

    def __init__(self, api_key: str | None = None, model: str | None = None, base_url: str | None = None):
        self.api_key = api_key or QWEN_KEY
        self.model = model or DEFAULT_MODEL
        self.base_url = base_url or QWEN_BASE

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
            "note": "检索候选面引用（search_info）；必须走 DashScope 原生协议",
        }

    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult:
        if not self.api_key:
            raise ProbeError("通义未配置 API Key（backend/.env 的 QWEN_KEY 或 DASHSCOPE_KEY）")
        use_model = model or self.model
        payload = {
            "model": use_model,
            "input": {"messages": [{"role": "user", "content": question}]},
            "parameters": {
                "enable_search": True,
                # result_format=message 才会返回 output.choices；否则只有 output.text
                "result_format": "message",
                "search_options": {
                    "search_strategy": "turbo",
                    "forced_search": True,
                    "enable_source": True,
                    "enable_citation": True,
                    "citation_format": CITATION_FORMAT,
                },
            },
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        t0 = time.monotonic()

        def _call() -> dict[str, Any]:
            with httpx.Client(timeout=180.0) as client:
                resp = client.post(self.base_url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise _qwen_error(resp)
            return resp.json()

        data = call_with_retry(_call)
        latency = int((time.monotonic() - t0) * 1000)

        answer, citations, searched = _parse(data, self.citation_type)
        if not answer:
            raise ProbeError(
                f"通义返回空正文（检索到 {len(citations)} 条来源），检查模型是否支持 enable_search：{use_model}"
            )

        output = data.get("output") or {}
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
                "request_id": data.get("request_id"),
                "searched": searched,
                "search_count": ((data.get("usage") or {}).get("plugins") or {}).get("search", {}).get("count", 0),
                "has_extra_tool_info": bool((output.get("search_info") or {}).get("extra_tool_info")),
            },
        )


def _qwen_error(resp: httpx.Response) -> ProbeError:
    body = resp.text or ""
    if resp.status_code == 400 and ("enable_search" in body or "web search" in body.lower()):
        return ProbeError(
            f"该模型不支持联网搜索或不支持当前 search_options，换 qwen-plus / qwen-max：{body[:300]}",
            status_code=400,
        )
    return http_error_to_probe_error(resp.status_code, body)


def _parse(data: dict[str, Any], citation_type: str) -> tuple[str, list[Citation], bool]:
    output = data.get("output") or {}
    answer = _extract_text(output)

    results = ((output.get("search_info") or {}).get("search_results")) or []
    citations: list[Citation] = []
    seen: set[str] = set()
    for idx, item in enumerate(results):
        url = str(item.get("url") or item.get("link") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        index = item.get("index")
        citations.append(
            Citation(
                url=url,
                title=str(item.get("title") or ""),
                site_name=str(item.get("site_name") or ""),
                publish_time=str(item.get("publish_time") or ""),
                citation_type=citation_type,
                position=_ref_position(answer, index) if index is not None else idx,
            )
        )
    return answer, citations, bool(results)


def _extract_text(output: dict[str, Any]) -> str:
    """result_format=message 走 choices；某些版本仍返回 output.text，两者都认。"""
    if output.get("text"):
        return str(output["text"]).strip()
    parts: list[str] = []
    for ch in output.get("choices") or []:
        msg = ch.get("message") or {}
        if msg.get("content"):
            parts.append(str(msg["content"]))
    return "\n".join(parts).strip()


def _ref_position(answer: str, index: Any) -> int:
    if not answer or index is None:
        return 0
    m = re.search(rf"ref_{index}(?!\d)", answer)
    return m.start() if m else 0
