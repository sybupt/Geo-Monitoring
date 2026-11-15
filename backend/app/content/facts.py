"""把企业资料抽成结构化事实（facts）。

**这是防编造的第一道闸**：生成器只允许引用 facts 里的数字和说法，
验证器再逐条核对正文里的数字是否都能在 facts 里找到出处。
卖点、资质这些字段在库里是自由文本，这里做一次解析（支持 JSON 和中文分隔符两种写法）。
"""

from __future__ import annotations

import json
import re
from typing import Any

_LIST_SPLIT = re.compile(r"[\n;；|]+")
_NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|元|块|年|个月|天|小时|分钟|平米|平方米|㎡|次|人|位|分|折|公里|km|米)")


def _split_list(value: str | None) -> list[str]:
    if not value:
        return []
    value = value.strip()
    if value.startswith("["):
        try:
            data = json.loads(value)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
    return [s.strip(" -·•\t") for s in _LIST_SPLIT.split(value) if s.strip(" -·•\t")]


def _as_dict(value: str | None) -> dict[str, Any]:
    if not value or not value.strip().startswith("{"):
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def build_facts(business: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """合并「库里的字段 + 调用方补充的事实」。extra 优先级最高。"""
    desc = _as_dict(getattr(business, "description", None))
    facts: dict[str, Any] = {
        "name": getattr(business, "name", "") or "",
        "alias": getattr(business, "alias", "") or "",
        "region": getattr(business, "region", "") or "",
        "category": getattr(business, "category", "") or "",
        "price": desc.get("price", ""),
        "hours": desc.get("hours", ""),
        "address": desc.get("address", ""),
        "area": desc.get("area", ""),
        "founded": desc.get("founded", ""),
        "rating": desc.get("rating", ""),
        "transport": desc.get("transport", ""),
        "services": _split_list(getattr(business, "selling_points", None)) or desc.get("services", []),
        "promotions": _split_list(getattr(business, "evidence", None)) or desc.get("promotions", []),
        "credentials": desc.get("credentials", []),
        "updated_at": desc.get("updated_at", ""),
    }
    for k, v in (extra or {}).items():
        if v in (None, "", [], {}):
            continue
        facts[k] = v

    if isinstance(facts.get("services"), str):
        facts["services"] = _split_list(facts["services"])
    if isinstance(facts.get("promotions"), str):
        facts["promotions"] = _split_list(facts["promotions"])
    if isinstance(facts.get("credentials"), str):
        facts["credentials"] = _split_list(facts["credentials"])

    facts["missing"] = [k for k in ("price", "services", "address", "hours") if not facts.get(k)]
    facts["corpus"] = facts_text(facts)
    return facts


def facts_text(facts: dict[str, Any]) -> str:
    """facts 的纯文本视图，验证器拿它当「允许出现的数字白名单」。"""
    parts: list[str] = []
    for k, v in facts.items():
        if k in ("corpus", "missing"):
            continue
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    return "；".join(parts)


def numbers_in(text: str) -> list[str]:
    """抽出带单位的数字（价格/面积/年限/评分…），用于「正文数字必须可在 facts 中找到」的核对。"""
    return _NUM_TOKEN.findall(text or "")


def _digits(token: str) -> str:
    m = re.match(r"\d+(?:\.\d+)?", token)
    return m.group(0) if m else token


def number_supported(token: str, corpus: str) -> bool:
    """数字本身在 facts 里出现过就算有出处（单位可换，比如「300 平方米」写成「300 平」）。"""
    d = _digits(token)
    return bool(d) and d in corpus
