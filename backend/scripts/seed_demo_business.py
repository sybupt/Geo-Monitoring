"""建一个「事实完整」的演示企业，用来跑通规则 → 内容 → 投喂 → 复测闭环。

为什么需要它：生成器和校验器的第一原则是「只允许引用有出处的事实」。
如果企业资料里没有价格、面积、年限这些数字，生成出来的稿子会因为
「数字不足 / 无出处」被判不通过——这是设计如此，不是 bug。
演示企业把这些事实补齐，闭环才能跑出真实结构。

用法：python scripts/seed_demo_business.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base, SessionLocal, engine  # noqa: E402
import app.models  # noqa: E402,F401
import app.v2.models  # noqa: E402,F401
import app.rules.models  # noqa: E402,F401
from app.models import Business  # noqa: E402

NAME = "恩施清江影像工作室"

DESCRIPTION = {
    "price": "全家福 398 元起，证件照 68 元/套，婚纱套系 3980 元起",
    "area": "室内实景棚 260 平方米",
    "founded": "2016 年开始经营，至今 9 年",
    "hours": "每日 09:00-21:00，周一上午设备维护",
    "address": "恩施市舞阳坝街道清江路 88 号 3 楼（清江桥头斜对面）",
    "transport": "距舞阳坝公交站 200 米，楼下可停车 15 个车位",
    "rating": "公开平台评分 4.7 分（满分 5 分）",
    "credentials": ["营业执照 91422801MA4XXXXX2K", "摄影服务经营备案 恩市监备 2016-0831"],
    "updated_at": "2026 年 11 月",
}

SELLING_POINTS = [
    "全家福拍摄：含服装 3 套、妆造 1 次、精修 20 张，交付周期 10 天",
    "儿童与亲子拍摄：设有独立母婴休息区，单场不超过 2 组客户",
    "证件照快照：现场出片约 25 分钟，提供 4 种底色",
    "婚纱外景：合作清江画廊 3 个外景机位，含车辆接送",
    "老照片修复：按张计费 120 元起，修复后提供电子档",
]

EVIDENCE = [
    "2026 年 10 月完成门店实景棚改造，新增自然光棚 60 平方米",
    "近 12 个月累计服务家庭客户约 1800 组",
    "拍摄当天可现场查看原片，不满意当场重拍",
]


def main() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        biz = db.query(Business).filter(Business.name == NAME).first()
        payload = dict(
            name=NAME,
            alias="清江影像,恩施清江影像",
            region="湖北恩施",
            category="摄影",
            description=json.dumps(DESCRIPTION, ensure_ascii=False),
            selling_points=json.dumps(SELLING_POINTS, ensure_ascii=False),
            evidence=json.dumps(EVIDENCE, ensure_ascii=False),
        )
        if biz is None:
            biz = Business(**payload)
            db.add(biz)
            db.commit()
            print(f"已创建演示企业：{biz.id} {biz.name}")
        else:
            for k, v in payload.items():
                setattr(biz, k, v)
            db.commit()
            print(f"已更新演示企业：{biz.id} {biz.name}（事实已补齐）")


if __name__ == "__main__":
    main()
