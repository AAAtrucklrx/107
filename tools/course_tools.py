"""
小蜗 — 课业助手 Agent 工具
提供课表查询、空教室查找、成绩查询、GPA计算、考试查询、课程搜索

v3.0: 课表和成绩对接 jw 内部 API（需 CAS 登录），catalog API 对接空教室/考试/课程搜索
"""

from langchain_core.tools import tool
from datetime import date, timedelta

from config import DEMO_STUDENT
from services.service_container import ServiceContainer
from utils.logger import get_logger
from utils.course_periods import parse_periods, periods_to_range
import re as _re

log = get_logger("xiaowo.tools.course")


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
_LOGIN_MSG = "🔒 此功能需要登录教务系统。请在左侧侧边栏输入学号和密码登录。"


def _is_locked(student_id: str) -> bool:
    """检查是否应该锁定个人数据查询（未登录 + 非演示学生）"""
    return _cas() is None and student_id != DEMO_STUDENT["id"]


# ── 内部查询函数（非 tool 装饰器，供 tool 复用） ──────

def _query_grades(student_id: str, course_name: str = None, semester: str = None) -> list[dict]:
    """直接查询成绩（避免 @tool 包装带来的递归调用问题）"""
    sql = "SELECT * FROM student_grades WHERE student_id = ?"
    params = [student_id]
    if semester:
        sql += " AND semester = ?"
        params.append(semester)
    if course_name:
        sql += " AND course_name LIKE ?"
        params.append(f"%{course_name}%")
    return _db().query(sql, tuple(params))


_DAY_MAP = {"1": "周一", "2": "周二", "3": "周三", "4": "周四", "5": "周五", "6": "周六", "7": "周日"}


def _parse_schedule_group_str(s: str) -> dict:
    """
    解析 jw 课表的 scheduleGroupStr，如：
    '2~11周 西区电三楼406 :5(6,7,8,9) 徐伟'
    返回 {weeks, location, day_str, periods, teacher_hint}
    """
    import re as _re
    result = {"weeks": "", "location": "", "day_str": "", "periods": "", "teacher_hint": ""}
    if not s:
        return result

    # 提取周次范围
    wm = _re.search(r'(\d+~\d+)周', s)
    if wm:
        result["weeks"] = wm.group(1) + "周"

    # 提取 :N(Periods) 部分
    dm = _re.search(r':(\d)\(([^)]+)\)', s)
    if dm:
        day_num = dm.group(1)
        result["day_str"] = _DAY_MAP.get(day_num, f"周{day_num}")
        result["periods"] = dm.group(2)

    # 提取教室位置（在 :N 之前，周次之后）
    loc_part = s
    if dm:
        loc_part = s[:dm.start()]
    if wm:
        loc_part = loc_part[wm.end():]
    loc_part = loc_part.strip()
    if loc_part:
        result["location"] = loc_part

    # 提取教师（在括号后面）
    if dm:
        after = s[dm.end():].strip()
        if after:
            result["teacher_hint"] = after

    return result


def _fetch_real_schedule(cas_client, semester_id: int) -> list[dict] | None:
    """
    从 jw 内部 API 获取个人课表，解析为标准格式。
    返回 None 表示失败。
    """
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
        schedule_str = lesson.get("scheduleGroupStr", "")
        parsed = _parse_schedule_group_str(schedule_str)

        # 如果 teacher 为空，用 scheduleGroupStr 中提取的
        if not teacher_str and parsed["teacher_hint"]:
            teacher_str = parsed["teacher_hint"]

        time_str = f"{parsed['day_str']} {parsed['weeks']} 第{parsed['periods']}节" if parsed["day_str"] else schedule_str
        location = parsed["location"]

        # 节次编号 → 精确到分钟的起止时间（官方节次时间表）
        periods_list = parse_periods(parsed["periods"])
        time_range = periods_to_range(periods_list)
        if time_range:
            time_str = f"{time_str} {time_range['start']}-{time_range['end']}"
            start_time, end_time = time_range["start"], time_range["end"]
        else:
            start_time, end_time = "", ""

        courses.append({
            "course_code": course_code,
            "course_name": course_name,
            "teacher": teacher_str,
            "credits": credits or 0,
            "time": time_str,
            "location": location,
            "day": parsed["day_str"],
            "weeks": parsed["weeks"],
            "periods": parsed["periods"],
            "start_time": start_time,
            "end_time": end_time,
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
                "INSERT INTO student_courses (student_id, course_code, course_name, teacher, credits, time, location, semester) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (student_id, c["course_code"], c["course_name"], c.get("teacher", ""),
                 c.get("credits", 0), c.get("time", ""), c.get("location", ""), semester),
            )
    log.info(f"已同步 {len(courses)} 门课表到数据库")


def _fetch_real_grades(cas_client) -> list[dict] | None:
    """
    从 jw 内部 API 获取成绩，解析为标准格式。
    返回 None 表示失败。
    """
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
            # score 可能是数字或字符串
            try:
                score_int = int(score)
            except (ValueError, TypeError):
                continue

            credits = sc.get("credits", 0) or 0
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
                "credits": credits,
                "score": score_int,
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
                "INSERT INTO student_grades (student_id, semester, course_name, credits, score, grade_point) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (student_id, g["semester"], g["course_name"],
                 g.get("credits", 0), g.get("score", 0), g.get("grade_point", 0)),
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
        student_id: 学号，如 "PB20240001"（默认使用演示学生）
        week: 周次（可选），不指定则返回所有周的课表
        day: 星期几（可选），如 "周一"、"周二"

    Returns:
        {"student_id": "...", "courses": [...], "count": N,
         "source": "real"|"fallback"}
    """
    sid = student_id or DEMO_STUDENT["id"]

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
            log.warning(f"课表 API 失败 (student_id={sid}, week={week}, day={day})，降级到本地数据: {e}")

    # ── Fallback: SQLite（演示学生）或 锁定提示 ──
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
         "courses": [{"course_name": "...", "teacher": "...", "location": "...",
                      "start_time": "08:00", "end_time": "09:35",
                      "periods": "1-2节", "weeks": "2~11周"}, ...],
         "count": N, "source": "real"|"fallback"|"locked"}
    """
    from utils.time_parser import parse_natural_time
    sid = student_id or DEMO_STUDENT["id"]

    if date:
        parsed = parse_natural_time(date)
    else:
        parsed = {"date": date.today(), "day_of_week": ""}
    target_date = parsed["date"]
    weekday = _DAY_MAP.get(str(target_date.isoweekday()), "")

    # 优先真实 jw API（登录后）
    cas = _cas()
    if cas:
        try:
            sem_id = _get_current_semester_id()
            if sem_id:
                real_courses = _fetch_real_schedule(cas, sem_id)
                if real_courses:
                    day_courses = [c for c in real_courses if c.get("day") == weekday]
                    return {"student_id": sid, "date": target_date.isoformat(), "weekday": weekday,
                            "courses": day_courses, "count": len(day_courses), "source": "real"}
        except Exception as e:
            log.warning(f"课表 API 失败 (student_id={sid}, date={date or target_date.isoformat()})，降级到本地数据: {e}")

    # Fallback: SQLite / 锁定提示
    if _is_locked(sid):
        return {"student_id": sid, "date": target_date.isoformat(), "weekday": weekday,
                "courses": [], "count": 0, "source": "locked", "message": _LOGIN_MSG}

    rows = _db().query(
        "SELECT * FROM student_courses WHERE student_id = ?", (sid,))
    courses = []
    for r in rows:
        time_str = r.get("time", "") or ""
        # 解析 time 中所有 ":N(节次)" 段，判断课程是否在目标星期
        day_matches = _re.findall(r":(\d)\(([^)]+)\)", time_str)
        periods_this_day = []
        for dn, ps in day_matches:
            if _DAY_MAP.get(dn) == weekday:
                for p in ps.split(","):
                    p = p.strip()
                    if p.isdigit() and 1 <= int(p) <= 13:
                        periods_this_day.append(int(p))
        if not periods_this_day:
            continue
        time_range = periods_to_range(sorted(set(periods_this_day)))
        courses.append({
            "course_code": r.get("course_code", ""),
            "course_name": r.get("course_name", ""),
            "teacher": r.get("teacher", ""),
            "location": r.get("location", ""),
            "time": time_str,
            "start_time": time_range["start"] if time_range else "",
            "end_time": time_range["end"] if time_range else "",
            "periods": time_range["periods_text"] if time_range else "",
        })
    return {"student_id": sid, "date": target_date.isoformat(), "weekday": weekday,
            "courses": courses, "count": len(courses), "source": "fallback",
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
    target_date = parsed.get("date", date.today())

    building_code = resolve_building(building)
    building_display = building  # 默认使用用户输入

    # 尝试真实 API
    try:
        api = _catalog()
        date_str = target_date.isoformat() if isinstance(target_date, date) else str(target_date)
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

    # ── Fallback: 模拟数据 ──
    all_rooms = _db().query(
        "SELECT DISTINCT location FROM student_courses WHERE location LIKE ?",
        (f"%{building}%",),
    )
    room_codes = {r["location"] for r in all_rooms}
    all_room_codes = list(room_codes) + [
        f"{building}3A103", f"{building}3A104",
        f"{building}3B201", f"{building}3B202",
    ]

    empty_rooms = []
    for i, room in enumerate(all_room_codes):
        if i % 3 != 0:
            empty_rooms.append({
                "room": room,
                "free_slots": f"{period_start}-17:35",
                "capacity": 80 + (i * 20),
            })

    return {
        "building": building,
        "time": f"{period}({period_start}-{period_end})",
        "empty_rooms": empty_rooms[:5],
        "count": min(len(empty_rooms), 5),
        "source": "fallback",
        "message": "⚠️ 教务接口暂时不可用，以下为空教室模拟数据，仅供参考",
    }


@tool
def query_grade(student_id: str = None, course_name: str = None, semester: str = None) -> dict:
    """
    查询学生成绩。
    优先从 jw 内部 API 获取真实成绩，失败时回退到 SQLite。

    Args:
        student_id: 学号（默认使用演示学生）
        course_name: 课程名（可选），支持模糊匹配
        semester: 学期（可选），如 "2025-2026-1"

    Returns:
        {"student_id": "...", "grades": [...], "count": N,
         "source": "real"|"fallback"}
    """
    sid = student_id or DEMO_STUDENT["id"]

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

    # ── Fallback: SQLite（演示学生）或 锁定提示 ──
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
        student_id: 学号（默认使用演示学生）
        semester: 学期（可选），不指定则计算所有学期累计GPA

    Returns:
        {"student_id": "...", "semester": "...", "gpa": 3.53,
         "total_credits": 13, "details": [...], "source": "real"|"fallback"}
    """
    from utils.gpa_calculator import calculate_gpa

    sid = student_id or DEMO_STUDENT["id"]

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

    # ── Fallback: SQLite（演示学生）或 锁定提示 ──
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
        student_id: 学号（默认使用演示学生）
        course_name: 课程名（可选），不指定则返回所有考试

    Returns:
        {"exams": [{"course": "...", "date": "...", "time": "...",
                    "location": "...", "type": "期末考试"}, ...],
         "source": "real"|"fallback"}
    """
    sid = student_id or DEMO_STUDENT["id"]

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
                    # 按课程名过滤
                    if course_name:
                        exams = [e for e in exams if course_name in e.get("course", "")]
                    # 按日期排序
                    exams.sort(key=lambda x: x.get("date", ""))
                    return {"exams": exams, "count": len(exams), "source": "real",
                            "semester": current_sem.get("nameZh", "")}
    except Exception as e:
        log.warning(f"考试 API 失败 (student_id={sid}, course_name={course_name})，降级到模拟数据: {e}")

    # ── Fallback: 模拟数据 ──
    courses = _db().query(
        "SELECT course_name, time, location FROM student_courses WHERE student_id = ?",
        (sid,),
    )
    today = date.today()
    exams = []
    for i, c in enumerate(courses):
        exam_date = today + timedelta(days=14 + i * 2)
        exams.append({
            "course": c["course_name"],
            "date": exam_date.isoformat(),
            "time": "14:00-16:00",
            "location": c["location"] or "待定",
            "type": "期末考试",
        })
    if course_name:
        exams = [e for e in exams if course_name in e["course"]]
    return {"exams": exams, "source": "fallback",
            "message": "⚠️ 教务接口暂时不可用，以下为考试模拟数据，仅供参考"}


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
        "WHERE name LIKE ? OR code LIKE ? LIMIT ?",
        (f"%{keyword}%", f"%{keyword}%", limit),
    )
    courses = [{"course_code": r.get("code", ""), "course_name": r.get("name", ""),
                "teacher": r.get("teacher", ""), "credits": r.get("credits", 0)}
               for r in rows]
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
            today = date.today().isoformat()
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
        student_id: 学号（默认使用演示学生）
        semester: 学期（可选），不指定则查询当前学期

    Returns:
        {"student_id": "...", "selections": [...], "count": N,
         "source": "real"|"fallback"}
    """
    sid = student_id or DEMO_STUDENT["id"]

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

    # ── Fallback: 本地数据（演示学生）或 锁定提示 ──
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
        student_id: 学号（默认使用演示学生）
        module_id: 模块ID（可选），不指定则获取根模块

    Returns:
        {"student_id": "...", "modules": [...], "source": "real"|"fallback"}
    """
    sid = student_id or DEMO_STUDENT["id"]

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
