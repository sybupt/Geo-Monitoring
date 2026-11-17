"""合成数据生成器。

为什么要自己造数据：真实推荐日志拿不到，而 AutoGEO 的公开数据集（E-commerce /
Researchy-GEO）是"每问若干文档 + 目标文档"，没有用户与行为，无法直接驱动用户-物品推荐。

这里构造一个可复现的中文电商场景，关键是保留一条真实的因果链：
    物品文案质量 q  ->  文案结构特征  ->  GEO 规则分 GQS  ->  排序特征
    物品文案质量 q  ->  用户点击概率（隐变量）
因此 GQS 不是"另一个分数"，而是对 q 的有噪观测，A/B 才有可解释的增益。
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta

from .config import N_COLD_ITEMS, N_ITEMS, N_USERS, MAX_INTERACTIONS, MIN_INTERACTIONS, RANDOM_SEED
from .db import init_db, insert_interactions, insert_items, insert_users, table_counts
from .geo.analyzer import analyze

CATEGORIES: dict[str, dict] = {
    "笔记本电脑": {
        "brands": ["联想", "华硕", "戴尔", "荣耀"],
        "price": (4200, 9800),
        "params": [("重量", "1.32kg"), ("续航", "11.5小时"), ("屏幕", "14英寸 2.8K"), ("内存", "16GB LPDDR5"), ("硬盘", "512GB SSD"), ("功率", "65W")],
        "terms": ["LPDDR5", "SSD", "色域"],
    },
    "无线耳机": {
        "brands": ["漫步者", "索尼", "小米", "倍思"],
        "price": (199, 1499),
        "params": [("重量", "4.6g"), ("续航", "8小时"), ("降噪深度", "42dB"), ("蓝牙", "5.3"), ("总续航", "32小时")],
        "terms": ["主动降噪", "LDAC", "动圈"],
    },
    "扫地机器人": {
        "brands": ["科沃斯", "石头", "追觅", "云鲸"],
        "price": (1299, 4599),
        "params": [("吸力", "8000Pa"), ("电池", "5200mAh"), ("噪音", "58dB"), ("水箱", "300ml"), ("续航", "180分钟")],
        "terms": ["激光导航", "边刷", "拖布自清洁"],
    },
    "空气净化器": {
        "brands": ["小米", "飞利浦", "布鲁雅尔", "352"],
        "price": (699, 3299),
        "params": [("颗粒物CADR", "500m³/h"), ("噪音", "22dB"), ("功率", "45W"), ("适用面积", "60㎡"), ("滤芯寿命", "12个月")],
        "terms": ["HEPA", "CADR", "活性炭"],
    },
    "显示器": {
        "brands": ["AOC", "戴尔", "明基", "HKC"],
        "price": (799, 3999),
        "params": [("尺寸", "27英寸"), ("分辨率", "3840×2160"), ("刷新率", "144Hz"), ("亮度", "400nit"), ("接口", "HDMI2.1")],
        "terms": ["IPS", "HDR", "色准"],
    },
    "咖啡机": {
        "brands": ["德龙", "飞利浦", "百胜图", "马克西姆"],
        "price": (899, 5999),
        "params": [("泵压", "15bar"), ("水箱", "1.5L"), ("功率", "1350W"), ("预热", "25秒")],
        "terms": ["半自动", "蒸汽棒", "研磨度"],
    },
    "电动牙刷": {
        "brands": ["飞利浦", "欧乐B", "素士", "usmile"],
        "price": (129, 899),
        "params": [("振动频率", "62000次/分钟"), ("续航", "60天"), ("防水", "IPX7"), ("模式", "5档")],
        "terms": ["声波", "压力感应", "刷头"],
    },
    "智能手表": {
        "brands": ["华为", "小米", "佳明", "OPPO"],
        "price": (399, 2499),
        "params": [("续航", "14天"), ("防水", "50米"), ("屏幕", "1.43英寸 AMOLED"), ("重量", "32g")],
        "terms": ["血氧", "GPS", "息屏显示"],
    },
}

QUALITY_NOTES = {
    "high": ["连续两年在同类横评中位于前 20%", "返修率低于 2.1%", "第三方实验室实测数据与标称值偏差小于 5%"],
    "mid": ["用户评价集中在 4.6 分左右", "销量位于同价位中上"],
}


def _price_for(cat: str, rng: random.Random) -> float:
    lo, hi = CATEGORIES[cat]["price"]
    raw = math.exp(rng.uniform(math.log(lo), math.log(hi)))
    return round(raw / 10) * 10 - 1


def _high_quality_text(title: str, cat: str, brand: str, price: float, params, rng) -> str:
    pairs = list(params)
    p1, p2, p3 = pairs[0], pairs[1] if len(pairs) > 1 else pairs[0], pairs[2] if len(pairs) > 2 else pairs[0]
    full = f"{brand} {title}"
    pros = rng.choice(["重量控制到位，通勤携带无负担", "续航实测达标，一天一充足够", "噪音控制好，夜间使用不打扰", "接口齐全，外接设备不用转接"])
    cons = rng.choice(["高负载下机身温度会升到 42°C 左右，长时间使用建议垫高", "原装配件偏少，扩展需要额外购买", "低亮度下调光有轻微频闪，敏感人群注意"])
    return f"""【结论】{full} 适合预算 {int(price)} 元档、看重{pros.split('，')[0]}的用户；它不是堆参数的路线，胜在稳定。

参数版本：2026-11，具体以官方参数页为准。

## 核心参数
- {p1[0]}：{p1[1]}
- {p2[0]}：{p2[1]}
- {p3[0]}：{p3[1]}
- 价格：{int(price)} 元（2026 年 11 月主流渠道价）

## 适合与不适合
- 适合：{pros}，日常使用场景覆盖完整。
- 注意：{cons}

## 说明
{full} 的核心取舍是把成本放在{p1[0]}上，因此{p1[0]}做到 {p1[1]}，没有为轻薄牺牲{p2[0]}。
{rng.choice(QUALITY_NOTES['high'])}。与同价位竞品相比，它在{p3[0]}上更保守，没有明显短板。

## 依据
- 来源：品牌官方参数页（2026-11 更新）、第三方实验室实测报告、电商平台近 90 天评价抽样。
- 数据来自 300 条用户评价与 2 份独立测评，引用时请以官方页面为准。
"""


def _mid_quality_text(title: str, cat: str, brand: str, price: float, params, rng) -> str:
    p = dict(params)
    full = f"{brand} {title}"
    return f"""这款 {full} 整体表现不错，价格 {int(price)} 元，属于该品类里比较有竞争力的选择。

## 主要特点
- {list(p.keys())[0]}：{list(p.values())[0]}
- {list(p.keys())[1] if len(p) > 1 else '性能'}：{list(p.values())[1] if len(p) > 1 else '良好'}
- {rng.choice(QUALITY_NOTES['mid'])}

使用一段时间后，感觉{dict(params).get('续航', '整体体验')}是它最突出的地方。当然也有不足，比如细节做工一般。

总的来说值得考虑，具体可以看官方页面。
"""


def _low_quality_text(title: str, cat: str, brand: str, price: float, params, rng) -> str:
    full = f"{brand} {title}"
    return (
        f"随着近年来消费升级的不断推进，越来越多的人开始关注{cat}这个品类，{full} 就是在这样的背景下推出的产品。"
        f"作为一款备受关注的新品，它在设计和体验上都做了不少努力，很多用户反馈用起来感觉非常好，可以说是同价位里最好的选择之一。"
        f"我们相信这款产品能够满足绝大多数家庭的需求，无论是日常使用还是特殊场景都能轻松应对，绝对是闭眼入不后悔的一款。"
        f"现在下单还有限时优惠，仅此一次，感兴趣的朋友可以扫码了解更多详情，点击下方链接查看最新活动！"
        f"总之，这是一款100%值得推荐的产品，史上最强性价比，买了绝对不亏！"
    )


def build_items(rng: random.Random) -> list[dict]:
    items: list[dict] = []
    per_cat = N_ITEMS // len(CATEGORIES)
    idx = 0
    for cat, meta in CATEGORIES.items():
        for k in range(per_cat):
            idx += 1
            brand = rng.choice(meta["brands"])
            series = rng.choice(["Pro", "Air", "Max", "Lite", "S", "Plus", "青春版", "旗舰版"])
            model = f"{rng.choice(['X', 'S', 'T', 'V', 'Z'])}{rng.randint(10, 99)}"
            name = f"{model} {series}"
            price = _price_for(cat, rng)
            # 隐变量：文案质量 q（决定文案档位，也决定点击倾向）
            q = min(0.98, max(0.03, rng.betavariate(2.2, 2.0)))
            if q >= 0.70:
                text = _high_quality_text(name, cat, brand, price, meta["params"], rng)
            elif q >= 0.42:
                text = _mid_quality_text(name, cat, brand, price, meta["params"], rng)
            else:
                text = _low_quality_text(name, cat, brand, price, meta["params"], rng)
            report = analyze(text)
            items.append({
                "item_id": f"I{idx:04d}",
                "title": f"{brand} {name} {cat}",
                "category": cat,
                "brand": brand,
                "price": price,
                "text": text.strip(),
                "gqs": report.gqs,
                "geo_json": json.dumps(report.to_dict(), ensure_ascii=False),
                "popularity": 0.0,
                "created_at": (datetime(2026, 1, 1) + timedelta(days=rng.randint(0, 300))).strftime("%Y-%m-%d"),
                "_q": q,
            })

    # 新品（零交互）：验证「文案质量 -> 冷启曝光」这条链路
    cold_cats = rng.sample(list(CATEGORIES), k=6)
    for k in range(N_COLD_ITEMS):
        cat = cold_cats[k % len(cold_cats)]
        meta = CATEGORIES[cat]
        idx += 1
        brand = rng.choice(meta["brands"])
        name = f"{rng.choice(['X', 'S', 'T', 'V', 'Z'])}{rng.randint(10, 99)} {rng.choice(['Pro', 'Air', 'Max', 'Lite', '新品'])}"
        price = _price_for(cat, rng)
        q = min(0.98, max(0.03, rng.betavariate(2.2, 2.0)))
        if q >= 0.70:
            text = _high_quality_text(name, cat, brand, price, meta["params"], rng)
        elif q >= 0.42:
            text = _mid_quality_text(name, cat, brand, price, meta["params"], rng)
        else:
            text = _low_quality_text(name, cat, brand, price, meta["params"], rng)
        report = analyze(text)
        items.append({
            "item_id": f"I{idx:04d}",
            "title": f"{brand} {name} {cat}",
            "category": cat,
            "brand": brand,
            "price": price,
            "text": text.strip(),
            "gqs": report.gqs,
            "geo_json": json.dumps(report.to_dict(), ensure_ascii=False),
            "popularity": 0.0,
            "created_at": f"2026-11-{rng.randint(1, 13):02d}",
            "_q": q,
            "_cold": True,
        })
    return items


PERSONAS = ["参数党", "价格敏感", "品质优先", "轻度尝鲜", "家庭采购", "办公效率"]


def build_users(rng: random.Random) -> list[dict]:
    cats = list(CATEGORIES)
    users = []
    for i in range(1, N_USERS + 1):
        pref = rng.sample(cats, k=2)
        brand_pool = sorted({b for c in pref for b in CATEGORIES[c]["brands"]})
        brands = rng.sample(brand_pool, k=min(2, len(brand_pool)))
        band = rng.choice(["<1000", "1000-3000", "3000-6000", ">6000"])
        persona = rng.choice(PERSONAS)
        users.append({
            "user_id": f"U{i:04d}",
            "name": f"用户{i:04d}",
            "persona": persona,
            "price_band": band,
            "preferred": pref,
            "brands": brands,
            "activity": round(rng.uniform(0.6, 1.6), 2),
            "_quality_sensitivity": round(rng.uniform(0.1, 1.0) * (1.4 if persona == "品质优先" else 1.0), 2),
            "_price_sensitivity": round(rng.uniform(0.2, 1.0) * (1.4 if persona == "价格敏感" else 1.0), 2),
        })
    return users


BAND_CENTER = {"<1000": 500, "1000-3000": 1800, "3000-6000": 4200, ">6000": 8000}


def build_interactions(users: list[dict], items: list[dict], rng: random.Random) -> list[dict]:
    by_cat: dict[str, list[dict]] = {}
    for it in items:
        if it.get("_cold"):
            continue
        by_cat.setdefault(it["category"], []).append(it)

    base_ts = int(datetime(2026, 6, 1).timestamp())
    rows: list[dict] = []
    for u in users:
        target = int(rng.uniform(MIN_INTERACTIONS, MAX_INTERACTIONS) * u["activity"])
        pool: list[dict] = []
        for _ in range(2):
            for c in u["preferred"]:
                pool.extend(rng.sample(by_cat[c], k=min(34, len(by_cat[c]))))
        others = [c for c in by_cat if c not in u["preferred"]]
        for c in others:
            pool.extend(rng.sample(by_cat[c], k=8))
        seen: set[str] = set()
        picked: list[dict] = []
        ideal = BAND_CENTER[u["price_band"]]
        for it in pool:
            if it["item_id"] in seen:
                continue
            seen.add(it["item_id"])
            cat_aff = 1.0 if it["category"] == u["preferred"][0] else 0.62 if it["category"] == u["preferred"][1] else -0.55
            brand_aff = 1.0 if it["brand"] in u["brands"] else -0.25
            price_fit = -abs(math.log(max(it["price"], 1) / ideal))
            qual = (it["_q"] - 0.5) * 2.0 * u["_quality_sensitivity"]
            pop = math.log1p(it["popularity"])
            logit = (1.75 * cat_aff + 1.55 * brand_aff + 0.95 * price_fit
                     + 1.35 * qual + 0.45 * pop - 1.55 + rng.gauss(0, 0.45))
            p = 1 / (1 + math.exp(-logit))
            if rng.random() < p:
                picked.append(it)
            if len(picked) >= target:
                break
        picked = picked[:max(len(picked), MIN_INTERACTIONS)]
        for j, it in enumerate(picked):
            ts = base_ts + int(rng.uniform(0, 120)) * 86400 + j * 3600
            rows.append({
                "user_id": u["user_id"],
                "item_id": it["item_id"],
                "event": "click",
                "ts": ts,
                "label": 1,
            })
    return rows


def build_cold_probe(users: list[dict], cold_items: list[dict], rng: random.Random) -> list[dict]:
    """模拟新品上线 30 天后的真实点击，作为冷启动评测的标签（不参与训练）。"""
    rows: list[dict] = []
    base_ts = int(datetime(2026, 10, 1).timestamp())
    for u in users:
        ideal = BAND_CENTER[u["price_band"]]
        for it in cold_items:
            if it["category"] not in u["preferred"]:
                continue
            cat_aff = 1.0 if it["category"] == u["preferred"][0] else 0.62
            brand_aff = 1.0 if it["brand"] in u["brands"] else -0.25
            price_fit = -abs(math.log(max(it["price"], 1) / ideal))
            qual = (it["_q"] - 0.5) * 2.0 * u["_quality_sensitivity"]
            logit = (1.75 * cat_aff + 1.55 * brand_aff + 0.95 * price_fit
                     + 2.35 * qual - 1.35 + rng.gauss(0, 0.45))
            p = 1 / (1 + math.exp(-logit))
            label = 1 if rng.random() < p else 0
            rows.append({
                "user_id": u["user_id"], "item_id": it["item_id"], "event": "cold_probe",
                "ts": base_ts + rng.randint(0, 30) * 86400, "label": label,
            })
    return rows


def generate(reset: bool = True) -> dict:
    rng = random.Random(RANDOM_SEED)
    init_db(reset=reset)
    items = build_items(rng)
    users = build_users(rng)
    cold_items = [it for it in items if it.get("_cold")]
    interactions = build_interactions(users, items, rng)
    probe = build_cold_probe(users, cold_items, rng)

    pop: dict[str, int] = {}
    for r in interactions:
        pop[r["item_id"]] = pop.get(r["item_id"], 0) + 1
    max_pop = max(pop.values()) if pop else 1

    for it in items:
        it["popularity"] = round(pop.get(it["item_id"], 0) / max_pop, 4)
        it.pop("_q", None)
        it.pop("_cold", None)

    insert_items(items)
    user_rows = []
    for u in users:
        row = {k: v for k, v in u.items() if not k.startswith("_")}
        row["preferred"] = json.dumps(row["preferred"], ensure_ascii=False)
        row["brands"] = json.dumps(row["brands"], ensure_ascii=False)
        user_rows.append(row)
    insert_users(user_rows)
    insert_interactions(interactions)
    insert_interactions(probe)

    counts = table_counts()
    gqs_values = sorted(i["gqs"] for i in items)
    summary = {
        "counts": counts,
        "gqs_min": gqs_values[0],
        "gqs_median": gqs_values[len(gqs_values) // 2],
        "gqs_max": gqs_values[-1],
        "avg_interactions_per_user": round(len(interactions) / len(users), 1),
        "cold_items": len(cold_items),
        "cold_probe_click_rate": round(sum(r["label"] for r in probe) / max(len(probe), 1), 3),
        "sparsity": round(1 - len(interactions) / (len(users) * len(items)), 4),
    }
    print("[data] " + json.dumps(summary, ensure_ascii=False))
    return summary


if __name__ == "__main__":
    generate()
