"""GEO 规则 DSL：把「模型喜欢什么」变成可执行、可统计、可复现的结构。

一条规则最少要能回答四个问题：
1. 这是**哪个引擎**的偏好（engine）——跨引擎规则可复用，但引擎专属规则效果最好；
2. 这是**哪个行业**的（industry）——跨行业重叠会骤降，所以必须分桶；
3. 说的是**哪个特征**（dimension）——必须是可检测的文本特征，不能是「要写得好」这种空话；
4. 有多大把握（evidence_n / lift）——没有证据量的规则不许进生产。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ── 特征维度 ────────────────────────────────────────────────────
# 每个维度都必须能被 features.extract() 用确定性规则检测出来（正则/计数），
# 不能依赖 LLM——否则「规则是否被满足」就变成了另一个黑箱。
DIMENSIONS: dict[str, dict[str, str]] = {
    "conclusion_first": {
        "label": "结论前置",
        "desc": "首段（前 120 字内）给出可被直接摘录的一句话结论",
        "instruction": "开头第一段就用一句话说清「谁、在哪个区域、解决什么问题、凭什么」，不要铺垫背景",
    },
    "number_density": {
        "label": "数字密度",
        "desc": "正文含具体数字（价格/面积/年限/服务量/评分）",
        "instruction": "至少给出 3 个可核实的数字（价格区间、营业年限、面积、服务人次、评分），禁止「很多/多年」这类模糊表述",
    },
    "list_structure": {
        "label": "列表结构",
        "desc": "正文使用编号或要点符号分条",
        "instruction": "把卖点拆成 3~6 条编号列表，每条以「动词+事实」开头",
    },
    "faq_block": {
        "label": "问答块",
        "desc": "正文含 Q/A 形式的问答对",
        "instruction": "结尾附 3~5 组「Q：…／A：…」，问题用用户真实会问的口语问法",
    },
    "heading_hierarchy": {
        "label": "小标题分层",
        "desc": "正文含 Markdown 小标题或中文序号小节",
        "instruction": "用 2~4 个小标题分段（如「适合谁／怎么选／价格与预约」），让信息可被切片引用",
    },
    "entity_repetition": {
        "label": "实体重复",
        "desc": "品牌名与地域词在正文中出现 ≥3 次",
        "instruction": "品牌全称出现 3 次以上，地域（城市/商圈）出现 2 次以上，保持自然不堆砌",
    },
    "source_attribution": {
        "label": "信源交代",
        "desc": "正文交代信息来源/资质/门店信息",
        "instruction": "写明信息来自门店公示/资质编号/公开点评数据，给模型一个可引用的出处",
    },
    "freshness": {
        "label": "时效信息",
        "desc": "正文含年份/月份/近期等时间锚点",
        "instruction": "补上时间锚点（更新于 2026 年、近 3 个月、当季活动），避免被判定为过期内容",
    },
    "comparison": {
        "label": "横向对比",
        "desc": "正文含适用场景或同类的对比结构",
        "instruction": "按「预算/场景/人群」做 2~3 组对比，帮用户做决策而不是只夸自己",
    },
    "first_person": {
        "label": "第一人称体验",
        "desc": "正文使用「我/我们」的体验叙述",
        "instruction": "用第一人称讲一次真实使用过程：什么时候去、花了多少、感受如何",
    },
    "emoji_markers": {
        "label": "视觉锚点",
        "desc": "正文含 emoji 或符号分点",
        "instruction": "用 emoji 做段落锚点（📍地址｜💰价格｜⭐体验），提高笔记类内容的可读性",
    },
    "official_tone": {
        "label": "客观口径",
        "desc": "正文为第三人称客观陈述，无夸张口语",
        "instruction": "用第三人称客观陈述，交代主体、地址、经营范围、资质，不出现夸张口语",
    },
    "local_geo": {
        "label": "地域锚点",
        "desc": "正文含城市/商圈/地标/交通信息",
        "instruction": "写清所在城市、商圈或地标、怎么到达（地铁/停车），本地问答强依赖这层信息",
    },
    "length_fit": {
        "label": "篇幅适配",
        "desc": "篇幅落在该渠道的合理区间内",
        "instruction": "按渠道控制篇幅：问答类 600~1200 字，笔记类 300~600 字，口播脚本 150~300 字",
    },
}

# 渠道画像：不同渠道的体裁、结构、篇幅不同，规则要按渠道翻译成写法
CHANNEL_SPECS: dict[str, dict[str, Any]] = {
    "zhihu": {
        "label": "知乎",
        "genre": "长文回答",
        "length": (500, 1400),
        "structure": ["开头结论", "分点论证", "适用场景", "FAQ"],
        "tone": "专业、克制、给依据",
        "supports": ["heading_hierarchy", "list_structure", "faq_block", "source_attribution", "comparison"],
    },
    "baijiahao": {
        "label": "百家号",
        "genre": "资讯稿",
        "length": (350, 1200),
        "structure": ["导语结论", "小标题分段", "信息补充"],
        "tone": "客观、信息密度高",
        "supports": ["heading_hierarchy", "number_density", "source_attribution", "freshness"],
    },
    "baike": {
        "label": "百科条目",
        "genre": "词条",
        "length": (300, 1000),
        "structure": ["定义句", "基本信息", "经营与服务", "参考"],
        "tone": "第三人称客观",
        "supports": ["conclusion_first", "official_tone", "source_attribution", "number_density"],
    },
    "wechat": {
        "label": "微信公众号",
        "genre": "推文",
        "length": (700, 1600),
        "structure": ["场景钩子", "故事化体验", "卖点拆解", "行动指引"],
        "tone": "亲和、有叙事",
        "supports": ["first_person", "heading_hierarchy", "list_structure", "local_geo"],
    },
    "xiaohongshu": {
        "label": "小红书",
        "genre": "探店笔记",
        "length": (300, 700),
        "structure": ["emoji 标题", "体验分点", "价格与地址", "标签"],
        "tone": "口语、真实体验",
        "supports": ["emoji_markers", "first_person", "list_structure", "local_geo", "number_density"],
    },
    "douyin": {
        "label": "抖音",
        "genre": "口播脚本",
        "length": (150, 400),
        "structure": ["3 秒钩子", "卖点快切", "地域锚点", "行动号召"],
        "tone": "短句、强节奏",
        "supports": ["conclusion_first", "local_geo", "number_density", "emoji_markers"],
    },
    "dianping": {
        "label": "大众点评",
        "genre": "商户评价/笔记",
        "length": (200, 800),
        "structure": ["体验结论", "分维度评价", "消费信息"],
        "tone": "真实、具体",
        "supports": ["first_person", "number_density", "local_geo", "list_structure"],
    },
    "meituan": {
        "label": "美团",
        "genre": "商户介绍",
        "length": (150, 600),
        "structure": ["一句话定位", "服务项", "价格与预约"],
        "tone": "简洁、信息优先",
        "supports": ["conclusion_first", "number_density", "local_geo"],
    },
    "amap": {
        "label": "高德地图",
        "genre": "地点信息",
        "length": (100, 400),
        "structure": ["地点定义", "地址与交通", "营业信息"],
        "tone": "事实优先",
        "supports": ["official_tone", "local_geo", "freshness"],
    },
    "gov_news": {
        "label": "政务/官媒",
        "genre": "通稿",
        "length": (400, 900),
        "structure": ["时间地点主体", "事实陈述", "官方口径"],
        "tone": "规范、无修饰",
        "supports": ["official_tone", "source_attribution", "freshness"],
    },
}


@dataclass
class Rule:
    """一条可执行规则。``weight`` 越大越该优先满足。"""

    dimension: str
    instruction: str
    engine: str = "shared"          # shared = 跨引擎通用
    industry: str = "local_life"
    channel: str = ""               # 空 = 不限渠道
    weight: float = 0.5
    evidence_n: int = 0
    lift: float = 0.0
    source: str = "prior"           # prior（先验）/ measured（实测）/ manual
    scope: str = "engine"           # shared / engine
    note: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dimension_label"] = DIMENSIONS.get(self.dimension, {}).get("label", self.dimension)
        return d

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.engine, self.industry, self.channel, self.dimension)


def rule_id(r: Rule) -> str:
    return f"{r.engine}:{r.industry}:{r.channel or '*'}:{r.dimension}"


def applies_to_channel(dimension: str, channel: str) -> bool:
    """该维度在这个渠道是否构成「合理要求」。

    口播脚本不该被要求写 FAQ，地图地点页不该被要求写第一人称体验——
    规则库如果不做这层过滤，生成器会被迫塞入不合适的段落，反而降低质量。
    渠道没定义画像时一律视为适用（保守放行）。
    """
    spec = CHANNEL_SPECS.get(channel)
    if not spec:
        return True
    return dimension in spec["supports"]


def confidence_label(r: Rule) -> str:
    """给运营看的把握度。先验规则永远不标「已验证」。"""
    if r.source == "measured" and r.evidence_n >= 20:
        return "已验证"
    if r.source == "measured" and r.evidence_n >= 8:
        return "初步验证"
    return "待验证（先验）"
