"""玻璃 UI 移动性能冒烟：CPU 6x 节流（逼近中端手机）测 rAF 帧率。

演示基线：华为 Mate 70 Pro+ 逻辑宽 424px。GPU 不节流（真机 GPU 更强），
本脚本给出保守下界；目标交互帧率 >= 50fps。
用法：先起 XIAOWO_AUTH_MODE=anonymous 实例于 8766，再运行本脚本。
"""
from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8766"
SHOTS = Path("scripts/data/tmp_test/web_e2e/screenshots")

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


def measure(page, label: str) -> float:
    data = page.evaluate(FPS_SCRIPT)
    fps = data["frames"] / (data["ms"] / 1000)
    print(f"{label}: {fps:.1f} fps ({data['frames']} frames / {data['ms']:.0f} ms)")
    return fps


def main() -> None:
    with sync_playwright() as p:
        exe = next(
            (
                path
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
        browser = p.chromium.launch(headless=True, executable_path=str(exe) if exe else None)
        context = browser.new_context(viewport={"width": 424, "height": 956}, device_scale_factor=2)
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": 6})
        page.goto(URL, wait_until="networkidle")

        results = [measure(page, "chat idle (CPU 6x)")]

        page.goto(URL + "/campus", wait_until="networkidle")
        results.append(measure(page, "campus tiles (CPU 6x)"))

        # 主题切换连拍：玻璃 crossfade 期间帧率
        page.evaluate("document.documentElement.dataset.theme = 'dark'")
        results.append(measure(page, "theme crossfade -> dark (CPU 6x)"))
        page.screenshot(path=str(SHOTS / "glass-perf-mobile-424-dark.png"))
        page.evaluate("document.documentElement.dataset.theme = 'light'")
        results.append(measure(page, "theme crossfade -> light (CPU 6x)"))

        # 滚动压力：对 campus 主容器做程序化滚动
        scroll_fps = page.evaluate(
            """(async () => {
              const el = document.querySelector('.workspace-canvas');
              if (!el) return null;
              let n = 0;
              const start = performance.now();
              for (let y = 0; y < 40; y += 1) {
                el.scrollTop = y * 8;
                await new Promise(r => requestAnimationFrame(r));
                n += 1;
              }
              return { frames: n, ms: performance.now() - start };
            })()"""
        )
        if scroll_fps:
            fps = scroll_fps["frames"] / (scroll_fps["ms"] / 1000)
            results.append(fps)
            print(f"campus scroll (CPU 6x): {fps:.1f} fps")

        worst = min(results)
        print(f"\nworst case: {worst:.1f} fps -> {'PASS (>=50)' if worst >= 50 else 'BELOW 50, 需启用二级降级'}")
        browser.close()


if __name__ == "__main__":
    main()
