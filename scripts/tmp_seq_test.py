import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    pid = 2763  # 2024 计算机方案（之前在 program-search 树里验证过缺英语）

    seqs = [
        ("A: info 先行", [("GET", f"/for-std/program/info/{pid}"), ("GET", f"/for-std/program/root-module-json/{pid}")]),
        ("B: search 树先行", [("GET", f"/for-std/program-search/root-module-json/{pid}"), ("GET", f"/for-std/program/root-module-json/{pid}")]),
    ]
    for label, steps in seqs:
        for method, path in steps:
            r = page.evaluate("""async (p) => {
              const r = await fetch(p, { credentials: 'include' });
              const t = await r.text();
              return { s: r.status, n: t.length, hasEng: t.includes('英语通修') };
            }""", path)
            print(f"{label} {path} -> {r['s']} {r['n']}B 含英语通修={r['hasEng']}")
