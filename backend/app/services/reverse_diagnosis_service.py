"""逆向诊断：对每个目标问题，问模型"现在提了谁"，并推断为什么没提我的品牌。"""
from typing import List, Optional
from app.models import ReverseDiagnosis, Question
from app.database import SessionLocal
from app.services.monitor_service import ask_text   # 复用现成函数，不另写 query_model

REASON = {
    "source_missing": "信源缺失",
    "low_weight":    "权重不足",
    "geo_mismatch":  "地域标签错乱",
    "competitor":    "竞品挤压",
}


def _classify_reason(answer: str, brand: str, competitors: List[str],
                     geo_keywords: Optional[List[str]] = None) -> str:
    if any(c in answer for c in competitors):
        return "competitor"
    if geo_keywords and any(g in answer for g in geo_keywords) and brand not in answer:
        return "geo_mismatch"
    if len(answer.strip()) < 30:
        return "low_weight"
    return "source_missing"


def diagnose_question(question: Question, brand: str, competitors: List[str],
                      model_name: str = "kimi_search",
                      geo_keywords: Optional[List[str]] = None) -> ReverseDiagnosis:
    answer = ask_text(model_name, question.text)
    mentioned = [c for c in competitors if c in answer]
    reason_code = _classify_reason(answer, brand, competitors, geo_keywords)
    return ReverseDiagnosis(
        question_id=question.id,
        model_name=model_name,
        brand_name=brand,
        current_mentioned="、".join(mentioned) or "（未提及任何竞品/品牌）",
        missing_reason=REASON[reason_code],
        raw_answer=answer[:2000],
    )
