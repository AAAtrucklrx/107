"""在登录态浏览器上下文内 fetch 教务关键接口完整响应：搜英语通修 + 导出接口清单。"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(r"f:\小蜗\scripts\data\jw_api_full.json")
BASE = "https://jw.ustc.edu.cn"

ENDPOINTS = [
    ("/for-std/program/root-module-json/3011", "我的培养方案树(3011)"),
    ("/for-std/program-search/root-module-json/3011", "全校方案查询树(3011)"),
    ("/for-std/course-table/get-data", "我的课表"),
    ("/for-std/exam-arrange/info/504586", "我的考试安排"),
    ("/for-std/program/info/504586", "我的培养方案信息"),
    ("/home/get-current-teach-week", "当前教学周"),
    ("/home/menu", "系统菜单"),
]

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((pg for pg in ctx.pages if "jw.ustc.edu.cn" in pg.url), ctx.pages[0])
    if "jw.ustc.edu.cn" not in page.url:
        page.goto(BASE + "/home", wait_until="domcontentloaded")
    page.wait_for_timeout(1500)

    out = {}
    for path, label in ENDPOINTS:
        try:
            body = page.evaluate(
                """async (path) => {
                  const resp = await fetch(path, { credentials: 'include' });
                  const text = await resp.text();
                  try { return { status: resp.status, json: JSON.parse(text) }; }
                  catch { return { status: resp.status, text: text.slice(0, 300) }; }
                }""",
                path,
            )
            payload = body.get("json")
            full = json.dumps(payload, ensure_ascii=False) if payload is not None else str(body.get("text", ""))
            info = {
                "label": label,
                "status": body["status"],
                "bytes": len(full),
                "has_英语": "英语" in full,
                "has_外语": "外语" in full,
                "structure": json.dumps(payload, ensure_ascii=False)[:400] if payload is not None else full,
            }
            # 培养方案树：全树搜英语节点路径
            if "root-module-json" in path and isinstance(payload, dict):
                paths_found = []

                def walk(node, path_str=""):
                    t = node.get("type") or {}
                    name = t.get("nameZh") if isinstance(t, dict) else None
                    cur = f"{path_str}/{name}" if name else path_str
                    n_pc = len(node.get("planCourses") or [])
                    for pc in node.get("planCourses") or []:
                        cname = ((pc.get("course") or {}).get("nameZh")) or ""
                        if "英语" in cname or "外语" in (name or ""):
                            paths_found.append(f"{cur} | {cname} | planCourses@{n_pc}")
                    for ch in node.get("children") or []:
                        walk(ch, cur)

                for root_node in payload.get("courses") or [payload]:
                    if isinstance(root_node, dict):
                        walk(root_node)
                info["英语节点路径"] = paths_found[:12] or "未找到"
            out[path] = info
            print(f"[{body['status']}] {label}: {info['bytes']}B 英语={info['has_英语']} 外语={info['has_外语']}")
        except Exception as exc:
            out[path] = {"label": label, "error": str(exc)[:150]}
            print(f"[ERR] {label}: {str(exc)[:80]}")

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n完整结果 → {OUT}")
