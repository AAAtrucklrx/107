"""
小蜗 — 课业助手 Agent 工具
提供课表查询、空教室查找、成绩查询、GPA计算、考试查询、课程搜索

v3.0: 课表和成绩对接 jw 内部 API（需 CAS 登录），catalog API 对接空教室/考试/课程搜索
"""

import json
import sqlite3
from datetime import date as Date
from pathlib import Path

from langchain_core.tools import tool

from services.service_container import ServiceContainer
from utils import course_name as _norm
from utils.course_periods import parse_periods, periods_to_range
from utils.logger import get_logger

log = get_logger("xiaowo.tools.course")

# 评课库课程表路径（与 advisor_tools 的 COURSE_DB 相同）: 完整 2351 门课程。
# 本地种子课程表仅少量样例，课程搜索时合并两份数据避免「数学分析B1」搜不到。
_REVIEW_DB = Path(__file__).resolve().parents[1] / "data" / "course_data.db"

# 测试版离线模式开关（P1-2 治理：替代旧 monkey-patch——app_test 直接替换函数对象）
_OFFLINE = False


def set_offline_mode(on: bool) -> None:
    """测试版用：强制个人数据实时拉取走 SQLite 降级（避免真发请求到 jw 造成超时）。"""
    global _OFFLINE
    _OFFLINE = bool(on)


def _db():
    """获取数据库实例"""
    return ServiceContainer().db


def _catalog():
    """获取 CatalogAPI 实例"""
    return ServiceContainer().catalog_api


def _cas():
    """获取 CASClient 实例（未登录返回 None）"""
    sc = ServiceContainer()
    return sc.cas_client if sc.has_cas() else None


# ── 未登录锁定提示 ──
_LOGIN_MSG = "🔒 此功能需要登录教务系统。请在左侧通过科大统一身份认证登录。"


def _is_locked(student_id: str) -> bool:
    """仅允许当前认证会话读取其自身的个人数据。"""
    cas = _cas()
    return cas is None or not student_id or cas.student_id != student_id


# ── 内部查询函数（非 tool 装饰器，供 tool 复用） ──────

def _norm_course_name(name: str) -> str:
    """规范化课程名称（共享实现 utils/course_name）：去括号/引号/空白并 ASCII 大写，
    例：计算机 (B1) -> 计算机B1"""
    return _norm.norm_course_name(name)


def _query_grades(student_id: str, course_name: str = None, semester: str = None) -> list[dict]:
    """直接查询成绩（避免 @tool 包装带来的递归调用问题）"""
    sql = "SELECT * FROM student_grades WHERE student_id = ?"
    params = [student_id]
    if semester:
        sql += " AND semester = ?"
        params.append(semester)
    if course_name:
        sql += " AND REPLACE(REPLACE(REPLACE(REPLACE(course_name,'(',''),')',''),' ',''),'　','') LIKE ?"
        params.append(f"%{_norm_course_name(course_name)}%")
    rows = _db().query(sql, tuple(params))
    # 展示口径：等级制（score 哨兵 -1 且有原文）用原文
    for r in rows:
        r["score_display"] = r.get("score_text") if r.get("score") == -1 and r.get("score_text") else r.get("score", 0)
    return rows


_DAY_MAP = {"1": "周一", "2": "周二", "3": "周三", "4": "周四", "5": "周五", "6": "周六", "7": "周日"}


def _parse_schedule_groups(s: str) -> list[dict]:
    """
    解析 jw 课表的一个或多个 scheduleGroupStr 分段，如：
    '2~11周 西区电三楼406 :5(6,7,8,9) 徐伟'
    返回 [{weeks, week_numbers, location, day_str, day_num, periods, teacher_hint, raw}, ...]
    """
    import re as _re

    from utils.schedule_parse import parse_course_time

    if not s:
        return []
    parts = [part.strip() for part in _re.split(r"(?:\r?\n|[;；])+", s) if part.strip()]
    results: list[dict] = []
    for part in parts:
        result = {
            "weeks": "",
            "week_numbers": [],
            "location": "",
            "day_str": "",
            "day_num": None,
            "periods": "",
            "teacher_hint": "",
            "raw": part,
        }
        week_slot = next(
            (slot for slot in parse_course_time(part) if slot.get("week_numbers") is not None),
            None,
        )
        if week_slot:
            result["weeks"] = week_slot["weeks_raw"]
            result["week_numbers"] = list(week_slot.get("week_numbers") or [])

        dm = _re.search(r":([1-7])\(([^)]+)\)", part)
        if dm:
            result["day_num"] = int(dm.group(1))
            result["day_str"] = _DAY_MAP.get(dm.group(1), f"周{dm.group(1)}")
            result["periods"] = dm.group(2).strip()

        loc_part = part[:dm.start()] if dm else part
        if result["weeks"]:
            loc_part = loc_part.replace(result["weeks"], "", 1)
        result["location"] = loc_part.strip()

        if dm:
            result["teacher_hint"] = part[dm.end():].strip()
        results.append(result)
    return results


def _parse_schedule_group_str(s: str) -> dict:
    """Compatibility projection for legacy scripts that only consume one meeting."""
    parsed = _parse_schedule_groups(s)
    if parsed:
        return parsed[0]
    return {
        "weeks": "",
        "week_numbers": [],
        "location": "",
        "day_str": "",
        "day_num": None,
        "periods": "",
        "teacher_hint": "",
        "raw": str(s or ""),
    }


def _fetch_real_schedule(cas_client, semester_id: int) -> list[dict] | None:
    """
    从 jw 内部 API 获取个人课表，解析为标准格式。
    返回 None 表示失败。
    """
    if _OFFLINE:
        return None
    data = cas_client.get_course_table(semester_id)
    if not isinstance(data, dict) or "error" in data:
        return None

    lessons = data.get("lessons", [])
    if not lessons:
        return None

    courses = []
    for lesson in lessons:
        if not isinstance(lesson, dict):
            continue
        course_obj = lesson.get("course", {})
        course_code = course_obj.get("code", lesson.get("code", ""))
        course_name = course_obj.get("nameZh", lesson.get("nameZh", ""))
        credits = lesson.get("credits", 0) or course_obj.get("credits", 0)

        # 解析教师
        teachers = lesson.get("teacherAssignmentList", [])
        teacher_names = []
        for t in teachers:
            if isinstance(t, dict):
                person = t.get("person", {})
                name = person.get("nameZh", t.get("nameZh", ""))
                if name:
                    teacher_names.append(name)
        teacher_str = ",".join(teacher_names) if teacher_names else ""

        # 解析时间地点
        schedule_str = str(lesson.get("scheduleGroupStr") or "")
        parsed_groups = _parse_schedule_groups(schedule_str)
        meetings = []
        teacher_hints: list[str] = []
        for parsed in parsed_groups:
            periods_list = parse_periods(parsed["periods"])
            time_range = periods_to_range(periods_list)
            if parsed["teacher_hint"] and parsed["teacher_hint"] not in teacher_hints:
                teacher_hints.append(parsed["teacher_hint"])
            meetings.append({
                "day": parsed["day_str"],
                "weekday": parsed["day_num"],
                "weeks": parsed["weeks"],
                "week_numbers": parsed["week_numbers"],
                "periods": periods_list,
                "period_label": time_range["periods_text"] if time_range else "",
                "start_time": time_range["start"] if time_range else "",
                "end_time": time_range["end"] if time_range else "",
                "location": parsed["location"],
                "raw": parsed["raw"],
            })

        if not teacher_str and teacher_hints:
            teacher_str = ",".join(teacher_hints)

        rendered_times = []
        for meeting in meetings:
            rendered_times.append(" ".join(value for value in (
                str(meeting.get("day") or ""),
                str(meeting.get("weeks") or ""),
                str(meeting.get("period_label") or ""),
                (
                    f"{meeting['start_time']}-{meeting['end_time']}"
                    if meeting.get("start_time") and meeting.get("end_time") else ""
                ),
            ) if value))
        time_str = "; ".join(rendered_times) or schedule_str
        locations = list(dict.fromkeys(
            str(meeting.get("location") or "") for meeting in meetings if meeting.get("location")
        ))
        location = " / ".join(locations)
        first_meeting = meetings[0] if len(meetings) == 1 else {}

        courses.append({
            "course_code": course_code,
            "course_name": course_name,
            "teacher": teacher_str,
            "credits": credits or 0,
            "time": time_str,
            "location": location,
            "day": first_meeting.get("day", ""),
            "weeks": first_meeting.get("weeks", ""),
            "periods": ",".join(str(value) for value in first_meeting.get("periods", [])),
            "start_time": first_meeting.get("start_time", ""),
            "end_time": first_meeting.get("end_time", ""),
            "meetings": meetings,
            "compulsory": lesson.get("compulsory", True),
        })

    return courses


def _sync_courses_to_db(student_id: str, courses: list[dict], semester: str) -> None:
    """将真实课表数据同步到 student_courses 表（单事务先删后插，失败整体回滚）"""
    db = _db()
    with db.transaction() as conn:
        conn.execute("DELETE FROM student_courses WHERE student_id = ? AND semester = ?",
                     (student_id, semester))
        for c in courses:
            conn.execute(
                "INSERT INTO student_courses (student_id, course_code, course_name, teacher, credits, time, location, semester, schedule_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (student_id, c["course_code"], c["course_name"], c.get("teacher", ""),
                 c.get("credits", 0), c.get("time", ""), c.get("location", ""), semester,
                 json.dumps(c.get("meetings") or [], ensure_ascii=False, separators=(",", ":"))),
            )
    log.info(f"已同步 {len(courses)} 门课表到数据库")


def _fetch_real_grades(cas_client) -> list[dict] | None:
    """
    从 jw 内部 API 获取成绩，解析为标准格式。
    返回 None 表示失败。
    """
    if _OFFLINE:
        return None
    # 1. 获取学期列表
    sem_data = cas_client.get_grade_semesters()
    if not isinstance(sem_data, list) or not sem_data:
        return None

    sem_ids = [s["id"] for s in sem_data if isinstance(s, dict) and "id" in s]
    if not sem_ids:
        return None

    # 建立 sem_id → sem_name 映射
    sem_map = {}
    for s in sem_data:
        if isinstance(s, dict):
            sem_map[s.get("id")] = s.get("nameZh", str(s.get("id", "")))

    # 2. 获取全部成绩
    grade_data = cas_client.get_grades(sem_ids)
    if not isinstance(grade_data, dict) or "error" in grade_data:
        return None

    semesters = grade_data.get("semesters", [])
    if not semesters:
        return None

    # 3. 解析成绩
    from utils.gpa_calculator import score_to_grade_point

    grades = []
    for sem in semesters:
        if not isinstance(sem, dict):
            continue
        sem_id = sem.get("id")
        sem_name = sem_map.get(sem_id, str(sem_id))
        scores = sem.get("scores", [])
        for sc in scores:
            if not isinstance(sc, dict):
                continue
            score = sc.get("score")
            if score is None:
                continue
            # score 可能是数字或字符串（百分制数字；等级制如 "优秀"/"通过"）
            try:
                score_int = int(score)
                score_text = None
            except (ValueError, TypeError):
                # 等级制：保留原文（哨兵 -1），绩点以 jw 返回的 gp 为准；
                # 无 gp 时不做编造映射，跳过并告警
                score_int = -1
                score_text = str(score)
                if sc.get("gp") is None:
                    log.warning(f"等级制成绩无绩点字段，跳过: {sc.get('courseNameCh', '')} = {score_text}")
                    continue

            credits = sc.get("credits", 0) or 0
            gp = sc.get("gp")
            if gp is None:
                gp = score_to_grade_point(score_int)
            else:
                try:
                    gp = float(gp)
                except (ValueError, TypeError):
                    gp = score_to_grade_point(score_int) if score_int >= 0 else 0.0

            grades.append({
                "semester": sem_name,
                "course_name": sc.get("courseNameCh", ""),
                "course_code": sc.get("courseCode", ""),
                "credits": credits,
                "score": score_int,
                "score_text": score_text,
                "score_display": score_text if score_int == -1 else score_int,
                "grade_point": gp,
                "passed": sc.get("passed", score_int >= 60),
            })

    return grades


def _sync_grades_to_db(student_id: str, grades: list[dict]) -> None:
    """将真实成绩数据同步到 student_grades 表（单事务先删后插，失败整体回滚）"""
    db = _db()
    with db.transaction() as conn:
        conn.execute("DELETE FROM student_grades WHERE student_id = ?", (student_id,))
        for g in grades:
            conn.execute(
                "INSERT INTO student_grades (student_id, semester, course_name, credits, score, score_text, grade_point) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (student_id, g["semester"], g["course_name"],
                 g.get("credits", 0), g.get("score", 0), g.get("score_text"), g.get("grade_point", 0)),
            )
    log.info(f"已同步 {len(grades)} 条成绩到数据库")


def _get_current_semester_id() -> int | None:
    """从 CatalogAPI 获取当前学期 ID"""
    try:
        api = _catalog()
        current = api.get_current_semester()
        if current and isinstance(current, dict) and "error" not in current:
            return current.get("id")
    except Exception:
        pass
    return None


def _time_str_to_minutes(t: str) -> int:
    """将 '8:40' 或 '14:00' 转换为分钟数 520 或 840"""
    try:
        parts = t.strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except (ValueError, IndexError):
        return 0


def _int_time_to_str(t: int) -> str:
    """将整数时间（如 1930、840）转换为 '19:30' 或 '08:40'"""
    h, m = divmod(t, 100)
    return f"{h:02d}:{m:02d}"


# ── Tool 定义 ──────────────────────────────────────────

@tool
def query_schedule(student_id: str = None, week: int = None, day: str = None) -> dict:
    """
    查询指定学生的课程表。
    优先从 jw 内部 API 获取真实课表，失败时回退到 SQLite。

    Args:
        student_id: 学号（登录用户；未登录时此查询锁定）
        week: 已废弃——数据源（本地缓存/全学期课表）无周次维度，传参会被忽略；
            单日查询请用 query_daily_schedule
        day: 星期几（可选），如 "周一"、"周二"

    Returns:
        {"student_id": "...", "courses": [...], "count": N,
         "source": "real"|"fallback"}
    """
    sid = student_id

    # 尝试真实 jw API
    cas = _cas()
    if cas:
        try:
            sem_id = _get_current_semester_id()
            if sem_id:
                real_courses = _fetch_real_schedule(cas, sem_id)
                if real_courses:
                    # 获取当前学期名称
                    sem_name = ""
                    try:
                        current = _catalog().get_current_semester()
                        if current:
                            sem_name = current.get("nameZh", "")
                    except Exception:
                        pass

                    # 同步到数据库
                    _sync_courses_to_db(sid, real_courses, sem_name)

                    # 按 day 过滤
                    courses = real_courses
                    if day:
                        courses = [c for c in courses if day in c.get("time", "")]

                    return {"student_id": sid, "courses": courses, "count": len(courses),
                            "source": "real", "semester": sem_name}
        except Exception as e:
            log.warning(f"课表 API 失败 (student_id={sid}, day={day})，降级到本地数据: {e}")

    # ── Fallback: SQLite 本地缓存或 锁定提示 ──
    if _is_locked(sid):
        return {"student_id": sid, "courses": [], "count": 0,
                "source": "locked", "message": _LOGIN_MSG}

    sql = "SELECT * FROM student_courses WHERE student_id = ?"
    params = [sid]

    if day:
        sql += " AND time LIKE ?"
        params.append(f"%{day}%")

    courses = _db().query(sql, tuple(params))
    for c in courses:
        c["credits"] = c.get("credits") or 0
        try:
            c["meetings"] = json.loads(c.get("schedule_json") or "[]")
        except (TypeError, ValueError):
            c["meetings"] = []

    return {"student_id": sid, "courses": courses, "count": len(courses),
            "source": "fallback", "message": "⚠️ 教务接口暂时不可用，以下为本地缓存课表，仅供参考"}


@tool
def query_daily_schedule(date: str = None, student_id: str = None) -> dict:
    """
    查询指定学生某一天的课程安排，精确到分钟。
    支持 "今天"、"明天"、"下周三"、"2026-09-14" 等日期描述。

    Args:
        date: 日期描述（默认今天）
        student_id: 学号（默认当前登录学生）

    Returns:
         {"student_id": "...", "date": "2026-09-14", "weekday": "周一",
          "teaching_week": 3,
          "courses": [{"course_name": "...", "teacher": "...", "location": "...",
                      "start_time": "08:00", "end_time": "09:35",
                      "periods": "1-2节", "weeks": "2~11周"}, ...],
         "count": N, "source": "real"|"fallback"|"locked"}
    """
    from config import SEMESTER
    from utils.schedule_parse import (
        normalize_time_str,
        parse_course_time,
        slot_clock_range,
        slot_is_active_in_week,
        teaching_week,
    )
    from utils.time_parser import parse_natural_time
    sid = student_id

    if date:
        parsed = parse_natural_time(date)
    else:
        parsed = {"date": Date.today(), "day_of_week": ""}
    target_date = parsed["date"]
    weekday = _DAY_MAP.get(str(target_date.isoweekday()), "")

    if _is_locked(sid):
        return {
            "student_id": sid,
            "date": target_date.isoformat(),
            "weekday": weekday,
            "teaching_week": None,
            "courses": [],
            "count": 0,
            "source": "locked",
            "message": _LOGIN_MSG,
        }

    try:
        semester_start = Date.fromisoformat(str(SEMESTER.get("start_date", "")))
        total_weeks = int(SEMESTER.get("total_weeks", 18))
    except (TypeError, ValueError):
        return {
            "student_id": sid,
            "date": target_date.isoformat(),
            "weekday": weekday,
            "teaching_week": None,
            "courses": [],
            "count": 0,
            "source": "config_error",
            "message": "学期配置无效，无法判断目标日期对应的教学周",
        }
    current_week = teaching_week(target_date, semester_start, total_weeks)
    if current_week is None:
        return {
            "student_id": sid,
            "date": target_date.isoformat(),
            "weekday": weekday,
            "teaching_week": None,
            "courses": [],
            "count": 0,
            "source": "calendar",
            "message": "目标日期不在当前配置学期范围内",
        }

    # 优先真实 jw API（登录后）
    cas = _cas()
    if cas:
        try:
            sem_id = _get_current_semester_id()
            if sem_id:
                real_courses = _fetch_real_schedule(cas, sem_id)
                if real_courses:
                    day_courses = []
                    for course in real_courses:
                        slots = parse_course_time(
                            normalize_time_str(course.get("time") or "")
                        )
                        if any(
                            slot.get("day") == weekday
                            and slot_is_active_in_week(slot, current_week)
                            for slot in slots
                        ):
                            day_courses.append(course)
                    return {"student_id": sid, "date": target_date.isoformat(), "weekday": weekday,
                            "teaching_week": current_week, "courses": day_courses,
                            "count": len(day_courses), "source": "real"}
        except Exception as e:
            log.warning(f"课表 API 失败 (student_id={sid}, date={date or target_date.isoformat()})，降级到本地数据: {e}")

    # Fallback: SQLite 本地缓存
    rows = _db().query(
        "SELECT * FROM student_courses WHERE student_id = ?", (sid,))
    courses = []
    for r in rows:
        time_str = r.get("time", "") or ""
        # 统一时间解析器（兼容 jw/备份/时钟变体），按目标星期取该课的排课时段
        slots = parse_course_time(normalize_time_str(time_str))
        for slot in slots:
            if slot.get("day") != weekday or not slot_is_active_in_week(slot, current_week):
                continue
            clock = slot_clock_range(slot)
            periods = slot.get("periods") or []
            courses.append({
                "course_code": r.get("course_code", ""),
                "course_name": r.get("course_name", ""),
                "teacher": r.get("teacher", ""),
                "location": r.get("location", ""),
                "time": time_str,
                "start_time": f"{clock[0] // 60:02d}:{clock[0] % 60:02d}" if clock else "",
                "end_time": f"{clock[1] // 60:02d}:{clock[1] % 60:02d}" if clock else "",
                "periods": f"第{','.join(str(p) for p in periods)}节" if periods else "",
                "weeks": slot.get("weeks_raw", ""),
            })
    return {"student_id": sid, "date": target_date.isoformat(), "weekday": weekday,
            "teaching_week": current_week, "courses": courses,
            "count": len(courses), "source": "fallback",
            "message": "⚠️ 教务接口暂时不可用，以下为本地缓存课表，仅供参考"}


@tool
def find_empty_room(building: str, time_desc: str) -> dict:
    """
    查找指定教学楼的空教室。
    通过真实教室占用 API 判断：获取该楼所有教室，减去被占用的，得到空闲教室。

    Args:
        building: 教学楼名称，如 "三教"、"五教"、"高新A"
        time_desc: 自然语言时间段，如 "今天下午"、"周二上午"

    Returns:
        {"building": "...", "time": "...", "empty_rooms": [...], "count": N,
         "source": "real"|"fallback"}
    """
    from utils.time_parser import parse_natural_time
    from tools.api_client import resolve_building, building_short_name

    parsed = parse_natural_time(time_desc)
    period = parsed.get("period", "全天")
    period_start = parsed.get("period_start", "08:00")
    period_end = parsed.get("period_end", "18:00")
    target_date = parsed.get("date", Date.today())

    building_code = resolve_building(building)
    building_display = building  # 默认使用用户输入

    # 尝试真实 API
    try:
        api = _catalog()
        date_str = target_date.isoformat() if isinstance(target_date, Date) else str(target_date)
        timetable_data = api.get_timetable(date_str)

        if isinstance(timetable_data, dict) and "error" not in timetable_data:
            timetable = timetable_data.get("timetable", {})
            lessons = timetable.get("lessons", [])
            tmp_lessons = timetable.get("tmpLessons", [])
            room_occupies = timetable.get("roomOccupies", [])

            # 合并所有占用数据
            all_occupied = lessons + tmp_lessons + room_occupies

            if building_code is not None:
                code_str = str(building_code)
                building_display = building_short_name(building_code)

                # 收集该教学楼所有教室
                building_rooms: dict[str, dict] = {}  # classroomName → 占用信息
                occupied_in_period: set[str] = set()

                for item in all_occupied:
                    bc = item.get("buildingCode")
                    if str(bc) != code_str:
                        continue
                    room = item.get("classroomName", "")
                    if not room:
                        continue

                    building_rooms[room] = building_rooms.get(room, {})
                    # 检查时间冲突
                    item_start = _time_str_to_minutes(str(item.get("start", "0:0")))
                    item_end = _time_str_to_minutes(str(item.get("end", "23:59")))
                    req_start = _time_str_to_minutes(period_start)
                    req_end = _time_str_to_minutes(period_end)

                    # 有重叠 → 该时段被占用
                    if item_start < req_end and item_end > req_start:
                        occupied_in_period.add(room)

                # 空闲 = 全部 - 占用
                empty_rooms = []
                for room in sorted(building_rooms.keys()):
                    if room not in occupied_in_period:
                        empty_rooms.append({
                            "room": f"{building_display}{room}",
                            "free_slots": f"{period_start}-{period_end}",
                        })

                return {
                    "building": building_display,
                    "date": date_str,
                    "time": f"{period}({period_start}-{period_end})",
                    "empty_rooms": empty_rooms[:10],
                    "count": min(len(empty_rooms), 10),
                    "total_rooms": len(building_rooms),
                    "occupied_rooms": len(occupied_in_period),
                    "source": "real",
                }
    except Exception as e:
        log.warning(f"空教室 API 失败 (building={building}, time={time_desc})，降级到模拟数据: {e}")

    # ── Fallback: 如实提示无数据（不再生成模拟教室，避免误导）──
    return {
        "building": building,
        "time": f"{period}({period_start}-{period_end})",
        "empty_rooms": [],
        "count": 0,
        "source": "fallback",
        "message": "⚠️ 教务接口暂时不可用，暂无空教室数据，请稍后重试",
    }


@tool
def query_grade(student_id: str = None, course_name: str = None, semester: str = None) -> dict:
    """
    查询学生成绩。
    优先从 jw 内部 API 获取真实成绩，失败时回退到 SQLite。

    Args:
        student_id: 学号（登录用户；未登录时此查询锁定）
        course_name: 课程名（可选），支持模糊匹配
        semester: 学期（可选），如 "2025-2026-1"

    Returns:
        {"student_id": "...", "grades": [...], "count": N,
         "source": "real"|"fallback"}
    """
    sid = student_id

    # 尝试真实 jw API
    cas = _cas()
    if cas:
        try:
            real_grades = _fetch_real_grades(cas)
            if real_grades:
                _sync_grades_to_db(sid, real_grades)
                # 过滤
                grades = real_grades
                if semester:
                    grades = [g for g in grades if semester in g.get("semester", "")]
                if course_name:
                    grades = [g for g in grades if course_name in g.get("course_name", "")]
                return {"student_id": sid, "grades": grades, "count": len(grades),
                        "source": "real"}
        except Exception as e:
            log.warning(f"成绩 API 失败 (student_id={sid}, course_name={course_name}, semester={semester})，降级到本地数据: {e}")

    # ── Fallback: SQLite 本地缓存或 锁定提示 ──
    if _is_locked(sid):
        return {"student_id": sid, "grades": [], "count": 0,
                "source": "locked", "message": _LOGIN_MSG}

    grades = _query_grades(sid, course_name, semester)
    return {"student_id": sid, "grades": grades, "count": len(grades),
            "source": "fallback", "message": "⚠️ 教务接口暂时不可用，以下为本地缓存成绩，仅供参考"}


@tool
def calc_gpa(student_id: str = None, semester: str = None) -> dict:
    """
    计算GPA（科大4.3制）。
    GPA计算公式：sum(绩点 × 学分) / sum(学分)
    优先从 jw API 获取真实成绩计算，失败时回退到 SQLite。

    Args:
        student_id: 学号（登录用户；未登录时此查询锁定）
        semester: 学期（可选），不指定则计算所有学期累计GPA

    Returns:
        {"student_id": "...", "semester": "...", "gpa": 3.53,
         "total_credits": 13, "details": [...], "source": "real"|"fallback"}
    """
    from utils.gpa_calculator import calculate_gpa

    sid = student_id

    # 尝试真实 jw API
    cas = _cas()
    if cas:
        try:
            real_grades = _fetch_real_grades(cas)
            if real_grades:
                _sync_grades_to_db(sid, real_grades)
                grades = real_grades
                if semester:
                    grades = [g for g in grades if semester in g.get("semester", "")]
                result = calculate_gpa(grades)
                return {
                    "student_id": sid,
                    "semester": semester or "全部学期",
                    "gpa": result["gpa"],
                    "total_credits": result["total_credits"],
                    "details": grades,
                    "source": "real",
                }
        except Exception as e:
            log.warning(f"GPA 计算 API 失败 (student_id={sid}, semester={semester})，降级到本地数据: {e}")

    # ── Fallback: SQLite 本地缓存或 锁定提示 ──
    if _is_locked(sid):
        return {"student_id": sid, "semester": semester or "全部学期",
                "gpa": 0, "total_credits": 0, "details": [],
                "source": "locked", "message": _LOGIN_MSG}

    grades = _query_grades(sid, semester=semester)
    result = calculate_gpa(grades)

    return {
        "student_id": sid,
        "semester": semester or "全部学期",
        "gpa": result["gpa"],
        "total_credits": result["total_credits"],
        "details": grades,
        "source": "fallback",
        "message": "⚠️ 教务接口暂时不可用，以下为本地缓存成绩计算，仅供参考",
    }


@tool
def query_exam(student_id: str = None, course_name: str = None) -> dict:
    """
    查询考试安排。
    优先从真实 API 获取（专业课 + 通修课合并），失败时回退到模拟数据。

    Args:
        student_id: 学号（登录用户；未登录时此查询锁定）
        course_name: 课程名（可选），不指定则返回所有考试

    Returns:
        {"exams": [{"course": "...", "date": "...", "time": "...",
                    "location": "...", "type": "期末考试"}, ...],
         "source": "real"|"fallback"}
    """
    sid = student_id

    if _is_locked(sid):
        return {"student_id": sid, "exams": [], "count": 0,
                "source": "locked", "message": _LOGIN_MSG}

    # 尝试真实 API
    try:
        api = _catalog()
        # 获取当前学期
        current_sem = api.get_current_semester()
        if current_sem and "error" not in current_sem:
            sem_id = current_sem.get("id")
            if sem_id:
                # 同时获取专业课 + 通修课考试
                prof_exams = api.get_exams(sem_id)
                gen_exams = api.get_general_exams(sem_id)

                exams = []

                # 解析专业课考试
                if isinstance(prof_exams, list):
                    for e in prof_exams:
                        if not isinstance(e, dict) or "error" in e:
                            continue
                        lesson = e.get("lesson", {})
                        course = lesson.get("course", {})
                        course_name_cn = course.get("cn", lesson.get("code", "未知"))
                        teachers = lesson.get("teacherAssignmentList", [])
                        teacher_names = [t.get("cn", "") for t in teachers if isinstance(t, dict)]

                        rooms = e.get("examRooms", [])
                        location = rooms[0].get("room", "待定") if rooms else "待定"

                        start_time = _int_time_to_str(e.get("startTime", 0))
                        end_time = _int_time_to_str(e.get("endTime", 0))

                        exam_batch = e.get("examBatch", {})
                        exam_type = exam_batch.get("name", "期末考试") if exam_batch else "期末考试"

                        exams.append({
                            "course": course_name_cn,
                            "course_code": course.get("code", ""),
                            "date": e.get("examDate", ""),
                            "time": f"{start_time}-{end_time}",
                            "location": location,
                            "type": exam_type,
                            "mode": e.get("examMode", ""),
                            "teachers": teacher_names,
                            "source": "专业课",
                        })

                # 解析通修课考试
                if isinstance(gen_exams, list):
                    for e in gen_exams:
                        if not isinstance(e, dict) or "error" in e:
                            continue
                        # 通修课 examDate 可能是 ISO 格式 "2026-09-02T00:00:00+08:00"
                        raw_date = e.get("examDate", "")
                        exam_date = raw_date[:10] if "T" in raw_date else raw_date

                        start_time = _int_time_to_str(e.get("startTime", 0))
                        end_time = _int_time_to_str(e.get("endTime", 0))

                        exams.append({
                            "course": e.get("courseName", "未知"),
                            "course_code": e.get("courseCode", ""),
                            "date": exam_date,
                            "time": f"{start_time}-{end_time}",
                            "location": e.get("room", "待定"),
                            "type": e.get("batch", "通修考试"),
                            "dept": e.get("dept", ""),
                            "source": "通修课",
                        })

                if exams:
                    # Catalog 端点返回全校考试。只保留当前学生课表中的课程，
                    # 无法确认个人课表时宁可返回空，也不能把其他课程误作个人考试。
                    cas = _cas()
                    selected = _fetch_real_schedule(cas, sem_id) if cas else None
                    if selected is None:
                        rows = _db().query(
                            "SELECT course_code, course_name FROM student_courses "
                            "WHERE student_id = ?",
                            (sid,),
                        )
                        selected = rows
                    selected_codes = {
                        str(c.get("course_code") or "").strip().upper()
                        for c in selected if c.get("course_code")
                    }
                    selected_names = {
                        _norm_course_name(c.get("course_name") or "")
                        for c in selected if c.get("course_name")
                    }
                    if not selected_codes and not selected_names:
                        return {
                            "student_id": sid, "exams": [], "count": 0,
                            "source": "fallback",
                            "message": "无法确认当前学生的选课范围，未返回全校考试数据",
                        }
                    exams = [
                        e for e in exams
                        if str(e.get("course_code") or "").strip().upper() in selected_codes
                        or _norm_course_name(e.get("course") or "") in selected_names
                    ]
                    # 按课程名过滤
                    if course_name:
                        exams = [e for e in exams if course_name in e.get("course", "")]
                    # 按日期排序
                    exams.sort(key=lambda x: x.get("date", ""))
                    return {"student_id": sid, "exams": exams, "count": len(exams), "source": "real",
                            "semester": current_sem.get("nameZh", "")}
    except Exception as e:
        log.warning(f"考试 API 失败 (student_id={sid}, course_name={course_name})，如实提示无数据: {e}")

    # ── Fallback: 如实提示无数据（不再生成模拟考试日期，避免误导）──
    return {"exams": [], "source": "fallback",
            "message": "⚠️ 教务接口暂时不可用，暂无考试安排数据，请稍后重试"}


@tool
def search_courses(keyword: str, limit: int = 10) -> dict:
    """
    在全校课程库中按关键词搜索课程。
    支持课程名称、课程代码搜索。

    Args:
        keyword: 搜索关键词，如 "机器学习"、"英语"、"CS"
        limit: 返回数量上限，默认 10

    Returns:
        {"keyword": "...", "courses": [...], "count": N, "source": "real"|"fallback"}
    """
    # 尝试真实 API
    try:
        api = _catalog()
        results = api.search_courses(keyword)
        if isinstance(results, list) and results and "error" not in results[0]:
            courses = []
            for item in results[:limit]:
                if not isinstance(item, dict):
                    continue
                name = item.get("name", {})
                courses.append({
                    "course_code": item.get("id", ""),
                    "course_name": name.get("cn", "") if isinstance(name, dict) else str(name),
                    "course_name_en": name.get("en", "") if isinstance(name, dict) else "",
                    "dept": item.get("dept", ""),
                })
            if courses:
                return {"keyword": keyword, "courses": courses, "count": len(courses),
                        "source": "real"}
    except Exception as e:
        log.warning(f"课程搜索 API 失败 (keyword={keyword}): {e}")

    # Fallback: 本地数据库
    rows = _db().query(
        "SELECT code, name, teacher, credits FROM courses "
        "WHERE REPLACE(REPLACE(REPLACE(REPLACE(name,'(',''),')',''),' ',''),'　','') LIKE ? OR code LIKE ? LIMIT ?",
        (f"%{_norm_course_name(keyword)}%", f"%{keyword}%", limit),
    )
    courses = [{"course_code": r.get("code", ""), "course_name": r.get("name", ""),
                "teacher": r.get("teacher", ""), "credits": r.get("credits", 0)}
               for r in rows]
    # 本地种子课程表仅少量样例（如只有数学分析(B2)）: 再合并评课库完整课程表,
    # 保证「数学分析B1」这类课程名也能被检索到（按归一化课程名去重）
    seen = {_norm_course_name(c["course_name"]) for c in courses}
    try:
        with sqlite3.connect(str(_REVIEW_DB), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            extra = conn.execute(
                "SELECT code, name, credit FROM courses WHERE "
                "REPLACE(REPLACE(REPLACE(REPLACE(name,'(',''),')',''),' ',''),'　','') LIKE ? "
                "LIMIT ?",
                (f"%{_norm_course_name(keyword)}%", limit),
            ).fetchall()
        for r in extra:
            key = _norm_course_name(r["name"])
            if key in seen:
                continue
            seen.add(key)
            courses.append({"course_code": r["code"] or "", "course_name": r["name"],
                            "teacher": "", "credits": r["credit"] or 0})
    except Exception as e:
        log.warning(f"评课库课程检索合并失败 (keyword={keyword}): {e}")
    courses = courses[:limit]
    return {"keyword": keyword, "courses": courses, "count": len(courses),
            "source": "fallback", "message": "⚠️ 教务接口暂时不可用，以下为本地课程数据，仅供参考"}


@tool
def get_semester_list() -> dict:
    """
    获取可用的学期列表。
    标注当前学期（is_current=True）。

    Returns:
        {"semesters": [...], "current_semester": "...", "source": "real"|"fallback"}
    """
    try:
        api = _catalog()
        semesters = api.get_semesters()
        if isinstance(semesters, list) and semesters and "error" not in semesters[0]:
            today = Date.today().isoformat()
            result = []
            current = None
            for s in semesters[-10:]:  # 最近 10 个学期
                if not isinstance(s, dict):
                    continue
                is_current = s.get("isLast", False)
                # 手动判断：start <= today <= end
                if s.get("start", "") <= today <= s.get("end", ""):
                    is_current = True
                entry = {
                    "id": s.get("id"),
                    "code": s.get("code", ""),
                    "name": s.get("nameZh", ""),
                    "start": s.get("start", ""),
                    "end": s.get("end", ""),
                    "is_current": is_current,
                }
                result.append(entry)
                if is_current:
                    current = entry["name"]

            return {"semesters": result, "current_semester": current or "",
                    "source": "real"}
    except Exception as e:
        log.warning(f"学期列表 API 失败: {e}")

    return {
        "semesters": [
            {"name": "2025-2026学年第二学期", "is_current": True},
            {"name": "2025-2026学年第一学期", "is_current": False},
        ],
        "current_semester": "2025-2026学年第二学期",
        "source": "fallback",
        "message": "⚠️ 教务接口暂时不可用，以下为本地学期数据，仅供参考",
    }


@tool
def query_course_selection(student_id: str = None, semester: str = None) -> dict:
    """
    查询选课结果。
    优先从 jw 内部 API 获取，失败时回退到本地课表数据。

    Args:
        student_id: 学号（登录用户；未登录时此查询锁定）
        semester: 学期（可选），不指定则查询当前学期

    Returns:
        {"student_id": "...", "selections": [...], "count": N,
         "source": "real"|"fallback"}
    """
    sid = student_id

    # 尝试真实 jw API
    cas = _cas()
    if cas:
        try:
            sem_id = _get_current_semester_id()
            if sem_id:
                data = cas.get_course_selection(sem_id)
                if isinstance(data, (dict, list)) and not (isinstance(data, dict) and "error" in data):
                    # 解析选课结果
                    selections = []
                    items = data if isinstance(data, list) else data.get("datas", data.get("results", []))
                    if isinstance(items, list):
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            course = item.get("course", item)
                            selections.append({
                                "course_code": course.get("code", item.get("courseCode", "")),
                                "course_name": course.get("nameZh", item.get("courseNameCh", "")),
                                "teacher": item.get("teacherName", ""),
                                "credits": course.get("credits", item.get("credits", 0)),
                                "status": item.get("status", item.get("selectStatus", "已选")),
                            })
                    if selections:
                        return {"student_id": sid, "selections": selections,
                                "count": len(selections), "source": "real"}
        except Exception as e:
            log.warning(f"选课结果 API 失败 (student_id={sid}, semester={semester})，降级到本地数据: {e}")

    # ── Fallback: 本地数据或 锁定提示 ──
    if _is_locked(sid):
        return {"student_id": sid, "selections": [], "count": 0,
                "source": "locked", "message": _LOGIN_MSG}

    courses = _db().query(
        "SELECT * FROM student_courses WHERE student_id = ?",
        (sid,),
    )
    selections = [{
        "course_code": c.get("course_code", ""),
        "course_name": c.get("course_name", ""),
        "teacher": c.get("teacher", ""),
        "credits": c.get("credits", 0),
        "time": c.get("time", ""),          # 组 B 修复：上课时间（冲突判断关键字段）
        "location": c.get("location", ""),
        "semester": c.get("semester", ""),
        "status": "已选",
    } for c in courses]
    return {"student_id": sid, "selections": selections, "count": len(selections),
            "source": "fallback", "message": "⚠️ 教务接口暂时不可用，以下为本地缓存选课数据，仅供参考"}


@tool
def query_program(student_id: str = None, module_id: int = None) -> dict:
    """
    查询培养方案。
    优先从 jw 内部 API 获取，失败时返回提示信息。

    Args:
        student_id: 学号（登录用户；未登录时此查询锁定）
        module_id: 模块ID（可选），不指定则获取根模块

    Returns:
        {"student_id": "...", "modules": [...], "source": "real"|"fallback"}
    """
    sid = student_id

    # 尝试真实 jw API
    cas = _cas()
    if cas:
        try:
            data = cas.get_program_modules(module_id)
            if isinstance(data, (dict, list)) and not (isinstance(data, dict) and "error" in data):
                modules = []
                items = data if isinstance(data, list) else data.get("subModules", data.get("modules", [data]))
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        modules.append({
                            "name": item.get("nameZh", item.get("name", "")),
                            "code": item.get("code", ""),
                            "credits_required": item.get("credits", item.get("requiredCredits", 0)),
                            "type": item.get("type", item.get("moduleType", "")),
                            "courses": item.get("courses", []),
                        })
                if modules:
                    return {"student_id": sid, "modules": modules,
                            "count": len(modules), "source": "real"}
        except Exception as e:
            log.warning(f"培养方案 API 失败 (student_id={sid}, module_id={module_id}): {e}")

    # ── Fallback: 锁定提示 ──
    if _is_locked(sid):
        return {"student_id": sid, "modules": [], "count": 0,
                "source": "locked", "message": _LOGIN_MSG}

    return {
        "student_id": sid,
        "modules": [
            {"name": "公共基础课", "type": "必修", "credits_required": 40},
            {"name": "专业基础课", "type": "必修", "credits_required": 30},
            {"name": "专业方向课", "type": "选修", "credits_required": 20},
            {"name": "通识选修课", "type": "选修", "credits_required": 10},
        ],
        "count": 4,
        "source": "fallback",
        "message": "⚠️ 教务接口暂时不可用，以下为本地培养方案数据，仅供参考",
    }
