"""v2 数据模型：多采样、渠道账号、发布任务、人工抽检。

设计要点：
- **一次采样一行**（ProbeSample），不做「一次跑批一行」的聚合。聚合随时能算，原始回答算不回来。
- 每条采样都带 ``tier`` / ``citation_type`` / ``model_version``，报表才能分口径呈现。
- 所有表都有 ``business_id``，它是租户隔离边界——我们自己服务多个客户时靠它隔数据。
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text

from app.database import Base


class ProbeBatch(Base):
    __tablename__ = "v2_probe_batches"

    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), index=True)
    stage = Column(String, default="baseline")  # baseline / after / adhoc
    engines = Column(Text, default="[]")        # JSON list
    competitors = Column(Text, default="[]")    # JSON list，判定与份额统计用
    question_set_id = Column(Integer, nullable=True)
    n_samples = Column(Integer, default=5)
    judge_mode = Column(String, default="per_sample")  # per_sample / rule_only
    status = Column(String, default="pending")  # pending / running / done / failed / partial
    total_tasks = Column(Integer, default=0)
    done_tasks = Column(Integer, default=0)
    failed_tasks = Column(Integer, default=0)
    note = Column(Text, default="")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)


class ProbeSample(Base):
    __tablename__ = "v2_probe_samples"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("v2_probe_batches.id"), index=True)
    business_id = Column(Integer, index=True)
    engine = Column(String, index=True)
    question_key = Column(String, index=True)
    question_text = Column(Text)
    intent = Column(String, default="")
    is_control = Column(Integer, default=0)
    sample_idx = Column(Integer, default=0)

    tier = Column(String, default="L1")
    citation_type = Column(String, default="none")
    memory_mode = Column(Integer, default=0)
    model = Column(String, default="")
    model_version = Column(String, default="unknown")

    ok = Column(Integer, default=1)
    error = Column(Text, default="")
    answer = Column(Text, default="")
    latency_ms = Column(Integer, default=0)

    mentioned = Column(Integer, default=0)
    is_recommended = Column(Integer, default=0)
    first_rank = Column(Integer)
    sentiment = Column(Integer, default=0)
    judge_method = Column(String, default="rule")
    judge_reason = Column(Text, default="")
    competitors = Column(Text, default="[]")
    search_queries = Column(Text, default="[]")
    citations = Column(Text, default="[]")
    usage = Column(Text, default="{}")

    created_at = Column(DateTime, default=datetime.utcnow)


Index("ix_v2_samples_batch_engine", ProbeSample.batch_id, ProbeSample.engine)
Index("ix_v2_samples_batch_q", ProbeSample.batch_id, ProbeSample.question_key)


class ChannelAccount(Base):
    """渠道账号绑定。只存凭据引用，不存明文密码/cookie。"""

    __tablename__ = "v2_channel_accounts"

    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), index=True)
    channel_key = Column(String, index=True)
    account_name = Column(String, default="")
    bind_mode = Column(String, default="guide")  # api / rpa / guide
    status = Column(String, default="active")    # active / expired / unbound
    credential_ref = Column(String, default="")  # 指向密钥管理里的条目，不放明文
    note = Column(Text, default="")
    bound_at = Column(DateTime, default=datetime.utcnow)
    last_checked_at = Column(DateTime)


class PublishTask(Base):
    """投喂任务：三种模式——api 直发、rpa 代发、guide 引导用户去平台发。"""

    __tablename__ = "v2_publish_tasks"

    id = Column(Integer, primary_key=True)
    business_id = Column(Integer, ForeignKey("businesses.id"), index=True)
    asset_id = Column(Integer, ForeignKey("feeding_assets.id"), nullable=True)
    channel_key = Column(String, index=True)
    account_id = Column(Integer, ForeignKey("v2_channel_accounts.id"), nullable=True)
    mode = Column(String, default="guide")
    status = Column(String, default="ready")  # ready / awaiting_user / published / failed
    guide_url = Column(String, default="")
    payload = Column(Text, default="{}")      # 标题/正文/话题/图片，给前端一键复制
    published_url = Column(String, default="")
    published_at = Column(DateTime)
    indexed = Column(Integer, default=0)      # 是否已被搜索收录（≠被 AI 引用）
    indexed_checked_at = Column(DateTime)
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class JudgeAudit(Base):
    """人工抽检。用来算判定准确率与 Cohen's kappa，是「数据可信」的证据。"""

    __tablename__ = "v2_judge_audits"

    id = Column(Integer, primary_key=True)
    sample_id = Column(Integer, ForeignKey("v2_probe_samples.id"), index=True)
    human_mentioned = Column(Integer)
    human_sentiment = Column(Integer)
    human_rank = Column(Integer)
    agree = Column(Integer)
    note = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)
