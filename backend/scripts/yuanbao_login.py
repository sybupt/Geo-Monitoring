"""元宝登录态采集脚本（只需跑一次，之后跑批复用）。

用法（在 backend 目录下）：
    python scripts/yuanbao_login.py

会弹出一个真实的 Chrome 窗口：
1. 用微信/QQ 扫码登录腾讯元宝；
2. 登录成功、能看到输入框后，脚本自动保存登录态到 data/yuanbao_state.json；
3. 关掉窗口即可。

安全说明：
- 登录态文件等同账号凭据，只存本地，已在 .gitignore 里排除；
- 不用把密码交给脚本，脚本只持久化浏览器 cookie/localStorage。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.config import YUANBAO_CHANNEL, YUANBAO_STATE  # noqa: E402
from app.probes.yuanbao import SELECTORS, YUANBAO_HOME, _first_visible  # noqa: E402

POLL_SECONDS = 2
MAX_WAIT_SECONDS = 300


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[x] 未安装 playwright，先执行：pip install playwright")
        return 1

    state_path = Path(YUANBAO_STATE)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("  腾讯元宝登录态采集")
    print("  1) 在打开的浏览器里扫码登录")
    print(f"  2) 登录成功后会保存到：{state_path}")
    print("=" * 60)

    with sync_playwright() as pw:
        launch_kwargs = {"headless": False}
        if YUANBAO_CHANNEL:
            launch_kwargs["channel"] = YUANBAO_CHANNEL
        try:
            browser = pw.chromium.launch(**launch_kwargs)
        except Exception as exc:  # noqa: BLE001
            print(f"[!] 启动 Chrome 失败（{exc}），改用内置 Chromium")
            browser = pw.chromium.launch(headless=False)

        context = browser.new_context(locale="zh-CN")
        page = context.new_page()
        page.goto(YUANBAO_HOME, wait_until="domcontentloaded", timeout=60_000)

        print("[→] 等待登录：浏览器里出现输入框即视为成功…")
        deadline = time.monotonic() + MAX_WAIT_SECONDS
        ok = False
        while time.monotonic() < deadline:
            if _first_visible(page, SELECTORS["editor"], timeout_ms=1_500):
                ok = True
                break
            time.sleep(POLL_SECONDS)

        if not ok:
            print("[x] 超时未检测到输入框。确认已登录，或手动改 SELECTORS['editor'] 后重试。")
            browser.close()
            return 2

        context.storage_state(path=str(state_path))
        print(f"[✓] 登录态已保存：{state_path}")
        print("    现在可以关闭浏览器窗口，回到项目里跑批。")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
