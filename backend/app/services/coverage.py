"""
覆盖度预演（关键词匹配版）：投喂前检查"我的内容对目标问题覆盖够不够"。
覆盖低 = 模型即使搜到也没东西可引 → 优先补内容。
后续可升级为 embedding(bge-small-zh) 语义版。
"""
from typing import List
from app.models import CoverageCheck, Question, StandardAnswer   # ← 改这里：绝对导入 + 去掉 FeedRecord


def _extract_keywords(question: str, brand: str, category_words: List[str]) -> List[str]:
    """抽关键检查词：品牌词 + 品类词 + 问题里的实体词（简化版）。"""
    kws = [brand]
    kws += [w for w in category_words if w in question]
    return list(dict.fromkeys(kws))  # 去重保序


def check_coverage(question: Question, brand: str,
                   category_words: List[str]) -> CoverageCheck:
    """用"我的已投喂内容 + 标准答案句"去覆盖问题关键词。"""
    kws = _extract_keywords(question.text, brand, category_words)

    contents = []
    sa = question.standard_answer
    if sa:
        contents.append(sa.answer_text)
    feeds = question.feeds or []          # 依赖 models.py 里的 feeds 关系
    contents += [f.content for f in feeds]

    merged = "\n".join(contents)
    matched = [k for k in kws if k in merged]
    gap = [k for k in kws if k not in merged]

    score = round(len(matched) / len(kws), 2) if kws else 0.0
    return CoverageCheck(
        question_id=question.id,
        coverage_score=score,
        matched_keywords="、".join(matched),
        gap_keywords="、".join(gap),
        gap_note=f"缺失 {len(gap)} 个关键覆盖词：{'、'.join(gap)}，建议优先补内容" if gap
                 else "覆盖充分，可投喂",
    )
