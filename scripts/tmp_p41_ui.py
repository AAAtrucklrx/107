# -*- coding: utf-8 -*-
"""P4-1 UI 冒烟：think 决策调用生态工具 eco:echo（临时脚本）。"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DOCS = Path(r"F:\小蜗\docs")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        page = browser.new_context(viewport={"width": 1500, "height": 950}).new_page()
        page.goto("http://localhost:8502", wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(6000)

        box = page.locator('[data-testid="stChatInputTextArea"]')
        box.fill("请测试生态工具：用 eco:echo 回显一下「协议通了」")
        box.press("Enter")

        msgs = page.locator('[data-testid="stChatMessage"]')
        deadline = time.time() + 240
        while time.time() < deadline:
            busy = page.locator('[data-testid="stStatusWidget"]').count()
            if msgs.count() >= 2 and busy == 0:
                time.sleep(2)
                break
            time.sleep(2)
        page.screenshot(path=str(DOCS / "e2e_p41_eco_echo.png"), full_page=True)
        content = page.locator('[data-testid="stChatMessageContent"]')
        text = content.nth(content.count() - 1).inner_text()
        ok = ("协议通了" in text) and ("第三方" in text or "小蜗开发组" in text)
        print(f"[{'PASS' if ok else 'FAIL'}] 生态工具端到端（回显+署名）")
        print(text[:500])
        browser.close()
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
