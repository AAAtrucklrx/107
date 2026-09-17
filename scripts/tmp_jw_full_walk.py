"""教务菜单全量遍历：逐页面导航并捕获数据接口，产出完整接口-页面对照表。"""
from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path

from playwright.sync_api import sync_playwright

MENU = Path(r"f:\小蜗\scripts\data\jw_menu.json")
OUT = Path(r"f:\小蜗\scripts\data\jw_api_all_pages.json")
XIAOWO_USED = {
    "/for-std/student-info/info",
    "/for-std/course-table/get-data",
    "/for-std/course-table/semester",
    "/for-std/course-take-query/semester",
    "/for-std/grade/sheet/getSemesters",
    "/for-std/grade/sheet/getGradeList",
    "/for-std/program/root-module-json",
    "/for-std/sport-grade/list",
    "/for-std/exam-arrange/info",
    "/home/get-current-teach-week",
}

menu = json.loads(MENU.read_text(encoding="utf-8"))
pages: "OrderedDict[str, str]" = OrderedDict()
for item in menu:
    title, href = item.get("title"), item.get("href")
    if title and href and href not in pages:
        pages[href] = title

print(f"菜单页面总数: {len(pages)}")

captured: "OrderedDict[str, dict]" = OrderedDict()
with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])

    def on_response(resp):
        url = resp.url
        if "jw.ustc.edu.cn" not in url or url in captured:
            return
        if any(e in url for e in (".js", ".css", ".png", ".woff", ".ico", ".svg", ".ttf", ".html", ".map")):
            return
        if "json" not in (resp.headers.get("content-type") or ""):
            return
        try:
            body = resp.json()
        except Exception:
            return
        blob = json.dumps(body, ensure_ascii=False)
        captured[url] = {"bytes": len(blob), "structure": blob[:260], "pages": []}

    ctx.on("response", on_response)

    for idx, (href, title) in enumerate(pages.items(), 1):
        before = set(captured)
        try:
            page.goto("https://jw.ustc.edu.cn" + href, wait_until="networkidle", timeout=25000)
            time.sleep(2)
        except Exception:
            continue
        new = [u for u in captured if u not in before]
        for u in new:
            captured[u]["pages"] = []
        if new:
            for u in new:
                captured[u]["pages"].append(f"{title}({href})")
        print(f"[{idx:>2}/{len(pages)}] {title}: +{len(new)} 接口")

    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")

print(f"\n总端点: {len(captured)}")
used_hits = []
for url in captured:
    short = url.replace("https://jw.ustc.edu.cn", "").split("?")[0]
    for used in XIAOWO_USED:
        if short.startswith(used) or used in short:
            used_hits.append(url)
print(f"小蜗已用命中: {len(used_hits)}")
for u in used_hits:
    print("  ✓", u.replace("https://jw.ustc.edu.cn", ""))
