import json, time
from collections import OrderedDict
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\lesson_search_capture.json")
captured: "OrderedDict[str, dict]" = OrderedDict()

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    page.goto("https://jw.ustc.edu.cn/for-std/lesson-search", wait_until="networkidle")
    page.wait_for_timeout(2000)

    def on_response(resp):
        url = resp.url
        if "lesson-search" not in url or url in captured:
            return
        try:
            body = resp.json()
        except Exception:
            return
        captured[url] = {"bytes": len(json.dumps(body, ensure_ascii=False)), "structure": json.dumps(body, ensure_ascii=False)[:200]}

    ctx.on("response", on_response)

    # 找搜索框，输入并触发查询
    box_info = page.evaluate("""() => {
      const inputs = [...document.querySelectorAll('input[type=text], input:not([type])')].filter(i => i.offsetParent);
      const box = inputs.find(i => (i.placeholder || '').includes('课程') || (i.placeholder || '').includes('名称') || (i.placeholder || '').includes('搜索')) || inputs[0];
      if (!box) return null;
      return { placeholder: box.placeholder, cls: box.className.slice(0, 50) };
    }""")
    print("搜索框:", box_info)
    if box_info:
        page.evaluate("""() => {
          const inputs = [...document.querySelectorAll('input[type=text], input:not([type])')].filter(i => i.offsetParent);
          const box = inputs.find(i => (i.placeholder || '').includes('课程') || (i.placeholder || '').includes('名称') || (i.placeholder || '').includes('搜索')) || inputs[0];
          if (!box) return;
          const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
          setter.call(box, '英语');
          box.dispatchEvent(new Event('input', { bubbles: true }));
          box.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }));
        }""")
        time.sleep(1)
        page.keyboard.press("Enter")
        time.sleep(3)

    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    for url, info in captured.items():
        print(f"[{info['bytes']:>8}B] {url[:140]}")
