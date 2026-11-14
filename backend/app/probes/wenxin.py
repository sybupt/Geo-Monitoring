"""文心一言（百度千帆）适配器。

关键事实（2026-11 核对 cloud.baidu.com）：
- **当前推荐 v2 接口 + API Key 直连**：``POST https://qianfan.baidubce.com/v2/chat/completions``，
  请求头 ``Authorization: Bearer bce-v3/ALTAK-...``。这条路径**不需要 OAuth**，
  控制台「API Key」页生成即用。
- 老的 AK/SK 换 ``access_token`` 是 v1 路径（``aip.baidubce.com/rpc/2.0/...``）。
  本适配器保留它做兜底：只有配了 ``QIANFAN_AK`` + ``QIANFAN_SK`` 时才走。
- 搜索增强参数是 ``"web_search": {"enable": true, "enable_trace": true}``，
  **只有 ERNIE 4.5 系支持**；拿 3.5 跑会静默不检索，测出来的 0 提及是假的。
- 引用在 ``search_info.search_results[]``（index / url / title），属答案级；
  ``enable_trace`` 才能拿到检索轨迹，所以默认开。
- 这里不猜 endpoint：v1 的 ``{endpoint}`` 必须是控制台里真实开通的服务名，用
  ``QIANFAN_ENDPOINT`` 显式配置，默认 ``ernie-4.5-8k-preview``。
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.config import (
    QIANFAN_AK,
    QIANFAN_API_KEY,
    QIANFAN_ENDPOINT,
    QIANFAN_MODEL,
    QIANFAN_SK,
)
from app.probes.base import (
    CITATION_ANSWER_LEVEL,
    TIER_L1,
    Citation,
    ProbeError,
    ProbeResult,
    call_with_retry,
    http_error_to_probe_error,
)

V2_URL = "https://qianfan.baidubce.com/v2/chat/completions"
OAUTH_URL = "https://aip.baidubce.com/oauth/2.0/token"
V1_CHAT_URL = "https://aip.baidubce.com/rpc/2.0/ai_custom/v1/wenxinworkshop/chat/{endpoint}"

DEFAULT_MODEL = QIANFAN_MODEL or "ernie-4.5-turbo-32k"


class WenxinAdapter:
    engine_id = "wenxin"
    label = "文心一言"
    tier = TIER_L1
    citation_type = CITATION_ANSWER_LEVEL
    search_enabled = True
    implemented = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        ak: str | None = None,
        sk: str | None = None,
        endpoint: str | None = None,
    ):
        self.api_key = api_key or QIANFAN_API_KEY
        self.ak = ak or QIANFAN_AK
        self.sk = sk or QIANFAN_SK
        self.model = model or DEFAULT_MODEL
        self.endpoint = endpoint or QIANFAN_ENDPOINT
        self._token: str | None = None

    # ── 能力 ──────────────────────────────────────────────────
    @property
    def auth_mode(self) -> str:
        if self.api_key:
            return "v2_bearer"
        if self.ak and self.sk:
            return "v1_oauth"
        return "none"

    def capabilities(self) -> dict[str, Any]:
        return {
            "engine": self.engine_id,
            "label": self.label,
            "tier": self.tier,
            "citation_type": self.citation_type,
            "search_enabled": self.search_enabled,
            "implemented": self.implemented,
            "configured": self.auth_mode != "none",
            "model": self.model,
            "auth_mode": self.auth_mode,
            "setup_hint": "未配置凭据：填 QIANFAN_API_KEY（千帆 API Key，推荐）或 QIANFAN_AK + QIANFAN_SK",
            "note": "答案级引用；v2 用 API Key 直连，AK/SK 走 OAuth 兜底；搜索增强仅 ERNIE 4.5 支持",
        }

    # ── 主流程 ────────────────────────────────────────────────
    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult:
        if self.auth_mode == "none":
            raise ProbeError(
                "文心未配置凭据：填 QIANFAN_API_KEY（千帆控制台 API Key，推荐）"
                "或 QIANFAN_AK + QIANFAN_SK（走 OAuth）"
            )
        t0 = time.monotonic()
        if self.auth_mode == "v2_bearer":
            data = call_with_retry(lambda: self._call_v2(question, model))
        else:
            data = call_with_retry(lambda: self._call_v1(question, model))
        latency = int((time.monotonic() - t0) * 1000)

        answer, citations, searched = _parse(data, self.citation_type)
        if not answer:
            raise ProbeError(
                f"文心返回空正文（searched={searched}），检查模型是否支持联网搜索：{model or self.model}"
            )
        use_model = model or self.model
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
                "id": data.get("id") or data.get("request_id"),
                "searched": searched,
                "auth_mode": self.auth_mode,
            },
        )

    # ── v2：API Key 直连（推荐）───────────────────────────────
    def _call_v2(self, question: str, model: str | None) -> dict[str, Any]:
        payload = {
            "model": model or self.model,
            "messages": [{"role": "user", "content": [{"type": "text", "text": question}]}],
            "web_search": {"enable": True, "enable_trace": True},
            "stream": False,
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(V2_URL, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise http_error_to_probe_error(resp.status_code, resp.text)
        return resp.json()

    # ── v1：AK/SK → OAuth access_token（兜底）──────────────────
    def _call_v1(self, question: str, model: str | None) -> dict[str, Any]:
        token = self._access_token()
        url = V1_CHAT_URL.format(endpoint=self.endpoint)
        payload = {
            "messages": [{"role": "user", "content": question}],
            "web_search": {"enable": True, "enable_citation": True},
            "temperature": 0.01,
        }
        with httpx.Client(timeout=180.0) as client:
            resp = client.post(url, params={"access_token": token}, json=payload)
        if resp.status_code >= 400:
            raise http_error_to_probe_error(resp.status_code, resp.text)
        return resp.json()

    def _access_token(self) -> str:
        """OAuth client_credentials 换 token，进程内缓存。token 有效期 30 天。"""
        if self._token:
            return self._token
        with httpx.Client(timeout=30.0) as client:
            resp = client.get(
                OAUTH_URL,
                params={
                    "grant_type": "client_credentials",
                    "client_id": self.ak,
                    "client_secret": self.sk,
                },
            )
        if resp.status_code >= 400:
            raise http_error_to_probe_error(resp.status_code, resp.text)
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise ProbeError(f"OAuth 未返回 access_token：{str(data)[:300]}（检查 AK/SK 是否正确）")
        self._token = token
        return token


def _parse(data: dict[str, Any], citation_type: str) -> tuple[str, list[Citation], bool]:
    """兼容 v2（choices[].message.content）与 v1（result）两种响应。"""
    answer = _extract_answer(data)

    results = _extract_results(data)
    citations: list[Citation] = []
    seen: set[str] = set()
    for idx, item in enumerate(results):
        url = str(item.get("url") or item.get("link") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        citations.append(
            Citation(
                url=url,
                title=str(item.get("title") or ""),
                site_name=str(item.get("site_name") or item.get("website") or ""),
                publish_time=str(item.get("date") or item.get("publish_time") or ""),
                citation_type=citation_type,
                position=idx,
            )
        )
    return answer, citations, bool(results)


def _extract_answer(data: dict[str, Any]) -> str:
    if data.get("result"):
        return str(data["result"]).strip()
    parts: list[str] = []
    for ch in data.get("choices") or []:
        msg = ch.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    parts.append(str(block["text"]).strip())
    return "\n".join(p for p in parts if p).strip()


def _extract_results(data: dict[str, Any]) -> list[dict[str, Any]]:
    """v2 放在 search_info.search_results 或顶层 references；v1 在 search_info.search_results。"""
    candidates: list[Any] = [
        (data.get("search_info") or {}).get("search_results"),
        data.get("references"),
        data.get("search_results"),
    ]
    for ch in data.get("choices") or []:
        candidates.append((ch.get("search_info") or {}).get("search_results"))
    for cand in candidates:
        if isinstance(cand, list) and cand:
            return [c for c in cand if isinstance(c, dict)]
    return []
