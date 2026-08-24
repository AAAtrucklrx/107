"""
小蜗 — 日程管理 Agent 工具
提供添加日程、查看日程、冲突检测、导入课表
"""

from datetime import date, datetime, timedelta

from langchain_core.tools import tool

from services.service_container import ServiceContainer
from utils.logger import get_logger

log = get_logger("xiaowo.tools.schedule")


def _db():
    """获取数据库实例"""
    return ServiceContainer().db


# ── 内部函数 ───────────────────────────────────────────

def _check_conflicts(student_id: str, start_time: str, end_time: str) -> list:
    """检查时间冲突"""
    conflicts = _db().query(
        """SELECT id, title, start_time, end_time, event_type
           FROM events
           WHERE student_id = ? AND NOT (end_time <= ? OR start_time >= ?)""",
        (student_id, start_time, end_time),
    )
    return [
        {
            "event_id": c["id"],
            "title": c["title"],
            "time": f"{c['start_time']} - {c['end_time']}",
            "type": c["event_type"],
        }
        for c in conflicts
    ]


def _query_day_events(student_id: str, date_str: str) -> list[dict]:
    """直接查询某天的日程（供 get_day_view/get_week_view 批量调用）。

    is_recurring=1 语义为"每周 start_time 的星期重复"（课表导入），
    按星期过滤，避免任意日期混入全部循环事件。
    """
    events = _db().query(
        """SELECT * FROM events
           WHERE student_id = ?
           AND (date(start_time) = date(?) OR
                (is_recurring = 1 AND strftime('%w', start_time) = strftime('%w', ?)))
           ORDER BY start_time""",
        (student_id, date_str, date_str),
    )
    return [
        {
            "id": e["id"],
            "title": e["title"],
            "type": e["event_type"],
            "start_time": e["start_time"][11:16] if "T" in e["start_time"] else e["start_time"],
            "end_time": e["end_time"][11:16] if "T" in e["end_time"] else e["end_time"],
            "location": e.get("location", ""),
        }
        for e in events
    ]


# ── Tool 定义 ──────────────────────────────────────────

@tool
def add_event(
    student_id: str,
    title: str,
    start_time: str,
    end_time: str,
    location: str = None,
    description: str = None,
) -> dict:
    """
    添加一个日程事件。自动检测时间冲突。

    Args:
        student_id: 学号
        title: 日程标题
        start_time: 开始时间（ISO格式 "2026-07-29T15:00:00"）
        end_time: 结束时间（ISO格式）
        location: 地点（可选）
        description: 备注（可选）

    Returns:
        {"success": True, "event_id": 123, "conflicts": [...], "has_conflict": True}
    """
    if not title.strip():
        return {"success": False, "error": "日程标题不能为空"}

    try:
        st = datetime.fromisoformat(start_time)
        et = datetime.fromisoformat(end_time)
        if st >= et:
            return {"success": False, "error": "开始时间不能晚于结束时间"}
    except ValueError:
        return {"success": False, "error": "时间格式无效，请使用 ISO 格式"}

    conflicts = _check_conflicts(student_id, start_time, end_time)

    event_id = _db().execute(
        """INSERT INTO events (student_id, title, event_type, start_time, end_time,
           location, description, source)
           VALUES (?, ?, 'custom', ?, ?, ?, ?, 'manual')""",
        (student_id, title, start_time, end_time, location or "", description or ""),
    )

    return {
        "success": True,
        "event_id": event_id,
        "conflicts": conflicts,
        "has_conflict": len(conflicts) > 0,
    }


@tool
def get_day_view(student_id: str, date_str: str = None) -> dict:
    """
    获取指定日期的日程视图。

    Args:
        student_id: 学号
        date_str: 日期（ISO格式 "2026-07-24"），不指定则默认今天

    Returns:
        {"date": "...", "day_of_week": "...", "events": [...], "count": N}
    """
    target_date = date_str or date.today().isoformat()
    weekday = date.fromisoformat(target_date).weekday()
    day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    events = _query_day_events(student_id, target_date)

    return {
        "date": target_date,
        "day_of_week": day_names[weekday] if 0 <= weekday <= 6 else "未知",
        "events": events,
        "count": len(events),
    }


@tool
def get_week_view(student_id: str, start_date: str = None) -> dict:
    """
    获取本周日程概览。

    Args:
        student_id: 学号
        start_date: 周起始日期（ISO格式），默认本周一

    Returns:
        {"week_start": "...", "week_end": "...", "daily": {...},
         "total_events": N, "busiest_day": "...", "free_days": [...]}
    """
    today = date.today()
    if start_date:
        week_start = date.fromisoformat(start_date)
    else:
        week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    daily = {}
    total_events = 0

    for i in range(7):
        d = week_start + timedelta(days=i)
        events = _query_day_events(student_id, d.isoformat())
        count = len(events)
        total_events += count

        daily[day_names[i]] = {
            "event_count": count,
            "busy_hours": count * 1.5,
        }

    busy_day = max(daily, key=lambda k: daily[k]["busy_hours"])
    free_days = [k for k, v in daily.items() if v["event_count"] == 0]

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "daily": daily,
        "total_events": total_events,
        "busiest_day": busy_day,
        "free_days": free_days,
    }


@tool
def check_conflict(student_id: str, start_time: str, end_time: str) -> dict:
    """
    检查指定时间段是否有冲突。

    Args:
        student_id: 学号
        start_time: 开始时间（ISO格式）
        end_time: 结束时间（ISO格式）

    Returns:
        {"has_conflict": True, "conflicts": [...]}
    """
    conflicts = _check_conflicts(student_id, start_time, end_time)
    return {
        "has_conflict": len(conflicts) > 0,
        "conflicts": conflicts,
    }


@tool
def import_schedule(student_id: str) -> dict:
    """
    从课业助手导入课表数据为有限的具体日程。

    开学日期与学期周数读 config.SEMESTER（每学期只更新一处）；
    节次→时钟换算复用 utils/course_periods.PERIOD_TIMES（含晚课 11-13 节）；
    按课程实际周次生成事件，不连续周与单双周不会被错误补齐。周次缺失时才
    按全学期处理并在 week_assumed 中披露；无法解析时间的课程不猜时间、不导入。

    Args:
        student_id: 学号

    Returns:
        {"imported_count": N, "event_count": M, "courses": [...],
         "week_assumed": [...], "time_unparsed": [...], "note": "..."}
    """
    from config import SEMESTER
    from tools.course_tools import query_schedule
    from utils.schedule_parse import (
        normalize_time_str,
        parse_course_time,
        slot_clock_range,
        slot_week_numbers,
    )

    try:
        base_date = date.fromisoformat(str(SEMESTER.get("start_date", "")))
    except ValueError:
        return {
            "imported_count": 0,
            "courses": [],
            "time_unparsed": [],
            "error": f"学期配置无效（SEMESTER={SEMESTER}），请检查 config.py",
        }
    try:
        total_weeks = int(SEMESTER.get("total_weeks", 18))
    except (TypeError, ValueError):
        total_weeks = 0
    if total_weeks < 1:
        return {
            "imported_count": 0,
            "event_count": 0,
            "courses": [],
            "week_assumed": [],
            "time_unparsed": [],
            "error": f"学期周数无效（SEMESTER={SEMESTER}），请检查 config.py",
        }

    schedule = query_schedule.invoke({"student_id": student_id})
    courses = schedule.get("courses") or []
    if not courses:
        message = schedule.get("message") or "当前课表为空，没有可导入的课程"
        return {
            "imported_count": 0,
            "event_count": 0,
            "courses": [],
            "week_assumed": [],
            "time_unparsed": [],
            "note": message,
        }

    imported: list[str] = []
    week_assumed: list[str] = []
    time_unparsed: list[str] = []
    event_rows: list[tuple] = []
    seen_occurrences: set[tuple] = set()
    for c in courses:
        course_name = str(c.get("course_name") or "").strip()
        time_str = (c.get("time") or "").strip()
        if not course_name:
            continue

        # 旧备份偶尔把周次和教室一起放在 location；合并后仅供结构化解析，
        # 原 location 仍原样写回，避免猜测、截断真实地点。
        parse_text = " ".join(
            part for part in (time_str, str(c.get("location") or "").strip()) if part
        )
        slots = parse_course_time(normalize_time_str(parse_text)) if parse_text else []
        placed = False
        for slot in slots:
            if slot.get("day_num") is None:
                continue
            clock = slot_clock_range(slot)
            if not clock:
                continue
            start_hhmm = f"{clock[0] // 60:02d}:{clock[0] % 60:02d}"
            end_hhmm = f"{clock[1] // 60:02d}:{clock[1] % 60:02d}"
            if slot.get("week_numbers") is None and course_name not in week_assumed:
                week_assumed.append(course_name)
            for week in slot_week_numbers(slot, total_weeks):
                event_date = base_date + timedelta(
                    weeks=week - 1,
                    days=slot["day_num"] - 1,
                )
                occurrence = (
                    course_name,
                    event_date.isoformat(),
                    start_hhmm,
                    end_hhmm,
                    str(c.get("location") or ""),
                )
                if occurrence in seen_occurrences:
                    continue
                seen_occurrences.add(occurrence)
                event_rows.append((
                    student_id,
                    course_name,
                    f"{event_date.isoformat()}T{start_hhmm}:00",
                    f"{event_date.isoformat()}T{end_hhmm}:00",
                    c.get("location", ""),
                    f"课表导入 · 第{week}周",
                ))
                placed = True
        if placed:
            imported.append(course_name)
        else:
            time_unparsed.append(f"{course_name}({time_str or '无时间'})")

    if not event_rows:
        note = "没有可解析的课程时间，已保留原有课表日程"
        if time_unparsed:
            note += "；未导入：" + "、".join(time_unparsed)
        return {
            "imported_count": 0,
            "event_count": 0,
            "courses": [],
            "week_assumed": week_assumed,
            "time_unparsed": time_unparsed,
            "note": note,
        }

    db = _db()
    with db.transaction() as conn:
        conn.execute(
            """DELETE FROM reminders
               WHERE event_id IN (
                   SELECT id FROM events
                   WHERE student_id=? AND source='schedule_import'
               )""",
            (student_id,),
        )
        conn.execute(
            "DELETE FROM events WHERE student_id=? AND source='schedule_import'",
            (student_id,),
        )
        conn.executemany(
            """INSERT INTO events
               (student_id, title, event_type, start_time, end_time,
                location, description, is_recurring, source)
               VALUES (?, ?, 'course', ?, ?, ?, ?, 0, 'schedule_import')""",
            event_rows,
        )

    note = (
        f"已按实际周次导入 {len(imported)} 门课程、{len(event_rows)} 个上课时段，"
        f"范围为第1-{total_weeks}周"
    )
    if week_assumed:
        note += "；周次缺失、按全学期处理：" + "、".join(week_assumed)
    if time_unparsed:
        note += "；以下课程时间无法解析，未导入：" + "、".join(time_unparsed)

    return {
        "imported_count": len(imported),
        "event_count": len(event_rows),
        "courses": imported,
        "week_assumed": week_assumed,
        "time_unparsed": time_unparsed,
        "note": note,
    }
