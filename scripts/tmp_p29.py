from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    tests = [
        "/for-std/lesson-search/semester/461/search/504586?nameZh=英语",
        "/for-std/lesson-search/semester/461/search/504586?keyword=英语",
        "/for-std/lesson-search/semester/461/search/504586?nameZhLike=%E8%8B%B1%E8%AF%AD",
        "/for-std/lesson-search/semester/461/search/504586?courseNameZh=%E8%8B%B1%E8%AF%AD",
    ]
    for t in tests:
        r = page.evaluate("""async (u) => {
          const r = await fetch(u, { credentials: 'include' });
          const j = await r.json();
          const arr = j.data || j;
          const n = Array.isArray(arr) ? arr.length : (j._page_ ? String(j._page_.totalRows) : 'obj');
          const first = Array.isArray(arr) && arr[0] ? String(arr[0].courseName || arr[0].nameZh || '').slice(0, 30) : '';
          return { n, first };
        }""", t)
        print(t.split("?")[1], "->", r)
