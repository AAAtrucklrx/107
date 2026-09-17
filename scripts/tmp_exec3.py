import json, time
from collections import OrderedDict
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\exec_plan_capture.json")
captured: "OrderedDict[str, dict]" = OrderedDict()

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "program-search" in pg.url), ctx.pages[0])

    def on_response(resp):
        url = resp.url
        if "jw.ustc.edu.cn" not in url:
            return
        if "json" not in (resp.headers.get("content-type") or ""):
            return
        if url in captured:
            return
        try:
            body = resp.json()
        except Exception:
            return
        blob = json.dumps(body, ensure_ascii=False)
        captured[url] = {
            "bytes": len(blob),
            "has_英语交流进阶I": "英语交流进阶I" in blob,
            "structure_head": blob[:120],
        }

    ctx.on("response", on_response)
    page.goto("https://jw.ustc.edu.cn/for-std/program-search/info/2763", wait_until="networkidle")
    time.sleep(4)
    # 点开展示层级 5（最深层）
    page.evaluate("""() => {
      const b = [...document.querySelectorAll('a,button,span,li')].find(x => x.textContent.trim() === '5');
      if (b) b.click();
    }""")
    time.sleep(4)

    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"捕获 {len(captured)} 请求:")
    for url, info in captured.items():
        mark = " ★" if info["has_英语交流进阶I"] else ""
        print(f"  [{info['bytes']:>9}B] {url[:130]}  {mark}")
