"""移动端 424px 全景截图：chat/campus/academic × 浅色，另截 390 对照。"""
from __future__ import annotations

from playwright.sync_api import sync_playwright

PAGES = [("chat", "/"), ("campus", "/campus"), ("academic", "/academic")]

with sync_playwright() as p:
    exe = next(
        str(path)
        for path in (
            "C:/Program Files/Google/Chrome/Application/chrome.exe",
            "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
            "C:/Program Files/Microsoft/Edge/Application/msedge.exe",
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
        )
        if __import__("pathlib").Path(path).is_file()
    )
    browser = p.chromium.launch(headless=False, executable_path=exe)
    for width, tag in ((424, "424"), (390, "390")):
        page = browser.new_page(viewport={"width": width, "height": 956}, device_scale_factor=2)
        for name, path in PAGES:
            page.goto(f"http://127.0.0.1:8766{path}", wait_until="networkidle")
            page.wait_for_timeout(500)
            page.screenshot(path=f"docs/qa/mob-audit-{name}-{tag}.png")
            print(f"mob-audit-{name}-{tag}.png")
        page.close()
    browser.close()
