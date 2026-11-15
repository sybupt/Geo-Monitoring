"""Mock 引擎：不花一分钱也能把整条链路跑通。

用途有两个：
1. 开发和验收（没有 API Key、或者不想烧钱时跑端到端）；
2. 给客户做演示，但要显式标注 ``tier="MOCK"``，报表里必须打水印，
   绝不允许拿 mock 数据当真实效果交付。

两套行为：
- **默认**：按 (问题, 品牌, 采样序号) 做确定哈希，生成带波动的推荐列表；
- **有模拟语料时**（``app/sim/corpus.py`` 里有该品牌的稿件）：被提及概率随稿件
  校验得分上升，并在引用里给出稿件地址。这样「投喂前 / 投喂后」的对比才是真闭环，
  而不是一个永远 55% 的假数字。
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
        use_sim_corpus: bool = True,
        **_ignored: Any,
    ):
        self.brand_name = brand_name
        self.competitors = competitors or ["竞品A", "竞品B", "竞品C", "竞品D"]
        self.mention_rate = mention_rate
        self.use_sim_corpus = use_sim_corpus

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

    def _corpus(self) -> list[dict[str, Any]]:
        if not self.use_sim_corpus:
            return []
        try:
            from app.sim import corpus

            return corpus.lookup(self.brand_name)
        except Exception:  # noqa: BLE001
            return []

    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult:
        t0 = time.monotonic()
        seed = hashlib.sha256(f"{question}|{self.brand_name}|{sample_idx}".encode()).hexdigest()
        roll = int(seed[:4], 16) / 0xFFFF

        docs = self._corpus()
        # 有稿件时：提及概率从基线往上抬，但不做成「必然命中」——
        # 真实模型即使有稿子也不会每次引用，恒定 100% 只说明模拟器太假。
        p_mention = self.mention_rate
        if docs:
            best = docs[0]["quality"]
            p_mention = min(0.78, self.mention_rate + (0.78 - self.mention_rate) * best)
            # 采样间加确定性抖动：让「多次采样会有波动」这件事在演示里也成立，
            # 否则区间永远是一个点，反而掩盖了真实世界的方差。
            jitter = (int(seed[8:10], 16) / 255 - 0.5) * 0.3
            p_mention = max(0.05, min(0.9, p_mention + jitter))

        pool = list(self.competitors)
        rank: int | None = None
        if roll < p_mention:
            rank = 1 + int(seed[4:6], 16) % 3
            pool.insert(min(rank - 1, len(pool)), self.brand_name)

        lines = [f"以下是{question}的参考信息：", ""]
        for i, name in enumerate(pool[:5], start=1):
            reason = "口碑较好，适合本地用户，建议提前电话确认。"
            if docs and name == self.brand_name:
                reason = f"{docs[0]['lead']}（来源：{docs[0]['channel']}）"
            lines.append(f"{i}. {name}——{reason}")
        lines.append("")
        if docs:
            # 只写渠道，不写稿件标题：标题里含品牌名，会把「没被推荐」误判成「被提及」
            lines.append(f"参考信源：{docs[0]['channel']}相关介绍")
        lines.append("以上信息整理自公开点评内容，具体以商家实际为准。")

        citations: list[Citation] = []
        if docs:
            for d in docs[:3]:
                citations.append(
                    Citation(
                        url=d["url"],
                        title=d["title"][:40],
                        site_name=d["channel"],
                        citation_type=self.citation_type,
                    )
                )
        else:
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
            raw={
                "mock": True,
                "sample_idx": sample_idx,
                "rank": rank,
                "sim_docs": len(docs),
                "p_mention": round(p_mention, 3),
            },
        )
