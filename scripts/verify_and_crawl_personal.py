# -*- coding: utf-8 -*-
"""CAS 登录态注入 cas_client + 全接口验证 + 完整个人数据爬取。
- 从 CDP(9223,已登录 jw)提取 cookies 注入 cas_client 会话(与 CAS 登录等价,不碰凭据)
- 逐个调用所有核心数据接口,输出验证报告
- 完整数据包 -> scripts/data/personal_full.json;同步更新演示 fixture
"""
import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

CDP = "http://127.0.0.1:9223"
JW = "https://jw.ustc.edu.cn"
STUDENT = "PB25111691"
DATA_ID = 504586
OUT = Path(__file__).resolve().parents[1] / "scripts" / "data" / "personal_full.json"
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "demo" / "PB25111691.json"

REPORT: list[dict] = []


def report(name, ok, detail="", extra=None):
    REPORT.append({"name": name, "ok": ok, "detail": detail[:180]})
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}: {detail[:150]}")
    return extra


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if not page.url.startswith(JW):
            page.goto(JW, timeout=40000)
            time.sleep(3)
        # ── 1. 从浏览器提取 jw cookie 注入 requests 会话 ──
        cookies = ctx.cookies()
        from services.session_ctx import set_student, reset_student
        token = set_student(STUDENT)  # 先设学号桶
        try:
            from services.service_container import ServiceContainer
            sc = ServiceContainer()
            sc.init_database(Path(__file__).resolve().parents[1] / "database" / "xiaowo.db",
                             Path(__file__).resolve().parents[1] / "database" / "schema.sql", seed_sql=None)
            client = sc.cas_client  # 当前桶实例
            jw_cookies = [c for c in cookies if "ustc.edu.cn" in c.get("domain", "")]
            for c in jw_cookies:
                client._session.cookies.set(c["name"], c["value"], domain=c["domain"].lstrip("."))
            client._logged_in = True
            client._student_id = STUDENT
            client._student_data_id = DATA_ID
            print(f"cookie 注入: {len(jw_cookies)} 个(域: {sorted({c['domain'] for c in jw_cookies})})")

            # ── 2. 全接口验证 ──
            print("\n===== 成绩 =====")
            sem = client.get_grade_semesters()
            sem_ids = [str(s["id"]) for s in sem if isinstance(s, dict)][:4] if isinstance(sem, list) else []
            report("getSemesters", bool(sem), f"{len(sem_ids)} 学期: {[(s.get('id'), s.get('nameZh')) for s in sem if isinstance(s, dict)][:3]}")
            grades = client.get_grades([int(x) for x in sem_ids]) if sem_ids else {}
            gcnt = sum(len(sem_i.get("scores", [])) for sem_i in (grades.get("semesters") or []) if isinstance(sem_i, dict)) if isinstance(grades, dict) else 0
            rank = grades.get("stdGradeRank") if isinstance(grades, dict) else {}
            report("getGradeList", isinstance(grades, dict) and gcnt > 0, f"{gcnt} 门成绩; stdGradeRank: {rank.get('studentName')}/{rank.get('majorName')}/{rank.get('mngtDepartmentName')}")
            types = client.get_json("/for-std/grade/sheet/getGradeSheetTypes")
            report("getGradeSheetTypes", isinstance(types, list) and len(types) > 0, f"{len(types) if isinstance(types, list) else 0} 种成绩单类型")

            print("\n===== 课表 =====")
            table = client.get_course_table(int(sem_ids[0]) if sem_ids else 421)
            lessons = table.get("lessons", []) if isinstance(table, dict) else []
            report("course-table/get-data", isinstance(table, dict) and len(lessons) > 0, f"{len(lessons)} 门课")
            week1 = client.get_course_table_by_week(int(sem_ids[0]) if sem_ids else 421, 1)
            report("print-data(第1周)", isinstance(week1, dict) and "studentTableVm" in week1, "studentTableVm 结构")

            print("\n===== 选课 =====")
            sel = client.get_course_selection(int(sem_ids[0]) if sem_ids else 421)
            sel_list = sel.get("data", []) if isinstance(sel, dict) else []
            report("course-take-query/search", isinstance(sel, dict) and "data" in sel, f"{len(sel_list)} 门已选; 首门含 openDepartmentName={'openDepartmentName' in (sel_list[0] if sel_list else {})}")

            print("\n===== 档案 =====")
            info_page = client.get_student_info_page()
            prof = info_page.get("profile", {}) if isinstance(info_page, dict) else {}
            report("student-info/info 页解析", info_page.get("fields", 0) > 50, f"{info_page.get('fields', 0)} 字段; 学院={prof.get('所在学院')} 专业={prof.get('所修专业')} 班级={prof.get('行政班级')}")
            info = client.get_student_info()
            report("get_student_info(含档案页兜底)", bool(info and info.get("major")), f"{info.get('name')}/{info.get('major')}/{info.get('grade')}/{info.get('college', '')}")

            print("\n===== 体育 / 考试 =====")
            sport = client.get_sport_grades()
            report("sport-grade/list", isinstance(sport, list) and len(sport) > 0, f"{sport}")
            exam = client.get_exam_arrange()
            report("exam-arrange 页解析", isinstance(exam, dict) and "exams" in exam, f"{exam.get('count', 0)} 场考试(期末前为空属正常)")

            print("\n===== 个人方案 =====")
            tree = client.get_my_program_tree()
            def tree_count(node):
                n = len(node.get("planCourses") or []) if isinstance(node, dict) else 0
                for ch in (node.get("children") or []) if isinstance(node, dict) else []:
                    n += tree_count(ch)
                return n
            tc = tree_count(tree)
            report("program/root-module-json", isinstance(tree, dict) and tree.get("id"), f"方案树节点 {tc} 门方案课; major={tree.get('major')}")

            print("\n===== 其他 search/JSON 接口 =====")
            extra_checks = [
                ("exchange-out-info/search", "/for-std/exchange-out-info/search"),
                ("oversea-research-topic/search", "/for-std/oversea-research-topic/search"),
                ("oversea-research-defense/search", "/for-std/oversea-research-defense/search"),
                ("program-search/search", "/for-std/program-search/search"),
                ("thesis-topic/search", "/for-std/thesis-topic/search"),
                ("startup-plan-defense/available-batches", "/for-std/startup-plan-defense/available-batches"),
                ("exchange-credit-apply/check-date", "/for-std/exchange-credit-apply/check-date"),
                ("competition-achievement", "/for-std/competition-achievement"),
            ]
            for name, path in extra_checks:
                try:
                    r = client.get_json(path)
                    if isinstance(r, dict) and "error" in r and isinstance(r["error"], str) and "未登录" in str(r["error"]):
                        report(name, False, "未登录")
                    elif isinstance(r, dict) and ("data" in r or "result" in r or "exams" in r):
                        report(name, True, f"键: {list(r.keys())[:6]}")
                    elif isinstance(r, list):
                        report(name, True, f"list[{len(r)}]")
                    elif isinstance(r, dict):
                        report(name, True, f"键: {list(r.keys())[:8]}")
                    else:
                        report(name, False, f"意外返回 {type(r).__name__}")
                except Exception as e:
                    report(name, False, f"ERR {str(e)[:80]}")

            # ── 3. 完整数据包 ──
            print("\n===== 完整数据包 =====")
            from tools.program_tools import get_my_program
            program = get_my_program.invoke({
                "major": rank.get("majorName") or "", "grade": f"{rank.get('grade')}级",
                "personal_tree": tree if isinstance(tree, dict) else None,
            })
            # 课表课程(含开课学院)
            from tools.course_tools import _parse_schedule_group_str
            courses = []
            for lesson in lessons:
                if not isinstance(lesson, dict):
                    continue
                course_obj = lesson.get("course", {})
                teachers = []
                for t in lesson.get("teacherAssignmentList", []):
                    if isinstance(t, dict):
                        n = (t.get("person", {}) or {}).get("nameZh", "")
                        if n:
                            teachers.append(n)
                parsed = _parse_schedule_group_str(lesson.get("scheduleGroupStr", ""))
                courses.append({
                    "course_code": course_obj.get("code", lesson.get("code", "")),
                    "course_name": course_obj.get("nameZh", lesson.get("nameZh", "")),
                    "teacher": ",".join(teachers) or parsed.get("teacher_hint", ""),
                    "credits": lesson.get("credits") or 0,
                    "time": f"{parsed['day_str']} {parsed['weeks']} 第{parsed['periods']}节" if parsed.get("day_str") else lesson.get("scheduleGroupStr", ""),
                    "location": parsed.get("location", ""),
                    "semester": "2026年春季学期",
                    "open_department": (lesson.get("openDepartment") or {}).get("nameZh", ""),
                })
            # 成绩转 fixture 格式
            grades_rows = []
            for sem_item in (grades.get("semesters") or []) if isinstance(grades, dict) else []:
                semester = f"{sem_item.get('schoolYear', '')[:4]}年{'秋' if '秋' in str(sem_item.get('nameZh', '')) else '春'}季学期"
                for sc in sem_item.get("scores", []):
                    if not isinstance(sc, dict):
                        continue
                    raw = str(sc.get("score") or "").strip()
                    import re as _re
                    numeric = _re.fullmatch(r"\d+(\.\d+)?", raw)
                    grades_rows.append({
                        "semester": semester,
                        "course_name": sc.get("courseNameCh", ""),
                        "credits": float(sc.get("credits") or 0),
                        "score": float(raw) if numeric else -1,
                        "score_text": None if numeric else (sc.get("scoreCh") or raw or None),
                        "grade_point": float(sc.get("gp") or 0),
                    })
            acad = grades.get("overview", {}) if isinstance(grades, dict) else {}
            package = {
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "source": "jw.ustc.edu.cn(CDP 登录态, 2026-08-27 用户授权)",
                "user": {
                    "id": STUDENT, "name": rank.get("studentName"),
                    "major": rank.get("majorName"), "grade": f"{rank.get('grade')}级",
                    "department": rank.get("mngtDepartmentName"),
                    "college": prof.get("所在学院"), "class_name": prof.get("行政班级"),
                    "status": prof.get("学籍状态"), "political": prof.get("政治面貌"),
                    "ethnicity": prof.get("民族"), "hometown": prof.get("籍贯(省份)"),
                    "enroll_date": prof.get("入学日期"), "education_years": prof.get("学制"),
                    "rank": {"majorRank": rank.get("majorRank"), "majorStdCount": rank.get("majorStdCount")},
                },
                "academic_overview": {
                    "gpa": acad.get("gpa"), "passed_credits": acad.get("passedCredits"),
                    "weighted_score": acad.get("weightedScore"), "arithmetic_score": acad.get("arithmeticScore"),
                },
                "semesters": [{"id": s.get("id"), "name": s.get("nameZh")} for s in (sem if isinstance(sem, list) else [])],
                "grades": grades_rows,
                "courses": courses,
                "selection": sel_list,
                "sport_grades": sport if isinstance(sport, list) else [],
                "exam_arrange": exam.get("exams", []) if isinstance(exam, dict) else [],
                "student_info_page": prof,
                "program": program,
                "program_tree": tree if isinstance(tree, dict) else {},
            }
            OUT.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"完整数据包已保存: {OUT} ({OUT.stat().st_size} 字节)")
            print(f"  成绩 {len(grades_rows)} | 课表 {len(courses)} | 选课 {len(sel_list)} | 方案 {len(program.get('courses') or [])} 门 | 档案字段 {len(prof)}")

            # ── 4. 同步演示 fixture ──
            old = json.loads(FIX.read_text(encoding="utf-8"))
            fixture = {
                "fixture_version": 4,
                "synthetic": True,
                "generated_for": "xiaowo-demo",
                "note": "真实教务数据(2026-08-27 用户授权爬取 jw.ustc.edu.cn; 含选课/体育/档案扩展)",
                "academic_overview": package["academic_overview"],
                "user": {
                    "id": STUDENT, "name": rank.get("studentName") or old["user"]["name"],
                    "major": rank.get("majorName") or old["user"]["major"],
                    "grade": f"{rank.get('grade')}级" or old["user"]["grade"],
                    "department": rank.get("mngtDepartmentName"),
                    "college": prof.get("所在学院"),
                    "class_name": prof.get("行政班级"),
                    "rank": package["user"]["rank"],
                },
                "grades": grades_rows or old["grades"],
                "courses": courses or old["courses"],
                "semester": "2026年春季学期",
                "program": program or old["program"],
                "selection": sel_list,
                "sport_grades": package["sport_grades"],
                "student_info": {k: v for k, v in package["user"].items() if v is not None},
            }
            FIX.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"演示 fixture 已同步: {FIX}")

        finally:
            reset_student(token)

    ok_count = sum(1 for r in REPORT if r["ok"])
    print(f"\n======== 验证报告: {ok_count}/{len(REPORT)} 通过 ========")
    for r in REPORT:
        if not r["ok"]:
            print(f"  ❌ {r['name']}: {r['detail']}")
    return 0 if ok_count == len(REPORT) else 2


if __name__ == "__main__":
    sys.exit(main())
