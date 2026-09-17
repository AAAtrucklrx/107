# -*- coding: utf-8 -*-
"""P4-B UI 实测：活动问答实时链路（临时脚本）。"""
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DOCS = Path(r"F:\小蜗\docs")


def ask(page, q):
    box = page.locator('[data-testid="stChatInputTextArea"]')
    box.fill(q)
    box.press("Enter")
    msgs = page.locator('[data-testid="stChatMessage"]')
    base = msgs.count()
    deadline = time.time() + 240
    while time.time() < deadline:
        busy = page.locator('[data-testid="stStatusWidget"]').count()
        if msgs.count() >= base + 2 and busy == 0:
            time.sleep(2)
            break
        time.sleep(2)
    c = page.locator('[data-testid="stChatMessageContent"]')
    return c.nth(c.count() - 1).inner_text()


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        page = browser.new_context(viewport={"width": 1500, "height": 950}).new_page()
        page.goto("http://localhost:8502", wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(8000)
        # token 有效时每日活动弹窗会弹出，先关闭
        for close_sel in ('[data-testid="stDialogCloseButton"]', 'button[aria-label="Close"]',
                          'button[aria-label="close"]', 'button:has-text("×")'):
            try:
                page.locator(close_sel).first.click(timeout=2000)
                page.wait_for_timeout(1500)
                break
            except Exception:
                continue
        page.wait_for_timeout(2000)

        a1 = ask(page, "最近有什么活动可以报名？")
        page.screenshot(path=str(DOCS / "e2e_pb_activities.png"), full_page=True)
        print("== 最近有什么活动可以报名 ==\n", a1[:700], "\n")
        ok1 = ("辩论" in a1 or "活动" in a1) and ("报名" in a1)

        a2 = ask(page, "有辩论相关的活动吗？")
        print("== 有辩论相关的活动吗 ==\n", a2[:400])
        ok2 = "辩论" in a2

        print(f"\n[{'PASS' if ok1 else 'FAIL'}] 活动列表问答")
        print(f"[{'PASS' if ok2 else 'FAIL'}] 关键词过滤问答")
        browser.close()
        return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
