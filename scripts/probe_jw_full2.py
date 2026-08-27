# -*- coding: utf-8 -*-
"""教务+目录全接口完整探测 v2:抓主要页面提取全部 API 路径,逐个 GET 记录状态/返回结构。
输出:console 摘要 + scripts/data/api_inventory_full.json
"""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
JW = "https://jw.ustc.edu.cn"
CATALOG = "https://catalog.ustc.edu.cn"
OUT = Path(__file__).resolve().parents[1] / "scripts" / "data" / "api_inventory_full.json"

# 主要功能页面(从中提取接口路径)
PAGES = [
    "/for-std/home",
    "/for-std/grade/sheet",
    "/for-std/course-table",
    "/for-std/program",
    "/for-std/course-take-query",
    "/for-std/exam/arrange/index",
    "/for-std/classroom",
    "/for-std/student/home",
    "/for-std/query/course-result",
    "/for-std/query/student-result",
    "/for-std/student/info",
    "/for-std/graduation/credit-query",
]

INVENTORY: list[dict] = []


def record(name, url, status, note, keys=None, auth=None):
    entry = {"name": name, "url": url, "status": status, "auth": auth or "session", "note": note}
    if keys:
        entry["top_keys"] = keys
    INVENTORY.append(entry)
    print(f"  [{status}] {name}: {note}")


def probe_session(page, name, url, auth="session"):
    try:
        out = page.evaluate(
            """async (u) => { const r = await fetch(u, {credentials:'include', redirect:'follow'});
            const t = await r.text(); let j = null; try { j = JSON.parse(t); } catch(e) {}
            return {status: r.status, json: j, len: t.length, head: t.slice(0,80)}; }""", url)
    except Exception as e:
        record(name, url, None, f"ERR {str(e)[:60]}", auth=auth)
        return None
    status = out.get("status")
    j = out.get("json")
    keys = None
    if status == 200 and j is not None:
        keys = list(j.keys())[:20] if isinstance(j, dict) else (list(j[0].keys())[:20] if isinstance(j, list) and j and isinstance(j[0], dict) else None)
        record(name, url, status, f"JSON ok keys={keys}", keys, auth)
    else:
        record(name, url, status, f"len={out.get('len')} head={str(out.get('head'))[:70]}", auth=auth)
    return j


def discover_paths(page, page_paths) -> dict[str, list[str]]:
    """抓页面 HTML,提取 /for-std/ 与 /api/ 路径(去重)。"""
    found: dict[str, list[str]] = {"for_std": [], "api": []}
    html_bundle = ""
    for p in page_paths:
        try:
            html = page.evaluate(
                "async (u) => { const r = await fetch(u,{credentials:'include'}); return await r.text(); }",
                JW + p)
            html_bundle += html
        except Exception:
            pass
    seen = set()
    for m in re.finditer(r'["\'](/for-std/[A-Za-z0-9_\-/\.]*)', html_bundle):
        path = m.group(1)
        if path not in seen and path.count("/") <= 5:
            seen.add(path)
            found["for_std"].append(path)
    seen2 = set()
    for m in re.finditer(r'["\'](/api/[A-Za-z0-9_\-/\.]*)', html_bundle):
        path = m.group(1)
        if path not in seen2 and path.count("/") <= 5:
            seen2.add(path)
            found["api"].append(path)
    return found


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not page.url.startswith(JW):
            page.goto(JW, timeout=40000)
            time.sleep(3)

        print("======== A. 主要页面接口路径发现 ========")
        found = discover_paths(page, PAGES)
        print(f"发现 /for-std/ 路径 {len(found['for_std'])} 个:")
        for x in found["for_std"]:
            print("   ", x)
        print(f"发现 /api/ 路径 {len(found['api'])} 个:")
        for x in found["api"]:
            print("   ", x)

        print("\n======== B. 已知核心接口(带正确参数) ========")
        sem = probe_session(page, "getSemesters", f"{JW}/for-std/grade/sheet/getSemesters")
        sem_ids = [str(s["id"]) for s in sem if isinstance(s, dict) and s.get("id")][:3] if isinstance(sem, list) else []
        if sem_ids:
            probe_session(page, "getGradeList", f"{JW}/for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds={','.join(sem_ids)}")
            # dataId 从成绩 studentAssoc
            data_id = None
            g = page.evaluate(
                """async (u) => { const r = await fetch(u,{credentials:'include'}); return await r.json(); }""",
                f"{JW}/for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds={','.join(sem_ids)}")
            for sem_item in g.get("semesters", []):
                for sc in sem_item.get("scores", []):
                    if isinstance(sc, dict) and sc.get("studentAssoc"):
                        data_id = str(sc["studentAssoc"])
                        break
                if data_id:
                    break
            if data_id:
                probe_session(page, "课表 get-data", f"{JW}/for-std/course-table/get-data?bizTypeId=2&semesterId={sem_ids[0]}&dataId={data_id}")
                probe_session(page, "课表 print-data(第1周)", f"{JW}/for-std/course-table/semester/{sem_ids[0]}/print-data/{data_id}?weekIndex=1")
                probe_session(page, "选课查询 search", f"{JW}/for-std/course-take-query/semester/{sem_ids[0]}/search?dataId={data_id}")
            probe_session(page, "选课查询 search(无参)", f"{JW}/for-std/course-take-query/semester/{sem_ids[0]}/search")
        # 方案树
        try:
            html, _ = page.evaluate(
                """async () => { const r = await fetch('/for-std/program',{credentials:'include', redirect:'follow'}); return [await r.text(), r.url]; }""")
            pid = None
            m = re.search(r"program/info/(\d+)", html[1] or html[0])
            if m:
                pid = m.group(1)
            if not pid:
                m = re.search(r"'hasAttachment':\s*null,\s*'id':\s*(\d+)", html[0])
                if m:
                    pid = m.group(1)
            if pid:
                probe_session(page, "方案信息 program/info", f"{JW}/for-std/program/info/{pid}")
                probe_session(page, "方案树 root-module-json", f"{JW}/for-std/program/root-module-json/{pid}")
        except Exception as e:
            print(f"  方案探测 ERR: {str(e)[:80]}")

        print("\n======== C. 发现的 /for-std/ 路径逐个 GET ========")
        for path in found["for_std"]:
            if any(k in path for k in ("get-data", "root-module-json", "print-data", "getGradeList", "getSemesters", "search", "info/", "datum")):
                continue  # 已测/需参数
            probe_session(page, f"路径 {path}", JW + path)
            time.sleep(0.1)

    print("\n======== D. catalog 公开接口(免登录) ========")
    headers = {"User-Agent": "Mozilla/5.0"}
    catalog_apis = [
        ("学期列表", f"{CATALOG}/api/teach/semester/list"),
        ("当前学期", f"{CATALOG}/api/teach/semester/current"),
        ("考试安排(2026春421)", f"{CATALOG}/api/teach/exam/list/421"),
        ("课程搜索", f"{CATALOG}/api/teach/course/search?keyword=%E6%95%B0%E6%8D%AE%E7%BB%93%E6%9E%84"),
        ("教室占用", f"{CATALOG}/api/teach/classroom/occupancy"),
        ("排课列表", f"{CATALOG}/api/teach/lesson/list?semesterId=421"),
    ]
    for name, url in catalog_apis:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            j = None
            try:
                j = r.json()
            except Exception:
                pass
            if j is not None:
                keys = list(j.keys())[:15] if isinstance(j, dict) else (list(j[0].keys())[:15] if isinstance(j, list) and j else None)
                note = f"JSON ok keys={keys}"
            else:
                note = f"非JSON len={len(r.text)} head={r.text[:60]!r}"
            record(name, url, r.status_code, note, auth="public")
        except Exception as e:
            record(name, url, None, f"ERR {str(e)[:60]}", auth="public")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "api_count": len(INVENTORY),
        "apis": INVENTORY,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n======== 完成: {len(INVENTORY)} 个接口记录 -> {OUT} ========")
    return 0


if __name__ == "__main__":
    sys.exit(main())
