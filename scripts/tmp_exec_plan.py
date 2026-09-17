"""搜索目标方案 → 点击'执行计划' → 捕获生成触发与完整树接口序列。"""
from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\exec_plan_capture.json")
captured: "OrderedDict[str, dict]" = OrderedDict()

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "program-search" in pg.url), ctx.pages[0])
    page.goto("https://jw.ustc.edu.cn/for-std/program-search", wait_until="networkidle")
    page.wait_for_timeout(1500)

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
            "query": url.split("?")[1][:120] if "?" in url else "",
            "bytes": len(blob),
            "has_英语交流进阶I": "英语交流进阶I" in blob,
        }

    ctx.on("response", on_response)

    # 搜索
    page.evaluate("""() => {
      const inputs = [...document.querySelectorAll('input')].filter(i => !i.disabled);
      const box = inputs.find(i => (i.placeholder || '').includes('名称') || (i.name || '').includes('name')) || inputs[0];
      if (box) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(box, '计算机科学与技术专业培养方案');
        box.dispatchEvent(new Event('input', { bubbles: true }));
      }
    }""")
    time.sleep(1)
    page.evaluate("""() => {
      const btn = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === '查询');
      if (btn) btn.click();
    }""")
    time.sleep(2.5)

    # 找 2024 级行及行内按钮
    rowinfo = page.evaluate("""() => {
      const tds = [...document.querySelectorAll('td')].filter(td => (td.textContent || '').trim() === '计算机科学与技术专业培养方案');
      for (const td of tds) {
        const row = td.closest('tr');
        if (row && row.textContent.includes('2024')) {
          return [...row.querySelectorAll('a,button')].map(b => ({ text: b.textContent.trim(), href: b.getAttribute('href') }));
        }
      }
      return [];
    }""")
    print("2024 级行按钮:", json.dumps(rowinfo, ensure_ascii=False))

    # 点击'执行计划'（新标签）
    with ctx.expect_page(timeout=20000) as page_info:
        page.evaluate("""() => {
          const tds = [...document.querySelectorAll('td')].filter(td => (td.textContent || '').trim() === '计算机科学与技术专业培养方案');
          for (const td of tds) {
            const row = td.closest('tr');
            if (row && row.textContent.includes('2024')) {
              const btn = [...row.querySelectorAll('a,button')].find(b => b.textContent.includes('执行计划')) ||
                          [...row.querySelectorAll('a,button')].pop();
              if (btn) { btn.click(); return; }
            }
          }
        }""")
    detail = page_info.value
    detail.wait_for_load_state("networkidle")
    print("执行计划页 URL:", detail.url)
    time.sleep(3)

    # 展开全部树节点
    for _ in range(3):
        n = detail.evaluate("""() => {
          let n = 0;
          document.querySelectorAll('i[class*=right], [class*=caret], [class*=arrow], [class*=expand]').forEach(el => { try { el.click(); n++; } catch {} });
          return n;
        }""")
        time.sleep(1.5)
        if not n:
            break

    detail.screenshot(path="docs/qa/exec-plan-page.png")
    OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"捕获 {len(captured)} 端点:")
    for url, info in captured.items():
        mark = " ★英语" if info["has_英语交流进阶I"] else ""
        print(f"  [{info['bytes']:>9}B] {url}  {mark}")
