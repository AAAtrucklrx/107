"""正确打开培养方案详情：捕获 URL、全部 XHR；尝试展开英语通修节点。"""
from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\lazy_api_capture2.json")
captured: "OrderedDict[str, dict]" = OrderedDict()

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if pg.url.rstrip("/").endswith("/home") or "/for-std" not in pg.url), ctx.pages[0])
    page.goto("https://jw.ustc.edu.cn/home", wait_until="domcontentloaded")

    def on_response(resp):
        url = resp.url
        if "jw.ustc.edu.cn" not in url:
            return
        ct = resp.headers.get("content-type") or ""
        if "json" not in ct:
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
            "query": url.split("?")[1] if "?" in url else "",
            "bytes": len(blob),
            "has_英语通修": "英语通修" in blob,
            "has_英语交流进阶": "英语交流进阶" in blob,
        }

    ctx.on("response", on_response)

    page.goto("https://jw.ustc.edu.cn/for-std/program-search", wait_until="networkidle")
    time.sleep(2)
    href = page.evaluate("""() => {
      const rows = [...document.querySelectorAll('a')].filter(a => (a.textContent || '').includes('计算机科学与技术专业培养方案'));
      const target = rows.find(a => (a.textContent || '').includes('2024')) || rows[0];
      return target ? target.getAttribute('href') : null;
    }""")
    print("方案链接 href:", href)
    if href and href.startswith("/"):
        page.goto("https://jw.ustc.edu.cn" + href, wait_until="networkidle")
    else:
        page.evaluate("""() => {
          const rows = [...document.querySelectorAll('a,button')].filter(a => (a.textContent || '').includes('计算机科学与技术专业培养方案'));
          const target = rows.find(a => (a.textContent || '').includes('2024')) || rows[0];
          if (target) target.click();
        }""")
        page.wait_for_load_state("networkidle")
    time.sleep(3)
    print("详情页 URL:", page.url)

    # 自动点击所有树形展开箭头（含二级树）
    for _ in range(4):
        n = page.evaluate("""() => {
          let n = 0;
          document.querySelectorAll('i[class*=right], i[class*=caret], [class*=arrow], [class*=expand-icon], [class*=tree] i, [class*=tree] [class*=icon]')
            .forEach(el => { try { el.click(); n++; } catch {} });
          return n;
        }""")
        time.sleep(1.5)
        print("点击展开:", n)
        if not n:
            break

    pass
    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"捕获 {len(captured)} 端点:")
    for url, info in captured.items():
        mark = " ★" if info["has_英语交流进阶"] else ""
        print(f"  [{info['bytes']:>8}B] {url}  英语交流进阶={info['has_英语交流进阶']}{mark}")
