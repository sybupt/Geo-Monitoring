"""腾讯元宝适配器（浏览器自动化，数据等级 L3）。

**必须先说清楚这件事**：元宝没有公开 API。任何声称「API 接入元宝」的方案，
要么是拿混元 + 腾讯搜索自己拼（那不是元宝的回答），要么是爬网页。
本适配器选择**如实爬网页**，并为此付出三个代价，报表里必须体现：

1. 数据等级 L3：DOM 结构随前端发版变化，今天能抓明天可能失效；
2. 需要人工登录一次（扫码），登录态存 ``data/yuanbao_state.json``；
3. 比 API 慢一个数量级（每问一次开一个页面，约 8–20 秒）。

用法：
    python scripts/yuanbao_login.py      # 首次：弹出浏览器扫码登录
    # 之后跑批时自动复用登录态

口径纪律：
- 抓不到回答 → 抛 ``ProbeError``，绝不返回空串（否则会被判成「未提及」）；
- 登录态失效 → 明确报「需要重新登录」，而不是静默 0 命中；
- 引用只取回答区域内的外链，腾讯自家域名（yuanbao.tencent.com 等）不算信源。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from app.config import YUANBAO_CHANNEL, YUANBAO_HEADLESS, YUANBAO_STATE, YUANBAO_TIMEOUT_SEC
from app.probes.base import (
    CITATION_ANSWER_LEVEL,
    TIER_L3,
    Citation,
    ProbeError,
    ProbeResult,
)

YUANBAO_HOME = "https://yuanbao.tencent.com/"

# DOM 选择器集中放这里，前端改版只需要改这一处。
# 顺序即优先级：命中即用，全不命中就报错（不猜）。
SELECTORS = {
    "editor": [
        'div[contenteditable="true"]',
        "div.ql-editor",
        "textarea",
    ],
    "answer": [
        'div[class*="agent-chat__bubble__content"]',
        'div[class*="bubble__content"]',
        'div[class*="markdown-body"]',
        'div[class*="hyc-content"]',
    ],
    "generating": [
        '[class*="stop"]',
        'button:has-text("停止")',
        'button:has-text("停止生成")',
    ],
    # 登录墙：无登录态时页面中央弹「微信登录」二维码，左下角显示「未登录」。
    # 实测（2026-11）：此时 contenteditable 输入框**依然存在**，
    # 所以必须先查登录墙再找输入框，顺序反了会把未登录当成正常页面跑下去。
    # 这些文本登录后不会出现，不会误伤「退出登录」按钮。
    "login_gate": [
        'text=微信登录',
        'text=请使用微信扫描二维码登录',
        'text=未登录',
    ],
}

# 这些域名不算「信源」，是元宝自己的页面
_OWN_DOMAINS = ("yuanbao.tencent.com", "tencent.com", "qq.com")

_thread_local = threading.local()


class YuanbaoAdapter:
    engine_id = "yuanbao"
    label = "腾讯元宝"
    tier = TIER_L3
    citation_type = CITATION_ANSWER_LEVEL
    search_enabled = True
    implemented = True

    def __init__(
        self,
        state_path: str | None = None,
        headless: bool | None = None,
        channel: str | None = None,
        timeout_sec: int | None = None,
    ):
        self.state_path = state_path or YUANBAO_STATE
        self.headless = YUANBAO_HEADLESS if headless is None else headless
        self.channel = YUANBAO_CHANNEL if channel is None else channel
        self.timeout_sec = timeout_sec or YUANBAO_TIMEOUT_SEC

    def capabilities(self) -> dict[str, Any]:
        has_state = bool(self.state_path and os.path.exists(self.state_path))
        return {
            "engine": self.engine_id,
            "label": self.label,
            "tier": self.tier,
            "citation_type": self.citation_type,
            "search_enabled": self.search_enabled,
            "implemented": self.implemented,
            # 没登录态等于没配好，不能让它进跑批
            "configured": has_state,
            "model": "yuanbao-web",
            "setup_hint": "未检测到登录态，先运行 python scripts/yuanbao_login.py 扫码登录",
            "note": "无公开 API，浏览器自动化（L3）；需先运行 scripts/yuanbao_login.py 扫码登录"
            + ("" if has_state else "（当前未检测到登录态）"),
        }

    # ── 主流程 ────────────────────────────────────────────────
    def probe(self, question: str, *, model: str | None = None, sample_idx: int = 0) -> ProbeResult:
        if not os.path.exists(self.state_path):
            raise ProbeError(
                f"元宝未登录：找不到 {self.state_path}。"
                "先运行 python scripts/yuanbao_login.py 扫码登录（登录态只存本地）"
            )
        try:
            from playwright.sync_api import TimeoutError as PWTimeout
        except ImportError as exc:  # pragma: no cover - 环境问题
            raise ProbeError("未安装 playwright：pip install playwright") from exc

        t0 = time.monotonic()
        browser = _get_browser(self.headless, self.channel)
        context = browser.new_context(storage_state=self.state_path, locale="zh-CN")
        page = context.new_page()
        try:
            page.goto(YUANBAO_HOME, wait_until="domcontentloaded", timeout=60_000)
            _assert_logged_in(page)
            _ask(page, question)
            answer, citations = _collect(page, question, self.timeout_sec)
        except PWTimeout as exc:
            raise ProbeError(f"元宝页面操作超时：{exc}", retryable=True) from exc
        finally:
            context.close()

        if not answer:
            raise ProbeError("元宝未抓到回答正文（DOM 可能已改版，检查 SELECTORS）", retryable=True)

        return ProbeResult(
            engine=self.engine_id,
            tier=self.tier,
            answer=answer,
            model="yuanbao-web",
            model_version="web",
            citation_type=self.citation_type,
            citations=citations,
            search_queries=[question],
            latency_ms=int((time.monotonic() - t0) * 1000),
            usage={},
            raw={"transport": "playwright", "headless": self.headless, "channel": self.channel or "bundled"},
        )


# ── 浏览器复用 ──────────────────────────────────────────────────
def _get_browser(headless: bool, channel: str | None):
    """线程内复用浏览器实例。

    跑批是「引擎间并行、引擎内串行」，每个引擎在自己的线程里跑，
    所以用 threading.local 是最省事又不会踩 Playwright 线程限制的做法。
    """
    holder = getattr(_thread_local, "holder", None)
    if holder and holder.get("alive"):
        return holder["browser"]

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    launch_kwargs: dict[str, Any] = {"headless": headless}
    if channel:
        launch_kwargs["channel"] = channel
    browser = pw.chromium.launch(**launch_kwargs)
    _thread_local.holder = {"pw": pw, "browser": browser, "alive": True}
    return browser


def _assert_logged_in(page) -> None:
    """先查登录墙，再找输入框。

    实测发现未登录时输入框也在页面上（只是被二维码弹窗盖住），
    只查输入框会把未登录当成正常页面，一直跑到超时或抓到空结果。
    """
    page.wait_for_load_state("domcontentloaded")
    if _first_visible(page, SELECTORS["login_gate"], timeout_ms=4_000):
        raise ProbeError(
            "元宝登录态已失效（页面出现登录墙）。"
            "请重新运行 python scripts/yuanbao_login.py 扫码登录后再跑批"
        )
    if not _first_visible(page, SELECTORS["editor"], timeout_ms=15_000):
        raise ProbeError("元宝页面未找到输入框（可能改版或加载失败），检查 SELECTORS['editor']")


def _first_visible(page, selectors: list[str], timeout_ms: int = 5_000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:  # noqa: BLE001 - 选择器不合法/元素销毁都跳过
                continue
        page.wait_for_timeout(300)
    return None


def _ask(page, question: str) -> None:
    editor = _first_visible(page, SELECTORS["editor"], timeout_ms=15_000)
    if editor is None:
        raise ProbeError("元宝输入框不可用，检查 SELECTORS['editor']")
    editor.click()
    page.keyboard.type(question, delay=15)
    page.wait_for_timeout(200)
    page.keyboard.press("Enter")


def _collect(page, question: str, timeout_sec: int) -> tuple[str, list[Citation]]:
    """等回答稳定再抓。

    判定「说完了」用两条：出现停止按钮后消失，或文本长度连续两次不变。
    只靠固定等待会抓到半句话，那种样本比没有更糟。
    """
    deadline = time.monotonic() + timeout_sec
    last_text = ""
    stable = 0
    while time.monotonic() < deadline:
        page.wait_for_timeout(1500)
        text = _answer_text(page)
        if text and text == last_text:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
            last_text = text
        if _first_visible(page, SELECTORS["generating"], timeout_ms=300):
            stable = 0  # 还在生成，继续等

    answer = last_text.strip()
    if not answer:
        return "", []
    # 元宝会把用户问题回显在页面上，去掉以免污染实体判定
    if answer.startswith(question):
        answer = answer[len(question) :].strip()
    return answer, _citations(page)


def _answer_text(page) -> str:
    for sel in SELECTORS["answer"]:
        try:
            loc = page.locator(sel)
            if loc.count():
                return loc.nth(loc.count() - 1).inner_text().strip()
        except Exception:  # noqa: BLE001
            continue
    return ""


def _citations(page) -> list[Citation]:
    """只取回答区域内的外链，并按域名去重。"""
    raw: list[dict[str, str]] = []
    for sel in SELECTORS["answer"]:
        try:
            loc = page.locator(sel)
            if not loc.count():
                continue
            raw = loc.nth(loc.count() - 1).evaluate(
                """el => Array.from(el.querySelectorAll('a[href^="http"]'))
                     .map(a => ({url: a.href, title: (a.innerText || '').trim()}))"""
            )
            if raw:
                break
        except Exception:  # noqa: BLE001
            continue

    citations: list[Citation] = []
    seen: set[str] = set()
    for idx, item in enumerate(raw or []):
        url = item.get("url", "")
        if not url or any(d in url for d in _OWN_DOMAINS) or url in seen:
            continue
        seen.add(url)
        citations.append(
            Citation(url=url, title=item.get("title", ""), citation_type=CITATION_ANSWER_LEVEL, position=idx)
        )
    return citations
