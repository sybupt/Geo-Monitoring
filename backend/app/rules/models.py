"""规则库与内容资产的持久化。"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from app.database import Base


class RuleRow(Base):
    """规则库一行 = 一条规则。按 (engine, industry, channel, dimension) 唯一。"""

    __tablename__ = "rule_library"

    id = Column(Integer, primary_key=True)
    engine = Column(String, index=True)          # shared / doubao / kimi …
    industry = Column(String, index=True)        # local_life …
    channel = Column(String, default="", index=True)  # 空 = 不限渠道
    dimension = Column(String, index=True)
    instruction = Column(Text, default="")
    weight = Column(Float, default=0.5)
    evidence_n = Column(Integer, default=0)
    lift = Column(Float, default=0.0)
    source = Column(String, default="prior")      # prior / measured / manual
    scope = Column(String, default="engine")      # shared / engine
    note = Column(Text, default="")
    version = Column(Integer, default=1)
    updated_at = Column(DateTime, default=datetime.utcnow)


Index("ix_rule_key", RuleRow.engine, RuleRow.industry, RuleRow.channel, RuleRow.dimension, unique=True)


class ContentRow(Base):
    """按渠道/引擎生成并校验过的投喂稿。校验不通过的不许进入投喂队列。"""

    __tablename__ = "content_assets"

    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, index=True)
    engine = Column(String, index=True)
    channel = Column(String, index=True)
    question = Column(Text, default="")
    title = Column(String, default="")
    content = Column(Text, default="")
    rule_hits = Column(Text, default="[]")        # JSON，命中了哪些规则、怎么落地的
    validation = Column(Text, default="{}")       # JSON，校验报告
    score = Column(Float, default=0.0)
    status = Column(String, default="draft")      # draft / passed / rejected / fed
    created_at = Column(DateTime, default=datetime.utcnow)


class SimDoc(Base):
    """模拟语料：仅用于无 Key 时演示闭环，**不是真实投放**。

    报表里凡是基于这张表得出的数字都要标 ``SIMULATION``，不能对外说成真实引用提升。
    """

    __tablename__ = "sim_docs"

    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, index=True)
    engine = Column(String, index=True)
    channel = Column(String, index=True)
    title = Column(String, default="")
    content = Column(Text, default="")
    url = Column(String, default="")
    quality = Column(Float, default=0.0)          # 校验得分 0~1，决定被「引用」的概率
    content_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
