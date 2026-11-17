"""问法 × 文档的相关度。

两处共用，必须只有一份实现：
  1) 模拟标签：模型只在"文档确实回答了这条问法"时才可能引用它；
  2) 投喂适配分：改写后的稿子要覆盖问题集里的问法，否则检索端根本排不上来。

用 2-gram 覆盖率而不是向量余弦：长文档里塞进模板段会把余弦稀释，
早期就踩过这个坑（改写稿相似度从 0.68 掉到 0.05）。按"问法词被命中多少"
计分，与 BM25 的直觉一致，也不需要额外依赖。
"""
from __future__ import annotations

from collections import Counter

from .analyzer import content_tokens

# 覆盖率放大系数：一篇正常回答该问法的文档，覆盖 45% 的问法词就算"答到了"，
# 因为中文 2-gram 里必然有一半是跨词边界的噪声 token（笔记/记本/本电…）。
_SCALE = 2.2


def coverage(text: str, query: str) -> float:
    """问法词被文档覆盖的比例，0-1。"""
    q = {t for t in content_tokens(query)}
    if not q:
        return 0.0
    d = Counter(content_tokens(text))
    hit = sum(1 for t in q if t in d)
    return hit / len(q)


def relevance(text: str, query: str) -> float:
    """放大并截断后的相关度，0-1，用于标签门控。"""
    return min(1.0, coverage(text, query) * _SCALE)


def coverage_mean(text: str, queries: list[str]) -> float:
    if not queries:
        return 0.0
    return sum(coverage(text, q) for q in queries) / len(queries)
