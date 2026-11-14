"""豆包（火山方舟）适配器。

关键事实（2026-11 核对官方文档）：
- 必须走 ``POST /api/v3/responses`` 且 ``tools=[{"type": "web_search"}]``；
  裸调 ``/chat/completions`` 在多数模型上不触发联网检索，测出来的「0 命中」是没测到，不是真的没有。
- 引用在 ``output[].content[].annotations``，类型 ``url_citation``，属于**答案级引用**（最强）。
- 深思考模型推理会吃掉输出预算，``max_output_tokens`` 给小了会出现「有推理没正文」的空返回。
- 有没有触发搜索，看 ``output[]`` 里有没有 ``web_search_call``；annotations 为空时，
  「模型判断不需要检索」和「检索了但一条没采纳」必须分开记录，否则统计会同时丢掉这两类。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import DOUBAO_KEY, DOUBAO_MODEL
from app.probes.base import (
    CITATION_ANSWER_LEVEL,
    TIER_L1,
    Citation,
    ProbeError,
    ProbeResult,
    call_with_retry,
    http_error_to_probe_error,
)

ARK_RESPONSES_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"
DEFAULT_MODEL = DOUBAO_MODEL or "doubao-seed-1-6-250615"
# 默认给足输出预算。深思考模型的推理 token 也算在 max_output_tokens 里。
DEFAULT_MAX_OUTPUT_TOKENS = 4096


class DoubaoAdapter:
    engine_id = "doubao"
    label = "豆包"
    tier = TIER_L1
    citation_type = CITATION_ANSWER_LEVEL
    search_enabled = True
    implemented = True

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or DOUBAO_KEY
        self.model = model or DEFAULT_MODEL

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
            "note": "答案级引用；必须走 /responses + web_search 工具",
        }

    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult:
        if not self.api_key:
            raise ProbeError("豆包未配置 API Key（backend/.env 的 DOUBAO_KEY）")
        use_model = model or self.model
        payload = {
            "model": use_model,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": question}]}],
            "tools": [{"type": "web_search"}],
            "max_output_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
            "temperature": 0,
            "stream": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        t0 = time.monotonic()

        def _call() -> dict[str, Any]:
            with httpx.Client(timeout=180.0) as client:
                resp = client.post(ARK_RESPONSES_URL, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise http_error_to_probe_error(resp.status_code, resp.text)
            return resp.json()

        data = call_with_retry(_call)
        latency = int((time.monotonic() - t0) * 1000)
        answer, citations, queries, searched = _parse_response(data, self.citation_type)

        if not answer:
            # 空正文但已检索：属于「有推理没正文」，明确报错而不是当成空回答
            raise ProbeError(
                f"豆包返回空正文（searched={searched}），请调大 max_output_tokens 或换非深思考模型"
            )

        return ProbeResult(
            engine=self.engine_id,
            tier=self.tier,
            answer=answer,
            model=use_model,
            model_version=_model_version(data, use_model),
            citation_type=self.citation_type,
            citations=citations,
            search_queries=queries,
            latency_ms=latency,
            usage=data.get("usage") or {},
            raw={"id": data.get("id"), "searched": searched, "status": data.get("status")},
        )


def _parse_response(data: dict[str, Any], citation_type: str) -> tuple[str, list[Citation], list[str], bool]:
    texts: list[str] = []
    citations: list[Citation] = []
    queries: list[str] = []
    searched = False

    for item in data.get("output") or []:
        itype = item.get("type")
        if itype == "web_search_call":
            searched = True
            action = item.get("action") or {}
            queries.extend(action.get("queries") or ([action["query"]] if action.get("query") else []))
        if itype != "message":
            continue
        for block in item.get("content") or []:
            if block.get("type") != "output_text":
                continue
            text = block.get("text") or ""
            texts.append(text)
            for ann in block.get("annotations") or []:
                if ann.get("type") != "url_citation":
                    continue
                url = ann.get("url") or ""
                if not url:
                    continue
                citations.append(
                    Citation(
                        url=url,
                        title=ann.get("title") or "",
                        site_name=ann.get("site_name") or "",
                        publish_time=ann.get("publish_time") or "",
                        citation_type=citation_type,
                        position=text.find(ann.get("title") or "") if ann.get("title") else 0,
                    )
                )
    # 去重（同一 URL 可能被多次引用）
    deduped: list[Citation] = []
    seen: set[str] = set()
    for c in citations:
        if c.url in seen:
            continue
        seen.add(c.url)
        deduped.append(c)
    return "\n".join(t for t in texts if t).strip(), deduped, list(dict.fromkeys(queries)), searched


def _model_version(data: dict[str, Any], fallback: str) -> str:
    return str(data.get("model") or fallback or "unknown")
