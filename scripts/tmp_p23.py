import json, time
from collections import OrderedDict
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\lazy_requests.json")
captured: "OrderedDict[str, dict]" = OrderedDict()

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "info/3011" in pg.url), ctx.pages[0])

    def on_response(resp):
        url = resp.url
        if url in captured or "jw.ustc.edu.cn" not in url:
            return
        try:
            body = resp.json()
        except Exception:
            return
        blob = json.dumps(body, ensure_ascii=False)
        captured[url] = {
            "method": resp.request.method,
            "post": (resp.request.post_data or "")[:200],
            "bytes": len(blob),
            "has_FL1007": "FL1007" in blob,
            "has_英语通修L3": "英语通修L3" in blob,
        }

    page.on("response", on_response)

    # 定位英语通修节点的可展开元素
    found = page.evaluate("""() => {
      const out = [];
      const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (walker.nextNode()) {
        const t = walker.currentNode.textContent;
        if (t.trim() === '英语通修' || t.includes('英语通修') && t.length < 30) {
          let el = walker.currentNode.parentElement;
          for (let up = 0; el && up < 5; up += 1) {
            const clickable = el.querySelector('[class*=expand], [class*=caret], [class*=toggle], [class*=icon], [class*=plus]');
            out.push({ tag: el.tagName, cls: (el.className || '').toString().slice(0, 70), hasIcon: !!clickable });
            const icon = el.querySelector('[class*=expand], [class*=caret], [class*=toggle], [class*=plus], i');
            if (icon) { icon.click(); out.push({ clicked: icon.className.toString().slice(0, 60) }); break; }
            el = el.parentElement;
          }
          break;
        }
      }
      return out;
    }""")
    print(json.dumps(found, ensure_ascii=False, indent=1)[:800])
    time.sleep(4)
    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"捕获 {len(captured)} 请求:")
    for url, info in captured.items():
        mark = " ★FL1007" if info["has_FL1007"] else ""
        print(f"  [{info['method']} {info['bytes']:>8}B] {url[:130]}{mark}")
        if info["post"]:
            print(f"      POST data: {info['post']}")
