"""渠道 × 模型 注册表。

矩阵的两个维度都必须是"能被检索到"的实体，否则规则无从学起：
  - 模型维度 = 带联网检索能力的入口（纯离线 LLM 不会引用你的页面）；
  - 渠道维度 = 内容实际落地的地方，它同时带来"硬约束"（字数/外链/审核）
    和"软偏好"（模型更愿意引用哪类站点）。

本文件只放静态目录（厂商、地区、是否带检索、硬约束），不放学习结果。
学习出来的权重存到 cell_rule_sets 表，见 store.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import GEO_RULE_LABELS

# 规则维度（10 个检查器）+ 客观维度，共同构成学习特征
RULE_DIMS: list[str] = list(GEO_RULE_LABELS.keys())
EXTRA_DIMS = ["length_fit", "has_table", "has_faq", "freshness", "authority", "link_count", "depth"]
ALL_DIMS: list[str] = RULE_DIMS + EXTRA_DIMS

DIM_LABELS: dict[str, str] = {
    **GEO_RULE_LABELS,
    "length_fit": "篇幅适配渠道",
    "has_table": "含表格结构",
    "has_faq": "含问答段落",
    "freshness": "时效新鲜度",
    "authority": "来源权威度",
    "link_count": "外链数量",
    "depth": "信息密度",
}


@dataclass(frozen=True)
class ModelEntry:
    id: str
    name: str
    vendor: str
    region: str            # cn / global
    retrieval: bool        # 是否带联网检索（False 则无法被引用）
    api_style: str         # openai / anthropic / gemini / none
    locale: str            # zh / en
    prior: dict[str, float] = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class ChannelEntry:
    id: str
    name: str
    region: str
    locale: str
    min_words: int
    max_words: int
    max_links: int
    title_max: int
    style: str             # 写作风格标签
    constraints: tuple[str, ...] = ()
    bias: float = 0.0      # 模型对该渠道的平均引用倾向（先验）


# 先验权重仅用于「模拟器」生成引用标签，不是学习结果。
# 真实接入 API 后，这一层会被真实引用数据替换。
MODELS: tuple[ModelEntry, ...] = (
    ModelEntry(
        id="deepseek_web", name="DeepSeek 联网", vendor="深度求索", region="cn",
        retrieval=True, api_style="openai", locale="zh",
        prior={"structured": 0.9, "term_defined": 0.9, "quantified": 0.7, "conclusion_first": 0.6,
               "sourced": 0.5, "depth": 0.8, "has_table": 0.6},
        note="中文技术问答偏强，偏好结构化与术语定义",
    ),
    ModelEntry(
        id="qwen_web", name="通义千问 联网", vendor="阿里云", region="cn",
        retrieval=True, api_style="openai", locale="zh",
        prior={"conclusion_first": 0.9, "quantified": 0.9, "structured": 0.8, "has_faq": 0.7,
               "sourced": 0.7, "atomic": 0.7, "no_hype": 0.6},
        note="中文综合检索均衡，偏好结论前置、量化参数与清晰分点问答",
    ),
    ModelEntry(
        id="doubao_web", name="豆包", vendor="字节跳动", region="cn",
        retrieval=True, api_style="openai", locale="zh",
        prior={"conclusion_first": 1.0, "faq": 0.0, "has_faq": 0.8, "atomic": 0.7,
               "length_fit": 0.8, "no_hype": 0.5, "freshness": 0.5},
        note="消费向，短句、结论前置、问答段落更容易被摘取",
    ),
    ModelEntry(
        id="kimi_web", name="Kimi 联网", vendor="月之暗面", region="cn",
        retrieval=True, api_style="openai", locale="zh",
        prior={"sourced": 1.0, "freshness": 0.9, "quantified": 0.8, "authority": 0.8,
               "structured": 0.5, "balanced": 0.5},
        note="长文档与溯源，偏好带来源与时效的内容",
    ),
    ModelEntry(
        id="chatgpt_search", name="ChatGPT Search", vendor="OpenAI", region="global",
        retrieval=True, api_style="openai", locale="en",
        prior={"sourced": 1.0, "authority": 1.0, "structured": 0.8, "freshness": 0.7,
               "balanced": 0.6, "no_hype": 0.8, "link_count": 0.4},
        note="偏好权威域名与可核验来源，对营销腔敏感",
    ),
    ModelEntry(
        id="perplexity", name="Perplexity", vendor="Perplexity", region="global",
        retrieval=True, api_style="openai", locale="en",
        prior={"sourced": 1.1, "quantified": 0.9, "freshness": 1.0, "has_table": 0.7,
               "atomic": 0.8, "authority": 0.9},
        note="强引用型，几乎逐句标注来源，时效与数字权重高",
    ),
    ModelEntry(
        id="gemini_ground", name="Gemini (Grounding)", vendor="Google", region="global",
        retrieval=True, api_style="gemini", locale="en",
        prior={"has_table": 0.9, "structured": 0.9, "authority": 0.9, "term_defined": 0.7,
               "sourced": 0.7, "depth": 0.6},
        note="偏结构化与实体清晰，表格与定义段落易被采纳",
    ),
)


CHANNELS: tuple[ChannelEntry, ...] = (
    ChannelEntry(
        id="own_blog", name="官网博客", region="both", locale="zh", min_words=800, max_words=6000,
        max_links=8, title_max=60, style="完整结构：结论、参数、依据、FAQ",
        constraints=(), bias=0.3,
    ),
    ChannelEntry(
        id="wechat_mp", name="微信公众号", region="cn", locale="zh", min_words=800, max_words=4000,
        max_links=1, title_max=30, style="口语化长文，短段落，有小标题",
        constraints=("正文外链受限", "标题党会被限流"), bias=0.08,
    ),
    ChannelEntry(
        id="zhihu", name="知乎", region="cn", locale="zh", min_words=600, max_words=3500,
        max_links=3, title_max=40, style="问答体，先给结论再拆解，需数据与来源",
        constraints=("禁止明显导流", "禁止仅贴链接"), bias=0.25,
    ),
    ChannelEntry(
        id="xiaohongshu", name="小红书", region="cn", locale="zh", min_words=150, max_words=1000,
        max_links=0, title_max=20, style="短句、分点、话题标签，弱化硬广",
        constraints=("站外链接基本不可点", "极限词会被限流"), bias=-0.15,
    ),
    ChannelEntry(
        id="baijiahao", name="百家号", region="cn", locale="zh", min_words=600, max_words=3000,
        max_links=1, title_max=30, style="资讯长文，直给结论，段落短",
        constraints=("标题与内容需一致", "营销味重会限流"), bias=0.1,
    ),
    ChannelEntry(
        id="toutiao", name="今日头条", region="cn", locale="zh", min_words=500, max_words=3000,
        max_links=1, title_max=30, style="资讯体，标题信息量大，段落短、多小标题",
        constraints=("标题党会被扣分", "禁止外链导流"), bias=0.06,
    ),
    ChannelEntry(
        id="dianping", name="大众点评/口碑", region="cn", locale="zh", min_words=200, max_words=1200,
        max_links=0, title_max=25, style="消费体验第一人称，讲环境/口味/服务",
        constraints=("禁止商家自夸口吻", "虚假评价会被删"), bias=-0.05,
    ),
    ChannelEntry(
        id="gov_portal", name="政府官网/文旅局", region="cn", locale="zh", min_words=600, max_words=4000,
        max_links=3, title_max=40, style="正式书面语，突出资质、荣誉、公共价值",
        constraints=("禁止任何营销话术", "需可核验的资质与荣誉"), bias=0.35,
    ),
    ChannelEntry(
        id="ota", name="OTA/行业平台", region="cn", locale="zh", min_words=300, max_words=2000,
        max_links=0, title_max=30, style="平台详情页文案，参数齐全，真实体验导向",
        constraints=("信息需与平台备案一致", "禁止站外联系方式"), bias=0.15,
    ),
    ChannelEntry(
        id="reddit", name="Reddit", region="global", locale="en", min_words=150, max_words=2500,
        max_links=3, title_max=120, style="英文，第一人称经验，避免推广口吻",
        constraints=("禁止自我推广刷贴", "需遵守各 subreddit 规则"), bias=0.05,
    ),
    ChannelEntry(
        id="medium", name="Medium", region="global", locale="en", min_words=600, max_words=5000,
        max_links=4, title_max=100, style="英文长文，标题清晰，含数据与结论",
        constraints=(), bias=0.12,
    ),
)

MODEL_BY_ID = {m.id: m for m in MODELS}
CHANNEL_BY_ID = {c.id: c for c in CHANNELS}


# ------------------------------------------------------- 会员套餐 ----
# 套餐决定第 4 环节能生成哪些渠道的投喂文案；渠道清单在这里集中配置，可直接增删。
PLANS: dict[str, dict] = {
    "trial": {
        "label": "试用（免费）", "price": "免费", "rank": 0,
        "channels": ["own_blog", "wechat_mp"],
        "desc": "可投放 2 个平台：官网博客 + 微信公众号",
    },
    "standard": {
        "label": "普通会员", "price": "¥20,000 / 年", "rank": 1,
        "channels": ["own_blog", "wechat_mp", "zhihu", "xiaohongshu", "baijiahao", "toutiao"],
        "desc": "可投放 6 个平台：官网博客、微信公众号、知乎、小红书、百家号、今日头条",
    },
    "premium": {
        "label": "高级会员", "price": "¥40,000 / 年", "rank": 2,
        "channels": ["own_blog", "wechat_mp", "zhihu", "xiaohongshu", "baijiahao", "toutiao",
                     "dianping", "gov_portal", "ota"],
        "desc": "可投放 9 个平台：普通会员全部 + 大众点评/口碑、政府官网/文旅局、OTA/行业平台",
    },
}

PLAN_ORDER = ["trial", "standard", "premium"]


def plan_channels(plan: str) -> list[ChannelEntry]:
    p = PLANS.get(plan) or PLANS["trial"]
    return [CHANNEL_BY_ID[c] for c in p["channels"] if c in CHANNEL_BY_ID]


def channels_for_plan(plan: str) -> list[str]:
    return (PLANS.get(plan) or PLANS["trial"])["channels"]


def cells() -> list[tuple[ModelEntry, ChannelEntry]]:
    """全部模型×渠道组合（6×6=36 格）。"""
    return [(m, c) for m in MODELS for c in CHANNELS]


def describe() -> dict:
    return {
        "models": [
            {"id": m.id, "name": m.name, "vendor": m.vendor, "region": m.region,
             "retrieval": m.retrieval, "locale": m.locale, "note": m.note}
            for m in MODELS
        ],
        "channels": [
            {"id": c.id, "name": c.name, "region": c.region, "locale": c.locale,
             "min_words": c.min_words, "max_words": c.max_words, "max_links": c.max_links,
             "title_max": c.title_max, "style": c.style, "constraints": list(c.constraints)}
            for c in CHANNELS
        ],
        "plans": [
            {"id": k, "label": p["label"], "price": p["price"],
             "channels": p["channels"], "desc": p["desc"]}
            for k, p in PLANS.items()
        ],
        "dims": [{"key": k, "label": DIM_LABELS.get(k, k)} for k in ALL_DIMS],
    }
