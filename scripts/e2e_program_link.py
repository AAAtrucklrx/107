# -*- coding: utf-8 -*-
"""培养方案页 × 选课顾问联动端到端回归（Phase 1b）。

实测链路：app_test(8502) 培养方案页点击「💬 问问小蜗」按钮
→ 自动切到选课顾问模块 → 自动发起该课程的点评问答 → 输出回答。

用法（需 LLM key 与已启动的 app_test 实例，端口用 --port 覆盖）:
    python scripts/e2e_program_link.py --base http://localhost:8502

注意：消息内容断言必须用 [data-testid="stChatMessageContent"]——
stChatMessage 的 inner_text 会混入头像 Material 图标文字（助手头像即
"smart_toy"，用户头像为 "face"），历史上因此误判"回答含 smart_toy 脏前缀"。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
DOCS = Path(__file__).resolve().parents[1] / "docs"
SHOT = DOCS / "e2e_v5_link_answer.png"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8501")
    ap.add_argument("--shot", action="store_true", help="截图到 docs/")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=EDGE, headless=True)
        ctx = browser.new_context(viewport={"width": 1500, "height": 950})
        page = ctx.new_page()
        page.goto(args.base, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(4000)

        # 切到培养方案模块
        page.locator('[data-testid="stSidebar"] label:has-text("培养方案")').first.click()
        page.wait_for_timeout(6000)
        page.wait_for_selector('button:has-text("💬")', timeout=60000)
        btn = page.locator('button:has-text("💬")').first
        course_hint = btn.inner_text().replace("💬", "").strip()
        btn.click()
        print(f"[OK] 已点击「问问小蜗」: {course_hint}")

        # 等待回答完成（消息数≥2 且无 spinner）
        msgs = page.locator('[data-testid="stChatMessage"]')
        deadline = time.time() + 240
        ok = False
        while time.time() < deadline:
            busy = page.locator('[data-testid="stStatusWidget"]').count()
            if msgs.count() >= 2 and busy == 0:
                page.wait_for_timeout(2000)
                ok = True
                break
            page.wait_for_timeout(2000)
        if args.shot:
            page.screenshot(path=str(SHOT), full_page=True)
        if not ok:
            print("[FAIL] 超时未等到回答")
            return 1

        # 内容断言：仅取内容区（排除头像图标文字 smart_toy/face 干扰）
        content = page.locator('[data-testid="stChatMessageContent"]')
        text = content.nth(content.count() - 1).inner_text()
        print(f"回答长度: {len(text)}")
        print(f"回答开头: {text[:300].replace(chr(10), ' | ')}")
        clean = not text.startswith("smart_toy")
        print(f"[{'PASS' if clean else 'FAIL'}] 回答正文无 smart_toy 脏前缀: {clean}")
        browser.close()
        return 0 if clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
