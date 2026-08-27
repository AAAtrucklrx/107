# -*- coding: utf-8 -*-
"""深挖补测页面(24 个)的 JS 接口路径,并逐个探测新发现的路径。
输出:console + scripts/data/api_deep_results.json
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
JW = "https://jw.ustc.edu.cn"
PAGES = [
    "/for-std/e-category-sub-apply", "/for-std/exam-arrange", "/for-std/grade/sheet",
    "/for-std/program", "/for-std/research-plan-interim", "/for-std/research-plan-selection",
    "/for-std/research-plan-topic", "/for-std/research-plan/grade-choose",
    "/for-std/return-school-apply", "/for-std/school-report-print", "/for-std/sport-grade",
    "/for-std/startup-plan-change-apply", "/for-std/startup-plan-defense",
    "/for-std/startup-plan-flow", "/for-std/startup-plan-selection", "/for-std/startup-plan-topic",
    "/for-std/std-enter-grade", "/for-std/student-info", "/for-std/talent-union-cross",
    "/for-std/talent-union-cross-exit", "/for-std/thesis-change-apply", "/for-std/thesis-flow",
    "/for-std/thesis-selection", "/for-std/thesis-topic",
]
OUT = Path(__file__).resolve().parents[1] / "scripts" / "data" / "api_deep_results.json"

JS_FETCH = """async (u) => {
    const r = await fetch(u, {credentials:'include', redirect:'follow'});
    return {status: r.status, finalUrl: r.url, text: await r.text()};
}"""


def collect(html: str) -> set[str]:
    out = set()
    for m in re.finditer(r'["\'`](/for-std/[A-Za-z0-9_\-/\.]*)["\'`]', html):
        path = m.group(1)
        if 2 <= path.count("/") <= 5 and not any(path.endswith(s) for s in (".js", ".css", ".png", ".jpg", ".ico")):
            out.add(path)
    return out


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        page = browser.contexts[0].pages[0]
        if not page.url.startswith(JW):
            page.goto(JW, timeout=40000)
            time.sleep(2)

        new_paths: set[str] = set()
        print("==== 1. 页面 JS 深挖 ====")
        for path in PAGES:
            try:
                out = page.evaluate(JS_FETCH, JW + path)
                if out.get("status") != 200:
                    continue
                html = out.get("text") or ""
                js_urls = set()
                for m in re.finditer(r'["\'](/[A-Za-z0-9_\-/\.]*\.js(?:\?[^"\']*)?)["\']', html):
                    js_urls.add(m.group(1).split("?")[0])
                chunks = [html]
                for js in list(js_urls)[:6]:
                    try:
                        o = page.evaluate(JS_FETCH, JW + js)
                        if o.get("status") == 200:
                            chunks.append(o.get("text") or "")
                    except Exception:
                        pass
                before = len(new_paths)
                for chunk in chunks:
                    new_paths |= collect(chunk)
                added = [x for x in sorted(new_paths) if x not in set()][:0]
                print(f"  {path}: JS {len(js_urls)} 个, 累计路径 {len(new_paths)} (+{len(new_paths) - before})")
            except Exception as e:
                print(f"  {path}: ERR {str(e)[:60]}")

        print("\n==== 2. 新路径探测 ====")
        results = []
        for path in sorted(new_paths):
            try:
                out = page.evaluate(
                    """async (u) => { const r = await fetch(u, {credentials:'include', redirect:'follow'});
                    const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch(e) {}
                    return {status: r.status, json: j, len: t.length, finalUrl: r.url}; }""", JW + path)
            except Exception as e:
                results.append({"path": path, "status": None, "note": str(e)[:50]})
                continue
            st = out.get("status")
            j = out.get("json")
            if st == 200 and j is not None:
                keys = list(j.keys())[:12] if isinstance(j, dict) else (list(j[0].keys())[:12] if isinstance(j, list) and j else None)
                results.append({"path": path, "status": 200, "json": True, "keys": keys})
                print(f"  [200 JSON] {path} keys={keys}")
            else:
                results.append({"path": path, "status": st, "json": False, "note": f"len={out.get('len')} final={str(out.get('finalUrl'))[60:90]}"})
                if st == 200:
                    print(f"  [200 HTML] {path} len={out.get('len')}")
            time.sleep(0.08)

        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"probed_at": time.strftime("%Y-%m-%d %H:%M:%S"), "paths": sorted(new_paths), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n新路径 {len(new_paths)} 个,已保存: {OUT}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
