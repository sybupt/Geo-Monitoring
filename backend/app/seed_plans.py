import json
from app.database import SessionLocal, engine, Base
import app.models
from app.models import Platform, Plan

PLATFORMS = [
    {"key": "wechat_personal", "label": "微信个人号", "authority": 2,  "difficulty": 3, "publish_url": "", "sort_order": 1},
    {"key": "wechat",          "label": "微信公众号", "authority": 6,  "difficulty": 5, "publish_url": "https://mp.weixin.qq.com", "sort_order": 2},
    {"key": "official_site",   "label": "企业官网",   "authority": 9,  "difficulty": 2, "publish_url": "", "sort_order": 3},
    {"key": "rednote",         "label": "小红书",     "authority": 5,  "difficulty": 8, "publish_url": "https://creator.xiaohongshu.com", "sort_order": 4},
    {"key": "zhihu",           "label": "知乎",       "authority": 7,  "difficulty": 7, "publish_url": "https://zhuanlan.zhihu.com/write", "sort_order": 5},
    {"key": "baidu",           "label": "百度（百家号）", "authority": 8, "difficulty": 6, "publish_url": "https://baijiahao.baidu.com", "sort_order": 6},
    {"key": "tourism_bureau",  "label": "文旅局",     "authority": 9,  "difficulty": 8, "publish_url": "", "sort_order": 7},
    {"key": "government",      "label": "政府平台",   "authority": 10, "difficulty": 9, "publish_url": "", "sort_order": 8},
]

PLANS = [
    {"key": "trial",    "label": "试用版", "annual_price": 0,     "platform_keys": ["wechat_personal"], "description": "免费试用，仅含微信个人号", "sort_order": 0},
    {"key": "basic",    "label": "基础版", "annual_price": 20000, "platform_keys": ["wechat_personal", "wechat", "official_site"], "description": "入门，低难度平台", "sort_order": 1},
    {"key": "standard", "label": "标准版", "annual_price": 30000, "platform_keys": ["wechat_personal", "wechat", "official_site", "zhihu", "rednote", "baidu"], "description": "加高价值内容平台", "sort_order": 2},
    {"key": "premium",  "label": "旗舰版", "annual_price": 40000, "platform_keys": ["wechat_personal", "wechat", "official_site", "zhihu", "rednote", "baidu", "tourism_bureau", "government"], "description": "全渠道 + 最高权威", "sort_order": 3},
]


def score_of(authority, difficulty):
    return round(0.6 * authority + 0.4 * difficulty, 2)


def seed():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        for p in PLATFORMS:
            if not db.query(Platform).filter(Platform.key == p["key"]).first():
                db.add(Platform(**p, score=score_of(p["authority"], p["difficulty"])))
        for plan in PLANS:
            if not db.query(Plan).filter(Plan.key == plan["key"]).first():
                data = dict(plan)
                data["platform_keys"] = json.dumps(plan["platform_keys"], ensure_ascii=False)
                db.add(Plan(**data))

        db.commit()
        print("✅ 平台和套餐种子数据已写入")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
