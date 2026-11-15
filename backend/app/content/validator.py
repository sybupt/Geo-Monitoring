"""投喂稿校验：编造数据、极限词、规则缺失、渠道篇幅四道闸。

顺序很重要——**先查事实，再查合规，最后查规则**。
因为「符合模型偏好」但「数字是编的」的稿子一旦被引用，风险远大于没被引用。
"""

from __future__ import annotations

from typing import Any

from app.content.facts import facts_text, number_supported, numbers_in
from app.rules.dsl import CHANNEL_SPECS, Rule
from app.rules.features import _EXTREME, extract  # noqa: PLC2701 - 极限词表与特征层共用一份


def validate(
    content: str,
    *,
    facts: dict[str, Any],
    rules: list[Rule],
    channel: str = "",
    brand: str = "",
) -> dict[str, Any]:
    brand = brand or facts.get("name", "")
    region = facts.get("region", "")
    corpus = facts.get("corpus") or facts_text(facts)
    issues: list[dict[str, str]] = []

    # ① 数字必须能在 facts 里找到出处（品牌名里的数字不算，例如「A4+」）
    body = content.replace(brand, "") if brand else content
    unsupported = [t for t in numbers_in(body) if not number_supported(t, corpus)]
    if unsupported:
        issues.append(
            {
                "level": "fatal",
                "code": "unsupported_number",
                "message": f"正文出现 {len(unsupported)} 个 facts 里没有的数字：{'、'.join(unsupported[:6])}",
                "detail": "投喂稿禁止出现无出处数字，请补进企业事实或从正文删除",
            }
        )

    # ② 极限词/绝对化用语（广告法高风险）
    extreme = [w for w in _EXTREME.findall(body)]
    if extreme:
        issues.append(
            {
                "level": "fatal",
                "code": "extreme_words",
                "message": f"出现极限词：{'、'.join(sorted(set(extreme)))}",
                "detail": "绝对化用语违反广告法，模型也会因此降低采信度",
            }
        )

    # ③ 主体与地域必须出现，否则模型无法把内容归到该品牌
    if brand and content.count(brand) < 2:
        issues.append(
            {
                "level": "fatal",
                "code": "brand_weak",
                "message": f"品牌名仅出现 {content.count(brand)} 次",
                "detail": "实体信息太弱，检索后也难以被归因到该品牌",
            }
        )
    if region and region not in content:
        issues.append(
            {
                "level": "warn",
                "code": "region_missing",
                "message": "正文没有出现所在城市/区域",
                "detail": "本地生活类查询强依赖地域锚点",
            }
        )

    # ④ 规则满足度
    feats = extract(content, brand=brand, region=region, channel=channel)
    total_w = sum(r.weight for r in rules) or 1.0
    hit_w = sum(r.weight for r in rules if feats.get(r.dimension))
    satisfied, missing_required = [], []
    for r in rules:
        ok = bool(feats.get(r.dimension))
        item = {
            "dimension": r.dimension,
            "weight": r.weight,
            "satisfied": ok,
            "source": r.source,
        }
        satisfied.append(item)
        if not ok and r.weight >= 0.6:
            missing_required.append(item)
    if missing_required:
        issues.append(
            {
                "level": "warn",
                "code": "rules_missing",
                "message": f"{len(missing_required)} 条高权重规则未满足",
                "detail": "可补事实后重新生成，或确认该规则是否适用于此渠道",
            }
        )

    # ⑤ 渠道篇幅
    spec = CHANNEL_SPECS.get(channel)
    if spec:
        lo, hi = spec["length"]
        if not (lo <= len(content) <= hi):
            issues.append(
                {
                    "level": "warn",
                    "code": "length_mismatch",
                    "message": f"篇幅 {len(content)} 字，渠道建议 {lo}~{hi} 字",
                    "detail": f"{spec['label']}的{spec['genre']}体裁对篇幅敏感",
                }
            )

    score = round(hit_w / total_w, 3)
    fatal = [i for i in issues if i["level"] == "fatal"]
    return {
        "pass": not fatal,
        "score": score,
        "chars": len(content),
        "issues": issues,
        "satisfied": satisfied,
        "missing_required": missing_required,
        "supported_numbers": [t for t in numbers_in(body) if number_supported(t, corpus)],
    }
