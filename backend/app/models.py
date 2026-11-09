from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base


class Business(Base):
    __tablename__ = "businesses"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    alias = Column(String)
    region = Column(String)
    category = Column(String)
    description = Column(Text)
    selling_points = Column(Text)
    evidence = Column(Text)
    website = Column(String)
    plan_key = Column(String, default="trial")
    subscription_status = Column(String, default="trial")
    subscription_expires_at = Column(DateTime)
    contact_name = Column(String)
    contact_phone = Column(String)
    contact_email = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    text = Column(Text)
    category = Column(String)
    region = Column(String)
    # ↓ 新增：关联诊断/覆盖/标准答案句/投喂内容
    diagnoses = relationship("ReverseDiagnosis", backref="question")
    coverage_checks = relationship("CoverageCheck", backref="question")
    standard_answer = relationship("StandardAnswer", backref="question", uselist=False)
    feeds = relationship("FeedingAsset", backref="question")


class QuestionSet(Base):
    __tablename__ = "question_sets"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"))
    version = Column(Integer, default=1)
    status = Column(String, default="draft")
    source = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    items = relationship("QuestionSetItem", backref="question_set", cascade="all, delete-orphan")


class QuestionSetItem(Base):
    __tablename__ = "question_set_items"
    id = Column(Integer, primary_key=True)
    question_set_id = Column(Integer, ForeignKey("question_sets.id"))
    text = Column(Text)
    category = Column(String)


class MonitorRun(Base):
    __tablename__ = "monitor_runs"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"))
    engine = Column(String)
    status = Column(String, default="pending")
    stage = Column(String, default="legacy")
    error = Column(Text)
    question_set_id = Column(Integer, ForeignKey("question_sets.id"))
    created_at = Column(DateTime, default=datetime.utcnow)
    question_set = relationship("QuestionSet")
    results = relationship("MonitorResult", backref="run")


class MonitorResult(Base):
    __tablename__ = "monitor_results"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("monitor_runs.id"))
    question_id = Column(Integer, ForeignKey("questions.id"))
    question_text = Column(Text)
    question_set_item_id = Column(Integer, ForeignKey("question_set_items.id"))
    mentioned = Column(Integer, default=0)
    snippet = Column(Text)
    score = Column(Float, default=0.0)
    raw_answer = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Citation(Base):
    __tablename__ = "citations"
    id = Column(Integer, primary_key=True)
    result_id = Column(Integer, ForeignKey("monitor_results.id"))
    source_url = Column(String)
    source_domain = Column(String)
    position = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)


class FeedingPlan(Base):
    __tablename__ = "feeding_plans"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"))
    name = Column(String)
    brief = Column(Text, default="{}")
    status = Column(String, default="draft")
    created_at = Column(DateTime, default=datetime.utcnow)
    assets = relationship("FeedingAsset", backref="plan")


class FeedingAsset(Base):
    __tablename__ = "feeding_assets"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("feeding_plans.id"))
    # ↓ 新增：内容挂到问题 + 一致性打分
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=True)
    consistency_score = Column(Float, nullable=True)
    # ↓ 原有字段
    channel = Column(String)
    title = Column(String)
    content = Column(Text)
    citability = Column(Text, default=None)
    fingerprints = Column(Text, default=None)
    status = Column(String, default="draft")
    published_url = Column(String)
    published_at = Column(DateTime)
    images = relationship("FeedingImage", backref="asset")


class FeedingImage(Base):
    __tablename__ = "feeding_images"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("feeding_assets.id"))
    stored_name = Column(String)
    original_name = Column(String)
    content_type = Column(String)
    alt_text = Column(String)


class EffectReport(Base):
    __tablename__ = "effect_reports"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"))
    feeding_plan_id = Column(Integer, ForeignKey("feeding_plans.id"))
    baseline_run_id = Column(Integer)
    after_run_id = Column(Integer)
    title = Column(String)
    snapshot = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class Platform(Base):
    __tablename__ = "platforms"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    label = Column(String)
    authority = Column(Integer)
    difficulty = Column(Integer)
    score = Column(Float)
    ai_cite_weight = Column(Float, default=0)
    publish_url = Column(String)
    sort_order = Column(Integer, default=0)


class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)
    label = Column(String)
    annual_price = Column(Integer)
    billing_cycle = Column(String, default="annual")
    platform_keys = Column(Text)
    description = Column(String)
    sort_order = Column(Integer, default=0)


# ══════════════ 以下为新增三张表 ══════════════

class ReverseDiagnosis(Base):
    __tablename__ = "reverse_diagnosis"
    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id"))
    model_name = Column(String)
    brand_name = Column(String)
    current_mentioned = Column(Text)      # 当前答案里提到了谁
    missing_reason = Column(String)       # 信源缺失/权重不足/地域标签错乱/竞品挤压
    raw_answer = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class CoverageCheck(Base):
    __tablename__ = "coverage_checks"
    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id"))
    coverage_score = Column(Float)        # 0~1
    matched_keywords = Column(Text)
    gap_keywords = Column(Text)
    gap_note = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class StandardAnswer(Base):
    __tablename__ = "standard_answers"
    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id"), unique=True)
    brand_name = Column(String)
    answer_text = Column(Text)            # 标准答案句：定义+数据+结论
    created_at = Column(DateTime, default=datetime.utcnow)
