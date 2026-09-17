import json, time
from collections import OrderedDict
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\info3011_capture.json")
captured: "OrderedDict[str, dict]" = OrderedDict()

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])

    def on_response(resp):
        url = resp.url
        if "jw.ustc.edu.cn" not in url:
            return
        ct = resp.headers.get("content-type") or ""
        if any(e in url for e in (".js", ".css", ".png", ".woff", ".ico", ".svg", ".ttf")):
            return
        if url in captured:
            return
        try:
            body = resp.json()
            blob = json.dumps(body, ensure_ascii=False)
            is_json = True
        except Exception:
            return
        captured[url] = {
            "method": resp.request.method,
            "status": resp.status,
            "bytes": len(blob),
            "has_FL1007": "FL1007" in blob,
            "has_英语通修L3": "英语通修L3" in blob,
        }

    ctx.on("response", on_response)
    page.goto("https://jw.ustc.edu.cn/for-std/program-search/info/3011", wait_until="networkidle")
    time.sleep(5)
    page.evaluate("""() => {
      const b = [...document.querySelectorAll('a,button,span,li')].find(x => x.textContent.trim() === '5');
      if (b) b.click();
    }""")
    time.sleep(4)

    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"捕获 {len(captured)} 请求:")
    for url, info in captured.items():
        mark = " ★FL1007" if info["has_FL1007"] else ""
        print(f"  [{info['method']} {info['status']} {info['bytes']:>9}B] {url[:120]}{mark}")
