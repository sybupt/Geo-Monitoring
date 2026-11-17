"""效果检测：模拟一次"带检索的模型回答"。

真实链路是：给模型发 query → 它自己检索 → 返回答案 + 引用列表。
这里没有 API，所以用一个可解释的模拟检索器替代：

    score = 0.55 * 相关性(BM25/TF-IDF) + 0.45 * 该格规则适配度

规则适配度用的正是 learn.py 学出来的那一格权重，因此"改了内容 → 引用概率变化"
这条因果链是通的。接入真实 API 时，只需把 `_retrieve` 换成真实返回的引用列表，
上层归因逻辑不用动。
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from ..cells.registry import ALL_DIMS, ChannelEntry, ModelEntry
from ..geo.analyzer import content_tokens
from ..geo.learn import features, simulate_prob


class Corpus:
    """BM25 语料，用于模拟检索。

    早期用全文档 TF-IDF 余弦，结果"改写稿反而检索不到"——因为模板补充段
    稀释了整篇向量。真实搜索引擎按查询词计分（BM25），与文档里无关的
    套话几乎不影响排序，这才符合"投喂后能否被检索命中"的真实机制。
    """

    K1, B = 1.5, 0.75

    def __init__(self, docs: list[dict]):
        self.docs = docs
        self.tokens = [Counter(content_tokens(d["text"])) for d in docs]
        df: Counter = Counter()
        for t in self.tokens:
            df.update(t.keys())
        n = max(1, len(docs))
        self.idf = {w: math.log((1 + n) / (1 + c)) + 1.0 for w, c in df.items()}
        self.dl = [sum(t.values()) for t in self.tokens]
        self.avgdl = sum(self.dl) / max(1, len(self.dl))
        self._sim_cache: dict[str, list[float]] = {}

    def similarity(self, query: str) -> list[float]:
        if query in self._sim_cache:
            return self._sim_cache[query]
        q = Counter(content_tokens(query))
        out: list[float] = []
        for t, dl in zip(self.tokens, self.dl):
            score = 0.0
            for w, qtf in q.items():
                tf = t.get(w, 0)
                if not tf:
                    continue
                denom = tf + self.K1 * (1 - self.B + self.B * dl / max(1, self.avgdl))
                score += self.idf.get(w, 1.0) * tf * (self.K1 + 1) / denom * (1 + math.log(qtf))
            out.append(score)
        # 归一化到 0-1，供与规则适配度加权融合
        mx = max(out) or 1.0
        res = [x / mx for x in out]
        self._sim_cache[query] = res
        return res


_CACHE: dict[str, Corpus] = {}


def build_corpus(docs: list[dict], key: str = "default") -> Corpus:
    # 缓存键必须包含内容指纹：归因场景里同一 key 对应的语料每次都不同
    # （原稿/改写稿互换），复用旧缓存会导致"改写稿根本没进索引"的假结果。
    import hashlib as _h
    import json as _j
    fp = _h.md5(_j.dumps([d["text"][:200] for d in docs], ensure_ascii=False).encode()).hexdigest()[:8]
    full_key = f"{key}::{fp}"
    if full_key not in _CACHE:
        _CACHE[full_key] = Corpus(docs)
    return _CACHE[full_key]


def get_corpus(docs_provider, key: str = "default") -> Corpus:
    if key not in _CACHE:
        _CACHE[key] = Corpus(docs_provider())
    return _CACHE[key]


def _noise(query: str, doc_id: str) -> float:
    """确定性噪声：模拟检索排序波动，避免"目标文档永远第一"这种假结果。"""
    h = hashlib.md5(f"{query}::{doc_id}".encode()).hexdigest()[:6]
    return (int(h, 16) / 0xFFFFFF - 0.5) * 0.22


_FEAT_CACHE: dict[tuple, dict[str, float]] = {}


def _doc_features(doc: dict, channel: ChannelEntry) -> dict[str, float]:
    """特征只跟"文档内容 + 渠道"有关，与 query 无关。

    早期版本在每次检索里重算 features()，一轮 6 渠道 × 24 问法要跑 4 万多
    次正则检查（5 秒+），页面直接卡住。缓存后同一篇文档只算一次。
    """
    key = (doc["id"], channel.id, hash(doc["text"]))
    f = _FEAT_CACHE.get(key)
    if f is None:
        f = features(doc["text"], channel)
        _FEAT_CACHE[key] = f
    return f


def _retrieve(query: str, corpus: Corpus, model: ModelEntry, channel: ChannelEntry,
              weights: dict[str, float] | None, exclude: str | None, top_k: int = 5) -> list[dict]:
    sims = corpus.similarity(query)
    scored: list[tuple[float, int]] = []
    for i, doc in enumerate(corpus.docs):
        if exclude and doc["id"] == exclude:
            continue
        rel = max(0.0, min(1.0, sims[i] + _noise(query, doc["id"])))
        if weights:
            x = _doc_features(doc, channel)
            fit = sum(w * x.get(d, 0.0) for d, w in weights.items())
            fit = 1 / (1 + math.exp(-fit / max(1, len(weights)) * 3))
        else:
            fit = 0.5
        scored.append((0.55 * rel + 0.45 * fit, i))
    scored.sort(reverse=True)
    out: list[dict] = []
    seen_titles: set[str] = set()
    for score, i in scored:
        doc = corpus.docs[i]
        if doc["title"] in seen_titles:
            continue
        seen_titles.add(doc["title"])
        out.append({
            "id": doc["id"], "title": doc["title"], "channel": doc.get("channel", ""),
            "score": round(score, 4),
        })
        if len(out) >= top_k:
            break
    return out


def retrieve(query: str, corpus: Corpus, model: ModelEntry, channel: ChannelEntry,
             weights: dict[str, float] | None = None, exclude: str | None = None,
             top_k: int = 5) -> list[dict]:
    """对外暴露的检索入口：模拟"模型在某渠道检索这条 query 会引用谁"。"""
    return _retrieve(query, corpus, model, channel, weights, exclude, top_k)


def run(queries: list[str], model: ModelEntry, channel: ChannelEntry,
        weights: dict[str, float] | None, corpus: Corpus,
        target_ids: list[str], exclude: str | None = None) -> list[dict]:
    """对每个 query 跑一次检索，返回命中情况。"""
    rows: list[dict] = []
    for q in queries:
        cites = _retrieve(q, corpus, model, channel, weights, exclude)
        hit_rank = None
        for n, c in enumerate(cites, start=1):
            if c["id"] in target_ids:
                hit_rank = n
                break
        rows.append({
            "query": q,
            "cited": hit_rank is not None,
            "rank": hit_rank,
            "citations": cites,
        })
    return rows


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    hits = sum(1 for r in rows if r["cited"])
    ranks = [r["rank"] for r in rows if r["rank"]]
    return {
        "samples": n,
        "hits": hits,
        "hit_rate": round(hits / max(n, 1), 3),
        "avg_rank": round(sum(ranks) / len(ranks), 2) if ranks else None,
    }


def build_queries(asset_title: str, channel: ChannelEntry, category: str = "",
                  extra: list[str] | None = None) -> list[str]:
    """构造探针 query。

    必须包含**类目级问法**：如果只用商品全名提问，目标文档是全网唯一命中，
    引用率必然 100%，那是自证，不是效果。真实场景里用户问的是
    "笔记本电脑怎么选"，你的页面要和同品类几十篇内容竞争。
    """
    core = re.sub(r"[（(].*?[）)]", "", asset_title).strip()
    parts = core.split()
    brand = parts[0] if parts else core
    name = " ".join(parts[:3])
    cat = category or (parts[-1] if parts else core)

    aspects = ["续航", "噪音", "价格", "散热", "接口", "重量", "屏幕", "售后", "参数", "缺点"]
    base = (
        [f"{cat} 怎么选", f"{cat} 2026 推荐", f"{cat} 对比", f"{cat} 排行",
         f"{brand} {cat} 怎么样", f"{name} 值得买吗", f"{name} 优缺点"]
        + [f"{cat} {a} 怎么样" for a in aspects]
        + [f"{name} {a}" for a in aspects[:5]]
    )
    if channel.locale == "en":
        aspects_en = ["battery", "noise", "price", "thermals", "ports", "weight", "display", "support"]
        base = (
            [f"best {cat} 2026", f"how to choose {cat}", f"{cat} comparison", f"{cat} ranking",
             f"{brand} {cat} review", f"is {name} worth it", f"{name} pros and cons"]
            + [f"{cat} {a} review" for a in aspects_en]
            + [f"{name} {a}" for a in aspects_en[:5]]
        )
    if extra:
        base = base + list(extra)
    return base
