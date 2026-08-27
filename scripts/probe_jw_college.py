# -*- coding: utf-8 -*-
"""教务接口学院字段探测:连 CDP(9223)复用 Edge 登录态,逐个 fetch jw API,dump 字段。
用法:先以登录态启动 Edge 调试端口(CDP=9223),再运行本脚本。
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

JS_GET = """async (url) => {
    const r = await fetch(url, {credentials:'include', redirect:'follow'});
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) {}
    return {status: r.status, finalUrl: r.url, textLen: text.length, json: json, textHead: text.slice(0, 500)};
}"""


def probe(page, name: str, url: str, key_paths: tuple = ()):
    print(f"\n===== {name} =====")
    print(f"GET {url}")
    try:
        out = page.evaluate(JS_GET, url)
    except Exception as e:
        print(f"  ERR: {type(e).__name__} {str(e)[:120]}")
        return None
    status = out.get("status")
    print(f"  status={status} finalUrl={out.get('finalUrl', '')[:90]} textLen={out.get('textLen')}")
    j = out.get("json")
    if status != 200:
        print(f"  非200, 响应头: {out.get('textHead', '')[:200]}")
        return None
    if j is None:
        print(f"  非 JSON, HTML 头: {out.get('textHead', '')[:200]}")
        return None

    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                np = f"{path}.{k}" if path else k
                if isinstance(v, (dict, list)):
                    walk(v, np)
                else:
                    if any(kw in str(k).lower() for kw in ("college", "academ", "dept", "院", "系", "学院")):
                        print(f"  ★ 学院相关字段: {np} = {str(v)[:80]}")
        elif isinstance(obj, list) and obj:
            walk(obj[0], f"{path}[0]")

    walk(j)
    # 顶层键一览
    if isinstance(j, dict):
        print(f"  顶层键: {list(j.keys())[:25]}")
    elif isinstance(j, list) and j:
        print(f"  数组[{len(j)}] 元素键: {list(j[0].keys())[:25] if isinstance(j[0], dict) else type(j[0]).__name__}")
    return j


def main() -> int:
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(CDP)
        except Exception as e:
            print(f"无法连接 CDP {CDP}: {e}")
            print("请先以登录态启动 Edge 调试端口: msedge.exe --remote-debugging-port=9223 --user-data-dir=... ")
            return 1
        pages = browser.contexts[0].pages if browser.contexts else []
        print(f"CDP 已连接, 页面数: {len(pages)}")
        if not pages:
            print("无页面, 新开一个")
            page = browser.contexts[0].new_page()
            page.goto(JW, timeout=30000)
            time.sleep(3)
        else:
            page = pages[0]

        # 1) 学生档案(重点:学院)
        info = probe(page, "学生档案 student-info", f"{JW}/for-std/student/home/student-info")

        # 2) 成绩学期
        sem = probe(page, "成绩学期 getSemesters", f"{JW}/for-std/grade/sheet/getSemesters")

        # 3) 成绩列表(带 stdGradeRank, 注释称含院系)
        grade = None
        if sem and isinstance(sem, list):
            ids = ",".join(str(s.get("id")) for s in sem[:2] if isinstance(s, dict) and s.get("id"))
            if ids:
                grade = probe(page, "成绩列表 getGradeList", f"{JW}/for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds={ids}")
                if isinstance(grade, dict):
                    rank = grade.get("stdGradeRank")
                    print(f"\n  ★ stdGradeRank = {json.dumps(rank, ensure_ascii=False)[:500] if rank else '无'}")

        # 4) 课表(需要 semesterId/dataId, 先探测 print-data 缺参响应看结构)
        probe(page, "课表 print-data(探测)", f"{JW}/for-std/course-table/semester/0/print-data/0?weekIndex=1")

        # 5) 选课查询(探测)
        probe(page, "选课查询 search(探测)", f"{JW}/for-std/course-take-query/semester/0/search")

        # 6) 个人信息页 HTML(兜底找学院)
        html = page.evaluate(
            "async () => { const r = await fetch('/for-std/student/home/student-info',{credentials:'include'}); return await r.text(); }"
        )
        for kw in ("学院", "院系", "college", "academy", "department"):
            for m in re.finditer(kw + r'["\']?\s*[:：=]\s*["\']?([^"\'<,，]{2,40})', html):
                print(f"  ★ HTML {kw}: {m.group(1).strip()[:40]}")
        print("\n探测完成")
        return 0


if __name__ == "__main__":
    sys.exit(main())
