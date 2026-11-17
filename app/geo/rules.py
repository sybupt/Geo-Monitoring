"""AutoGEO 规则的可执行化移植。

原论文的流程是：成对文档比较 -> LLM 解释胜出原因 -> 提炼自然语言规则 -> 规则写进
改写 prompt。这套流程的产物是「人可读、但机器不可算」的规则，无法直接进推荐排序。

本模块把规则降维成 10 个可确定性计算的检查器（checker），每条规则输出
score ∈ [0,1] + evidence + hint，从而让 GEO 偏好成为推荐系统的数值特征。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Callable

from ..config import GEO_RULE_WEIGHTS, GEO_RULE_LABELS

# ---------------------------------------------------------------- 词表 ----
CONCLUSION_MARKERS = (
    "结论", "总结", "一句话", "直接说", "建议", "推荐", "首选", "适合",
    "核心优势", "总的来说", "重点是", "答案是", "选购要点",
)
BACKGROUND_MARKERS = ("随着", "近年来", "众所周知", "在当今", "如今", "不知道大家")

HYPE_WORDS = (
    "全网最低", "最低价", "第一名", "第一品牌", "最好", "最强", "最佳", "100%",
    "绝对", "秒杀", "限时", "抢购", "包治", "永久", "无敌", "史上", "唯一",
    "顶级", "绝无仅有", "史无前例", "闭眼入", "爆款", "疯抢", "仅此一次",
    "彻底解决", "零风险", "万能",
)

SOURCE_MARKERS = (
    "来源", "据", "检测报告", "官方参数", "官方数据", "第三方", "认证", "标准",
    "实测", "抽样", "白皮书", "实验室", "国标", "GB/T", "数据来自", "引用",
)

EXTERNAL_DEPS = ("扫码", "点击查看", "详见图片", "如图", "见下文链接", "点击链接", "长按识别", "见官网链接")

BALANCE_MARKERS = ("缺点", "不足", "不适合", "需要注意", "局限", "适用", "不推荐", "代价", "权衡", "短板")

TERM_MARKERS = ("即", "也就是", "指的是", "是指", "（", "(")

UNIT_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:mm|cm|km|kg|g|mg|ml|mL|L|W|kW|V|mAh|Wh|GB|TB|MB|"
    r"英寸|寸|小时|分钟|秒|天|年|个月|元|块|%|℃|°C|dB|Hz|kHz|MP|万像素|核|档|层|片|支|包)"
)
PARAM_WORDS = ("重量", "容量", "功率", "续航", "分辨率", "材质", "尺寸", "噪音", "能效", "保修", "参数", "型号", "接口", "频率")

HEADING_PATTERN = re.compile(r"(?m)^(?:\s{0,3}#{1,4}\s+\S|\s*[一二三四五六七八九十]+[、.]\s*\S|\s*【[^】]{2,20}】)")
LIST_PATTERN = re.compile(r"(?m)^\s*(?:[-*·•]\s+\S|\d{1,2}[.)、]\s*\S|[①-⑩]\s*\S)")
TABLE_PATTERN = re.compile(r"(?m)^\s*\|.*\|.*$")


@dataclass
class RuleResult:
    key: str
    label: str
    score: float
    evidence: str
    hint: str
    weight: float

    def to_dict(self) -> dict:
        return asdict(self)


def _paragraphs(text: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in text.split("\n") if p.strip()]
    return parts or [text.strip()]


def _sentences(text: str) -> list[str]:
    # 换行也要切：否则"小标题\n- 参数：27英寸"会被当成一个句子，污染要点抽取
    parts = re.split(r"(?<=[。！？!?；;])|\n+", text)
    return [s.strip() for s in parts if s.strip()]


# ------------------------------------------------------------ 10 个检查器 --
def check_conclusion_first(text: str, paras: list[str]) -> tuple[float, str, str]:
    head = "\n".join(paras[:2])[:220]
    hits = [m for m in CONCLUSION_MARKERS if m in head]
    bg = [m for m in BACKGROUND_MARKERS if m in head]
    if hits and not bg:
        return 1.0, f"开篇命中结论标记：{'、'.join(hits[:3])}", "保持结论前置的写法"
    if hits:
        return 0.6, f"开篇有结论词（{'、'.join(hits[:2])}），但被背景铺垫稀释", "把背景段移到结论之后，首段只讲结论"
    if bg:
        return 0.15, f"开篇是背景铺垫（{bg[0]}），核心结论被推后", "首段直接给出一句话结论，再展开理由"
    return 0.35, "开篇未出现明确结论表述", "首段补充「一句话结论」或推荐意见"


def check_structured(text: str, paras: list[str]) -> tuple[float, str, str]:
    heads = HEADING_PATTERN.findall(text)
    lists = LIST_PATTERN.findall(text)
    tables = TABLE_PATTERN.findall(text)
    score = min(1.0, 0.34 * len(heads) + 0.16 * min(len(lists), 4) + 0.5 * min(len(tables), 1))
    if score >= 0.8:
        return 1.0, f"结构完整：{len(heads)} 个小标题 / {len(lists)} 条列表 / {len(tables)} 行表格", "结构已达标"
    if score >= 0.4:
        return round(score, 2), f"有部分结构：{len(heads)} 个小标题 / {len(lists)} 条列表", "补齐「核心要点」列表与参数表"
    return round(max(score, 0.05), 2), "整段纯文字，无小标题、列表或表格", "拆成 3-5 个小标题 + 要点列表"


def check_quantified(text: str, paras: list[str]) -> tuple[float, str, str]:
    text = "\n".join(ln for ln in text.splitlines() if not any(m in ln for m in PLACEHOLDER_MARKERS))
    units = UNIT_PATTERN.findall(text)
    params = [w for w in PARAM_WORDS if w in text]
    ratio = len(units) / max(len(_sentences(text)), 1)
    score = min(1.0, 0.5 * min(len(units) / 6, 1) + 0.3 * min(len(params) / 4, 1) + 0.2 * min(ratio * 6, 1))
    if score >= 0.75:
        return 1.0, f"含 {len(units)} 处带单位数值、{len(params)} 类参数词", "量化已充分"
    if score >= 0.35:
        return round(score, 2), f"含 {len(units)} 处数值，参数覆盖 {len(params)} 类", "补充续航/重量/尺寸等关键参数的具体数值"
    return round(max(score, 0.05), 2), "几乎全是形容词，缺少可核验的具体数值", "把「很好/很强」替换成具体参数与实测数据"


PLACEHOLDER_MARKERS = ("待补充", "建议引用", "建议补充", "需补充", "待填", "TODO", "待完善")


def check_sourced(text: str, paras: list[str]) -> tuple[float, str, str]:
    # 占位符不算证据：改写器留下的"来源：待补充"不能拿来骗分
    evidence_lines = [ln for ln in text.splitlines() if any(m in ln for m in SOURCE_MARKERS)]
    real_lines = [ln for ln in evidence_lines if not any(m in ln for m in PLACEHOLDER_MARKERS)]
    if evidence_lines and not real_lines:
        return 0.05, "仅存在来源占位（待补充），未提供可核验出处", "用真实来源替换占位行"
    hits = [m for m in SOURCE_MARKERS if any(m in ln for ln in real_lines)]
    if len(hits) >= 3:
        return 1.0, f"来源标注充分：{'、'.join(hits[:4])}", "来源可信度已达标"
    if hits:
        return 0.55, f"提到来源（{'、'.join(hits[:3])}），但未指明具体出处", "为关键结论补「据 XX 检测报告/官方参数页」"
    return 0.05, "全文无任何来源或实测依据", "给核心结论补 1-2 个可核验来源"


def check_atomic(text: str, paras: list[str]) -> tuple[float, str, str]:
    body = [p for p in paras if not HEADING_PATTERN.match(p)]
    if not body:
        return 0.4, "无可评估正文段落", "补充正文内容"
    long_paras = [p for p in body if len(p) > 260]
    avg = sum(len(p) for p in body) / len(body)
    multi_idea = sum(1 for p in body if p.count("。") >= 4)
    score = 1.0
    if long_paras:
        score -= 0.35
    if avg > 180:
        score -= 0.25
    if multi_idea / len(body) > 0.4:
        score -= 0.25
    score = max(score, 0.05)
    evidence = f"{len(body)} 个正文段落，平均 {avg:.0f} 字，超长段 {len(long_paras)} 个，多要点段 {multi_idea} 个"
    hint = "每段压缩到 1 个要点、120 字以内" if score < 0.8 else "段落粒度合适"
    return round(score, 2), evidence, hint


def check_no_hype(text: str, paras: list[str]) -> tuple[float, str, str]:
    hits = [w for w in HYPE_WORDS if w in text]
    bangs = text.count("！") + text.count("!")
    score = 1.0 - min(0.16 * len(hits), 0.75) - min(0.06 * bangs, 0.2)
    score = max(round(score, 2), 0.05)
    if not hits and bangs == 0:
        return 1.0, "无绝对化用语、无营销感叹句", "表述客观"
    return score, f"绝对化/促销用语 {len(hits)} 处（{'、'.join(hits[:4])}），感叹号 {bangs} 个", "把绝对化措辞改为有条件的事实描述"


def check_self_contained(text: str, paras: list[str]) -> tuple[float, str, str]:
    deps = [w for w in EXTERNAL_DEPS if w in text]
    length_factor = min(len(text) / 320, 1.0)
    score = max(0.0, (0.65 * length_factor + 0.35) - 0.3 * len(deps))
    score = max(round(score, 2), 0.05)
    if not deps and length_factor > 0.8:
        return 1.0, "核心信息在正文内闭环", "自包含性良好"
    return score, f"依赖外部内容 {len(deps)} 处，正文信息量约 {len(text)} 字", "把外链/图片里的关键信息直接写进正文"


def check_term_defined(text: str, paras: list[str]) -> tuple[float, str, str]:
    parens = len(re.findall(r"[（(][^（()）]{2,30}[)）]", text))
    markers = sum(1 for m in ("即", "也就是", "指的是", "是指") if m in text)
    score = min(1.0, 0.45 + 0.18 * min(parens, 3) + 0.15 * min(markers, 2)) if (parens or markers) else 0.5
    if not any(k in text for k in ("电机", "电池", "mAh", "降噪", "能效", "分辨率", "材质")):
        return round(min(score + 0.15, 1.0), 2), "无高门槛术语，阅读无门槛", "保持通俗表述"
    if parens or markers:
        return round(score, 2), f"术语带解释 {parens + markers} 处", "术语解释已到位"
    return 0.4, "出现专业术语但未解释", "首次出现的参数/术语补一句通俗解释"


def check_timeliness(text: str, paras: list[str]) -> tuple[float, str, str]:
    years = re.findall(r"(20\d{2})\s*(?:年|[-/.]\d{1,2})", text)
    marks = [w for w in ("更新", "版本", "截至", "现行", "本月") if w in text]
    if years or marks:
        return 1.0, f"出现时间信息：{'、'.join(sorted(set(years))[:2]) or marks[0]}", "时效标注已具备"
    return 0.2, "无更新时间或参数版本标注", "补一行「参数版本 / 最后更新日期」"


def check_balanced(text: str, paras: list[str]) -> tuple[float, str, str]:
    real = "\n".join(ln for ln in text.splitlines() if not any(m in ln for m in PLACEHOLDER_MARKERS))
    hits = [m for m in BALANCE_MARKERS if m in real]
    has_pro = any(w in real for w in ("优势", "优点", "适合", "推荐"))
    if len(hits) >= 2 and has_pro:
        return 1.0, f"同时给出优势与限制（{'、'.join(hits[:3])}）", "对比客观"
    if hits or has_pro:
        return 0.55, "只覆盖单侧信息（优点或限制之一）", "补「不适合谁 / 代价是什么」一段"
    return 0.15, "既无优势说明也无限制说明", "补适用人群与不适用场景"


CHECKS: dict[str, Callable[[str, list[str]], tuple[float, str, str]]] = {
    "conclusion_first": check_conclusion_first,
    "structured": check_structured,
    "quantified": check_quantified,
    "sourced": check_sourced,
    "atomic": check_atomic,
    "no_hype": check_no_hype,
    "self_contained": check_self_contained,
    "term_defined": check_term_defined,
    "timeliness": check_timeliness,
    "balanced": check_balanced,
}


def evaluate_rules(text: str) -> list[RuleResult]:
    """对单篇文本跑全部规则，返回逐条结果。"""
    paras = _paragraphs(text)
    results: list[RuleResult] = []
    for key, fn in CHECKS.items():
        score, evidence, hint = fn(text, paras)
        results.append(
            RuleResult(
                key=key,
                label=GEO_RULE_LABELS[key],
                score=round(float(score), 3),
                evidence=evidence,
                hint=hint,
                weight=GEO_RULE_WEIGHTS[key],
            )
        )
    return results


def gqs_from_results(results: list[RuleResult]) -> float:
    total = sum(r.weight for r in results) or 1.0
    return round(sum(r.score * r.weight for r in results) / total * 100, 1)
