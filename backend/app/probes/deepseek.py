"""DeepSeek 适配器（官方 API 不联网）。

这是一个必须说清楚的口径问题：DeepSeek 官方 chat API 不带检索，用它测出来的是
**模型记忆里的品牌认知**，不是「AI 搜索把谁推给了用户」。

所以这里把 citation_type 记为 none、tier 记为 L2，并在结果里带 ``memory_mode=True``。
指标层必须把这类数据和检索型引擎分开呈现，否则客户会拿两套不可比的数字互相排名。

需要「检索口径」时，由调度层接入第三方检索（火山搜索 / 博查 / 自建），把候选页塞进
``retrieval_context`` —— 此时引用记为 caller_supplied，仍不等于引擎自己的引用。
"""

from __future__ import annotations

import time
from typing import Any

from openai import OpenAI

from app.config import DEEPSEEK_BASE, DEEPSEEK_KEY, DEEPSEEK_MODEL
from app.probes.base import (
    CITATION_CALLER_SUPPLIED,
    CITATION_NONE,
    TIER_L2,
    ProbeError,
    ProbeResult,
    call_with_retry,
)


class DeepSeekAdapter:
    engine_id = "deepseek"
    label = "DeepSeek"
    tier = TIER_L2
    citation_type = CITATION_NONE
    search_enabled = False
    implemented = True
    memory_mode = True

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or DEEPSEEK_KEY
        self.model = model or DEEPSEEK_MODEL

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
            "memory_mode": True,
            "note": "官方 API 不联网，测的是模型记忆口径，不可与检索型引擎混算",
        }

    def probe(
        self,
        question: str,
        *,
        model: str | None = None,
        sample_idx: int = 0,
        retrieval_context: str | None = None,
    ) -> ProbeResult:
        if not self.api_key:
            raise ProbeError("DeepSeek 未配置 API Key（backend/.env 的 DEEPSEEK_KEY）")
        use_model = model or self.model
        client = OpenAI(api_key=self.api_key, base_url=DEEPSEEK_BASE)

        citations = []
        prompt = question
        citation_type = CITATION_NONE
        if retrieval_context:
            citation_type = CITATION_CALLER_SUPPLIED
            prompt = (
                "请仅依据下面提供的资料回答问题，并在句末用 [n] 标注来源编号。\n\n"
                f"资料：\n{retrieval_context}\n\n问题：{question}"
            )

        t0 = time.monotonic()

        def _call() -> str:
            resp = client.chat.completions.create(
                model=use_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            return resp.choices[0].message.content or ""

        answer = call_with_retry(_call, attempts=3, base_delay=3.0)
        if not answer:
            raise ProbeError("DeepSeek 返回空正文")
        return ProbeResult(
            engine=self.engine_id,
            tier=self.tier,
            answer=answer,
            model=use_model,
            model_version=use_model,
            citation_type=citation_type,
            citations=citations,
            latency_ms=int((time.monotonic() - t0) * 1000),
            usage={},
            raw={"memory_mode": not retrieval_context},
        )
