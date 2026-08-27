# -*- coding: utf-8 -*-
"""批量探测收割到的全部 /for-std/ 路径:GET 状态分组,输出可用接口清单。
用法:CDP 9223 已登录。输出 console + scripts/data/api_probe_results.json
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
JW = "https://jw.ustc.edu.cn"
SRC = Path(__file__).resolve().parents[1] / "scripts" / "data" / "api_paths_discovered.json"
OUT = Path(__file__).resolve().parents[1] / "scripts" / "data" / "api_probe_results.json"


def main() -> int:
    paths = json.loads(SRC.read_text(encoding="utf-8"))["for_std"]
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not page.url.startswith(JW):
            page.goto(JW, timeout=40000)
            time.sleep(2)

        results = []
        for path in sorted(paths):
            url = JW + path
            try:
                out = page.evaluate(
                    """async (u) => { const r = await fetch(u, {credentials:'include', redirect:'follow'});
                    const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch(e) {}
                    return {status: r.status, json: j, len: t.length, finalUrl: r.url}; }""", url)
            except Exception as e:
                results.append({"path": path, "status": None, "note": f"ERR {str(e)[:50]}"})
                continue
            status = out.get("status")
            j = out.get("json")
            if status == 200 and j is not None:
                keys = list(j.keys())[:15] if isinstance(j, dict) else (list(j[0].keys())[:15] if isinstance(j, list) and j else None)
                results.append({"path": path, "status": 200, "json": True, "keys": keys, "note": f"JSON keys={keys}"})
            else:
                results.append({"path": path, "status": status, "json": False, "note": f"len={out.get('len')}"})
            time.sleep(0.08)

    by_status: dict[int, list] = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    for status in sorted(by_status, key=lambda s: (s is None, s)):
        rows = by_status[status]
        print(f"\n===== status={status}: {len(rows)} 个 =====")
        for r in rows:
            extra = f" keys={r.get('keys')}" if r.get("json") else f" {r.get('note')}"
            print(f"  [{status}] {r['path']}{extra[:110]}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(results),
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已保存: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
