"""打开培养方案详情页，监听 XHR，自动展开'英语通修'节点，捕获懒加载接口。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\lazy_api_capture.json")
captured = {}

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])

    def on_response(resp):
        url = resp.url
        if "jw.ustc.edu.cn" not in url or "root-module" not in url and "module" not in url and "plan" not in url:
            # 宽松捕获：除静态资源外全录
            if any(e in url for e in (".js", ".css", ".png", ".woff", ".ico")):
                return
        key = url.split("?")[0]
        if key in captured:
            return
        try:
            body = resp.json()
        except Exception:
            return
        captured[key] = {
            "query": url.split("?")[1] if "?" in url else "",
            "bytes": len(json.dumps(body, ensure_ascii=False)),
            "has_英语通修": "英语通修" in json.dumps(body, ensure_ascii=False),
        }

    ctx.on("response", on_response)
    page.on("response", on_response)

    detail = ctx.new_page()
    detail.goto("https://jw.ustc.edu.cn/for-std/program-search/2763", wait_until="networkidle")
    time.sleep(2)

    # 找到"英语通修"节点并点击其展开箭头
    clicked = detail.evaluate("""() => {
      const hits = [];
      const walk = (el) => {
        for (const child of el.children || []) {
          const text = child.textContent || '';
          if (text.includes('英语通修') && !text.includes('英语通修拓展')) {
            const arrow = child.querySelector('i[class*=icon], [class*=arrow], [class*=caret], [class*=toggle]');
            if (arrow) { arrow.click(); hits.push(arrow.className); }
          }
          walk(child);
        }
      };
      walk(document.body);
      return hits;
    }""")
    print("点击的箭头:", clicked[:3])
    time.sleep(3)

    # 兜底：点击所有树形展开图标
    detail.evaluate("""() => {
      document.querySelectorAll('[class*=expand], [class*=caret], [class*=arrow]').forEach(e => { try { e.click(); } catch {} });
    }""")
    time.sleep(3)

    detail.screenshot(path="docs/qa/lazy-capture-page.png")
    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"捕获 {len(captured)} 个请求:")
    for url, info in captured.items():
        print(f"  {url}  [{info['bytes']}B 英语通修={info['has_英语通修']}]")
