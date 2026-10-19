from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, text
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
    created_at = Column(DateTime, default=datetime.utcnow)

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    text = Column(Text)
    category = Column(String)
    region = Column(String)

class MonitorRun(Base):
    __tablename__ = "monitor_runs"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"))
    engine = Column(String)
    question_set_id = Column(Integer, ForeignKey("question_sets.id"))
    stage = Column(String, default="after", nullable=False,
                    server_default=text("'legacy'"))
    status = Column(String, default="running")  # running/done/failed
    error = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    results = relationship("MonitorResult", backref="run")
    question_set = relationship("QuestionSet")

class MonitorResult(Base):
    __tablename__ = "monitor_results"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("monitor_runs.id"))
    question_id = Column(Integer, ForeignKey("questions.id"))
    question_set_item_id = Column(Integer, ForeignKey("question_set_items.id"))
    question_text = Column(Text)
    mentioned = Column(Integer, default=0)   # 0/1 是否被提到
    snippet = Column(Text)                   # 回答里提到企业的片段
    score = Column(Float, default=0.0)
    raw_answer = Column(Text)                # AI 完整回答（客户看真实情况）
    created_at = Column(DateTime, default=datetime.utcnow)


class QuestionSet(Base):
    __tablename__ = "question_sets"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String, default="draft", nullable=False)
    source = Column(String, default="generated", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    items = relationship("QuestionSetItem", backref="question_set", cascade="all, delete-orphan")


class QuestionSetItem(Base):
    __tablename__ = "question_set_items"
    id = Column(Integer, primary_key=True)
    question_set_id = Column(Integer, ForeignKey("question_sets.id"), nullable=False)
    text = Column(Text, nullable=False)
    category = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)


class FeedingPlan(Base):
    __tablename__ = "feeding_plans"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    name = Column(String, nullable=False)
    brief = Column(Text, nullable=False, default="{}")
    status = Column(String, default="draft", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    assets = relationship("FeedingAsset", backref="plan", cascade="all, delete-orphan")


class FeedingAsset(Base):
    __tablename__ = "feeding_assets"
    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("feeding_plans.id"), nullable=False)
    channel = Column(String, nullable=False)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String, default="draft", nullable=False)
    published_url = Column(String)
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    images = relationship(
        "FeedingImage",
        backref="asset",
        cascade="all, delete-orphan",
        order_by="FeedingImage.id",
    )


class FeedingImage(Base):
    __tablename__ = "feeding_images"
    id = Column(Integer, primary_key=True)
    asset_id = Column(Integer, ForeignKey("feeding_assets.id"), nullable=False)
    stored_name = Column(String, nullable=False, unique=True)
    original_name = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    alt_text = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class EffectReport(Base):
    __tablename__ = "effect_reports"
    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), nullable=False)
    feeding_plan_id = Column(Integer, ForeignKey("feeding_plans.id"))
    baseline_run_id = Column(Integer, ForeignKey("monitor_runs.id"), nullable=False)
    after_run_id = Column(Integer, ForeignKey("monitor_runs.id"), nullable=False)
    title = Column(String, nullable=False)
    snapshot = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
