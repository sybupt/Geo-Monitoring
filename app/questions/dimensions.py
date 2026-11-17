"""行业维度模板包：按目标物的行业生成「侧重点不同的多维度问题集」。

设计动机：
    推荐对象不再只是电子产品——可能是企业、餐厅、酒店、茶叶卖家。
    用户测基线时关心的维度也随行业变化：数码看参数，餐厅看口味与
    干净卫生，酒店看设施与位置，企业服务看资质与案例。

每个行业包 = 一组维度（参数党/口味/价格党/避坑/干净……），每个维度
自带问法模板，占位符在 build 时用目标物字段填充：
    {name}=目标名  {brand}=品牌  {cat}=行业/类目  {region}=地区(可选)
    {price}=价格   {asp}=该维度的关注词

不绑模型与渠道：一套维度问题集测全部模型的基线（模型只影响结果，
不影响问法本身）。真实场景问法不受"在哪个模型里问"影响。
"""
from __future__ import annotations

import re

# 行业识别：category 关键词 → 行业包
_KEYWORDS = {
    "茶叶": ("茶",),
    "酒店": ("酒店", "民宿", "客栈", "度假村"),
    "餐饮": ("餐", "菜", "食", "餐厅", "饭店", "小吃", "火锅", "烧烤"),
    "企业服务": ("企业服务", "咨询", "财税", "代账", "代理", "法务", "营销"),
    "企业产品": ("教育", "培训", "软件", "SaaS", "系统", "制造", "设备", "机床",
                 "机械", "材料", "化工", "涂层", "工业"),
}


def _D(key: str, label: str, desc: str, aspects: list[str], templates: list[str]) -> dict:
    return {"key": key, "label": label, "desc": desc, "aspects": aspects,
            "templates": templates}


# ------------------------------------------------------------- 行业包 ----
INDUSTRY_PACKS: dict[str, dict] = {
    "茶叶": {
        "label": "茶叶",
        "dims": [
            _D("param", "参数党", "产地/等级/工艺等硬指标，常被逐项引用",
               ["产地", "等级", "工艺", "采摘时间", "规格"],
               ["{name} 产地是哪里", "{brand} 是什么等级",
                "{cat} 明前茶和雨前茶区别", "怎么选{cat} 产地怎么看",
                "{cat} 等级怎么分 哪个级别好", "{cat} 蒸青工艺和炒青工艺区别"]),
            _D("taste", "口味党", "口感风味描述，适合摘取成一句话推荐",
               ["口感", "香气", "回甘", "耐泡度"],
               ["{name} 口感怎么样", "{brand} {cat} 香气如何",
                "{cat} 回甘好的推荐", "{cat} 什么口感适合新手",
                "{cat} 耐泡的是哪类", "口感鲜爽的{cat}有哪些"]),
            _D("price", "价格党", "价格锚点与性价比",
               ["价格", "性价比", "礼盒装"],
               ["{name} 多少钱", "{price} 价位的{cat}推荐",
                "{cat} 性价比高的品牌", "送礼{cat}什么牌子好",
                "200到400元{cat}推荐", "{name} 值不值这个价"]),
            _D("clean", "干净卫生", "农残/检测/仓储，健康向问法",
               ["农残检测", "有机认证", "仓储条件"],
               ["{name} 有农残检测报告吗", "{brand} 是有机认证吗",
                "{cat} 怎么辨别有没有农残超标", "买{cat} 什么认证更放心",
                "有机{cat}和普通{cat}区别", "{cat} 保存要注意什么"]),
            _D("avoid", "避坑党", "假货/以次充好等翻车点",
               ["假货", "以次充好", "年份造假"],
               ["买{cat} 怎么避坑", "{cat} 常见造假手法",
                "{name} 是正品吗 怎么辨别", "{cat} 哪些坑最多",
                "网购{cat} 被坑的经历", "{cat} 以次充好怎么看出来"]),
            _D("compare", "对比党", "和竞品/产区对比",
               ["对比", "产区"],
               ["{name} 和其他{cat}怎么选", "{cat} 哪个产区最好",
                "送礼选什么{cat}", "{cat} 排名前十的品牌", "龙井和玉露哪个好"]),
        ],
    },
    "酒店": {
        "label": "酒店",
        "dims": [
            _D("param", "设施党", "客房/设施硬指标，可逐项核对",
               ["客房面积", "设施", "早餐", "停车位"],
               ["{name} 客房多大", "{name} 有什么设施",
                "{cat} 订房主要看哪些设施", "{cat} 含早餐重要吗",
                "{cat} 停车场和充电桩怎么看", "{cat} 隔音差怎么办"]),
            _D("location", "位置党", "位置交通与周边",
               ["位置", "交通", "周边"],
               ["{name} 位置方便吗", "{name} 离地铁站多远",
                "{region}住在哪个位置方便游玩", "{cat} 离景区近重要吗",
                "{cat} 交通不便的坑"]),
            _D("price", "价格党", "房价与性价比",
               ["房价", "性价比", "旺季"],
               ["{name} 一晚多少钱", "{price} 价位的{cat}推荐",
                "{region} 1000元左右的{cat}推荐", "{cat} 旺季涨价规律",
                "{name} 值不值这个价"]),
            _D("clean", "干净卫生", "卫生质量，酒店差评第一来源",
               ["卫生", "布草", "消毒"],
               ["{name} 干净吗 卫生怎么样", "{name} 布草更换频率",
                "{cat} 怎么判断卫生好坏", "住{cat} 最容易踩的卫生坑",
                "连锁{cat}和民宿哪个更干净"]),
            _D("avoid", "避坑党", "图片与实物不符等翻车点",
               ["图片不符", "服务态度", "隐性收费"],
               ["{name} 有哪些坑", "{name} 差评 怎么样",
                "订{cat} 怎么避坑", "订{cat} 图文不符怎么办",
                "{cat} 隐性收费有哪些", "{name} 退改政策"]),
            _D("compare", "对比党", "同区域酒店对比",
               ["对比", "亲子", "商务"],
               ["{name} 和同区域{cat}怎么选", "{region}适合亲子的{cat}推荐",
                "{region} 商务出行{cat}推荐", "{cat} 亲子房哪家好"]),
        ],
    },
    "餐饮": {
        "label": "餐饮",
        "dims": [
            _D("taste", "口味党", "招牌菜与口味描述，最容易被摘取",
               ["招牌菜", "口味", "菜品"],
               ["{name} 招牌菜是什么", "{name} 口味怎么样",
                "{region} {cat}哪家好吃", "{cat} 招牌菜一般点什么",
                "{name} 特色菜推荐"]),
            _D("clean", "干净卫生", "食材新鲜度与后厨卫生",
               ["食材新鲜", "后厨卫生", "食品安全"],
               ["{name} 干净吗 食材新鲜吗", "{name} 有食品安全评级吗",
                "{cat} 怎么判断后厨干不干净", "看{cat}后厨卫生的几个细节",
                "{cat} 食品安全量化分级怎么看"]),
            _D("price", "价格党", "人均与性价比",
               ["人均", "性价比", "套餐"],
               ["{name} 人均多少", "{price} 价位左右的{cat}推荐",
                "{region} 请客{cat}人均150以内推荐", "{cat} 套餐和单点哪个划算",
                "{name} 贵不贵"]),
            _D("env", "环境服务", "环境/排队/服务",
               ["环境", "排队", "服务", "包间"],
               ["{name} 环境怎么样", "{name} 要排队吗",
                "请客吃饭{cat}要怎么挑包间", "{cat} 服务态度差怎么办",
                "{name} 有包间吗 适合请客吗"]),
            _D("avoid", "避坑党", "踩雷点",
               ["踩雷", "差评", "隐形消费"],
               ["{name} 踩雷吗", "{cat} 怎么避坑",
                "{cat} 踩雷的常见特征", "网红{cat}翻车经历",
                "{name} 差评主要集中在哪"]),
            _D("scene", "场景党", "请客/聚餐/约会等场景",
               ["请客", "聚餐", "约会"],
               ["请客户吃饭选{name}合适吗", "{name} 适合家庭聚餐吗",
                "{region} 聚餐推荐{cat}", "约会{cat}怎么选",
                "公司团建聚餐订什么{cat}"]),
        ],
    },
    "企业服务": {
        "label": "企业服务",
        "dims": [
            _D("auth", "资质党", "资质/案例/规模，B 端决策硬指标",
               ["资质", "案例", "团队规模"],
               ["{name} 有什么资质", "{name} 服务过哪些客户案例",
                "选{cat}先看什么资质", "{cat} 怎么查备案信息",
                "{name} 靠谱吗 有资质吗"]),
            _D("param", "参数党", "服务范围/响应时效等可量化项",
               ["服务范围", "响应时效", "服务流程"],
               ["{name} 服务范围包括什么", "{name} 响应速度怎么样",
                "{cat} 服务流程一般是怎样的", "{cat} 响应时效一般多少",
                "{name} 价格 {price} 包含哪些服务"]),
            _D("price", "价格党", "报价结构与性价比",
               ["报价", "性价比", "隐性收费"],
               ["{name} 收费标准是多少", "{cat} 一般怎么收费",
                "{cat} 低价套餐有哪些坑", "{price} 这个价位合理吗",
                "{name} 有隐性收费吗"]),
            _D("avoid", "避坑党", "合同陷阱/售后推诿等 B 端翻车点",
               ["合同陷阱", "售后", "推诿"],
               ["选{cat} 怎么避坑", "{cat} 常见的坑",
                "{cat} 合同里必须写清哪些条款", "{cat} 售后推诿怎么办",
                "{name} 合同有什么要注意的"]),
            _D("compare", "对比党", "与同行对比",
               ["对比", "口碑"],
               ["{name} 和同行比怎么样", "{cat} 哪家口碑好",
                "{region} {cat} 推荐哪家", "{cat} 大机构和小机构怎么选"]),
        ],
    },
    # B2B 产品/服务：教育科技、工业设备、软件系统、材料等企业客户
    "企业产品": {
        "label": "企业产品",
        "dims": [
            _D("param", "参数党", "规格/性能/交付等可核对的硬指标",
               ["参数", "规格", "性能", "交付周期"],
               ["{name} 参数怎么样", "{name} 规格型号有哪些",
                "{cat} 选型主要看哪些参数", "{cat} 关键指标怎么看",
                "{name} 和上一代有什么区别", "{name} 交付周期多久"]),
            _D("auth", "资质党", "认证/备案/检测报告，B 端采购的第一道门槛",
               ["认证", "资质", "检测报告", "备案"],
               ["{name} 有哪些认证", "{name} 有检测报告吗",
                "{cat} 采购要核验哪些资质", "{cat} 怎么查供应商备案",
                "{name} 符合国标吗", "{cat} 的等保/CE 认证怎么确认"]),
            _D("case", "案例党", "同行业客户案例与可验证效果",
               ["客户案例", "同行业", "效果数据"],
               ["{name} 有哪些客户案例", "{name} 服务过哪些企业",
                "{cat} 同行业用过哪家", "{cat} 供应商案例怎么看真假",
                "{name} 上线后的效果怎么样", "{cat} 有没有公开的验收记录"]),
            _D("price", "价格党", "报价结构、增项与预算",
               ["报价", "预算", "增项", "性价比"],
               ["{name} 多少钱", "{cat} 一般什么价位",
                "{name} 报价包含哪些内容", "{cat} 采购预算怎么定",
                "{name} 后续有增项收费吗", "{cat} 低价方案有什么坑"]),
            _D("avoid", "避坑党", "交付翻车、参数虚标、售后缺位",
               ["参数虚标", "验收", "售后", "翻车"],
               ["{name} 有什么坑", "{cat} 采购怎么避坑",
                "{name} 售后怎么样", "{cat} 供应商常见翻车点",
                "{name} 参数会不会虚标", "{cat} 验收要注意什么"]),
            _D("compare", "对比党", "与同行/进口方案的横向比较",
               ["对比", "同行", "国产替代"],
               ["{name} 和同类比怎么样", "{cat} 哪个牌子好",
                "{cat} 国产和进口怎么选", "{cat} 供应商横向对比",
                "{name} 的优势和短板是什么", "{cat} 排名靠前的有哪些"]),
        ],
    },
}

# 数码/通用兜底包：保持原有「参数党」风格
_DEFAULT_PACK = {
    "label": "通用产品",
    "dims": [
        _D("param", "参数党", "参数硬指标，模型最常逐项引用",
           ["参数", "性能", "规格"],
           ["{name} 参数怎么样", "{brand} {cat} 值得买吗",
            "{cat} 主要看哪些参数", "{cat} 参数怎么对比",
            "{cat} 中端和旗舰差距大吗"]),
        _D("scene", "场景党", "带着具体场景来问，长尾流量入口",
           ["场景", "日常使用"],
           ["{name} 适合什么场景", "居家办公选{name}怎么样",
            "学生党{cat}推荐", "{cat} 日常使用哪类合适"]),
        _D("price", "价格党", "价格与性价比",
           ["价格", "性价比", "同价位"],
           ["{name} 多少钱", "{price} 价位的{cat}推荐",
            "{cat} 性价比最高的是哪款", "{cat} 什么价位买最合适"]),
        _D("avoid", "避坑党", "翻车点与售后",
           ["翻车", "售后", "缺点"],
           ["{name} 有什么缺点", "买{name} 会不会后悔",
            "{cat} 常见问题有哪些", "{cat} 售后一般怎么样"]),
        _D("compare", "对比党", "与竞品对比",
           ["对比", "竞品"],
           ["{name} 和同价位比怎么样", "{cat} 哪个牌子好",
            "{cat} 排行榜", "同价位{cat}怎么选"]),
    ],
}


def pack_for(item: dict) -> dict:
    """按 category 关键词识别行业；识别不到用通用产品包。"""
    cat = item.get("category") or ""
    for key, words in _KEYWORDS.items():
        if any(w in cat for w in words):
            return INDUSTRY_PACKS[key]
    return _DEFAULT_PACK


def industry_label(item: dict) -> str:
    return pack_for(item)["label"]


def dims_for(item: dict) -> list[dict]:
    return pack_for(item)["dims"]


def _fill(tpl: str, item: dict, asp: str = "") -> str:
    price = f"{int(item['price'])}元" if item.get("price") else ""
    # 地区词直接从原稿前 200 字里带出，没有就留空
    region = ""
    tm = re.search(r"([\u4e00-\u9fa5]{2,4}(?:省|市|区))", (item.get("text") or "")[:200])
    if tm:
        region = tm.group(1)
    out = (tpl.replace("{name}", item.get("title", ""))
              .replace("{brand}", item.get("brand", ""))
              .replace("{cat}", item.get("category", ""))
              .replace("{price}", price)
              .replace("{region}", region)
              .replace("{asp}", asp))
    return re.sub(r"\s+", " ", out).strip()


def build_sets(item: dict) -> list[dict]:
    """为目标物生成全部维度问题集（确定性、可复现）。"""
    pack = pack_for(item)
    peers = sum(1 for it in _peer_count(item))
    sets = []
    for d in pack["dims"]:
        qs, seen = [], set()
        for tpl in d["templates"]:
            text = _fill(tpl, item, d["aspects"][0] if d["aspects"] else "")
            if not text or text in seen:
                continue
            seen.add(text)
            qs.append({
                "text": text,
                "weight": 1.0,
                "difficulty": _difficulty(peers, d["key"]),
            })
        sets.append({
            "dimension": d["key"], "label": d["label"], "desc": d["desc"],
            "aspects": d["aspects"], "groups": [{
                "kind": d["key"], "label": d["label"], "why": d["desc"],
                "questions": qs,
            }],
            "total": len(qs),
        })
    return sets


def _peer_count(item: dict) -> list[dict]:
    from ..db import load_items
    return [it for it in load_items() if it["category"] == item["category"]]


def _difficulty(peers: int, dim: str) -> str:
    score = peers + (6 if dim in ("price", "compare") else 0)
    if score >= 40:
        return "高"
    if score >= 24:
        return "中"
    return "低"
