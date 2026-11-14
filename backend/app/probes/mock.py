"""Mock 引擎：不花一分钱也能把整条链路跑通。

用途有两个：
1. 开发和验收（没有 API Key、或者不想烧钱时跑端到端）；
2. 给客户做演示，但要显式标注 ``tier="MOCK"``，报表里必须打水印，
   绝不允许拿 mock 数据当真实效果交付。

行为：按 (问题, 品牌, 采样序号) 做确定哈希，生成带波动的推荐列表，
用来验证「多次采样后区间收窄」这件事是不是真的成立。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from app.probes.base import (
    CITATION_ANSWER_LEVEL,
    Citation,
    ProbeResult,
)

DOMAINS = [
    ("https://www.dianping.com/shop/1", "大众点评", "大众点评"),
    ("https://www.xiaohongshu.com/explore/1", "小红书笔记", "小红书"),
    ("https://www.zhihu.com/question/1", "知乎回答", "知乎"),
    ("https://www.douyin.com/video/1", "抖音探店视频", "抖音"),
]


class MockAdapter:
    engine_id = "mock"
    label = "Mock（演练）"
    tier = "MOCK"
    citation_type = CITATION_ANSWER_LEVEL
    search_enabled = True
    implemented = True

    def __init__(
        self,
        *,
        brand_name: str = "示例品牌",
        competitors: list[str] | None = None,
        mention_rate: float = 0.55,
        **_ignored: Any,
    ):
        self.brand_name = brand_name
        self.competitors = competitors or ["竞品A", "竞品B", "竞品C", "竞品D"]
        self.mention_rate = mention_rate

    def capabilities(self) -> dict[str, Any]:
        return {
            "engine": self.engine_id,
            "label": self.label,
            "tier": self.tier,
            "citation_type": self.citation_type,
            "search_enabled": True,
            "implemented": True,
            "configured": True,
            "model": "mock-1",
            "note": "演练用，数据不可作为效果证明",
        }

    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult:
        t0 = time.monotonic()
        seed = hashlib.sha256(f"{question}|{self.brand_name}|{sample_idx}".encode()).hexdigest()
        roll = int(seed[:4], 16) / 0xFFFF

        pool = list(self.competitors)
        rank: int | None = None
        if roll < self.mention_rate:
            rank = 1 + int(seed[4:6], 16) % 3
            pool.insert(min(rank - 1, len(pool)), self.brand_name)

        lines = [f"以下是{question}的参考信息：", ""]
        for i, name in enumerate(pool[:5], start=1):
            lines.append(f"{i}. {name}——口碑较好，适合本地用户，建议提前电话确认。")
        lines.append("")
        lines.append("以上信息整理自公开点评内容，具体以商家实际为准。")

        citations = [
            Citation(url=url, title=f"{name} · {question[:12]}", site_name=site, citation_type=self.citation_type)
            for (url, name, site) in DOMAINS[: 2 + int(seed[6:8], 16) % 3]
        ]

        return ProbeResult(
            engine=self.engine_id,
            tier=self.tier,
            answer="\n".join(lines),
            model="mock-1",
            model_version="mock-1",
            citation_type=self.citation_type,
            citations=citations,
            search_queries=[question],
            latency_ms=int((time.monotonic() - t0) * 1000),
            usage={},
            raw={"mock": True, "sample_idx": sample_idx, "rank": rank},
        )
