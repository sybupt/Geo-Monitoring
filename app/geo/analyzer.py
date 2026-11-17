"""内容体检：把规则结果聚合成 GQS 分数、等级与可执行改进清单。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .rules import RuleResult, evaluate_rules, gqs_from_results

GRADE_BANDS = ((85, "A", "已具备强可见性"), (70, "B", "接近达标"), (55, "C", "有明显缺口"), (0, "D", "不利于被引用"))


@dataclass
class GeoReport:
    gqs: float
    grade: str
    grade_label: str
    rules: list[RuleResult]
    sub_scores: dict[str, float] = field(default_factory=dict)
    top_fixes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "gqs": self.gqs,
            "grade": self.grade,
            "grade_label": self.grade_label,
            "rules": [r.to_dict() for r in self.rules],
            "sub_scores": self.sub_scores,
            "top_fixes": self.top_fixes,
        }


def _grade(gqs: float) -> tuple[str, str]:
    for lower, grade, label in GRADE_BANDS:
        if gqs >= lower:
            return grade, label
    return "D", "不利于被引用"


def analyze(text: str, top_n: int = 4) -> GeoReport:
    results = evaluate_rules(text)
    gqs = gqs_from_results(results)
    grade, label = _grade(gqs)
    # 改进优先级 = (1 - score) * weight
    ranked = sorted(results, key=lambda r: (1 - r.score) * r.weight, reverse=True)
    fixes = [f"{r.label}：{r.hint}" for r in ranked if r.score < 0.75][:top_n]
    if not fixes:
        fixes = ["已达标，无需大改，可补充最新参数版本"]
    return GeoReport(
        gqs=gqs,
        grade=grade,
        grade_label=label,
        rules=results,
        sub_scores={r.key: r.score for r in results},
        top_fixes=fixes,
    )


def content_tokens(text: str) -> list[str]:
    """轻量中文分词：2-gram 切分，用于内容召回与 TF-IDF。"""
    cleaned = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]+", " ", text or "")
    tokens: list[str] = []
    for chunk in cleaned.split():
        if re.match(r"^[a-zA-Z0-9]+$", chunk):
            tokens.append(chunk.lower())
        else:
            tokens.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return tokens


def sentence_diff(before: str, after: str, limit: int = 2000) -> list[dict]:
    """基于行/句的粗粒度 diff，供前端做改写前后对比。"""
    a = [s.strip() for s in re.split(r"(?<=[。！？\n])", before) if s.strip()]
    b = [s.strip() for s in re.split(r"(?<=[。！？\n])", after) if s.strip()]
    set_a, set_b = set(a), set(b)
    rows: list[dict] = []
    for s in a:
        if s not in set_b:
            rows.append({"type": "del", "text": s[:limit]})
    for s in b:
        if s not in set_a:
            rows.append({"type": "add", "text": s[:limit]})
    return rows
