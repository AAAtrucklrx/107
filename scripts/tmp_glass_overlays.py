"""浮层验收截图：account-menu 展开态 + dialog 打开态（明/暗）。

前置：8765 = XIAOWO_AUTH_MODE=demo XIAOWO_PUBLIC_ORIGIN=http://127.0.0.1:8765 实例。
产出：docs/qa/glass-p2-overlay-*.png
"""
from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8765"
OUT = Path("docs/qa")


def open_menu(page) -> None:
    page.locator("aside.desktop-rail").get_by_role("button", name=re.compile("测试|PB25111691|未登录")).click()


def main() -> None:
    with sync_playwright() as p:
        exe = next(
            (
                str(path)
                for path in (
                    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
                    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
                    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
                )
                if path.is_file()
            ),
            None,
        )
        browser = p.chromium.launch(headless=True, executable_path=exe)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, color_scheme="light")
        page = context.new_page()
        page.goto(URL, wait_until="networkidle")

        # 1) account-menu 展开态（浅色）
        open_menu(page)
        page.locator(".account-menu").wait_for(state="visible")
        page.screenshot(path=str(OUT / "glass-p2-overlay-account-menu-light.png"))

        # 2) 进入演示身份 → campus 工具 → 提交工具 dialog（浅色）
        page.get_by_role("menuitem", name="进入演示身份").click()
        page.locator("aside.desktop-rail").get_by_role("button", name=re.compile("测试|PB25111691")).wait_for(timeout=15000)
        page.goto(URL + "/campus", wait_until="networkidle")
        page.get_by_role("tab", name="校园工具").click()
        page.get_by_role("button", name="提交工具").click()
        page.locator(".dialog-content").wait_for(state="visible")
        page.screenshot(path=str(OUT / "glass-p2-overlay-dialog-light.png"))
        page.keyboard.press("Escape")

        # 3) 切深色 → 再截 menu 与 dialog
        open_menu(page)
        page.get_by_role("menuitem", name="深色主题").click()
        page.locator("html[data-theme='dark']").wait_for(timeout=5000)
        open_menu(page)
        page.locator(".account-menu").wait_for(state="visible")
        page.screenshot(path=str(OUT / "glass-p2-overlay-account-menu-dark.png"))
        page.keyboard.press("Escape")

        page.get_by_role("tab", name="校园工具").click()
        page.get_by_role("button", name="提交工具").click()
        page.locator(".dialog-content").wait_for(state="visible")
        page.screenshot(path=str(OUT / "glass-p2-overlay-dialog-dark.png"))

        print("overlay screenshots saved to docs/qa/glass-p2-overlay-*.png")
        browser.close()


if __name__ == "__main__":
    main()
