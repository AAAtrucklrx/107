from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    for i, pg in enumerate(ctx.pages):
        print(f"{i}: {pg.url[:110]}")
