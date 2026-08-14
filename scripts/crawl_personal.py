# -*- coding: utf-8 -*-
"""爬取个人全量数据（学生信息/成绩/课表/考试/个人方案树），测试版数据源生成器。

数据流:
1. CDP 复用 Edge 登录态, 页面内 fetch 抓 jw 内部 API
2. 考试走 catalog 公开 API (无需认证, 与小蜗 query_exam 同源)
3. 输出 %TEMP%/xiaowo_personal/ JSON 备份 + 写入 xiaowo.db
   (student_grades / student_courses 先删后插, 供模拟登录测试)

用法: 先以登录态启动 Edge 调试端口（CDP=9223），再运行本脚本；
     产物供 app_test.py 测试版注入（见 app_test.py 缺失提示文案）。
"""
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from playwright.sync_api import sync_playwright

from tools.course_tools import _parse_schedule_group_str
from tools.program_tools import _parse_tree
from utils.gpa_calculator import score_to_grade_point

CDP = "http://127.0.0.1:9223"
JW = "https://jw.ustc.edu.cn"
CATALOG = "https://catalog.ustc.edu.cn"
ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "database" / "xiaowo.db"
OUT_DIR = Path.home() / "AppData" / "Local" / "Temp" / "xiaowo_personal"

JS_GET = """async (url) => {
    const r = await fetch(url, {credentials:'include', redirect:'follow'});
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) {}
    return {status: r.status, finalUrl: r.url, json: json};
}"""


def _fetch(page, url: str):
    out = page.evaluate(JS_GET, url)
    if not out or out.get("status") != 200:
        raise RuntimeError(f"fetch 失败 {url}: {out}")
    return out["json"]


def _extract_student_id(info: dict, page) -> str:
    """从 student-info 响应多字段探测学号; 兜底从页面 HTML 提取。"""
    for key in ("studentNo", "no", "code", "username", "studentCode", "sid"):
        v = info.get(key)
        if v:
            return str(v)
    html = page.evaluate(
        "async () => { const r = await fetch('/for-std/student/home/student-info',"
        "{credentials:'include'}); return await r.text(); }"
    )
    m = re.search(r'studentNo["\s:=]+"?([A-Za-z0-9]+)', html)
    if m:
        return m.group(1)
    return "TEST_STUDENT"


def _parse_grades(sem_data, grade_data) -> tuple:
    """返回 (grades, student_info)。student_info 取自 stdGradeRank（姓名/学号/院系/年级/专业）。"""
    sem_map = {s.get("id"): s.get("nameZh", str(s.get("id", "")))
               for s in sem_data if isinstance(s, dict)}
    grades = []
    for sem in grade_data.get("semesters", []):
        if not isinstance(sem, dict):
            continue
        sem_name = sem_map.get(sem.get("id"), str(sem.get("id")))
        for sc in sem.get("scores", []):
            if not isinstance(sc, dict) or sc.get("score") is None:
                continue
            try:
                score_int = int(sc["score"])
            except (ValueError, TypeError):
                continue
            gp = sc.get("gp")
            if gp is None:
                gp = score_to_grade_point(score_int)
            else:
                try:
                    gp = float(gp)
                except (ValueError, TypeError):
                    gp = score_to_grade_point(score_int)
            grades.append({
                "semester": sem_name,
                "course_name": sc.get("courseNameCh", ""),
                "course_code": sc.get("courseCode", ""),
                "credits": sc.get("credits", 0) or 0,
                "score": score_int,
                "grade_point": gp,
            })
    rank = grade_data.get("stdGradeRank") or {}
    info = {
        "name": rank.get("studentName", ""),
        "id": rank.get("studentCode", "TEST_STUDENT"),
        "major": rank.get("majorName", rank.get("mngtDepartmentName", "")),
        "grade": (str(rank.get("grade", "")) + "级") if rank.get("grade") else "",
        "department": rank.get("mngtDepartmentName", ""),
        "gpa": rank.get("gpa"),
    }
    return grades, info


def _parse_lessons(lessons: list) -> list[dict]:
    courses = []
    for lesson in lessons:
        if not isinstance(lesson, dict):
            continue
        course_obj = lesson.get("course", {})
        code = course_obj.get("code", lesson.get("code", ""))
        name = course_obj.get("nameZh", lesson.get("nameZh", ""))
        credits = lesson.get("credits", 0) or course_obj.get("credits", 0)
        teachers = []
        for t in lesson.get("teacherAssignmentList", []):
            if isinstance(t, dict):
                p = t.get("person", {})
                n = p.get("nameZh", t.get("nameZh", ""))
                if n:
                    teachers.append(n)
        parsed = _parse_schedule_group_str(lesson.get("scheduleGroupStr", ""))
        teacher = ",".join(teachers) or parsed["teacher_hint"]
        time_str = f"{parsed['day_str']} {parsed['weeks']} 第{parsed['periods']}节" \
            if parsed["day_str"] else lesson.get("scheduleGroupStr", "")
        courses.append({
            "course_code": code,
            "course_name": name,
            "teacher": teacher,
            "credits": credits or 0,
            "time": time_str,
            "location": parsed["location"],
        })
    return courses


def _write_db(student_id: str, grades: list[dict], courses: list[dict], semester: str) -> None:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        with conn:
            conn.execute("DELETE FROM student_grades WHERE student_id = ?", (student_id,))
            for g in grades:
                conn.execute(
                    "INSERT INTO student_grades (student_id, semester, course_name, credits, score, grade_point)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (student_id, g["semester"], g["course_name"], g["credits"],
                     g["score"], g["grade_point"]),
                )
            conn.execute("DELETE FROM student_courses WHERE student_id = ?", (student_id,))
            for c in courses:
                conn.execute(
                    "INSERT INTO student_courses (student_id, course_code, course_name, teacher, credits, time, location, semester)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (student_id, c["course_code"], c["course_name"], c.get("teacher", ""),
                     c.get("credits", 0), c.get("time", ""), c.get("location", ""), semester),
                )
    finally:
        conn.close()


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        page.goto(f"{JW}/for-std/grade/sheet", wait_until="domcontentloaded", timeout=60000)
        time.sleep(2)

        # 1) 成绩（全部学期）→ 顺带提取学生信息
        sem_data = _fetch(page, f"{JW}/for-std/grade/sheet/getSemesters")
        if not isinstance(sem_data, list) or not sem_data:
            raise RuntimeError("getSemesters 返回空")
        sem_ids = [s["id"] for s in sem_data if isinstance(s, dict) and s.get("id")]
        grade_data = _fetch(
            page, f"{JW}/for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds={','.join(map(str, sem_ids))}")
        grades, info = _parse_grades(sem_data, grade_data)
        name, student_id, major, grade = info["name"], info["id"], info["major"], info["grade"]
        print(f"学生: {name} / {student_id} / {major} / {grade} (GPA {info.get('gpa')})", flush=True)
        print(f"成绩: {len(grades)} 条 / {len(sem_ids)} 个学期", flush=True)

        # 3) 课表（最新学期）
        data_id = None
        for sem in grade_data.get("semesters", []):
            for sc in sem.get("scores", []):
                if sc.get("studentAssoc"):
                    data_id = sc["studentAssoc"]
                    break
            if data_id:
                break
        cur_sem_id = sem_ids[0]
        cur_sem_name = next((s.get("nameZh", "") for s in sem_data
                             if isinstance(s, dict) and s.get("id") == cur_sem_id), "")
        lessons = []
        if data_id:
            table = _fetch(
                page, f"{JW}/for-std/course-table/get-data?bizTypeId=2&semesterId={cur_sem_id}&dataId={data_id}")
            lessons = table.get("lessons", []) if isinstance(table, dict) else []
        courses = _parse_lessons(lessons)
        print(f"课表[{cur_sem_name}]: {len(courses)} 门 (dataId={data_id})", flush=True)

        # 4) 个人方案树
        prog_html = page.evaluate(
            "async () => { const r = await fetch('/for-std/program', {credentials:'include',"
            "redirect:'follow'}); return await r.text(); }"
        )
        m = re.search(r"'hasAttachment':null,'id':(\d+),'logs'", prog_html)
        if not m:
            raise RuntimeError("未能提取个人方案 program id")
        tree = _fetch(page, f"{JW}/for-std/program/root-module-json/{m.group(1)}")
        tree_courses = _parse_tree(tree)
        print(f"个人方案树: {m.group(1)} 号, {len(tree_courses)} 门课", flush=True)
        browser.close()

    # 5) 考试（catalog 公开 API, 与小蜗 query_exam 同源）
    exams = []
    try:
        r = requests.get(f"{CATALOG}/api/teach/exam/list/{cur_sem_id}", timeout=15)
        if r.status_code == 200:
            exams = r.json() if isinstance(r.json(), list) else []
    except Exception as e:
        print(f"考试接口失败: {e}", flush=True)
    print(f"考试: {len(exams)} 条", flush=True)

    # 6) 写库 + 备份
    _write_db(student_id, grades, courses, cur_sem_name)
    payload = {
        "user": {"id": student_id, "name": name, "major": major, "grade": grade},
        "grades": grades,
        "courses": courses,
        "semester": cur_sem_name,
        "exams": exams,
        "program_tree": tree,
        "summary": {
            "student": f"{name}/{student_id}",
            "semesters": len(sem_ids),
            "grades": len(grades),
            "courses": len(courses),
            "tree_courses": len(tree_courses),
            "exams": len(exams),
        },
    }
    (OUT_DIR / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    (OUT_DIR / "program_tree.json").write_text(json.dumps(tree, ensure_ascii=False),
                                               encoding="utf-8")
    print(f"备份: {OUT_DIR}", flush=True)
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)
    print("== 爬取完成 ==", flush=True)


if __name__ == "__main__":
    main()
