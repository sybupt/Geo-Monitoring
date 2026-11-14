"""判定层：从一段 AI 回答里判出「有没有提我们、排第几、是不是正向推荐」。

为什么不用 ``brand in answer``：
- 误判：名字是「华为」时会命中「华为路」「华为大厦」；
- 漏判：简称、英文名、错别字、括号补充说明都会漏；
- 判不出倾向：「XX 不建议去」也是提到了，但那是负向。

策略：**规则初筛 + LLM 终判 + 人工抽检校准**。
规则负责便宜地挡掉明显没提的样本（省钱），LLM 负责排名和倾向，抽检用来算准确率。
判定结果必须落 ``judge_version``，否则半年后没人知道这批评分是怎么来的。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from app.config import DEEPSEEK_BASE, DEEPSEEK_KEY, KIMI_BASE, KIMI_KEY, JUDGE_PROVIDER

JUDGE_VERSION = "judge-v1"
JUDGE_MODEL_DEFAULT = "deepseek-chat"

# 短名后面跟这些内容，多半是地址/地标，不是品牌。刻意不含「店」——
# 「老王烧烤店」是正规品牌写法，含进去会漏判。
# 原则：**宁可漏过滤（假阳性交给 LLM 纠正），也不要过度过滤（假阴性进不了 LLM）**。
_ADDRESS_SINGLE = set("路街弄巷号栋站口苑")
_ADDRESS_WORDS = ("大厦", "小区", "公寓", "地铁", "停车")

PROMPT = """你是品牌提及判定员。给你一个用户的提问、智能助手的回答、以及目标品牌和它的竞品。

请判断回答中目标品牌的情况，只输出 JSON，不要任何解释文字：
{{
  "mentioned": true/false,          // 回答是否提到了目标品牌（含简称、英文名、错别字）
  "is_recommended": true/false,     // 是否以推荐/正面口吻呈现（单纯罗列不算推荐）
  "first_rank": 整数或 null,        // 品牌在推荐列表里出现的位置，第1个=1；未提及=null
  "sentiment": -1/0/1,             // -1 负面或明确不推荐，0 中性罗列，1 正面推荐
  "reason": "不超过40字的中文理由",
  "competitors_mentioned": ["回答里提到的其他品牌名"]
}}

判定注意：
- 「XX 不建议去 / XX 踩雷 / 别去 XX」= mentioned=true, sentiment=-1
- 只是把品牌列在清单里、没有任何评价 = sentiment=0
- 回答说「没有找到相关商家」= mentioned=false
- 品牌名出现在地址、路名、小区名里不算提及

目标品牌：{brand}
品牌别名（都要认）：{aliases}
竞品：{competitors}

用户提问：{question}
智能助手回答：
{answer}
"""


@dataclass
class JudgeVerdict:
    mentioned: bool = False
    is_recommended: bool = False
    first_rank: int | None = None
    sentiment: int = 0
    reason: str = ""
    competitors: list[str] = field(default_factory=list)
    method: str = "rule"
    version: str = JUDGE_VERSION
    raw: dict[str, Any] = field(default_factory=dict)


def normalize(text: str) -> str:
    """全角转半角、去零宽字符、统一小写。中文不做分词，保持原样匹配。"""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u200b", "").replace("\ufeff", "")
    return text.lower()


def _looks_like_address(text: str, pos: int) -> bool:
    nxt = text[pos : pos + 1]
    if nxt and nxt in _ADDRESS_SINGLE:
        return True
    return any(text.startswith(w, pos) for w in _ADDRESS_WORDS)


def find_positions(answer: str, aliases: list[str]) -> list[int]:
    """返回品牌名出现的位置，过滤掉「XX路 / XX大厦」这类地点性误命中。"""
    text = normalize(answer)
    positions: list[int] = []
    for alias in aliases:
        key = normalize(alias).strip()
        if not key:
            continue
        start = 0
        while True:
            idx = text.find(key, start)
            if idx < 0:
                break
            start = idx + len(key)
            if len(key) <= 3 and _looks_like_address(text, idx + len(key)):
                continue
            positions.append(idx)
    return sorted(set(positions))


_NEGATION = re.compile(r"(不建议|不推荐|别去|踩雷|避雷|差评|不要去|不怎么样)")
_LIST_LINE = re.compile(r"^\s*(?:(\d{1,2})\s*[.、)）]|[-*•])\s*")


def infer_rank(answer: str, names: list[str]) -> int | None:
    """从编号/项目符号列表里推断品牌排第几。

    本地生活的回答大概率是「1. A  2. B  3. C」这种清单，规则就能拿到排名，
    不必每条都花钱调 LLM。拿不到就返回 None —— 报告里要区分「排第 N」和「排名未知」。
    """
    order = 0
    for line in (answer or "").splitlines():
        m = _LIST_LINE.match(line)
        if not m:
            continue
        order += 1
        if find_positions(line, names):
            return int(m.group(1)) if m.group(1) else order
    return None


def rule_judge(answer: str, brand: str, aliases: list[str], competitors: list[str]) -> JudgeVerdict:
    """不花钱的初筛。没提到就立刻返回，提到了才交给 LLM 细分。"""
    names = [brand] + [a for a in aliases if a and a != brand]
    positions = find_positions(answer, names)
    if not positions:
        return JudgeVerdict(mentioned=False, method="rule")

    window = normalize(answer)[max(0, positions[0] - 20) : positions[0] + 60]
    negative = bool(_NEGATION.search(window))
    return JudgeVerdict(
        mentioned=True,
        sentiment=-1 if negative else 0,
        first_rank=infer_rank(answer, names),
        reason="规则初筛：命中品牌名" + ("（附近出现否定词）" if negative else ""),
        method="rule",
        competitors=[c for c in competitors if c and normalize(c) in normalize(answer)],
    )


def _judge_client() -> tuple[OpenAI, str] | None:
    if JUDGE_PROVIDER == "kimi" and KIMI_KEY:
        return OpenAI(api_key=KIMI_KEY, base_url=KIMI_BASE), "kimi-k2.6"
    if DEEPSEEK_KEY:
        return OpenAI(api_key=DEEPSEEK_KEY, base_url=DEEPSEEK_BASE), JUDGE_MODEL_DEFAULT
    if KIMI_KEY:
        return OpenAI(api_key=KIMI_KEY, base_url=KIMI_BASE), "kimi-k2.6"
    return None


def _parse_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"判定结果不是 JSON：{text[:200]}")
    return json.loads(text[start : end + 1])


def judge(
    question: str,
    answer: str,
    brand: str,
    aliases: list[str] | None = None,
    competitors: list[str] | None = None,
    *,
    use_llm: bool = True,
) -> JudgeVerdict:
    aliases = aliases or []
    competitors = competitors or []
    verdict = rule_judge(answer, brand, aliases, competitors)

    if not verdict.mentioned:
        return verdict
    if not use_llm:
        return verdict

    client_model = _judge_client()
    if client_model is None:
        return verdict
    client, model = client_model

    prompt = PROMPT.format(
        brand=brand,
        aliases="、".join(aliases) or "无",
        competitors="、".join(competitors) or "无",
        question=question,
        answer=answer[:4000],
    )
    try:
        resp = client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": prompt}], temperature=0
        )
        data = _parse_json(resp.choices[0].message.content or "")
    except Exception as exc:  # noqa: BLE001 - 判定失败不能影响跑批，退回规则结果
        verdict.reason = f"{verdict.reason}；LLM 判定失败：{exc}"[:200]
        return verdict

    rank = data.get("first_rank")
    try:
        rank = int(rank) if rank is not None else None
    except (TypeError, ValueError):
        rank = None

    sentiment = data.get("sentiment", 0)
    try:
        sentiment = max(-1, min(1, int(sentiment)))
    except (TypeError, ValueError):
        sentiment = 0

    return JudgeVerdict(
        mentioned=bool(data.get("mentioned", True)),
        is_recommended=bool(data.get("is_recommended", sentiment > 0)),
        first_rank=rank,
        sentiment=sentiment,
        reason=str(data.get("reason") or "")[:200],
        competitors=[str(c) for c in (data.get("competitors_mentioned") or [])][:10],
        method="llm",
        raw=data,
    )
