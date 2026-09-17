from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    browser = p.chromium.launch(headless=False, executable_path=exe)
    page = browser.new_page(viewport={"width": 1440, "height": 1000})
    page.goto("http://127.0.0.1:8766", wait_until="networkidle")
    page.fill(".composer textarea", "你好，请用一句话介绍你自己")
    page.click(".send-button")
    # 等待回答流式完成（最长 90s）
    try:
        page.wait_for_selector(".message--assistant .markdown-body p", timeout=90000)
        page.wait_for_timeout(3000)
        answer = page.evaluate("() => document.querySelector('.message--assistant .markdown-body')?.innerText?.slice(0, 200)")
        stages = page.evaluate("() => Array.from(document.querySelectorAll('.stage-chip')).map(e => e.innerText)")
        print("STAGES:", stages)
        print("ANSWER:", (answer or "").replace("\n", " / "))
        page.screenshot(path="docs/qa/tmp-smoke-conversation.png")
        print("SMOKE: PASS")
    except Exception as e:
        print("SMOKE: FAIL —", type(e).__name__)
        page.screenshot(path="docs/qa/tmp-smoke-fail.png")
    browser.close()
