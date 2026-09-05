"""用本地 mermaid.min.js + Playwright(Edge) 离线渲染 submission/diagrams/*.mmd 为 PNG。

mmdc CLI 在本机挂起（puppeteer 启动问题），此脚本绕开：HTML 引本地
npm 缓存中的 mermaid.min.js，逐图渲染并整页截图。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DIAG = ROOT / "submission" / "diagrams"
MERMAID_JS = next(
    (Path.home() / "AppData/Local/npm-cache/_npx").glob("*/node_modules/mermaid/dist/mermaid.min.js")
)

HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body {{ margin: 0; background: white; padding: 16px; }}
  #diagram {{ width: 1600px; }}
  .mermaid {{ font-family: "Microsoft YaHei", sans-serif; }}
</style>
<script src="{mermaid}"></script>
</head>
<body>
<div class="mermaid" id="diagram">
{source}
</div>
<script>
  mermaid.initialize({{ startOnLoad: true, theme: "base", securityLevel: "loose",
    flowchart: {{ useMaxWidth: false }}, sequence: {{ useMaxWidth: false }},
    themeVariables: {{
      primaryColor: "#e2ecf7", primaryBorderColor: "#034ea1", primaryTextColor: "#1c2430",
      lineColor: "#566672", fontFamily: "Microsoft YaHei, sans-serif", fontSize: "15px",
    }} }});
  window.__ready = false;
  mermaid.run({{ querySelector: "#diagram" }}).then(() => {{ window.__ready = true; }});
</script>
</body></html>
"""


def main() -> None:
    work = DIAG / "_render"
    work.mkdir(exist_ok=True)
    with sync_playwright() as p:
        exe = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        browser = p.chromium.launch(headless=True, executable_path=exe)
        page = browser.new_page(viewport={"width": 1700, "height": 1200}, device_scale_factor=2)
        for mmd in sorted(DIAG.glob("*.mmd")):
            html_path = work / (mmd.stem + ".html")
            html_path.write_text(
                HTML.format(mermaid=MERMAID_JS.as_posix(), source=mmd.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            page.goto(html_path.as_uri())
            page.wait_for_function("window.__ready === true", timeout=30000)
            page.wait_for_timeout(500)
            out = DIAG / (mmd.stem + ".png")
            # 元素级截图：精确包围渲染后的 SVG，避免视口截断
            page.locator("#diagram svg").screenshot(path=str(out))
            box = page.locator("#diagram svg").bounding_box()
            print(f"渲染 {out.name} ({out.stat().st_size // 1024} KB, svg {box['width']:.0f}x{box['height']:.0f})")
        browser.close()
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
