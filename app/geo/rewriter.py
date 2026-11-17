"""规则驱动的确定性改写器（无需 API Key），并保留 LLM 改写适配层。

改写策略不是"重写全文"，而是对原文做结构手术：
  1) 清除绝对化/促销措辞（可逆、不改变事实）
  2) 抽取原文中最像"结论"的句子前移
  3) 把含数值的句子抽成「核心参数」列表
  4) 把适用/限制句抽成「适合与不适合」
  5) 补来源占位与参数版本行

所有新增文本都来自原文或显式占位，避免编造事实——这点与论文「preserve accuracy」的
约束一致。
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass

from ..config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, GEO_RULE_LABELS
from .analyzer import analyze, sentence_diff
from .rules import (
    BALANCE_MARKERS,
    CONCLUSION_MARKERS,
    HYPE_WORDS,
    SOURCE_MARKERS,
    UNIT_PATTERN,
    _paragraphs,
    _sentences,
)

HYPE_REPLACEMENTS = {
    "全网最低": "价格区间较低", "最低价": "较低价格", "第一名": "排名靠前",
    "第一品牌": "主流品牌", "最好": "较好", "最强": "较强", "最佳": "较优",
    "100%": "在测试条件下", "绝对": "通常", "秒杀": "促销", "限时": "活动期间",
    "抢购": "购买", "包治": "可缓解", "永久": "长期", "无敌": "表现突出",
    "史上": "历代中", "唯一": "较少见", "顶级": "高端", "闭眼入": "可优先考虑",
    "爆款": "销量较高", "疯抢": "关注度高", "仅此一次": "本期活动", "彻底解决": "明显改善",
    "零风险": "风险较低", "万能": "适用范围较广",
}

PARAM_ROW_LABELS = ("重量", "容量", "功率", "续航", "分辨率", "尺寸", "噪音", "能效", "保修", "接口", "频率", "材质")


@dataclass
class RewriteResult:
    before: str
    after: str
    before_gqs: float
    after_gqs: float
    fixed_rules: list[str]
    change_log: list[str]
    diff: list[dict]
    engine: str

    def to_dict(self) -> dict:
        return {
            "before": self.before,
            "after": self.after,
            "before_gqs": self.before_gqs,
            "after_gqs": self.after_gqs,
            "delta": round(self.after_gqs - self.before_gqs, 1),
            "fixed_rules": self.fixed_rules,
            "change_log": self.change_log,
            "diff": self.diff,
            "engine": self.engine,
        }


NOISE_MARKERS = (
    "随着", "近年来", "众所周知", "在当今", "如今", "总之", "扫码", "点击", "长按",
    "活动", "优惠", "下单", "抢购", "不亏", "闭眼", "相信", "我们相信", "轻松应对",
)


def _is_noise(sentence: str) -> bool:
    """营销铺垫/导流句：不能作为结论，也不该留在正文里充数。"""
    return any(m in sentence for m in NOISE_MARKERS)


def _strip_hype(text: str) -> tuple[str, list[str]]:
    log: list[str] = []
    out = text
    for bad, good in HYPE_REPLACEMENTS.items():
        if bad in out:
            out = out.replace(bad, good)
            log.append(f"去绝对化：「{bad}」→「{good}」")
    out = re.sub(r"[！!]{2,}", "。", out)
    if "！" in out or "!" in out:
        out = out.replace("！", "。").replace("!", "。")
        log.append("将感叹句改为陈述句")
    return out, log


def _pick(sentences: list[str], markers: tuple[str, ...], limit: int = 1) -> list[str]:
    return [s for s in sentences if any(m in s for m in markers)][:limit]


def _clean_bullet(s: str, max_len: int = 90) -> str:
    s = s.strip()
    s = re.sub(r"^#{1,4}\s*", "", s)
    s = s.replace("\n", " ").strip()
    s = re.sub(r"^[-·•*>\s]+", "", s)
    s = re.sub(r"^(那么|而且|同时|另外|此外)[，,]?", "", s)
    return s[:max_len] + ("…" if len(s) > max_len else "")


def rewrite_rule_based(text: str) -> RewriteResult:
    before_report = analyze(text)
    # 幂等保护：已达标的文案不该被"优化"成更差的结构，直接放行
    if before_report.gqs >= 85:
        return RewriteResult(
            before=text, after=text,
            before_gqs=before_report.gqs, after_gqs=before_report.gqs,
            fixed_rules=[],
            change_log=["内容质量已达标（GQS ≥ 85），跳过改写，避免破坏原有结构"],
            diff=[],
            engine="rule-based (no-op)",
        )
    base, log = _strip_hype(text)
    # 先把 markdown 小标题的 # 去掉，否则会被当成句子抽进要点
    base = re.sub(r"(?m)^\s*#{1,4}\s*", "", base)
    paras = _paragraphs(base)
    # 过滤掉标题式短句（无标点、长度很短），它们不是可抽取的要点
    sentences = [
        x for x in _sentences(base)
        if len(x) > 8 and re.search(r"[，。：:；]", x)
    ]

    # 1) 结论：优先"含结论标记且不是营销铺垫"的句子
    clean = [x for x in sentences if not _is_noise(x)]
    # 只接受真正带结论/适用性表述的句子；抽不出来就如实标注缺口，不硬凑
    conclusion_pool = (
        [x for x in clean if any(m in x for m in CONCLUSION_MARKERS) and len(x) <= 80]
        or [x for x in clean if "适合" in x and len(x) <= 80]
    )
    conclusion = _clean_bullet(conclusion_pool[0], 110) if conclusion_pool else "原文未给出可用结论，建议首段直接说明推荐意见与适用人群"

    # 2) 核心参数：含数值单位的句子
    param_seqs = [s for s in sentences if UNIT_PATTERN.search(s)]
    bullets = [_clean_bullet(s, 80) for s in param_seqs[:6]]
    if not bullets:
        bullets = ["（原文未提供可核验参数，需补充：重量 / 容量 / 功率 / 续航等具体数值）"]

    # 3) 适用与限制
    pro = [x for x in clean if any(m in x for m in ("适合", "推荐", "优势", "优点"))]
    con = [x for x in clean if any(m in x for m in BALANCE_MARKERS)]
    if not con:
        con = ["原文未说明限制（建议补充：不适合的场景与代价）"]

    # 4) 来源
    src = [x for x in clean if any(m in x for m in SOURCE_MARKERS)]
    src_line = _clean_bullet(src[0], 100) if src else "来源：待补充（建议引用官方参数页或第三方检测报告）"

    # 5) 主体：保留剩下的原始信息，压缩为原子段落
    used = set(conclusion_pool[:1]) | set(param_seqs[:6]) | set(pro[:1]) | set(con[:1]) | set(src[:1])
    rest = [x for x in clean if x not in used]
    body: list[str] = []
    buf: list[str] = []
    for s in rest:
        buf.append(re.sub(r"^[-·•*>\s]+", "", s).strip())
        if sum(len(x) for x in buf) >= 110:
            body.append(" ".join(buf))
            buf = []
    if buf:
        body.append(" ".join(buf))
    body = body[:6]

    suitable = "原文未明确适用人群（建议补充）"
    if pro and (not conclusion_pool or pro[0] != conclusion_pool[0]):
        suitable = _clean_bullet(pro[0], 70)

    parts = [
        f"【结论】{conclusion}",
        "参数版本：2026-11，具体以官方参数页为准。",
        "",
        "## 核心参数",
        *[f"- {b}" for b in bullets],
        "",
        "## 适合与不适合",
        f"- 适合：{suitable}",
        f"- 注意：{_clean_bullet(con[0], 70)}",
        "",
    ]
    if body:
        parts += ["## 说明", *[p for p in body if p.strip()], ""]
    parts += ["## 依据", f"- {src_line}"]

    after = "\n".join(parts).strip()
    after_report = analyze(after)

    fixed = [
        r.label for r, a in zip(before_report.rules, after_report.rules)
        if a.score - r.score >= 0.1
    ]
    log.insert(0, f"结论句前移并加「【结论】」标记")
    log.append(f"抽取 {len(bullets)} 条含数值的句子组成「核心参数」")
    log.append("新增「适合与不适合」与「依据」段")
    if before_report.gqs == 0:
        log.append("原文为空，已生成占位骨架")

    return RewriteResult(
        before=text,
        after=after,
        before_gqs=before_report.gqs,
        after_gqs=after_report.gqs,
        fixed_rules=fixed,
        change_log=log,
        diff=sentence_diff(text, after),
        engine="rule-based (offline)",
    )


LLM_PROMPT = """你是内容可见性优化器。以下文档将与其它来源一起被大语言模型用于回答用户问题，
模型会在答案的每一行标注来源。请在不改变事实、不新增未提供信息的前提下改写文档，
使其更容易被引用。

必须遵守的质量规则：
{rules}

只输出改写后的文档正文，不要解释。

原文：
{document}
"""


def rewrite_with_llm(text: str, rule_labels: list[str] | None = None) -> RewriteResult | None:
    """接入 OpenAI 兼容接口；未配置 Key 时返回 None，由上层回退到规则改写。"""
    if not LLM_API_KEY:
        return None
    rules = rule_labels or list(GEO_RULE_LABELS.values())
    payload = {
        "model": LLM_MODEL,
        "temperature": 0.3,
        "messages": [
            {"role": "user", "content": LLM_PROMPT.format(rules="- " + "\n- ".join(rules), document=text)}
        ],
    }
    req = urllib.request.Request(
        f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {LLM_API_KEY}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        after = data["choices"][0]["message"]["content"].strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[rewriter] LLM 调用失败，回退规则改写：{exc}")
        return None
    before_report, after_report = analyze(text), analyze(after)
    return RewriteResult(
        before=text,
        after=after,
        before_gqs=before_report.gqs,
        after_gqs=after_report.gqs,
        fixed_rules=[r.label for r, a in zip(before_report.rules, after_report.rules) if a.score - r.score >= 0.1],
        change_log=["通过 LLM 按 AutoGEO 规则整篇改写"],
        diff=sentence_diff(text, after),
        engine=f"llm:{LLM_MODEL}",
    )


def rewrite(text: str, use_llm: bool = True) -> RewriteResult:
    if use_llm:
        result = rewrite_with_llm(text)
        if result is not None:
            return result
    return rewrite_rule_based(text)
