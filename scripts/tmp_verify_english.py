"""验证前端培养方案页显示英语通修。"""
from __future__ import annotations

import json
import re
import time

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.new_page())
    page.goto("http://127.0.0.1:8765", wait_until="networkidle")
    page.wait_for_timeout(1000)
    # 进入演示身份
    try:
        page.locator("header.mobile-header button.account-trigger, aside.desktop-rail button.account-trigger").first.click()
        page.get_by_role("menuitem", name="进入演示身份").click()
        page.wait_for_timeout(2500)
    except Exception as exc:
        print("登录流程:", exc)
    # 打开培养方案 tab
    page.goto("http://127.0.0.1:8765/academic", wait_until="networkidle")
    page.wait_for_timeout(1500)
    try:
        page.get_by_role("tab", name="培养方案").click()
        page.wait_for_timeout(2500)
    except Exception as exc:
        print("tab 点击:", exc)
    body = page.evaluate("() => document.body.innerText")
    hits = {
        "英语通修": "英语通修" in body,
        "英语交流进阶": "英语交流进阶" in body,
        "英语读写进阶": "英语读写进阶" in body,
        "大学英语": "大学英语" in body,
    }
    print("培养方案页文本包含:", json.dumps(hits, ensure_ascii=False))
    page.screenshot(path="docs/qa/program-english-visible.png", full_page=False)
    browser.close()
