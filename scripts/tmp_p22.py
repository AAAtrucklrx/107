from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    page.goto("https://jw.ustc.edu.cn/for-std/program-search/info/3011", wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    html = page.content()
    print("HTML 大小:", len(html))
    for marker in ("FL1007", "英语通修L3", "英语交流进阶I", "英语通修"):
        print(f"  HTML 含'{marker}':", marker in html)
    # 内嵌数据块
    import re
    scripts = re.findall(r'<script[^>]*type="application/json"[^>]*>', html)
    print("application/json script 块:", len(scripts))
    open(r"f:\小蜗\scripts\data\info3011.html", "w", encoding="utf-8").write(html)
