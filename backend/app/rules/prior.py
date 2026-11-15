"""规则先验库：在还没有实测数据时，先按「已知的引擎信源画像」给一套可用规则。

**诚实声明**：这里的权重来自公开的引擎信源结构观察（各家偏好的站点、体裁），
不是我们自己测出来的数字。所以 ``source="prior"``、``confidence="待验证（先验）"``，
对外交付时不能说是「实测得出的模型偏好」。等接了真实引擎，用
``extractor.learn_from_samples()`` 跑一遍，``source="measured"`` 的规则会覆盖同键先验。
"""

from __future__ import annotations

from app.rules.dsl import CHANNEL_SPECS, DIMENSIONS, Rule

# 引擎 → 各特征维度的先验权重（0~1）。缺省表示不关心。
ENGINE_PRIOR_WEIGHTS: dict[str, dict[str, float]] = {
    "doubao": {
        "number_density": 0.85,
        "local_geo": 0.8,
        "conclusion_first": 0.7,
        "list_structure": 0.6,
        "freshness": 0.5,
        "emoji_markers": 0.35,
    },
    "kimi": {
        "heading_hierarchy": 0.85,
        "comparison": 0.8,
        "source_attribution": 0.75,
        "length_fit": 0.7,
        "list_structure": 0.6,
    },
    "deepseek": {
        "source_attribution": 0.85,
        "comparison": 0.75,
        "number_density": 0.7,
        "heading_hierarchy": 0.6,
        "conclusion_first": 0.55,
    },
    "qwen": {
        "list_structure": 0.8,
        "number_density": 0.75,
        "local_geo": 0.65,
        "conclusion_first": 0.55,
    },
    "zhipu": {
        "conclusion_first": 0.85,
        "heading_hierarchy": 0.7,
        "source_attribution": 0.6,
        "official_tone": 0.45,
    },
    "wenxin": {
        "conclusion_first": 0.8,
        "official_tone": 0.75,
        "freshness": 0.7,
        "source_attribution": 0.65,
        "number_density": 0.5,
    },
    "yuanbao": {
        "first_person": 0.75,
        "heading_hierarchy": 0.65,
        "local_geo": 0.6,
        "length_fit": 0.6,
        "number_density": 0.5,
    },
    # 跨引擎共性（AutoGEO 论文结论：跨引擎规则重叠 78~84%，共性部分可复用）
    "shared": {
        "number_density": 0.7,
        "entity_repetition": 0.65,
        "faq_block": 0.6,
        "conclusion_first": 0.6,
    },
}

# 引擎 → 渠道亲和度：决定「同一篇内容优先发哪个渠道才可能被它检索到」。
# 依据是各家检索池的构成（豆包偏抖音/头条、元宝偏公众号、文心偏百家号/百科、DeepSeek 偏知乎…）。
ENGINE_CHANNEL_AFFINITY: dict[str, dict[str, float]] = {
    "doubao": {"douyin": 0.9, "toutiao": 0.8, "dianping": 0.75, "xiaohongshu": 0.6, "baijiahao": 0.5},
    "kimi": {"zhihu": 0.9, "wechat": 0.55, "baijiahao": 0.5, "xiaohongshu": 0.4},
    "deepseek": {"zhihu": 0.85, "gov_news": 0.8, "baijiahao": 0.6, "wechat": 0.5},
    "qwen": {"dianping": 0.8, "xiaohongshu": 0.7, "baijiahao": 0.65, "meituan": 0.6},
    "zhipu": {"zhihu": 0.75, "baike": 0.8, "baijiahao": 0.6},
    "wenxin": {"baijiahao": 0.9, "baike": 0.85, "amap": 0.5, "zhihu": 0.45},
    "yuanbao": {"wechat": 0.9, "zhihu": 0.5, "xiaohongshu": 0.45},
    "mock": {"zhihu": 0.8, "dianping": 0.7, "xiaohongshu": 0.7, "baijiahao": 0.6, "douyin": 0.6},
}

# 行业修正：本地生活里「地域 + 价格 + 预约」比任何行业都重要
INDUSTRY_BONUS: dict[str, dict[str, float]] = {
    "local_life": {"local_geo": 0.15, "number_density": 0.1, "freshness": 0.05},
}


def best_channels(engine: str, limit: int = 3) -> list[dict[str, float]]:
    """该引擎最可能检索到的渠道（按亲和度降序）。"""
    aff = ENGINE_CHANNEL_AFFINITY.get(engine, {})
    return [{"channel": k, "affinity": v} for k, v in sorted(aff.items(), key=lambda x: -x[1])[:limit]]


def prior_rules(engine: str, industry: str = "local_life", channels: list[str] | None = None) -> list[Rule]:
    """按引擎生成先验规则。``channels`` 为空时只产出不限渠道的规则。"""
    weights = dict(ENGINE_PRIOR_WEIGHTS.get(engine, ENGINE_PRIOR_WEIGHTS["shared"]))
    bonus = INDUSTRY_BONUS.get(industry, {})
    for dim, b in bonus.items():
        weights[dim] = min(1.0, weights.get(dim, 0.4) + b)

    rules: list[Rule] = []
    for dim, w in sorted(weights.items(), key=lambda x: -x[1]):
        spec = DIMENSIONS.get(dim)
        if not spec:
            continue
        rules.append(
            Rule(
                dimension=dim,
                instruction=spec["instruction"],
                engine=engine,
                industry=industry,
                weight=round(w, 2),
                evidence_n=0,
                lift=0.0,
                source="prior",
                scope="shared" if engine == "shared" else "engine",
                note="先验规则，未经实测校准",
            )
        )

    # 渠道专属写法：同一维度在不同渠道要翻成不同的动作
    for ch in channels or []:
        spec = CHANNEL_SPECS.get(ch)
        if not spec:
            continue
        for dim in spec["supports"]:
            if dim not in weights or weights[dim] < 0.5:
                continue
            base = DIMENSIONS[dim]
            rules.append(
                Rule(
                    dimension=dim,
                    instruction=f"{base['instruction']}；渠道适配：{spec['genre']}，{spec['tone']}，篇幅 {spec['length'][0]}~{spec['length'][1]} 字",
                    engine=engine,
                    industry=industry,
                    channel=ch,
                    weight=round(min(1.0, weights[dim] + 0.05), 2),
                    source="prior",
                    scope="engine",
                    note=f"{spec['label']}渠道适配规则（先验）",
                )
            )
    return rules
