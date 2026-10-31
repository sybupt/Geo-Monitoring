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
    # ===== 会员字段 =====
    plan_key = Column(String, default="trial")
    subscription_status = Column(String, default="trial")  # trial/active/expired/suspended
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


class QuestionSet(Base):
    __tablename__ = "question_sets"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"))
    version = Column(Integer, default=1)
    status = Column(String, default="draft")   # draft / active / archived
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
    engine = Column(String)          # provider_id：kimi_search / deepseek
    status = Column(String, default="pending")  # pending / running / done / failed
    stage = Column(String, default="legacy")    # baseline / after / legacy
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


class FeedingPlan(Base):
    __tablename__ = "feeding_plans"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"))
    name = Column(String)
    brief = Column(Text, default="{}")
    status = Column(String, default="draft")   # draft / in_progress / published
    created_at = Column(DateTime, default=datetime.utcnow)
    assets = relationship("FeedingAsset", backref="plan")


class FeedingAsset(Base):
    __tablename__ = "feeding_assets"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("feeding_plans.id"))
    channel = Column(String)
    title = Column(String)
    content = Column(Text)
    status = Column(String, default="draft")   # draft / reviewed / published
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
    key = Column(String, unique=True)      # wechat_personal / wechat / zhihu ...
    label = Column(String)                 # 中文名
    authority = Column(Integer)            # 权威度 1-10
    difficulty = Column(Integer)           # 发布难度 1-10
    score = Column(Float)                  # 综合分 = 0.6*authority + 0.4*difficulty
    publish_url = Column(String)           # 发布入口 URL
    sort_order = Column(Integer, default=0)


class Plan(Base):
    __tablename__ = "plans"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True)      # trial / basic / standard / premium
    label = Column(String)
    annual_price = Column(Integer)         # 年费（元）
    billing_cycle = Column(String, default="annual")   # annual / monthly（预留）
    platform_keys = Column(Text)           # JSON 数组
    description = Column(String)
    sort_order = Column(Integer, default=0)
