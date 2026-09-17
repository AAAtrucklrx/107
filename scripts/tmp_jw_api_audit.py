"""教务系统接口全面探查：CDP 连用户浏览器，遍历主要页面，捕获所有 XHR 接口与响应结构。

输出：scripts/data/jw_api_audit.json（端点 → 响应结构样本）
"""
from __future__ import annotations

import json
import time
from collections import OrderedDict
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\jw_api_audit.json")

captured: "OrderedDict[str, dict]" = OrderedDict()


def summarize(obj, depth=0):
    """响应结构摘要：键名 + 类型（截断到 2 层，数组取首个元素）。"""
    if depth >= 3:
        return type(obj).__name__
    if isinstance(obj, dict):
        return {k: summarize(v, depth + 1) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return [summarize(obj[0], depth + 1), f"...{len(obj)} items"] if obj else []
    if isinstance(obj, str):
        return obj[:60]
    return obj


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0]
        page = next(
            (pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url),
            ctx.pages[0] if ctx.pages else ctx.new_page(),
        )

        def on_response(resp):
            url = resp.url
            if "jw.ustc.edu.cn" not in url:
                return
            # 只关注数据接口
            if any(ext in url for ext in (".js", ".css", ".png", ".jpg", ".woff", ".ico", ".html")):
                if "json" not in (resp.headers.get("content-type") or ""):
                    return
            key = url.split("?")[0]
            entry = captured.setdefault(key, {"method": resp.request.method, "samples": []})
            if len(entry["samples"]) >= 2:
                return
            try:
                body = resp.json()
            except Exception:
                try:
                    body = resp.text()[:200]
                except Exception:
                    return
            entry["samples"].append({
                "query": url.split("?")[1] if "?" in url else "",
                "status": resp.status,
                "structure": summarize(body),
            })

        ctx.on("response", on_response)

        # 培养方案页面：进详情、点开英语通修
        print("== 导航培养方案 ==")
        page.goto("https://jw.ustc.edu.cn/for-std/program-search", wait_until="networkidle")
        time.sleep(2)
        # 打开 2025 计算机方案（新标签）
        try:
            with ctx.expect_page() as page_info:
                page.evaluate("""() => {
                  const rows = [...document.querySelectorAll('a,button')].filter(a => a.textContent.includes('计算机科学与技术专业培养方案'));
                  const target = rows.find(a => a.textContent.includes('2025')) || rows[0];
                  if (target) target.click();
                }""")
            detail = page_info.value
            detail.wait_for_load_state("networkidle")
            time.sleep(2)
            print("详情页:", detail.url)
            # 点击全部"展开更多"和树形展开箭头
            for _ in range(3):
                expanded = detail.evaluate("""() => {
                  let n = 0;
                  document.querySelectorAll('.expand-more, [class*=expand]').forEach(el => { try { el.click(); n++; } catch {} });
                  document.querySelectorAll('i[class*=arrow], .fa-caret-right, [class*=collapsed]').forEach(el => { try { el.click(); n++; } catch {} });
                  return n;
                }""")
                time.sleep(1.5)
                if not expanded:
                    break
            time.sleep(2)
            page.goto(detail.url, wait_until="domcontentloaded")
        except Exception as exc:
            print("详情页处理异常(继续):", exc)

        # 主菜单页面遍历（常见路径）
        print("== 遍历主菜单页面 ==")
        menu_paths = [
            "/for-std/grade-query",
            "/for-std/course-table",
            "/for-std/exam-arrange",
            "/for-std/program",
            "/for-std/plan-search",
            "/home",
        ]
        for path in menu_paths:
            try:
                page.goto(f"https://jw.ustc.edu.cn{path}", wait_until="networkidle", timeout=20000)
                time.sleep(2.5)
                print(f"  {path} ok")
            except Exception:
                print(f"  {path} 跳过")

        # 当前页面上的可见菜单项文本（帮助补路径）
        try:
            page.goto("https://jw.ustc.edu.cn/home", wait_until="domcontentloaded", timeout=15000)
            time.sleep(2)
            menus = page.evaluate("""() => [...new Set([...document.querySelectorAll('a,[class*=menu] li, .el-menu-item')].map(e => e.textContent.trim()).filter(t => t && t.length < 12))]""")
            print("页面菜单项:", menus[:40])
        except Exception as exc:
            print("菜单读取失败:", exc)

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(captured, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n共捕获 {len(captured)} 个端点 → {OUT}")
        for url, entry in captured.items():
            print(f"  [{entry['method']}] {url}")


if __name__ == "__main__":
    main()
