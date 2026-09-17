# -*- coding: utf-8 -*-
"""P4-2 UI 实测：退课强操作 → 链接+辅助；学费 → 缴费平台链接（临时脚本）。"""
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
        page.wait_for_timeout(6000)

        a1 = ask(page, "我想退掉力学B这门课")
        page.screenshot(path=str(DOCS / "e2e_p42_drop_course.png"), full_page=True)
        print("== 我想退掉力学B ==")
        print(a1[:600], "\n")
        ok1 = ("jw.ustc.edu.cn" in a1) and ("退" in a1)

        a2 = ask(page, "学费在哪里交？")
        page.screenshot(path=str(DOCS / "e2e_p42_tuition.png"), full_page=True)
        print("== 学费在哪里交 ==")
        print(a2[:400])
        ok2 = "revenues.ustc.edu.cn" in a2

        print(f"\n[{'PASS' if ok1 else 'FAIL'}] 退课 → 教务系统链接")
        print(f"[{'PASS' if ok2 else 'FAIL'}] 学费 → 缴费平台链接")
        browser.close()
        return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
