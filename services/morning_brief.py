# -*- coding: utf-8 -*-
"""P5-2 晨报（主动推送）——每日首个登录打开时聚合今日信息。

三板块（每个板块缺数据自动隐藏，不占位）：
1. schedule 今日日程：get_day_view（本地课表/events，离线可用）；
2. exam     今日考试：query_exam（真实 API，失败回退 fallback 并如实标注无数据）；
3. ddl      未来 7 天近期待办：events 表手动事件（排除循环课表），按 start_time 升序。

返回结构（供 ui 渲染）：
{
  "date": "<iso>",
  "sections": [ {key,title,items:[...],source,note?}, ... ],   # 空板块不出现
  "sources": {key: source},                                     # 统一来源标注
}
每板块 items 缺数据时该 section 不进入 sections（不占位）；sources 保留便于调试/标注。
"""
from __future__ import annotations

from datetime import date, timedelta

from utils.logger import get_logger

log = get_logger("xiaowo.morning_brief")

# DDL 展示窗口（天），规格固定 7 天
DDL_WINDOW_DAYS = 7


def _db():
    from services.service_container import ServiceContainer
    return ServiceContainer().db


def _fmt_hhmm(ts: str) -> str:
    """'2026-08-24T15:30:00' → '15:30'；已是 HH:MM 则原样。"""
    if "T" in ts:
        return ts[11:16]
    return ts


def _build_schedule(student_id: str) -> dict | None:
    """今日日程板块。"""
    from tools.schedule_tools import get_day_view
    try:
        view = get_day_view.invoke({"student_id": student_id})  # @tool → StructuredTool，用 .invoke
    except Exception as e:
        log.warning(f"晨报·今日日程失败: {e}")
        return None
    events = view.get("events") or []
    if not events:
        return None
    items = [
        {
            "title": e.get("title", ""),
            "time": (_fmt_hhmm(e.get("start_time", "")) or "") + (
                "-" + _fmt_hhmm(e.get("end_time", "")) if e.get("end_time") else ""),
            "location": e.get("location", ""),
        }
        for e in events
    ]
    return {"key": "schedule", "title": f"📅 今日日程（{view.get('day_of_week', '')}）",
            "items": items, "source": "local", "note": "本地课表/日程"}


def _build_exam_today(student_id: str) -> dict | None:
    """今日考试板块；仅当日考试，缺/离线如实隐藏并标注。"""
    from tools.course_tools import query_exam
    try:
        res = query_exam.invoke({"student_id": student_id})  # @tool → StructuredTool，用 .invoke
    except Exception as e:
        log.warning(f"晨报·今日考试失败: {e}")
        return None
    exams = res.get("exams") or []
    today = date.today().isoformat()
    today_exams = [e for e in exams if str(e.get("date", ""))[:10] == today]
    if not today_exams:
        return None
    items = [
        {
            "course": e.get("course", "未知"),
            "time": e.get("time", ""),
            "location": e.get("location", "待定"),
        }
        for e in today_exams
    ]
    source = "real" if res.get("source") == "real" else "fallback"
    note = "教务实时" if source == "real" else "教务接口暂不可用·本地"
    return {"key": "exam", "title": "📝 今日考试", "items": items,
            "source": source, "note": note}


def _build_ddl(student_id: str) -> dict | None:
    """未来 DDL_WINDOW_DAYS 天内 events 手动事件（排除循环课表），按 start_time 升序。"""
    today = date.today()
    end = today + timedelta(days=DDL_WINDOW_DAYS - 1)
    try:
        db = _db()
        rows = db.query(
            """SELECT title, start_time, end_time, location
               FROM events
               WHERE student_id = ?
                 AND (is_recurring IS NULL OR is_recurring != 1)
                 AND date(start_time) >= date(?)
                 AND date(start_time) <= date(?)
               ORDER BY start_time""",
            (student_id, today.isoformat(), end.isoformat()),
        )
    except Exception as e:
        log.warning(f"晨报·近期DDL失败: {e}")
        return None
    if not rows:
        return None
    items = [
        {
            "title": r["title"],
            "day": date.fromisoformat(str(r["start_time"])[:10]).strftime("%m-%d"),
            "time": _fmt_hhmm(r["start_time"]),
            "location": r.get("location", ""),
        }
        for r in rows
    ]
    return {"key": "ddl", "title": f"⏳ 近期待办/截止（{DDL_WINDOW_DAYS}天内）",
            "items": items, "source": "local", "note": "本地日程·events 表"}


def build_morning_brief(student_id: str) -> dict:
    """聚合三板块晨报；空板块自动隐藏不占位。"""
    from services.session_ctx import reset_student, set_student

    token = set_student(student_id)
    try:
        sections = []
        sources: dict[str, str] = {}
        for builder in (_build_schedule, _build_exam_today, _build_ddl):
            sec = builder(student_id)
            if sec:
                sections.append(sec)
                sources[sec["key"]] = sec.get("source", "local")
        return {"date": date.today().isoformat(), "sections": sections, "sources": sources}
    finally:
        reset_student(token)
