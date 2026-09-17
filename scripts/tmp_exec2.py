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
        key = url.split("?")[0]
        if key in captured:
            return
        try:
            body = resp.json()
        except Exception:
            return
        blob = json.dumps(body, ensure_ascii=False)
        captured[key] = {
            "query": url.split("?")[1][:150] if "?" in url else "",
            "bytes": len(blob),
            "has_英语交流进阶I": "英语交流进阶I" in blob,
        }

    ctx.on("response", on_response)
    page.goto("https://jw.ustc.edu.cn/for-std/program-search/info/2763", wait_until="networkidle")
    time.sleep(3)
    print("info 页 URL:", page.url)

    # 页面上的按钮/标签
    btns = page.evaluate("""() => [...new Set([...document.querySelectorAll('a,button')].map(b => b.textContent.trim()).filter(t => t && t.length < 20))]""")
    print("info 页按钮/文本:", btns[:25])

    # 点击'执行计划'
    clicked = page.evaluate("""() => {
      const b = [...document.querySelectorAll('a,button')].find(x => x.textContent.trim().includes('执行计划'));
      if (b) { b.click(); return b.textContent.trim(); }
      return null;
    }""")
    print("点击:", clicked)
    time.sleep(5)

    # 展开树
    for _ in range(3):
        n = page.evaluate("""() => {
          let n = 0;
          document.querySelectorAll('i[class*=right], [class*=caret], [class*=arrow], [class*=expand]').forEach(el => { try { el.click(); n++; } catch {} });
          return n;
        }""")
        time.sleep(1.5)
        if not n:
            break
    time.sleep(2)

    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"捕获 {len(captured)} 端点:")
    for url, info in captured.items():
        mark = " ★" if info["has_英语交流进阶I"] else ""
        print(f"  [{info['bytes']:>9}B] {url}  {mark}")
