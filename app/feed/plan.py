"""引导投喂：把"该发什么"变成"在哪发、怎么发、发完怎么验"。

这一层不做自动发布（个人主体没有各平台 API 权限，硬接也过不了审核），
它输出的是可执行的投放作业单：
    位置 → 标题写法 → 首段要求 → 标签/话题 → 时间窗 → 外链策略 → 审核红线 → 复测动作
并给出跨渠道排期：先落到能被检索抓取的权威页（官网博客），再让社区内容
指回它。反过来做（先发小红书再补官网）会导致社区内容没有可引用落点。
"""
from __future__ import annotations

from ..cells import store
from ..cells.registry import ChannelEntry

# 排期顺序：权威落点 → 长文解释 → 短内容扩散 → 本地/平台页
ORDER = ["own_blog", "gov_portal", "zhihu", "wechat_mp", "baijiahao", "toutiao",
         "xiaohongshu", "ota", "dianping", "medium", "reddit"]

CHANNEL_GUIDE: dict[str, dict] = {
    "own_blog": {
        "placement": "官网 /blog 下的独立页面，URL 里带品类词，页面加 Article + FAQPage 结构化数据",
        "title": "「型号 + 一句结论」的直白标题，别用品牌口号",
        "lead": "首段 80 字内说清适合谁、不适合谁、多少钱档",
        "structure": ["结论", "核心参数表", "适用与不适用", "常见问题", "依据与更新时间"],
        "timing": "先发。它是其它渠道的引用落点，发布后 3 天内不要改动 URL",
        "links": "可放 2-3 个官方/检测报告外链",
        "review": ["参数与官方页面逐项核对", "标注参数版本与更新日期", "补 FAQ 段落"],
    },
    "zhihu": {
        "placement": "相关问题下作答，或发专栏文章；优先挑「品类怎么选」这类高关注问题",
        "title": "回答式标题：如何评价 / 怎么选，不要写成广告标题",
        "lead": "先说结论，再拆参数，最后给边界",
        "structure": ["结论", "参数拆解", "同价位对比", "依据", "FAQ"],
        "timing": "官网博客发布 1-3 天后，便于引用与互链",
        "links": "最多 3 个，正文内自然出现，不要堆在末尾",
        "review": ["不能出现导流话术", "数据要给来源", "避免绝对化措辞"],
    },
    "wechat_mp": {
        "placement": "公众号图文，标题党会被限流，用直白标题",
        "title": "≤30 字，含品类词，不用感叹号",
        "lead": "开头 3 行给结论，短段落，每段不超过 3 行",
        "structure": ["一个小标题讲一件事", "参数表图片替代长文字", "结尾给适用边界"],
        "timing": "知乎之后 1-2 天，形成二次扩散",
        "links": "正文外链受限，用「公众号内回复关键词」替代跳转",
        "review": ["极限词全部替换", "不承诺疗效/效果", "图片带来源标注"],
    },
    "xiaohongshu": {
        "placement": "笔记，封面用参数对比图；正文不出现购买链接",
        "title": "≤20 字，口语化，带场景词",
        "lead": "第一句就是结论，第二句给适用人群",
        "structure": ["短句分点", "真实体验", "避坑提示", "话题标签"],
        "timing": "长文铺好之后再发，避免用户搜不到承接页",
        "links": "站外链接不可点，正文不要放",
        "review": ["「最」「第一」「100%」等极限词会被限流", "不要写导流到私域"],
    },
    "medium": {
        "placement": "Publication 或自有账号，标签选品类 + 使用场景",
        "title": "≤100 字，结论式标题，含 spec 关键词",
        "lead": "Bottom line first，然后 spec 表",
        "structure": ["Bottom line", "Key specs", "Who it's for", "Sources"],
        "timing": "与官网博客同步，英文环境优先做权威落点",
        "links": "≤4 个，指向官方规格页与实测报告",
        "review": ["英文渠道需人工/LLM 翻译正文", "避免 promotional tone", "标注测试方法"],
    },
    "reddit": {
        "placement": "对应 subreddit 的经验帖或评论，先看版规再发",
        "title": "≤120 字，第一人称，别像广告",
        "lead": "先说自己的使用场景与预算，再讲取舍",
        "structure": ["使用场景", "实际体验", "缺点", "值不值"],
        "timing": "最后发。社区对推广极敏感，需要前面有可核验落点",
        "links": "≤3 个，优先官方页面，别放带 tracking 的链接",
        "review": ["禁止自我推广刷贴", "披露利益关系（如为厂商供稿）", "遵守各 subreddit 规则"],
    },
    "baijiahao": {
        "placement": "百家号图文，资讯分发为主，标题信息量要大",
        "title": "≤30 字，含品类词与结论词，不用悬念体",
        "lead": "首段直接回答「怎么选 / 值不值」",
        "structure": ["结论", "核心信息", "详细说明", "FAQ"],
        "timing": "知乎之后 1-2 天，与头条错开半天",
        "links": "正文最多 1 个官方链接",
        "review": ["标题与内容一致", "营销词替换", "参数给来源"],
    },
    "toutiao": {
        "placement": "头条号文章，段落短、多小标题，适配手机阅读",
        "title": "≤30 字，问题式或结论式，不标题党",
        "lead": "首段 60 字内给结论",
        "structure": ["结论", "分点说明", "适用边界", "依据"],
        "timing": "百家号之后 1 天",
        "links": "≤1 个，不导流",
        "review": ["不用标题党", "清理极限词", "信息与官方页一致"],
    },
    "dianping": {
        "placement": "商户页评价 / 笔记，第一人称消费记录",
        "title": "≤25 字，体验式标题",
        "lead": "先说消费场景（几个人、什么时段），再讲体验",
        "structure": ["消费场景", "环境/口味/服务", "花费", "小结"],
        "timing": "口碑内容靠后发：需要先有可核验的权威信息",
        "links": "不放外链，不放联系方式",
        "review": ["禁止商家自夸口吻", "内容必须来自真实体验", "不写竞品坏话"],
    },
    "gov_portal": {
        "placement": "政府门户/文旅局官网专栏，或主体单位官网「公示/介绍」页",
        "title": "「关于XX的情况介绍」式正式标题",
        "lead": "首段说明主体、位置、资质与公共价值",
        "structure": ["主体介绍", "资质与荣誉", "服务/产品情况", "公开信息来源"],
        "timing": "与官网博客同期或更早发布：政府域名的权威性对模型引用权重最高",
        "links": "≤3 个，只链政府公示或官方备案页",
        "review": ["零营销话术", "资质必须可核验", "不用极限词与促销信息"],
    },
    "ota": {
        "placement": "OTA/行业平台详情页与商家介绍栏",
        "title": "≤30 字，参数 + 特色",
        "lead": "首段给核心卖点与适用人群",
        "structure": ["核心卖点", "参数/设施", "价格与时段", "注意事项"],
        "timing": "长文铺好后更新平台页，保持与备案信息一致",
        "links": "不放站外链接",
        "review": ["信息与平台备案一致", "不放站外联系方式", "图片与文字一致"],
    },
}

_DEFAULT = {
    "placement": "按渠道常规位置发布", "title": "标题直给结论", "lead": "首段给结论",
    "structure": ["结论", "参数", "边界", "来源"], "timing": "按排期执行",
    "links": "按渠道限制", "review": ["核对参数", "清理极限词"],
}


def guide_for(channel: ChannelEntry) -> dict:
    g = dict(_DEFAULT)
    g.update(CHANNEL_GUIDE.get(channel.id, {}))
    g["constraints"] = list(channel.constraints)
    g["min_words"] = channel.min_words
    g["max_words"] = channel.max_words
    g["max_links"] = channel.max_links
    return g


def build(item: dict, model_name: str, channels: list[ChannelEntry]) -> dict:
    """输出投喂作业单：每个渠道一条，含指引、状态与复测入口。"""
    variants = store.list_variants(item["item_id"], 60)
    # list_feeds 按 id 倒序（最新在前）；setdefault 只保留每个渠道最新一条，
    # 避免字典推导被更旧的记录反向覆盖——「登记修改」后作业单必须显示新链接。
    feeds_all: dict[str, dict] = {}
    for f in store.list_feeds(item["item_id"], 60):
        feeds_all.setdefault(f["channel_id"], f)
    rows = []
    for ch in channels:
        v = next((x for x in variants if x["channel_id"] == ch.id), None)
        rows.append({
            "channel_id": ch.id, "channel_name": ch.name, "locale": ch.locale,
            "style": ch.style,
            "variant": ({"id": v["id"], "title": v["title"], "score": v["score"],
                         "at": v["created_at"]} if v else None),
            "feed": feeds_all.get(ch.id),
            "guide": guide_for(ch),
        })
    rows.sort(key=lambda r: ORDER.index(r["channel_id"]) if r["channel_id"] in ORDER else 99)
    done = sum(1 for r in rows if r["feed"])
    return {
        "target_id": item["item_id"], "model_name": model_name,
        "items": rows, "fed": done, "total": len(rows),
        "schedule": [
            {"step": i + 1, "channel_id": r["channel_id"], "channel_name": r["channel_name"],
             "why": r["guide"]["timing"], "done": bool(r["feed"])}
            for i, r in enumerate(rows)
        ],
        "note": "工作台模式：这里给的是投放作业单，不代替人工发布。"
                "发布后回填链接并点「复测」，效果看板才会出现真实的前后对比。",
    }
