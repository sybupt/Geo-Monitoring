"""渠道硬约束校验。

投喂失败最常见的原因不是"写得不好"，而是"不合平台规矩"：字数超了、
标题太长、放了站外链接、用了极限词。这些是硬约束，跟模型偏好无关，
必须在生成后立刻拦住，而不是发出去等限流。
"""
from __future__ import annotations

import re

from ..cells.registry import ChannelEntry
from ..geo.rules import HYPE_WORDS

FORBIDDEN_BY_CHANNEL: dict[str, tuple[str, ...]] = {
    "zhihu": ("加微信", "扫码", "私信我", "关注公众号", "点击下方链接"),
    "wechat_mp": ("点击阅读原文领取", "转发抽奖"),
    "xiaohongshu": ("加V", "私信", "代购", "全网最低"),
    "baijiahao": ("加微信", "扫码关注"),
    "toutiao": ("加微信", "扫码关注", "点击下方链接"),
    "dianping": ("加微信", "扫码", "私信我", "全网最低", "商家推荐"),
    "gov_portal": ("限时优惠", "抢购", "立即购买", "加微信", "扫码"),
    "ota": ("加微信", "扫码", "私信我"),
    "reddit": ("dm me", "check my profile", "buy now"),
    "medium": ("buy now",),
}

_URL = re.compile(r"https?://\S+")


def _cn_ratio(text: str) -> float:
    if not text:
        return 0.0
    cn = len(re.findall(r"[\u4e00-\u9fa5]", text))
    return cn / max(1, len(text))


def check(text: str, title: str, channel: ChannelEntry) -> dict:
    """返回 {passed, violations[], stats}，violations 每条都带可执行修复建议。"""
    body = text or ""
    chars = len(re.sub(r"\s", "", body))
    links = _URL.findall(body)
    violations: list[dict] = []

    if chars < channel.min_words:
        violations.append({
            "key": "min_words", "level": "block",
            "detail": f"正文 {chars} 字，低于 {channel.name} 下限 {channel.min_words} 字",
            "fix": f"补充参数、适用人群或问答，至少写到 {channel.min_words} 字",
        })
    if chars > channel.max_words:
        violations.append({
            "key": "max_words", "level": "warn",
            "detail": f"正文 {chars} 字，超过 {channel.name} 上限 {channel.max_words} 字",
            "fix": f"删减到 {channel.max_words} 字以内，优先砍背景铺垫",
        })
    if len(links) > channel.max_links:
        violations.append({
            "key": "links", "level": "block" if channel.max_links == 0 else "warn",
            "detail": f"外链 {len(links)} 个，{channel.name} 上限 {channel.max_links} 个",
            "fix": "改为在文末列出处名称与日期，不放可点击外链",
        })
    if len(title) > channel.title_max:
        violations.append({
            "key": "title_len", "level": "warn",
            "detail": f"标题 {len(title)} 字，超过 {channel.title_max} 字",
            "fix": "把品牌名与核心卖点前置，删掉修饰词",
        })

    hype = [w for w in HYPE_WORDS if w in body]
    if hype:
        violations.append({
            "key": "hype", "level": "warn",
            "detail": f"含极限词：{'、'.join(hype[:5])}",
            "fix": "替换为可验证描述（如把「最强」改为「在 X 场景下实测更稳」）",
        })

    for word in FORBIDDEN_BY_CHANNEL.get(channel.id, ()):
        if word.lower() in body.lower():
            violations.append({
                "key": "platform_rule", "level": "block",
                "detail": f"含平台禁止表述：{word}",
                "fix": "删除导流话术，改为中性说明",
            })

    if channel.locale == "en":
        ratio = _cn_ratio(body)
        if ratio > 0.15:
            violations.append({
                "key": "language", "level": "block",
                "detail": f"中文占比 {ratio:.0%}，{channel.name} 需要英文正文",
                "fix": "先出中文母稿，再走翻译流程生成英文版",
            })

    passed = not any(v["level"] == "block" for v in violations)
    return {
        "passed": passed,
        "violations": violations,
        "stats": {"chars": chars, "links": len(links), "title_len": len(title),
                  "hype": len(hype)},
    }
