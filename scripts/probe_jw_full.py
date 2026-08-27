# -*- coding: utf-8 -*-
"""教务接口全量探测:逐个 fetch jw API,记录状态/字段/学院信息,输出接口清单。
用法:Edge 9223 CDP 调试端口已登录 jw 后运行本脚本。
输出:console 摘要 + scripts/data/jw_api_inventory.json
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
OUT = Path(__file__).resolve().parents[1] / "scripts" / "data" / "jw_api_inventory.json"

JS_GET = """async (url) => {
    const r = await fetch(url, {credentials:'include', redirect:'follow'});
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) {}
    return {status: r.status, finalUrl: r.url, textLen: text.length, json: json, textHead: text.slice(0, 300)};
}"""

INVENTORY: list[dict] = []
COLLEGE_HITS: list[str] = []


def record(name: str, url: str, status: int | None, note: str, keys: list | None = None, college: list | None = None):
    entry = {"name": name, "url": url, "status": status, "note": note}
    if keys:
        entry["top_keys"] = keys
    if college:
        entry["college_fields"] = college
    INVENTORY.append(entry)
    print(f"[{status}] {name}: {note}")


def probe(page, name: str, url: str, dump=False):
    try:
        out = page.evaluate(JS_GET, url)
    except Exception as e:
        record(name, url, None, f"ERR {type(e).__name__} {str(e)[:80]}")
        return None
    status = out.get("status")
    j = out.get("json")
    college = []
    if status == 200 and j is not None:
        def walk(obj, path=""):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    np = f"{path}.{k}" if path else k
                    if isinstance(v, (dict, list)):
                        walk(v, np)
                    elif any(kw in str(k).lower() for kw in ("college", "academ", "dept", "院", "系")):
                        college.append(f"{np}={str(v)[:60]}")
            elif isinstance(obj, list) and obj:
                walk(obj[0], f"{path}[0]")
        walk(j)
        keys = list(j.keys())[:30] if isinstance(j, dict) else (list(j[0].keys())[:30] if isinstance(j, list) and j and isinstance(j[0], dict) else None)
        note = f"JSON ok, top_keys={keys}" if keys else f"JSON ok len={out.get('textLen')}"
        if dump:
            print(f"    dump: {json.dumps(j, ensure_ascii=False)[:600]}")
    else:
        note = f"status={status} len={out.get('textLen')} head={str(out.get('textHead'))[:100]}"
        keys = None
    if college:
        COLLEGE_HITS.append(name)
        note += f" ★学院字段: {college}"
    record(name, url, status, note, keys, college if college else None)
    return j


def discover_paths(page) -> list[str]:
    """从页面 HTML 提取 /for-std/ 接口路径。"""
    paths = set()
    for url in (f"{JW}/for-std/student/home/student-info", f"{JW}/for-std/course-table", f"{JW}/for-std/program", f"{JW}/for-std/grade/sheet"):
        try:
            html = page.evaluate(
                "async (u) => { const r = await fetch(u,{credentials:'include'}); return await r.text(); }", url)
            for m in re.finditer(r'["\'](/for-std/[A-Za-z0-9_\-/\.]*?)(?:\?[^"\']*)?["\']', html):
                p = m.group(1)
                if p.count("/") <= 5:
                    paths.add(p)
        except Exception:
            pass
    return sorted(paths)


def main() -> int:
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
        except Exception as e:
            print(f"无法连接 CDP: {e}")
            return 1
        pages = browser.contexts[0].pages if browser.contexts else []
        page = pages[0] if pages else browser.contexts[0].new_page()

        print("======== 阶段 1: 已知接口逐个探测 ========")
        # 1. 学生档案(JSON + HTML)
        probe(page, "学生档案 student-info", f"{JW}/for-std/student/home/student-info", dump=True)
        # 2. 成绩学期 → 成绩列表
        sem = probe(page, "成绩学期 getSemesters", f"{JW}/for-std/grade/sheet/getSemesters")
        sem_ids = []
        if isinstance(sem, list):
            sem_ids = [str(s.get("id")) for s in sem if isinstance(s, dict) and s.get("id")][:4]
            print(f"    学期: {[(s.get('id'), s.get('nameZh')) for s in sem if isinstance(s, dict)][:6]}")
        if sem_ids:
            g = probe(page, "成绩列表 getGradeList", f"{JW}/for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds={','.join(sem_ids)}", dump=True)
            if isinstance(g, dict):
                rank = g.get("stdGradeRank")
                if rank:
                    print(f"    ★ stdGradeRank = {json.dumps(rank, ensure_ascii=False)[:800]}")
                    record("stdGradeRank(成绩内嵌档案)", "(getGradeList 内嵌)", 200, f"字段: {list(rank.keys()) if isinstance(rank, dict) else rank}")
        # 3. 课表(dataId 从页面提取)
        try:
            html = page.evaluate("async () => { const r = await fetch('/for-std/course-table',{credentials:'include'}); return await r.text(); }")
            m = re.search(r"/for-std/[^\"']*\?.*?dataId=(\d+)", html)
            did = m.group(1) if m else None
            print(f"    课表 dataId: {did}")
            if did and sem_ids:
                probe(page, "课表数据 course-table/get-data", f"{JW}/for-std/course-table/get-data?bizTypeId=2&semesterId={sem_ids[0]}&dataId={did}", dump=True)
                probe(page, "课表打印 print-data", f"{JW}/for-std/course-table/semester/{sem_ids[0]}/print-data/{did}?weekIndex=1")
        except Exception as e:
            print(f"    课表探测 ERR: {str(e)[:100]}")
        # 4. 选课查询
        probe(page, "选课查询 course-take-query", f"{JW}/for-std/course-take-query/semester/{sem_ids[0] if sem_ids else 0}/search")
        # 5. 培养方案
        try:
            html = page.evaluate("async () => { const r = await fetch('/for-std/program',{credentials:'include', redirect:'follow'}); return await r.text(); }")
            m = re.search(r"program/info/(\d+)", html)
            pid = m.group(1) if m else None
            print(f"    program dataId: {pid}")
            if pid:
                probe(page, "个人方案信息 program/info", f"{JW}/for-std/program/info/{pid}")
                probe(page, "个人方案树 root-module-json", f"{JW}/for-std/program/root-module-json/{pid}", dump=True)
        except Exception as e:
            print(f"    方案探测 ERR: {str(e)[:100]}")

        print("\n======== 阶段 2: 页面中发现的其他接口 ========")
        paths = discover_paths(page)
        print(f"发现 {len(paths)} 个 /for-std/ 路径")
        for path in paths:
            probe(page, f"发现接口 {path}", f"{JW}{path}")
            time.sleep(0.15)

        # 保存清单
        OUT.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "probed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "college_found_in": COLLEGE_HITS,
            "api_count": len(INVENTORY),
            "apis": INVENTORY,
        }
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n======== 完成 ========")
        print(f"接口总数: {len(INVENTORY)}")
        print(f"含学院字段的接口: {COLLEGE_HITS}")
        print(f"清单已保存: {OUT}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
