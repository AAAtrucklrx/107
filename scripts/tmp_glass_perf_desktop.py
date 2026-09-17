"""桌面端（1440，含 tile blur）fps 实测：有头模式，真实 GPU 合成。"""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

FPS_SCRIPT = """(frames => new Promise(resolve => {
  let n = 0;
  const start = performance.now();
  function tick() {
    n += 1;
    if (n < frames) requestAnimationFrame(tick);
    else resolve({ frames: n, ms: performance.now() - start });
  }
  requestAnimationFrame(tick);
}))(180)"""


def main() -> None:
    with sync_playwright() as p:
        exe = next(
            (
                str(path)
                for path in (
                    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
                    Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
                    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
                )
                if path.is_file()
            ),
            None,
        )
        browser = p.chromium.launch(headless=False, executable_path=exe)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto("http://127.0.0.1:8766", wait_until="networkidle")

        data = page.evaluate(FPS_SCRIPT)
        print(f"home idle: {data['frames'] / (data['ms'] / 1000):.1f} fps")

        scroll = page.evaluate(
            """(async () => {
              const el = document.querySelector('.message-scroll');
              if (!el) return null;
              let n = 0;
              const start = performance.now();
              for (let y = 0; y < 60; y += 1) {
                el.scrollTop = y * 6;
                await new Promise(r => requestAnimationFrame(r));
                n += 1;
              }
              el.scrollTop = 0;
              return { frames: n, ms: performance.now() - start };
            })()"""
        )
        if scroll:
            print(f"home scroll (6 blurred tiles): {scroll['frames'] / (scroll['ms'] / 1000):.1f} fps")

        page.goto("http://127.0.0.1:8766/campus", wait_until="networkidle")
        data = page.evaluate(FPS_SCRIPT)
        print(f"campus tiles idle: {data['frames'] / (data['ms'] / 1000):.1f} fps")
        scroll = page.evaluate(
            """(async () => {
              const el = document.querySelector('.workspace-canvas');
              if (!el) return null;
              let n = 0;
              const start = performance.now();
              for (let y = 0; y < 60; y += 1) {
                el.scrollTop = y * 8;
                await new Promise(r => requestAnimationFrame(r));
                n += 1;
              }
              return { frames: n, ms: performance.now() - start };
            })()"""
        )
        if scroll:
            print(f"campus scroll (8 blurred tiles): {scroll['frames'] / (scroll['ms'] / 1000):.1f} fps")

        page.screenshot(path="docs/qa/glass-desktop-blur-tiles.png")
        print("screenshot: docs/qa/glass-desktop-blur-tiles.png")
        browser.close()


if __name__ == "__main__":
    main()
