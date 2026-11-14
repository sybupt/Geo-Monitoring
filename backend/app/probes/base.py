"""探测量层：引擎适配器统一接口。

三条铁律（改动前先读）：

1. **引用语义不许混算**。豆包是答案级引用（URL 挂在具体句子上），千问是检索候选面，
   DeepSeek 是调用方自备的候选。三者的数字不能相加、不能互相排名，所以每条引用都必须
   带 ``citation_type``，指标层按类型分组统计。
2. **拿不到就报错，绝不返回空结果**。空结果会被判定为「未提及」，直接污染提及率。
   所有失败必须抛 ``ProbeError`` 或返回 ``ok=False`` 并带上错误原因。
3. **必须记录 model_version**。模型静默升级会让基线失效，没有版本号的数字无法解释。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse


class ProbeError(RuntimeError):
    """探测失败。retryable 决定调度层要不要重试。"""

    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


# ── 引用语义分级 ────────────────────────────────────────────────
CITATION_ANSWER_LEVEL = "answer_level"      # URL 挂在答案句子上，能看到「这句话来自哪」
CITATION_RETRIEVAL_POOL = "retrieval_pool"  # 只拿到检索候选，不知道最终采纳了哪条
CITATION_CALLER_SUPPLIED = "caller_supplied"  # 我们自己喂给模型的候选，最弱
CITATION_NONE = "none"                      # 该链路完全不回传引用

# ── 数据来源可信等级 ────────────────────────────────────────────
TIER_L1 = "L1"  # 官方 API + 结构化引用字段
TIER_L2 = "L2"  # 官方 API 但需自建检索 / 引用为候选面
TIER_L3 = "L3"  # 浏览器自动化兜底，稳定性最差


@dataclass
class Citation:
    url: str
    title: str = ""
    site_name: str = ""
    publish_time: str = ""
    citation_type: str = CITATION_ANSWER_LEVEL
    position: int = 0

    @property
    def domain(self) -> str:
        try:
            host = urlparse(self.url).netloc.lower()
        except Exception:
            return ""
        return host[4:] if host.startswith("www.") else host

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "site_name": self.site_name,
            "publish_time": self.publish_time,
            "citation_type": self.citation_type,
            "position": self.position,
            "domain": self.domain,
        }


@dataclass
class ProbeResult:
    engine: str
    tier: str
    answer: str
    model: str = ""
    model_version: str = ""
    citation_type: str = CITATION_NONE
    citations: list[Citation] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)
    latency_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    error: str = ""


class ProbeAdapter(Protocol):
    """引擎适配器契约。新增引擎只需实现 capabilities + probe。"""

    engine_id: str
    label: str
    tier: str
    citation_type: str
    search_enabled: bool
    implemented: bool

    def capabilities(self) -> dict[str, Any]: ...

    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult: ...


class TokenBucket:
    """按引擎限流。豆包联网搜索是账号级 10 QPS，绝不能裸并发打过去。"""

    def __init__(self, per_minute: int, burst: int = 1):
        self.capacity = max(1, burst)
        self.rate = max(1, per_minute) / 60.0
        self._tokens = float(self.capacity)
        self._ts = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, n: int = 1, timeout: float = 300.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._ts) * self.rate)
                self._ts = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                need = (n - self._tokens) / self.rate
            if time.monotonic() + need > deadline:
                raise ProbeError("限流等待超时", retryable=True)
            time.sleep(min(max(need, 0.05), 1.0))


_RETRY_STATUS = {429, 500, 502, 503, 504}


def call_with_retry(fn, *, attempts: int = 3, base_delay: float = 2.0):
    """指数退避重试。只重试明确的限流/网关错误，4xx 参数错误立即抛出。"""
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except ProbeError as exc:
            last = exc
            if not exc.retryable or i == attempts - 1:
                raise
        except Exception as exc:  # noqa: BLE001 - 网络库异常形态不统一
            last = exc
            if i == attempts - 1:
                raise ProbeError(f"调用失败：{exc}") from exc
        time.sleep(base_delay * (2 ** i))
    raise ProbeError(str(last) if last else "调用失败")


def http_error_to_probe_error(status_code: int, body: str) -> ProbeError:
    snippet = (body or "")[:500]
    retryable = status_code in _RETRY_STATUS
    if status_code == 401:
        return ProbeError(f"鉴权失败（401），检查 API Key：{snippet}", status_code=status_code)
    if status_code == 404:
        return ProbeError(
            f"模型或接口不存在（404），检查 model / endpoint id：{snippet}", status_code=status_code
        )
    return ProbeError(f"HTTP {status_code}：{snippet}", retryable=retryable, status_code=status_code)
