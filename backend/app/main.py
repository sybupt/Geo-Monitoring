from datetime import datetime
from html import escape
from io import BytesIO
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, inspect
from starlette.responses import FileResponse, Response

from app.database import engine, Base, SessionLocal
import app.models
from app.models import (
    Business, EffectReport, FeedingAsset, FeedingImage, FeedingPlan,
    MonitorRun, MonitorResult, Question, QuestionSet, QuestionSetItem,
    Platform, Plan,
)

from app.services.monitor_service import (
    PROVIDERS,
    create_monitor_runs,
    list_providers,
    questions_for_business,
    run_monitor,
)
from app.services.feeding_service import CHANNEL_LABELS, build_assets
from app.services.question_service import generate_questions


Base.metadata.create_all(bind=engine)
IMAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "feeding_images"
MAX_IMAGE_SIZE = 10 * 1024 * 1024


def ensure_feeding_plan_columns():
    columns = {column["name"] for column in inspect(engine).get_columns("feeding_plans")}
    if "brief" not in columns:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE feeding_plans ADD COLUMN brief TEXT NOT NULL DEFAULT '{}'"
            )


ensure_feeding_plan_columns()


def ensure_monitor_run_columns():
    columns = {column["name"] for column in inspect(engine).get_columns("monitor_runs")}
    with engine.begin() as connection:
        if "stage" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE monitor_runs ADD COLUMN stage VARCHAR NOT NULL DEFAULT 'legacy'"
            )
        if "error" not in columns:
            connection.exec_driver_sql("ALTER TABLE monitor_runs ADD COLUMN error TEXT")
        if "question_set_id" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE monitor_runs ADD COLUMN question_set_id INTEGER"
            )


def ensure_monitor_result_columns():
    columns = {column["name"] for column in inspect(engine).get_columns("monitor_results")}
    with engine.begin() as connection:
        if "question_text" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE monitor_results ADD COLUMN question_text TEXT"
            )
        if "question_set_item_id" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE monitor_results ADD COLUMN question_set_item_id INTEGER"
            )


def ensure_business_columns():
    columns = {column["name"] for column in inspect(engine).get_columns("businesses")}
    additions = {
        "description": "TEXT",
        "selling_points": "TEXT",
        "evidence": "TEXT",
        "website": "VARCHAR",
        "plan_key": "VARCHAR DEFAULT 'trial'",
        "subscription_status": "VARCHAR DEFAULT 'trial'",
        "subscription_expires_at": "DATETIME",
        "contact_name": "VARCHAR",
        "contact_phone": "VARCHAR",
        "contact_email": "VARCHAR",
    }

    with engine.begin() as connection:
        for name, column_type in additions.items():
            if name not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE businesses ADD COLUMN {name} {column_type}"
                )


ensure_business_columns()
ensure_monitor_run_columns()
ensure_monitor_result_columns()

app = FastAPI(title="文旅 AI GEO")


class MonitorRequest(BaseModel):
    business_id: int = Field(gt=0)
    stage: Literal["baseline", "after"]
    providers: list[Literal["kimi_search", "deepseek"]] = Field(min_items=1)
    question_set_id: int | None = Field(default=None, gt=0)


class QuestionBatch(BaseModel):
    questions: list[str] = Field(min_items=1, max_items=100)
    region: str = ""
    category: str = ""


class BusinessInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    alias: str = ""
    region: str = ""
    category: str = ""
    description: str = ""
    selling_points: str = ""
    evidence: str = ""
    website: str = ""
    plan_key: str = "trial"
    subscription_status: str = "trial"
    subscription_expires_at: str = ""
    contact_name: str = ""
    contact_phone: str = ""
    contact_email: str = ""



class QuestionSetGenerate(BaseModel):
    business_id: int = Field(gt=0)
    provider: Literal["kimi_search", "deepseek"]
    count: int = Field(default=12, ge=3, le=30)


class QuestionInput(BaseModel):
    text: str = Field(min_length=2, max_length=500)
    category: str = Field(default="", max_length=100)


class QuestionSetApproval(BaseModel):
    questions: list[QuestionInput] = Field(min_items=1, max_items=100)


class FeedingPlanInput(BaseModel):
    business_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    provider: Literal["kimi_search", "deepseek"]
    channels: list[
        Literal[
            "rednote",
            "zhihu",
            "baidu",
            "google",
            "official_site",
            "government",
            "tourism_bureau",
            "wechat",
        ]
    ] = Field(min_items=1, max_items=8)
    business_keywords: str = Field(default="", max_length=2000)
    activity_name: str = Field(default="", max_length=500)
    promotional_points: str = Field(default="", max_length=3000)
    event_time: str = Field(default="", max_length=200)
    event_location: str = Field(default="", max_length=500)
    event_description: str = Field(default="", max_length=2000)


class FeedingAssetUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=20000)


class FeedingPublishInput(BaseModel):
    published_url: str = Field(min_length=1, max_length=2000)


class EffectReportInput(BaseModel):
    business_id: int = Field(gt=0)
    baseline_run_id: int = Field(gt=0)
    after_run_id: int = Field(gt=0)
    feeding_plan_id: int = Field(gt=0)
    title: str = Field(min_length=1, max_length=200)

def parse_datetime(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def business_data(business):
    return {
        "id": business.id,
        "name": business.name,
        "alias": business.alias,
        "region": business.region,
        "category": business.category,
        "description": business.description,
        "selling_points": business.selling_points,
        "evidence": business.evidence,
        "website": business.website,
        "plan_key": business.plan_key,
        "subscription_status": business.subscription_status,
        "subscription_expires_at": (
            business.subscription_expires_at.isoformat()
            if business.subscription_expires_at else None
        ),
        "contact_name": business.contact_name,
        "contact_phone": business.contact_phone,
        "contact_email": business.contact_email,

    }


def run_metrics(db, run):
    total = db.query(func.count(MonitorResult.id)).filter(
        MonitorResult.run_id == run.id
    ).scalar() or 0
    mentioned = db.query(func.count(MonitorResult.id)).filter(
        MonitorResult.run_id == run.id,
        MonitorResult.mentioned == 1,
    ).scalar() or 0
    return {
        "run_id": run.id,
        "stage": run.stage,
        "provider": run.engine,
        "question_set_id": run.question_set_id,
        "provider_name": PROVIDERS.get(run.engine, {}).get("label", run.engine),
        "status": run.status,
        "time": run.created_at.isoformat() if run.created_at else None,
        "total": total,
        "mentioned": mentioned,
        "mention_rate": round(mentioned / total * 100, 2) if total else None,
        "error": run.error,
    }


def question_set_data(question_set, include_questions=False):
    data = {
        "id": question_set.id,
        "business_id": question_set.business_id,
        "version": question_set.version,
        "status": question_set.status,
        "source": question_set.source,
        "created_at": (
            question_set.created_at.isoformat() if question_set.created_at else None
        ),
        "question_count": len(question_set.items),
    }
    if include_questions:
        data["questions"] = [
            {"id": item.id, "text": item.text, "category": item.category}
            for item in question_set.items
        ]
    return data


def feeding_plan_data(plan, include_content=False):
    brief = json.loads(plan.brief or "{}")
    if not isinstance(brief, dict):
        raise ValueError("投喂计划中的输入资料格式无效")
    data = {
        "id": plan.id,
        "business_id": plan.business_id,
        "name": plan.name,
        "campaign_input": brief,
        "status": plan.status,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "assets": [
            {
                "id": asset.id,
                "channel": asset.channel,
                "channel_name": CHANNEL_LABELS.get(asset.channel, asset.channel),
                "title": asset.title,
                "status": asset.status,
                "published_url": asset.published_url,
                "published_at": (
                    asset.published_at.isoformat() if asset.published_at else None
                ),
                "images": [
                    {
                        "id": image.id,
                        "filename": image.original_name,
                        "content_type": image.content_type,
                        "alt_text": image.alt_text or "",
                        "url": f"/api/feeding-assets/{asset.id}/images/{image.id}",
                    }
                    for image in asset.images
                ],
                **({"content": asset.content} if include_content else {}),
            }
            for asset in plan.assets
        ],
    }
    return data


def results_by_question(db, run):
    results = db.query(MonitorResult).filter(MonitorResult.run_id == run.id).all()
    old_question_ids = [row.question_id for row in results if row.question_id is not None]
    old_questions = {
        question.id: question.text
        for question in db.query(Question).filter(Question.id.in_(old_question_ids)).all()
    } if old_question_ids else {}
    return {
        result.question_text or old_questions.get(result.question_id, ""): result
        for result in results
    }


def report_html(snapshot):
    before_rate = snapshot["baseline"]["mention_rate"]
    after_rate = snapshot["after"]["mention_rate"]
    change = snapshot["change"]
    rows = "".join(
        "<tr>"
        f"<td>{escape(item['question'])}</td>"
        f"<td>{'提及' if item['baseline_mentioned'] else '未提及'}</td>"
        f"<td>{'提及' if item['after_mentioned'] else '未提及'}</td>"
        f"<td>{escape(item['baseline_answer'])}</td>"
        f"<td>{escape(item['after_answer'])}</td>"
        "</tr>"
        for item in snapshot["questions"]
    )
    publications = "".join(
        "<li>"
        f"{escape(item['channel_name'])}："
        f"{escape(item.get('published_url') or '未记录公开链接')}"
        "</li>"
        for item in snapshot.get("published_assets", [])
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{escape(snapshot['title'])}</title>
<style>
body{{font-family:Arial,'Microsoft YaHei',sans-serif;max-width:1100px;margin:40px auto;padding:0 20px;color:#182d2a}}
h1{{color:#116b55}} .metrics{{display:flex;gap:20px;margin:24px 0}}
.metric{{padding:18px;border:1px solid #dce6df;border-radius:12px;min-width:160px}}
table{{border-collapse:collapse;width:100%;margin-top:18px}} th,td{{border:1px solid #dce6df;padding:10px;text-align:left;vertical-align:top}}
th{{background:#edf5ef}} .note{{color:#62716a;font-size:14px}}
</style></head><body>
<h1>{escape(snapshot['title'])}</h1>
<p>{escape(snapshot['business']['name'])} · 检测模型：{escape(snapshot['provider_name'])}</p>
<p>报告生成时间：{escape(snapshot['created_at'])} · 问题集：{escape(snapshot['question_set'])}</p>
<div class="metrics">
<div class="metric">投喂前提及率<br><strong>{before_rate:.1f}%</strong><br>{snapshot['baseline']['mentioned']}/{snapshot['baseline']['total']} 题</div>
<div class="metric">投喂后提及率<br><strong>{after_rate:.1f}%</strong><br>{snapshot['after']['mentioned']}/{snapshot['after']['total']} 题</div>
<div class="metric">前后变化<br><strong>{change:+.1f} 个百分点</strong></div>
</div>
<p class="note">本报告展示所选模型、固定问题集上的观测差异，不构成投放效果的因果证明或未来推荐保证。</p>
<p>投喂前检测时间：{escape(snapshot['baseline']['time'] or '—')} · 投喂后检测时间：{escape(snapshot['after']['time'] or '—')}</p>
<p>关联投放计划：{escape(snapshot.get('feeding_plan_name') or '未关联')}</p>
<h2>已登记发布渠道</h2><ul>{publications or '<li>未关联已发布素材</li>'}</ul>
<h2>逐题检测结果</h2>
<table><thead><tr><th>用户问题</th><th>投喂前是否提及</th><th>投喂后是否提及</th><th>投喂前模型回答</th><th>投喂后模型回答</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""


@app.get("/")
def root():
    return {"status": "ok", "app": "文旅 AI GEO"}


@app.get("/api/businesses")
def list_businesses():
    db = SessionLocal()
    try:
        businesses = db.query(Business).order_by(Business.id.asc()).all()
        return [business_data(business) for business in businesses]
    finally:
        db.close()


@app.post("/api/businesses", status_code=201)
def create_business(request: BusinessInput):
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="企业名称不能为空")
    db = SessionLocal()
    try:
        business = Business(
            name=name,
            alias=request.alias.strip() or None,
            region=request.region.strip() or None,
            category=request.category.strip() or None,
            description=request.description.strip() or None,
            selling_points=request.selling_points.strip() or None,
            evidence=request.evidence.strip() or None,
            website=request.website.strip() or None,
            plan_key=request.plan_key or "trial",
            subscription_status=request.subscription_status or "trial",
            subscription_expires_at=parse_datetime(request.subscription_expires_at),
            contact_name=request.contact_name.strip() or None,
            contact_phone=request.contact_phone.strip() or None,
            contact_email=request.contact_email.strip() or None,
        )
        db.add(business)
        db.commit()
        db.refresh(business)
        return business_data(business)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.put("/api/businesses/{business_id}")
def update_business(business_id: int, request: BusinessInput):
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="企业名称不能为空")
    db = SessionLocal()
    try:
        business = db.get(Business, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="企业不存在")
        business.name = name
        business.alias = request.alias.strip() or None
        business.region = request.region.strip() or None
        business.category = request.category.strip() or None
        business.description = request.description.strip() or None
        business.selling_points = request.selling_points.strip() or None
        business.evidence = request.evidence.strip() or None
        business.website = request.website.strip() or None
        business.plan_key = request.plan_key or "trial"
        business.subscription_status = request.subscription_status or "trial"
        business.subscription_expires_at = parse_datetime(request.subscription_expires_at)
        business.contact_name = request.contact_name.strip() or None
        business.contact_phone = request.contact_phone.strip() or None
        business.contact_email = request.contact_email.strip() or None

        db.commit()
        db.refresh(business)
        return business_data(business)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/providers")
def providers():
    return list_providers()

@app.get("/api/platforms")
def list_platforms():
    db = SessionLocal()
    try:
        platforms = db.query(Platform).order_by(Platform.sort_order.asc()).all()
        return [
            {
                "key": p.key, "label": p.label,
                "authority": p.authority, "difficulty": p.difficulty,
                "score": p.score, "publish_url": p.publish_url,
            }
            for p in platforms
        ]
    finally:
        db.close()


@app.get("/api/plans")
def list_plans():
    db = SessionLocal()
    try:
        plans = db.query(Plan).order_by(Plan.sort_order.asc()).all()
        return [
            {
                "key": p.key, "label": p.label,
                "annual_price": p.annual_price,
                "billing_cycle": p.billing_cycle,
                "platform_keys": json.loads(p.platform_keys or "[]"),
                "description": p.description,
            }
            for p in plans
        ]
    finally:
        db.close()


@app.get("/api/businesses/{business_id}/platforms")
def business_platforms(business_id: int):
    db = SessionLocal()
    try:
        business = db.get(Business, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="企业不存在")
        plan = db.query(Plan).filter(Plan.key == business.plan_key).first()
        keys = json.loads(plan.platform_keys or "[]") if plan else []
        platforms = db.query(Platform).filter(Platform.key.in_(keys)).all() if keys else []
        order = {k: i for i, k in enumerate(keys)}
        platforms = sorted(platforms, key=lambda p: order.get(p.key, 999))
        return {
            "business_id": business.id,
            "plan_key": business.plan_key,
            "plan_label": plan.label if plan else "未知套餐",
            "platforms": [
                {"key": p.key, "label": p.label, "score": p.score, "publish_url": p.publish_url}
                for p in platforms
            ],
        }
    finally:
        db.close()

@app.get("/api/questions")
def list_questions(region: str | None = None):
    db = SessionLocal()
    try:
        query = db.query(Question)
        if region:
            query = query.filter(
                (Question.region == region)
                | Question.region.is_(None)
                | (Question.region == "")
            )
        questions = query.order_by(Question.id.asc()).all()
        return [
            {
                "id": question.id,
                "text": question.text,
                "category": question.category,
                "region": question.region,
            }
            for question in questions
        ]
    finally:
        db.close()


@app.post("/api/questions")
def add_questions(request: QuestionBatch):
    cleaned = [
        question.strip() for question in request.questions if question.strip()
    ]
    if not cleaned:
        raise HTTPException(status_code=400, detail="请至少填写一个非空问题")
    db = SessionLocal()
    try:
        existing = {
            question.text.strip().casefold()
            for question in db.query(Question.text).all()
            if question.text
        }
        added = []
        for text in cleaned:
            if text.casefold() in existing:
                continue
            question = Question(
                text=text,
                category=request.category.strip() or None,
                region=request.region.strip() or None,
            )
            db.add(question)
            added.append(text)
            existing.add(text.casefold())
        db.commit()
        return {
            "added": len(added),
            "skipped": len(cleaned) - len(added),
            "questions": added,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/question-sets/generate", status_code=201)
def generate_question_set(request: QuestionSetGenerate):
    db = SessionLocal()
    try:
        business = db.get(Business, request.business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="企业不存在")
        try:
            candidates = generate_questions(business_data(business), request.provider, request.count)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        version = (
            db.query(func.max(QuestionSet.version))
            .filter(QuestionSet.business_id == business.id)
            .scalar()
            or 0
        ) + 1
        question_set = QuestionSet(
            business_id=business.id,
            version=version,
            status="draft",
            source=f"generated:{request.provider}",
        )
        db.add(question_set)
        db.flush()
        for candidate in candidates:
            db.add(QuestionSetItem(
                question_set_id=question_set.id,
                text=candidate["text"],
                category=candidate["category"],
            ))
        db.commit()
        db.refresh(question_set)
        return question_set_data(question_set, include_questions=True)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/question-sets")
def list_question_sets(business_id: int):
    db = SessionLocal()
    try:
        if db.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="企业不存在")
        sets = (
            db.query(QuestionSet)
            .filter(QuestionSet.business_id == business_id)
            .order_by(QuestionSet.version.desc())
            .all()
        )
        return [question_set_data(item) for item in sets]
    finally:
        db.close()


@app.get("/api/question-sets/{question_set_id}")
def get_question_set(question_set_id: int):
    db = SessionLocal()
    try:
        question_set = db.get(QuestionSet, question_set_id)
        if question_set is None:
            raise HTTPException(status_code=404, detail="问题集不存在")
        return question_set_data(question_set, include_questions=True)
    finally:
        db.close()


@app.post("/api/question-sets/{question_set_id}/approve")
def approve_question_set(question_set_id: int, request: QuestionSetApproval):
    cleaned = []
    seen = set()
    for item in request.questions:
        text = item.text.strip()
        if text and text.casefold() not in seen:
            cleaned.append((text, item.category.strip() or None))
            seen.add(text.casefold())
    if not cleaned:
        raise HTTPException(status_code=400, detail="审核后至少保留一个问题")
    db = SessionLocal()
    try:
        question_set = db.get(QuestionSet, question_set_id)
        if question_set is None:
            raise HTTPException(status_code=404, detail="问题集不存在")
        if question_set.status != "draft":
            raise HTTPException(status_code=409, detail="已审核的问题集不可修改，请生成新版本")
        for item in list(question_set.items):
            db.delete(item)
        db.flush()
        for text, category in cleaned:
            db.add(QuestionSetItem(
                question_set_id=question_set.id,
                text=text,
                category=category,
            ))
        db.query(QuestionSet).filter(
            QuestionSet.business_id == question_set.business_id,
            QuestionSet.status == "active",
        ).update({"status": "archived"})
        question_set.status = "active"
        db.commit()
        db.refresh(question_set)
        return question_set_data(question_set, include_questions=True)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/feeding-plans", status_code=201)
def create_feeding_plan(request: FeedingPlanInput):
    if not request.name.strip():
        raise HTTPException(status_code=400, detail="投放计划名称不能为空")
    brief = {
        "business_keywords": request.business_keywords.strip(),
        "activity_name": request.activity_name.strip(),
        "promotional_points": request.promotional_points.strip(),
        "event_time": request.event_time.strip(),
        "event_location": request.event_location.strip(),
        "event_description": request.event_description.strip(),
    }
    event_fields = (
        brief["event_time"],
        brief["event_location"],
        brief["event_description"],
    )
    if any(event_fields) and not all(event_fields):
        raise HTTPException(
            status_code=400,
            detail="填写新鲜事件时，请补全事件时间、地点和事情。",
        )
    if not any(brief.values()):
        raise HTTPException(
            status_code=400,
            detail="请至少填写企业关键词、活动名称、宣传点或新鲜事件中的一项",
        )
    db = SessionLocal()
    try:
        business = db.get(Business, request.business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="企业不存在")
        channels = list(dict.fromkeys(request.channels))
        plan_obj = db.query(Plan).filter(Plan.key == business.plan_key).first()
        allowed_keys = json.loads(plan_obj.platform_keys or "[]") if plan_obj else []
        invalid = [c for c in channels if c not in allowed_keys]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"当前套餐（{plan_obj.label if plan_obj else '未知'}）不包含以下平台：{'、'.join(invalid)}",
            )

        try:
            assets = build_assets(business, channels, brief, request.provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        plan = FeedingPlan(
            business_id=business.id,
            name=request.name.strip(),
            brief=json.dumps(brief, ensure_ascii=False),
            status="draft",
        )
        db.add(plan)
        db.flush()
        for asset in assets:
            db.add(FeedingAsset(
                plan_id=plan.id,
                channel=asset["channel"],
                title=asset["title"],
                content=asset["content"],
            ))
        db.commit()
        db.refresh(plan)
        return feeding_plan_data(plan, include_content=True)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/feeding-plans")
def list_feeding_plans(business_id: int):
    db = SessionLocal()
    try:
        if db.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="企业不存在")
        plans = (
            db.query(FeedingPlan)
            .filter(FeedingPlan.business_id == business_id)
            .order_by(FeedingPlan.id.desc())
            .all()
        )
        return [feeding_plan_data(plan, include_content=True) for plan in plans]
    finally:
        db.close()


@app.put("/api/feeding-assets/{asset_id}")
def update_feeding_asset(asset_id: int, request: FeedingAssetUpdate):
    title = request.title.strip()
    content = request.content.strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="标题和正文不能为空")
    db = SessionLocal()
    try:
        asset = db.get(FeedingAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="投放素材不存在")
        if asset.status == "published":
            raise HTTPException(status_code=409, detail="已发布素材不可修改")
        asset.title = title
        asset.content = content
        asset.status = "draft"
        db.commit()
        return {"id": asset.id, "title": asset.title, "status": asset.status}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def detected_image_type(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if (
        len(content) >= 12
        and content.startswith(b"RIFF")
        and content[8:12] == b"WEBP"
    ):
        return "image/webp", ".webp"
    return None


@app.post("/api/feeding-assets/{asset_id}/images", status_code=201)
async def upload_feeding_image(
    asset_id: int,
    file: UploadFile = File(...),
    alt_text: str = Form(default="", max_length=500),
):
    if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise HTTPException(status_code=400, detail="仅支持 JPG、PNG 或 WebP 图片")
    content = await file.read(MAX_IMAGE_SIZE + 1)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(status_code=413, detail="图片大小不能超过 10 MB")
    detected = detected_image_type(content)
    if detected is None or detected[0] != file.content_type:
        raise HTTPException(status_code=400, detail="图片格式无效或与文件类型不匹配")

    db = SessionLocal()
    stored_path = None
    try:
        asset = db.get(FeedingAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="投放素材不存在")
        if asset.status == "published":
            raise HTTPException(status_code=409, detail="已发布素材不可修改")
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        stored_name = f"{uuid4().hex}{detected[1]}"
        stored_path = IMAGE_DIR / stored_name
        stored_path.write_bytes(content)
        image = FeedingImage(
            asset_id=asset.id,
            stored_name=stored_name,
            original_name=Path(file.filename or "image").name[:255],
            content_type=detected[0],
            alt_text=alt_text.strip(),
        )
        db.add(image)
        if asset.status == "reviewed":
            asset.status = "draft"
        db.commit()
        db.refresh(image)
        return {
            "id": image.id,
            "filename": image.original_name,
            "content_type": image.content_type,
            "alt_text": image.alt_text,
            "url": f"/api/feeding-assets/{asset.id}/images/{image.id}",
        }
    except HTTPException:
        db.rollback()
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise
    except Exception:
        db.rollback()
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise
    finally:
        db.close()


@app.get("/api/feeding-assets/{asset_id}/images/{image_id}")
def get_feeding_image(asset_id: int, image_id: int):
    db = SessionLocal()
    try:
        image = db.get(FeedingImage, image_id)
        if image is None or image.asset_id != asset_id:
            raise HTTPException(status_code=404, detail="图片不存在")
        path = IMAGE_DIR / image.stored_name
        if not path.is_file():
            raise HTTPException(status_code=404, detail="图片文件不存在")
        return FileResponse(path, media_type=image.content_type)
    finally:
        db.close()


@app.delete("/api/feeding-assets/{asset_id}/images/{image_id}")
def delete_feeding_image(asset_id: int, image_id: int):
    db = SessionLocal()
    try:
        asset = db.get(FeedingAsset, asset_id)
        image = db.get(FeedingImage, image_id)
        if asset is None or image is None or image.asset_id != asset_id:
            raise HTTPException(status_code=404, detail="图片不存在")
        if asset.status == "published":
            raise HTTPException(status_code=409, detail="已发布素材不可修改")
        stored_path = IMAGE_DIR / image.stored_name
        db.delete(image)
        if asset.status == "reviewed":
            asset.status = "draft"
        db.commit()
        stored_path.unlink(missing_ok=True)
        return {"id": image_id, "deleted": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/feeding-assets/{asset_id}/download")
def download_feeding_asset(asset_id: int):
    db = SessionLocal()
    try:
        asset = db.get(FeedingAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="投放素材不存在")
        output = BytesIO()
        with ZipFile(output, "w", ZIP_DEFLATED) as archive:
            material = [f"# {asset.title}", "", asset.content, ""]
            for image in asset.images:
                image_path = IMAGE_DIR / image.stored_name
                if not image_path.is_file():
                    raise HTTPException(
                        status_code=409,
                        detail=f"图片文件缺失：{image.original_name}",
                    )
                material.extend(
                    [
                        f"![{image.alt_text or image.original_name}]"
                        f"(images/{image.stored_name})",
                        "",
                    ]
                )
                archive.write(image_path, f"images/{image.stored_name}")
            archive.writestr("material.md", "\n".join(material))
        output.seek(0)
        return Response(
            content=output.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="feeding-material-{asset.id}.zip"'
            },
        )
    finally:
        db.close()


@app.put("/api/feeding-assets/{asset_id}/publish")
def mark_feeding_asset_published(asset_id: int, request: FeedingPublishInput):
    published_url = request.published_url.strip()
    parsed_url = urlsplit(published_url)
    if parsed_url.scheme not in {"https", "http"} or not parsed_url.netloc:
        raise HTTPException(status_code=400, detail="请填写有效的公开页面链接")
    db = SessionLocal()
    try:
        asset = db.get(FeedingAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="投放素材不存在")
        if asset.status not in {"reviewed", "published"}:
            raise HTTPException(status_code=409, detail="请先审核通过素材，再登记发布")
        asset.status = "published"
        asset.published_url = published_url
        asset.published_at = datetime.utcnow()
        plan = asset.plan
        if plan.assets and all(item.status == "published" for item in plan.assets):
            plan.status = "published"
        else:
            plan.status = "in_progress"
        db.commit()
        db.refresh(asset)
        return {
            "id": asset.id,
            "status": asset.status,
            "published_url": asset.published_url,
            "published_at": asset.published_at.isoformat(),
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.post("/api/feeding-assets/{asset_id}/review")
def review_feeding_asset(asset_id: int):
    db = SessionLocal()
    try:
        asset = db.get(FeedingAsset, asset_id)
        if asset is None:
            raise HTTPException(status_code=404, detail="投放素材不存在")
        if asset.status != "draft":
            raise HTTPException(status_code=409, detail="只有待审核素材可以执行审核")
        asset.status = "reviewed"
        db.commit()
        return {"id": asset.id, "status": asset.status}
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def build_effect_report(db, request: EffectReportInput):
    business = db.get(Business, request.business_id)
    baseline = db.get(MonitorRun, request.baseline_run_id)
    after = db.get(MonitorRun, request.after_run_id)
    if business is None:
        raise HTTPException(status_code=404, detail="企业不存在")
    if baseline is None or after is None:
        raise HTTPException(status_code=404, detail="检测批次不存在")
    if (
        baseline.business_id != business.id
        or after.business_id != business.id
        or baseline.status != "done"
        or after.status != "done"
        or baseline.stage != "baseline"
        or after.stage != "after"
    ):
        raise HTTPException(status_code=400, detail="请选择该企业已完成的投喂前和投喂后批次")
    if baseline.engine != after.engine:
        raise HTTPException(status_code=400, detail="前后批次必须使用同一个模型")
    before_results = results_by_question(db, baseline)
    after_results = results_by_question(db, after)
    if not before_results or set(before_results) != set(after_results):
        raise HTTPException(status_code=400, detail="前后检测的问题集不一致，不能生成对比报告")
    before_metrics = run_metrics(db, baseline)
    after_metrics = run_metrics(db, after)
    if before_metrics["mention_rate"] is None or after_metrics["mention_rate"] is None:
        raise HTTPException(status_code=400, detail="检测批次没有可用于报告的结果")
    feeding_plan = db.get(FeedingPlan, request.feeding_plan_id)
    if feeding_plan is None or feeding_plan.business_id != business.id:
        raise HTTPException(status_code=400, detail="投放计划不存在或不属于所选企业")
    assets = [
        {
            "channel_name": CHANNEL_LABELS.get(asset.channel, asset.channel),
            "published_url": asset.published_url,
            "published_at": asset.published_at.isoformat() if asset.published_at else None,
        }
        for asset in feeding_plan.assets
        if asset.status == "published"
    ]
    published_times = [
        asset.published_at
        for asset in feeding_plan.assets
        if asset.status == "published" and asset.published_at is not None
    ]
    if not published_times:
        raise HTTPException(
            status_code=400,
            detail="关联投放计划尚无已登记发布的素材",
        )
    if baseline.created_at and baseline.created_at >= min(published_times):
        raise HTTPException(
            status_code=400,
            detail="投喂前基线时间必须早于该计划首次发布",
        )
    if after.created_at and after.created_at <= max(published_times):
        raise HTTPException(
            status_code=400,
            detail="投喂后检测时间必须晚于关联计划最近一次发布",
        )
    questions = []
    for question_text in sorted(before_results):
        before = before_results[question_text]
        after_result = after_results[question_text]
        questions.append({
            "question": question_text,
            "baseline_mentioned": bool(before.mentioned),
            "after_mentioned": bool(after_result.mentioned),
            "baseline_answer": before.raw_answer or "",
            "after_answer": after_result.raw_answer or "",
        })
    question_set = (
        f"问题集 v{baseline.question_set.version}"
        if baseline.question_set_id and baseline.question_set
        else "历史问题库快照"
    )
    snapshot = {
        "title": request.title.strip(),
        "business": business_data(business),
        "provider_name": PROVIDERS.get(baseline.engine, {}).get("label", baseline.engine),
        "question_set": question_set,
        "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "baseline": before_metrics,
        "after": after_metrics,
        "change": round(after_metrics["mention_rate"] - before_metrics["mention_rate"], 2),
        "feeding_plan_name": feeding_plan.name if feeding_plan else None,
        "published_assets": assets,
        "questions": questions,
    }
    report = EffectReport(
        business_id=business.id,
        feeding_plan_id=feeding_plan.id if feeding_plan else None,
        baseline_run_id=baseline.id,
        after_run_id=after.id,
        title=request.title.strip(),
        snapshot=json.dumps(snapshot, ensure_ascii=False),
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@app.post("/api/reports", status_code=201)
def create_effect_report(request: EffectReportInput):
    if not request.title.strip():
        raise HTTPException(status_code=400, detail="报告名称不能为空")
    db = SessionLocal()
    try:
        report = build_effect_report(db, request)
        return {
            "id": report.id,
            "business_id": report.business_id,
            "title": report.title,
            "created_at": report.created_at.isoformat(),
            "snapshot": json.loads(report.snapshot),
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.get("/api/reports")
def list_effect_reports(business_id: int):
    db = SessionLocal()
    try:
        if db.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="企业不存在")
        reports = (
            db.query(EffectReport)
            .filter(EffectReport.business_id == business_id)
            .order_by(EffectReport.id.desc())
            .all()
        )
        return [
            {
                "id": report.id,
                "business_id": report.business_id,
                "title": report.title,
                "created_at": report.created_at.isoformat() if report.created_at else None,
            }
            for report in reports
        ]
    finally:
        db.close()


@app.get("/api/reports/{report_id}")
def get_effect_report(report_id: int):
    db = SessionLocal()
    try:
        report = db.get(EffectReport, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        return {
            "id": report.id,
            "business_id": report.business_id,
            "title": report.title,
            "created_at": report.created_at.isoformat() if report.created_at else None,
            "snapshot": json.loads(report.snapshot),
        }
    finally:
        db.close()


@app.get("/api/reports/{report_id}/html")
def download_effect_report(report_id: int):
    from fastapi.responses import HTMLResponse

    db = SessionLocal()
    try:
        report = db.get(EffectReport, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        return HTMLResponse(
            content=report_html(json.loads(report.snapshot)),
            headers={
                "Content-Disposition": f'attachment; filename="geo-report-{report.id}.html"'
            },
        )
    finally:
        db.close()


@app.post("/api/monitor-runs", status_code=202)
def start_monitor(request: MonitorRequest, background_tasks: BackgroundTasks):
    provider_ids = list(dict.fromkeys(request.providers))
    unconfigured = [
        PROVIDERS[provider_id]["label"]
        for provider_id in provider_ids
        if not PROVIDERS[provider_id]["key"]
    ]
    if unconfigured:
        raise HTTPException(
            status_code=400,
            detail=f"请先在 backend/.env 配置 API 密钥：{'、'.join(unconfigured)}",
        )
    try:
        runs = create_monitor_runs(
            request.business_id,
            request.stage,
            provider_ids,
            request.question_set_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for run in runs:
        background_tasks.add_task(run_monitor, run["run_id"])
    return {"runs": runs, "message": "监测任务已启动"}


@app.get("/api/monitor-runs")
def monitor_runs(business_id: int, limit: int = 30):
    db = SessionLocal()
    try:
        if db.get(Business, business_id) is None:
            raise HTTPException(status_code=404, detail="企业不存在")
        runs = (
            db.query(MonitorRun)
            .filter(MonitorRun.business_id == business_id)
            .order_by(MonitorRun.id.desc())
            .limit(max(1, min(limit, 100)))
            .all()
        )
        return [run_metrics(db, run) for run in runs]
    finally:
        db.close()


@app.get("/api/monitor-runs/{run_id}")
def monitor_run_detail(run_id: int):
    db = SessionLocal()
    try:
        run = db.get(MonitorRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="监测批次不存在")
        results = (
            db.query(MonitorResult, Question)
            .outerjoin(Question, MonitorResult.question_id == Question.id)
            .filter(MonitorResult.run_id == run.id)
            .order_by(MonitorResult.id.asc())
            .all()
        )
        return {
            **run_metrics(db, run),
            "results": [
                {
                    "question": result.question_text or (question.text if question else ""),
                    "category": question.category if question else None,
                    "mentioned": bool(result.mentioned),
                    "snippet": result.snippet,
                    "raw_answer": result.raw_answer,
                }
                for result, question in results
            ],
        }
    finally:
        db.close()


@app.get("/api/dashboard/overview")
def dashboard_overview(business_id: int = 1):
    db = SessionLocal()
    try:
        business = db.get(Business, business_id)
        if business is None:
            raise HTTPException(status_code=404, detail="企业不存在")

        runs = (
            db.query(MonitorRun)
            .filter(MonitorRun.business_id == business_id)
            .order_by(MonitorRun.id.desc())
            .all()
        )
        latest = {}
        trend = []
        for run in runs:
            if run.status != "done":
                continue
            metrics = run_metrics(db, run)
            trend.append(metrics)
            latest.setdefault((run.stage, run.engine), metrics)

        comparisons = []
        for provider_id, config in PROVIDERS.items():
            baseline = latest.get(("baseline", provider_id))
            after = latest.get(("after", provider_id))
            before_rate = baseline["mention_rate"] if baseline else None
            after_rate = after["mention_rate"] if after else None
            same_question_set = False
            if baseline and after:
                baseline_run = db.get(MonitorRun, baseline["run_id"])
                after_run = db.get(MonitorRun, after["run_id"])
                baseline_questions = set(results_by_question(db, baseline_run))
                after_questions = set(results_by_question(db, after_run))
                same_question_set = baseline_questions == after_questions
            comparisons.append({
                "provider": provider_id,
                "provider_name": config["label"],
                "baseline": baseline,
                "after": after,
                "same_question_set": same_question_set,
                "change": (
                    round(after_rate - before_rate, 2)
                    if before_rate is not None and after_rate is not None
                    and same_question_set
                    else None
                ),
            })

        return {
            "business": business_data(business),
            "providers": list_providers(),
            "comparisons": comparisons,
            "trend": list(reversed(trend)),
            "active_runs": sum(run.status == "running" for run in runs),
            "question_count": (
                db.query(QuestionSetItem)
                .join(QuestionSet, QuestionSetItem.question_set_id == QuestionSet.id)
                .filter(QuestionSet.business_id == business.id, QuestionSet.status == "active")
                .count()
                or len(questions_for_business(db, business))
            ),
            "updated_at": datetime.utcnow().isoformat(),
        }
    finally:
        db.close()


@app.get("/api/dashboard/summary")
def summary(business_id: int = 1):
    db = SessionLocal()
    try:
        business = db.get(Business, business_id)
        if not business:
            raise HTTPException(404, "企业不存在")
        latest = (
            db.query(MonitorRun)
            .filter(
                MonitorRun.business_id == business_id,
                MonitorRun.status == "done",
            )
            .order_by(MonitorRun.id.desc())
            .first()
        )
        if not latest:
            return {
                "business": business_data(business),
                "mention_rate": None,
                "total": 0,
                "mentioned": 0,
                "run_id": None,
                "message": "还没有监测记录",
            }
        metrics = run_metrics(db, latest)
        return {
            "business": business_data(business),
            "mention_rate": metrics["mention_rate"],
            "total": metrics["total"],
            "mentioned": metrics["mentioned"],
            "run_id": latest.id,
            "run_time": metrics["time"],
        }
    finally:
        db.close()


@app.get("/api/visibility/trend")
def trend(business_id: int = 1):
    db = SessionLocal()
    try:
        runs = (
            db.query(MonitorRun)
            .filter(
                MonitorRun.business_id == business_id,
                MonitorRun.status == "done",
            )
            .order_by(MonitorRun.id.asc())
            .all()
        )
        return [
            {
                "run_id": metric["run_id"],
                "time": metric["time"],
                "mention_rate": metric["mention_rate"] or 0,
                "mentioned": metric["mentioned"],
                "total": metric["total"],
            }
            for metric in (run_metrics(db, run) for run in runs)
        ]
    finally:
        db.close()


@app.get("/api/mentions/latest")
def latest_mentions(business_id: int = 1):
    db = SessionLocal()
    try:
        latest = (
            db.query(MonitorRun)
            .filter(
                MonitorRun.business_id == business_id,
                MonitorRun.status == "done",
            )
            .order_by(MonitorRun.id.desc())
            .first()
        )
        if not latest:
            return {"run_id": None, "results": []}
        rows = (
            db.query(MonitorResult, Question.text)
            .outerjoin(Question, MonitorResult.question_id == Question.id)
            .filter(MonitorResult.run_id == latest.id)
            .all()
        )
        return {
            "run_id": latest.id,
            "time": latest.created_at.isoformat(),
            "results": [
                {
                    "question": result.question_text or question_text,
                    "mentioned": bool(result.mentioned),
                    "snippet": result.snippet,
                    "raw_answer": result.raw_answer,
                }
                for result, question_text in rows
            ],
        }
    finally:
        db.close()
