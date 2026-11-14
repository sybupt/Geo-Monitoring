"""本地生活垂类：问题矩阵 + 渠道画像。

为什么单拎出来：本地生活的提问有强地域、强决策、强比价特征，问题形态和通用行业完全不同，
用通用的「XX 哪家好」模板跑出来的样本没有代表性。这里把问题拆成「地域 × 品类 × 意图」，
并且**必须同时生成对照组**——不投喂的问题，否则事后无法做 DID 归因。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

# ── 意图模板 ────────────────────────────────────────────────────
INTENTS: dict[str, dict[str, Any]] = {
    "recommend": {
        "label": "推荐类",
        "templates": [
            "{city}{category}哪家好",
            "{city}{category}推荐",
            "{district}附近{category}推荐",
            "{city}有哪些值得去的{category}",
        ],
    },
    "compare": {
        "label": "对比类",
        "templates": [
            "{city}{category}哪家性价比高",
            "{city}{category}排名",
            "{city}{category}前十名",
        ],
    },
    "price": {
        "label": "价格类",
        "templates": [
            "{city}{category}人均消费多少",
            "{city}{category}贵不贵",
        ],
    },
    "reputation": {
        "label": "口碑类",
        "templates": [
            "{city}{category}口碑怎么样",
            "{city}{category}评价好不好",
        ],
    },
    "avoid": {
        "label": "避坑类",
        "templates": [
            "{city}{category}避坑",
            "{city}{category}哪些不要去",
        ],
    },
    "nearby": {
        "label": "附近类",
        "templates": [
            "{district}附近有什么好的{category}",
            "{city}地铁站附近{category}",
        ],
    },
}

DEFAULT_CATEGORIES = ["火锅", "月子中心", "口腔诊所", "健身工作室", "宠物医院", "装修公司"]


def _fill(template: str, *, city: str, district: str, category: str) -> str:
    return (
        template.replace("{city}", city)
        .replace("{district}", district or city)
        .replace("{category}", category)
    )


def generate_questions(
    city: str,
    *,
    categories: list[str] | None = None,
    districts: list[str] | None = None,
    intents: list[str] | None = None,
    per_intent: int = 2,
    control_categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    """生成问题集。

    返回每项：text / intent / intent_label / category / is_control / question_key。
    对照组用「同城、不同品类」的问题构造——它在同一个地域与引擎环境里，但不接受投喂，
    所以能吸收模型升级、季节波动这些共同因素。
    """
    categories = categories or DEFAULT_CATEGORIES
    districts = districts or [""]
    intent_keys = intents or list(INTENTS.keys())

    out: list[dict[str, Any]] = []
    for category in categories:
        for intent in intent_keys:
            templates = INTENTS[intent]["templates"][: max(1, per_intent)]
            for i, tpl in enumerate(templates):
                district = districts[i % len(districts)]
                text = _fill(tpl, city=city, district=district, category=category)
                out.append(
                    {
                        "text": text,
                        "intent": intent,
                        "intent_label": INTENTS[intent]["label"],
                        "category": category,
                        "is_control": False,
                        "question_key": f"T:{category}:{intent}:{i}",
                    }
                )

    for category in control_categories or []:
        text = f"{city}{category}哪家好"
        out.append(
            {
                "text": text,
                "intent": "recommend",
                "intent_label": "推荐类",
                "category": category,
                "is_control": True,
                "question_key": f"C:{category}:recommend:0",
            }
        )

    return out


# ── 渠道画像（本地生活）────────────────────────────────────────
# mode: api=有开放接口 / rpa=账号登录后可自动化 / guide=只能引导人工去发 / manual=纯人工
CHANNELS: list[dict[str, Any]] = [
    {
        "key": "dianping",
        "label": "大众点评",
        "mode": "rpa",
        "guide_url": "https://passport.meituan.com/account/unitivelogin",
        "ai_weight": 0.9,
        "note": "本地生活第一大信源池，商户页 + 笔记双入口；反爬较强，建议账号池 + 低频",
    },
    {
        "key": "meituan",
        "label": "美团",
        "mode": "rpa",
        "guide_url": "https://passport.meituan.com/account/unitivelogin",
        "ai_weight": 0.8,
        "note": "与点评同源，商户信息一致性优先于内容量",
    },
    {
        "key": "xiaohongshu",
        "label": "小红书",
        "mode": "guide",
        "guide_url": "https://creator.xiaohongshu.com/publish/publish",
        "ai_weight": 0.85,
        "note": "探店笔记是 AI 回答的高频引用源；风控严，优先人工发布",
    },
    {
        "key": "douyin",
        "label": "抖音",
        "mode": "guide",
        "guide_url": "https://creator.douyin.com/creator-micro/content/upload",
        "ai_weight": 0.8,
        "note": "豆包系第一信源（占比约 40%），没抖音资产基本进不了豆包检索池",
    },
    {
        "key": "zhihu",
        "label": "知乎",
        "mode": "rpa",
        "guide_url": "https://www.zhihu.com/signin",
        "ai_weight": 0.7,
        "note": "DeepSeek / Kimi 的主要引用源，适合「XX 行业怎么选」类长文",
    },
    {
        "key": "baijiahao",
        "label": "百家号",
        "mode": "api",
        "guide_url": "https://baijiahao.baidu.com/builder/theme/bjh/login",
        "ai_weight": 0.75,
        "note": "文心系主入口，百度可直接抓取；有开放写入接口但需资质",
    },
    {
        "key": "gongzhonghao",
        "label": "微信公众号",
        "mode": "guide",
        "guide_url": "https://mp.weixin.qq.com/",
        "ai_weight": 0.65,
        "note": "元宝/搜狗主信源，但抓取受限，收录不稳定",
    },
    {
        "key": "official_site",
        "label": "官网",
        "mode": "api",
        "guide_url": "",
        "ai_weight": 0.5,
        "note": "必须补 Article / LocalBusiness 结构化标记，否则 AI 难以判定实体",
    },
    {
        "key": "map_claim",
        "label": "高德/百度地图商户认领",
        "mode": "manual",
        "guide_url": "https://ditu.amap.com/",
        "ai_weight": 0.6,
        "note": "地址、营业时间、电话的一致性直接影响「附近类」问题",
    },
]


def channels_for(city: str = "") -> list[dict[str, Any]]:
    """返回渠道画像，附上引导发布用的检索式（方便运营直接去平台搜竞品占位情况）。"""
    out = []
    for ch in CHANNELS:
        item = dict(ch)
        item["probe_query"] = f"{city} {ch['label']}" if city else ch["label"]
        item["search_url"] = f"https://www.baidu.com/s?wd={quote(item['probe_query'])}"
        out.append(item)
    return out


def channel(key: str) -> dict[str, Any] | None:
    return next((c for c in CHANNELS if c["key"] == key), None)
