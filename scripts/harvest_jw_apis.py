# -*- coding: utf-8 -*-
"""接口收割器:从 jw 首页菜单 + 各功能页 HTML/JS 提取全部 API 路径,去重输出。
用法:CDP 9223 已登录 jw。输出 console + scripts/data/api_paths_discovered.json
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
JW = "https://jw.ustc.edu.cn"
CATALOG = "https://catalog.ustc.edu.cn"
OUT = Path(__file__).resolve().parents[1] / "scripts" / "data" / "api_paths_discovered.json"

JS_FETCH_HTML = """async (u) => {
    const r = await fetch(u, {credentials:'include', redirect:'follow'});
    return {status: r.status, finalUrl: r.url, text: await r.text()};
}"""


def collect_paths(html: str) -> dict[str, set[str]]:
    """从 HTML/JS 源码提取接口路径(/for-std/、/api/),去重。"""
    result = {"for_std": set(), "api": set(), "urls": set()}
    # /for-std/xxx 路径(含带 query 的)
    for m in re.finditer(r'["\'`](/for-std/[A-Za-z0-9_\-/\.]*(?:\?[^"\'`]*)?)["\'`]', html):
        path = m.group(1).split("?")[0]
        if 2 <= path.count("/") <= 5 and not path.endswith(("/", ".js", ".css", ".png", ".jpg", ".ico")):
            result["for_std"].add(path)
    # /api/xxx 路径
    for m in re.finditer(r'["\'`](/api/[A-Za-z0-9_\-/\.]*(?:\?[^"\'`]*)?)["\'`]', html):
        path = m.group(1).split("?")[0]
        if 2 <= path.count("/") <= 5 and not path.endswith(("/", ".js", ".css", ".png", ".jpg", ".ico")):
            result["api"].add(path)
    # 完整 URL 引用(catalog 等)
    for m in re.finditer(r'["\'](https?://[A-Za-z0-9_\-\.]+(/[A-Za-z0-9_\-/\.]*))["\']', html):
        result["urls"].add(m.group(2))
    return result


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not page.url.startswith(JW):
            page.goto(JW, timeout=40000)
            time.sleep(3)

        print("======== 1. jw 首页菜单链接 ========")
        page.goto(f"{JW}/home", timeout=40000)
        time.sleep(4)
        print("首页:", page.url[:80], "|", page.title()[:50])
        links = page.evaluate("""() => Array.from(document.querySelectorAll('a[href]')).map(a => a.getAttribute('href')).filter(h => h && (h.includes('/for-std') || h.startsWith('/'))).slice(0, 200)""")
        menu = sorted(set(links))
        print(f"菜单链接 {len(menu)} 个:")
        for m in menu:
            print("  ", m[:100])

        all_for_std: set[str] = set()
        all_api: set[str] = set()
        all_urls: set[str] = set()

        print("\n======== 2. 逐个打开菜单页,提取页面内 JS/HTML 中的接口路径 ========")
        visited = set()
        queue = list(menu)
        while queue and len(visited) < 40:
            link = queue.pop(0)
            url = link if link.startswith("http") else JW + link
            if url in visited:
                continue
            visited.add(url)
            try:
                out = page.evaluate(JS_FETCH_HTML, url)
            except Exception:
                continue
            if out.get("status") not in (200, 302):
                continue
            html = out.get("text") or ""
            # 提取页面内引用的 js 文件
            js_urls = set()
            for m in re.finditer(r'["\'](/[A-Za-z0-9_\-/\.]*\.js(?:\?[^"\']*)?)["\']', html):
                js_urls.add(m.group(1).split("?")[0])
            chunks = [html]
            for js in list(js_urls)[:8]:
                try:
                    out2 = page.evaluate(JS_FETCH_HTML, JW + js)
                    if out2.get("status") == 200:
                        chunks.append(out2.get("text") or "")
                except Exception:
                    pass
            for chunk in chunks:
                found = collect_paths(chunk)
                all_for_std |= found["for_std"]
                all_api |= found["api"]
                all_urls |= found["urls"]
            # 页面里新发现的 /for-std/ 页面链接继续入队
            for m in re.finditer(r'["\'](/for-std/[A-Za-z0-9_\-/\.]*)["\']', html):
                cand = m.group(1)
                if 2 <= cand.count("/") <= 4 and not any(cand.endswith(s) for s in (".js", ".css", ".png", ".jpg")):
                    full = JW + cand
                    if full not in visited:
                        queue.append(full)
            print(f"  [{len(visited):2d}] {url.replace(JW, '')[:70]}")

        print("\n======== 3. catalog 公开页面接口提取 ========")
        catalog_html = ""
        for cpath in ["/", "/query/classroom", "/query/course", "/query/timetable", "/exam"]:
            try:
                r = requests.get(CATALOG + cpath, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code == 200:
                    catalog_html += r.text
            except Exception:
                pass
        cat_found = collect_paths(catalog_html)
        all_api |= cat_found["api"]
        print(f"catalog 发现 /api/ {len(cat_found['api'])} 个")

        print("\n======== 结果汇总 ========")
        print(f"/for-std/ 路径 {len(all_for_std)} 个:")
        for x in sorted(all_for_std):
            print("  ", x)
        print(f"\n/api/ 路径 {len(all_api)} 个:")
        for x in sorted(all_api):
            print("  ", x)
        print(f"\n其他 URL 路径 {len(all_urls)} 个:")
        for x in sorted(all_urls)[:40]:
            print("  ", x)

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "menu": menu,
            "for_std": sorted(all_for_std),
            "api": sorted(all_api),
            "other_urls": sorted(all_urls),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已保存: {OUT}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
