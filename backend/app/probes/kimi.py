"""Kimi（Moonshot）适配器。

- 通过内置函数 ``$web_search`` 触发联网，需要两轮调用（第一轮拿 tool_calls，第二轮喂回结果）。
- 引用是**检索候选面**：我们能看到模型检索到了哪些页面，但看不到「哪句话来自哪条」，
  所以 citation_type 记为 retrieval_pool，不能和豆包的答案级引用混算。
- Moonshot 的 tool 返回参数里通常带 ``search_results`` 列表（url/title/content）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from app.config import KIMI_BASE, KIMI_KEY, KIMI_SEARCH_MODEL
from app.probes.base import (
    CITATION_RETRIEVAL_POOL,
    TIER_L1,
    Citation,
    ProbeError,
    ProbeResult,
    call_with_retry,
)

TOOL = [{"type": "builtin_function", "function": {"name": "$web_search"}}]


class KimiAdapter:
    engine_id = "kimi"
    label = "Kimi"
    tier = TIER_L1
    citation_type = CITATION_RETRIEVAL_POOL
    search_enabled = True
    implemented = True

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or KIMI_KEY
        self.model = model or KIMI_SEARCH_MODEL

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
            "note": "检索候选面引用；两轮 tool call",
        }

    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult:
        if not self.api_key:
            raise ProbeError("Kimi 未配置 API Key（backend/.env 的 KIMI_KEY）")
        use_model = model or self.model
        client = OpenAI(api_key=self.api_key, base_url=KIMI_BASE)
        t0 = time.monotonic()

        def _call():
            first = client.chat.completions.create(
                model=use_model,
                messages=[{"role": "user", "content": question}],
                tools=TOOL,
                temperature=0,
            )
            msg = first.choices[0].message
            if not msg.tool_calls:
                return msg.content or "", [], []
            messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )
            for tc in msg.tool_calls:
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": tc.function.arguments}
                )
            second = client.chat.completions.create(
                model=use_model, messages=messages, tools=TOOL, temperature=0
            )
            citations, queries = _citations_from_tool_calls(msg.tool_calls)
            return second.choices[0].message.content or "", citations, queries

        answer, citations, queries = call_with_retry(_call, attempts=3, base_delay=3.0)
        latency = int((time.monotonic() - t0) * 1000)
        if not answer:
            raise ProbeError("Kimi 返回空正文")
        return ProbeResult(
            engine=self.engine_id,
            tier=self.tier,
            answer=answer,
            model=use_model,
            model_version=use_model,
            citation_type=self.citation_type,
            citations=citations,
            search_queries=queries,
            latency_ms=latency,
            usage={},
            raw={},
        )


def _citations_from_tool_calls(tool_calls) -> tuple[list[Citation], list[str]]:
    citations: list[Citation] = []
    queries: list[str] = []
    seen: set[str] = set()
    for tc in tool_calls or []:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            continue
        if isinstance(args.get("query"), str):
            queries.append(args["query"])
        for item in args.get("search_results") or []:
            url = item.get("url") or ""
            if not url or url in seen:
                continue
            seen.add(url)
            citations.append(
                Citation(
                    url=url,
                    title=item.get("title") or "",
                    site_name=item.get("site_name") or "",
                    publish_time=item.get("date") or item.get("publish_time") or "",
                    citation_type=CITATION_RETRIEVAL_POOL,
                )
            )
    return citations, queries
