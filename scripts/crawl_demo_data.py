# -*- coding: utf-8 -*-
"""爬取真实教务数据更新演示 fixture(CDP 复用 Edge 登录态)。
来源:jw.ustc.edu.cn 内部 API(student-info 404, 用 getGradeList 的 stdGradeRank 档案)。
输出:更新 fixtures/demo/PB25111691.json(备份旧版)。
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
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "demo" / "PB25111691.json"

JS_GET = """async (url) => {
    const r = await fetch(url, {credentials:'include', redirect:'follow'});
    const text = await r.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) {}
    return {status: r.status, finalUrl: r.url, json: json, text: text};
}"""


def _json(page, url):
    out = page.evaluate(JS_GET, url)
    if out.get("status") != 200:
        raise RuntimeError(f"GET {url} -> {out.get('status')}")
    return out["json"]


def _text(page, url):
    out = page.evaluate(JS_GET, url)
    if out.get("status") not in (200, 302):
        raise RuntimeError(f"GET {url} -> {out.get('status')}")
    return out.get("text") or "", out.get("finalUrl") or ""


def sem_name(ch: str) -> str:
    """'2026春' -> '2026年春季学期'; 兼容 '2025秋'。"""
    m = re.match(r"(\d{4})(春|秋)", ch or "")
    if m:
        return f"{m.group(1)}年{m.group(2)}季学期"
    return ch or ""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not page.url.startswith(JW):
            page.goto(JW, timeout=40000)
            time.sleep(4)
        print("页面:", page.url[:80])

        # ── 1. 成绩与档案(stdGradeRank 含院系/专业/排名) ──
        sem = _json(page, f"{JW}/for-std/grade/sheet/getSemesters")
        sem_ids = [str(s["id"]) for s in sem if isinstance(s, dict) and s.get("id")]
        print("学期:", [(s.get("id"), s.get("nameZh")) for s in sem if isinstance(s, dict)])
        grade = _json(page, f"{JW}/for-std/grade/sheet/getGradeList?trainTypeId=1&semesterIds={','.join(sem_ids)}")
        rank = grade.get("stdGradeRank") or {}
        print("档案:", rank.get("studentName"), rank.get("studentCode"),
              "|", rank.get("mngtDepartmentName"), "|", rank.get("majorName"), rank.get("grade"),
              "| 排名", rank.get("majorRank"), "/", rank.get("majorStdCount"))

        grades = []
        for sem_item in grade.get("semesters", []):
            semester = sem_name(sem_item.get("semesterCh"))
            for sc in sem_item.get("scores", []):
                if not isinstance(sc, dict):
                    continue
                score_raw = str(sc.get("score") or "").strip()
                numeric = re.fullmatch(r"\d+(\.\d+)?", score_raw)
                grades.append({
                    "semester": semester,
                    "course_name": sc.get("courseNameCh") or "",
                    "credits": float(sc.get("credits") or 0),
                    "score": float(score_raw) if numeric else -1,
                    "score_text": None if numeric else (sc.get("scoreCh") or score_raw or None),
                    "grade_point": float(sc.get("gp") or 0),
                })
        print(f"成绩 {len(grades)} 门")

        # ── 2. 课表: dataId 从 course-table/info/{student_data_id} 提取 ──
        sid = str(rank.get("studentCode") or "")
        # student_data_id: 从 getGradeList scores[0].studentAssoc 取(与课表 info 的 dataId 一致)
        data_id = None
        for sem_item in grade.get("semesters", []):
            for sc in sem_item.get("scores", []):
                if isinstance(sc, dict) and sc.get("studentAssoc"):
                    data_id = str(sc["studentAssoc"])
                    break
            if data_id:
                break
        courses = []
        if data_id:
            # 尝试 get-data(JSON)
            cur_sem = sem_ids[0]
            try:
                table = _json(page, f"{JW}/for-std/course-table/get-data?bizTypeId=2&semesterId={cur_sem}&dataId={data_id}")
                print("课表 get-data OK, 顶层键:", list(table.keys()) if isinstance(table, dict) else type(table).__name__)
                semester_name = sem_name(table.get("semesterCh")) if isinstance(table, dict) else ""
                lessons = table.get("lessons") or []
                # 复用 crawl_personal 的解析逻辑:scheduleGroupStr → day_str/weeks/periods/location
                from tools.course_tools import _parse_schedule_group_str
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
                    open_dept = lesson.get("openDepartment") or {}
                    courses.append({
                        "course_code": code,
                        "course_name": name,
                        "teacher": teacher,
                        "credits": credits or 0,
                        "time": time_str,
                        "location": parsed["location"],
                        "semester": semester_name,
                        "open_department": open_dept.get("nameZh") or "",
                    })
            except Exception as e:
                print(f"get-data 失败({str(e)[:80]}), 尝试 info HTML 解析")
            if not courses:
                # 回退: course-table/info/{dataId} HTML 内嵌 JSON 提取
                html, _ = _text(page, f"{JW}/for-std/course-table/info/{data_id}")
                m = re.search(r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});", html, re.S)
                if not m:
                    m = re.search(r"data\s*=\s*(\[.*?\]);", html, re.S)
                if m:
                    try:
                        payload = json.loads(m.group(1))
                        print("info HTML 内嵌 JSON 键:", list(payload.keys())[:10] if isinstance(payload, dict) else type(payload).__name__)
                    except Exception as e:
                        print(f"内嵌 JSON 解析失败: {str(e)[:80]}")
        print(f"课表 {len(courses)} 门")

        # ── 3. 个人方案树 ──
        program_tree = None
        try:
            html, final_url = _text(page, f"{JW}/for-std/program")
            mid = re.search(r"program/info/(\d+)", final_url or html)
            info_html = html
            if mid:
                info_html, _ = _text(page, f"{JW}/for-std/program/info/{mid.group(1)}")
            pid = re.search(r"'hasAttachment':\s*null,\s*'id':\s*(\d+)", info_html)
            if not pid:
                pid = re.search(r'"hasAttachment":null,"id":(\d+)', info_html)
            if pid:
                tree = _json(page, f"{JW}/for-std/program/root-module-json/{pid.group(1)}")
                print("个人方案树根键:", list(tree.keys())[:12] if isinstance(tree, dict) else type(tree).__name__)
                program_tree = tree
            else:
                print("方案 id 未提取到, 尝试页面 HTML 中搜索 program JSON")
        except Exception as e:
            print(f"方案爬取失败: {str(e)[:120]}")

        # ── 4. 生成 program 结构(真实个人方案树 → 标准结构) ──
        program = None
        if program_tree:
            from tools.program_tools import get_my_program
            program = get_my_program.invoke({
                "major": rank.get("majorName") or "",
                "grade": f"{rank.get('grade')}级",
                "personal_tree": program_tree,
            })
            print("program:", program.get("name"), "|", program.get("source"), "| 课程", len(program.get("courses") or []))

        # ── 5. 写回 fixture ──
        old = json.loads(FIX.read_text(encoding="utf-8"))
        acad_ov = grade.get("overview") or {}
        new_fixture = {
            "fixture_version": 3,
            "synthetic": True,
            "generated_for": "xiaowo-demo",
            "note": "真实教务数据(2026-08-27 用户授权爬取 jw.ustc.edu.cn)",
            "academic_overview": {
                "gpa": acad_ov.get("gpa"),
                "passed_credits": acad_ov.get("passedCredits"),
                "weighted_score": acad_ov.get("weightedScore"),
                "arithmetic_score": acad_ov.get("arithmeticScore"),
                "not_passed_credits": acad_ov.get("notPassedCredits"),
            },
            "user": {
                "id": rank.get("studentCode") or old["user"]["id"],
                "name": rank.get("studentName") or old["user"]["name"],
                "major": rank.get("majorName") or old["user"]["major"],
                "grade": f"{rank.get('grade')}级" if rank.get("grade") else old["user"]["grade"],
                "department": rank.get("mngtDepartmentName") or "",
                "rank": {"majorRank": rank.get("majorRank"), "majorStdCount": rank.get("majorStdCount")} if rank.get("majorRank") is not None else None,
            },
            "grades": grades or old["grades"],
            "courses": courses or old["courses"],
            "semester": "2026年春季学期",
            "program": program or old["program"],
        }
        FIX.write_text(json.dumps(new_fixture, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\nfixture 已更新:", FIX)
        print("  user:", new_fixture["user"]["id"], new_fixture["user"]["name"], new_fixture["user"]["major"], new_fixture["user"]["grade"], new_fixture["user"].get("department"))
        print(f"  grades: {len(new_fixture['grades'])} | courses: {len(new_fixture['courses'])} | program: {new_fixture['program'].get('name') if new_fixture['program'] else '无'}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
