"""模拟语料库：没有真实 Key 时，用它把「投喂 → 被引用」的闭环跑通。

**边界必须说清楚**：这里的「引用」是 mock 引擎按稿件校验得分算出来的概率，
不是任何真实模型的真实行为。所以：
- 只用于功能验证与流程演示；
- 报表里标 ``SIMULATION``；
- 接上真实 Key 后，必须用真实跑批复测，不能拿这里的数字对外宣称效果。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.rules.models import SimDoc


def feed(
    db: Session,
    *,
    business_id: int,
    engine: str,
    channel: str,
    title: str,
    content: str,
    url: str = "",
    quality: float = 0.0,
    content_id: int | None = None,
) -> SimDoc:
    doc = SimDoc(
        business_id=business_id,
        engine=engine,
        channel=channel,
        title=title,
        content=content,
        url=url or f"https://sim.local/{channel}/{business_id}-{engine}",
        quality=max(0.0, min(1.0, quality)),
        content_id=content_id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def docs_for_business(db: Session, business_id: int) -> list[SimDoc]:
    return db.query(SimDoc).filter(SimDoc.business_id == business_id).order_by(SimDoc.quality.desc()).all()


def lookup(brand_name: str, limit: int = 3) -> list[dict[str, Any]]:
    """mock 探针调用：按品牌名找已投喂的模拟语料。查不到就返回空（等于没投喂）。"""
    if not brand_name:
        return []
    try:
        from app.database import SessionLocal
        from app.models import Business

        with SessionLocal() as db:
            biz = db.query(Business).filter(Business.name == brand_name).first()
            if biz is None:
                return []
            rows = (
                db.query(SimDoc)
                .filter(SimDoc.business_id == biz.id)
                .order_by(SimDoc.quality.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "channel": r.channel,
                    "title": r.title,
                    "url": r.url,
                    "quality": r.quality,
                    "lead": (r.content or "").splitlines()[0] if r.content else "",
                }
                for r in rows
            ]
    except Exception:  # noqa: BLE001 - 模拟层失败绝不能拖垮跑批
        return []


def clear(db: Session, business_id: int | None = None) -> int:
    q = db.query(SimDoc)
    if business_id:
        q = q.filter(SimDoc.business_id == business_id)
    n = q.delete()
    db.commit()
    return n
