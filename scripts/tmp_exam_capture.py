"""打开教务考试安排页 + 课表页，捕获真实数据接口与响应结构。"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\jw_exam_capture.json")
captured: "OrderedDict[str, dict]" = OrderedDict()

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])

    def on_response(resp):
        url = resp.url
        if "jw.ustc.edu.cn" not in url or url in captured:
            return
        if any(e in url for e in (".js", ".css", ".png", ".woff", ".ico", ".svg", ".ttf", ".html")):
            return
        try:
            body = resp.json()
        except Exception:
            return
        captured[url] = {
            "method": resp.request.method,
            "post": (resp.request.post_data or "")[:150],
            "bytes": len(json.dumps(body, ensure_ascii=False)),
            "structure": json.dumps(body, ensure_ascii=False)[:350],
        }

    ctx.on("response", on_response)

    for path in ("/for-std/exam-arrange", "/for-std/course-table"):
        page.goto("https://jw.ustc.edu.cn" + path, wait_until="networkidle")
        page.wait_for_timeout(3500)
        # 尝试点击学期下拉/查询按钮触发数据加载
        page.evaluate("""() => {
          document.querySelectorAll('.el-select, [class*=select], [class*=dropdown]').forEach(e => { try { e.click(); } catch {} });
        }""")
        page.wait_for_timeout(2000)

    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"捕获 {len(captured)}:")
    for url, info in captured.items():
        print(f"  [{info['method']} {info['bytes']:>8}B] {url[:120]}")
        if info["post"]:
            print(f"      POST: {info['post']}")
