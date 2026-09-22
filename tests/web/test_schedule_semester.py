"""本周课程与日程的学期正确性（2026-09-18 修）。

用户实测症状：问「请汇总我本周的课程与个人日程，并按日期排序。」，小蜗排出的是
**2026 春季学期**的课表（还把春季周次套到秋季教学周上解释"本周还没到开课周次"），
而同一时刻「课表」页面是对的。三个根因各有一组测试：

1. `CatalogAPI.get_current_semester()` 在**升序**的学期列表上取"第一个 start <= today"
   → 永远命中 2002 年夏季学期（实测 id=1，正确值 461/2026年秋季学期）；
2. 本地兜底 SQL 不按学期过滤、也不披露学期 → 春季缓存冒充本学期；
3. 个人数据路由只调 `query_schedule`，"个人日程"被静默丢掉；且 `get_week_view`
   只回"有几件事"，模型拿不到标题/时间就没法"按日期排序汇总"。

收口（同日第二刀）：周维度问题**只**调 `get_week_view`，而它自己按教学周过滤课程。
此前把整学期课表一起喂给模型，模型自己合并 → 实测把 5~14 周的"模拟与数字电路实验"
铺到第 3 周周四晚上、把只有第 2 周的"图论"铺到本周日。
"""

from __future__ import annotations

from datetime import date as _Date

import agents.qa.nodes as nodes
import tools.course_tools as course_tools
import tools.schedule_tools as schedule_tools
from tools.api_client import CatalogAPI
from tools.course_tools import _current_semester_labels, _current_semester_zh, query_schedule
from tools.schedule_tools import get_week_view

DEMO = "PB25111691"
OTHER = "PB99999999"


class _FakeDB:
    """最小 DB 替身：只认 `student_courses` 与 `events` 两条查询。"""

    def __init__(self, courses=(), events=()) -> None:
        self.courses = [dict(c) for c in courses]
        self.events = [dict(e) for e in events]

    def query(self, sql, params=()):
        if "student_courses" in sql:
            sid = params[0]
            return [dict(r) for r in self.courses if r.get("student_id") == sid]
        if "FROM events" in sql:
            sid, day = params[0], params[1]
            out = []
            for event in self.events:
                if event.get("student_id") != sid:
                    continue
                start = str(event.get("start_time") or "")
                if start[:10] == day:
                    out.append(dict(event))
                elif event.get("is_recurring") and _weekday(start) == _weekday(day):
                    out.append(dict(event))
            return sorted(out, key=lambda row: str(row.get("start_time")))
        return []


def _weekday(iso: str) -> int:
    return _Date.fromisoformat(iso[:10]).isoweekday()


def _course(sid: str, name: str, semester: str, time: str = "周一 1~18周 第1,2节") -> dict:
    return {"student_id": sid, "course_code": "X.01", "course_name": name, "teacher": "某老师",
            "credits": 1, "time": time, "location": "3C101", "semester": semester,
            "schedule_json": "[]"}


def _event(sid: str, title: str, start: str, end: str, *, etype: str = "custom",
           recurring: int = 0, loc: str = "E2110") -> dict:
    return {"id": 1, "student_id": sid, "title": title, "event_type": etype,
            "start_time": start, "end_time": end, "location": loc,
            "is_recurring": recurring, "source": "manual", "description": ""}


def _patch_tools(monkeypatch, db: _FakeDB) -> None:
    monkeypatch.setattr(course_tools, "_db", lambda: db)
    monkeypatch.setattr(course_tools, "_cas", lambda: None)
    monkeypatch.setattr(course_tools, "_is_locked", lambda _sid: False)
    monkeypatch.setattr(schedule_tools, "_db", lambda: db)


# ---------- 1. 当前学期解析：不能再命中 2002 年夏季学期 ----------

def test_current_semester_skips_the_2002_entry() -> None:
    api = CatalogAPI()
    api.get_semesters = lambda: [
        {"id": 1, "nameZh": "2002年夏季学期", "start": "2002-07-01", "end": "2002-07-31", "isLast": False},
        {"id": 461, "nameZh": "2026年秋季学期", "start": "2026-08-30", "end": "2027-01-15", "isLast": True},
    ]
    assert api.get_current_semester()["id"] == 461


def test_current_semester_ignores_is_last_on_a_future_term() -> None:
    """`isLast` 标在还没开始的学期上时不能选它（否则答成下学期）。"""
    api = CatalogAPI()
    api.get_semesters = lambda: [
        {"id": 461, "nameZh": "2026年秋季学期", "start": "2026-08-30", "isLast": False},
        {"id": 500, "nameZh": "2027年春季学期", "start": "2027-03-01", "isLast": True},
    ]
    assert api.get_current_semester()["id"] == 461


def test_current_semester_falls_back_to_latest_started() -> None:
    api = CatalogAPI()
    api.get_semesters = lambda: [
        {"id": 1, "nameZh": "2002年夏季学期", "start": "2002-07-01"},
        {"id": 301, "nameZh": "2023年春季学期", "start": "2023-03-05"},
        {"id": 461, "nameZh": "2026年秋季学期", "start": "2026-08-30"},
    ]
    assert api.get_current_semester()["id"] == 461


def test_current_semester_labels_cover_all_spellings() -> None:
    """同一学期在 config / jw nameZh / jw code 里有三种写法，过滤时必须都认。"""
    from config import SEMESTER

    name = str(SEMESTER["name"])
    parts = name.split("-")
    year, term = parts[0], parts[-1]
    term_zh = {"1": "秋季", "2": "春季", "3": "夏季"}[term]
    assert {
        name, name.replace("-", ""), f"{year}年{term_zh}学期", f"{year}{term}",
    } <= _current_semester_labels()
    assert _current_semester_zh() == f"{year}年{term_zh}学期"


# ---------- 2. 本地兜底只认当前学期 ----------

def test_stale_cache_is_not_passed_off_as_current_semester(monkeypatch) -> None:
    db = _FakeDB(courses=[_course(OTHER, "力学B", "2026年春季学期")])
    _patch_tools(monkeypatch, db)
    out = query_schedule.invoke({"student_id": OTHER})
    assert out["count"] == 0, "旧学期缓存绝不能冒充本学期"
    assert out["source"] == "stale_cache"
    assert "2026年春季学期" in out["message"] and "登录" in out["message"]


def test_current_semester_rows_win(monkeypatch) -> None:
    label = _current_semester_zh()
    db = _FakeDB(courses=[
        _course(OTHER, "图论", label),
        _course(OTHER, "力学B", "2026年春季学期"),
    ])
    _patch_tools(monkeypatch, db)
    out = query_schedule.invoke({"student_id": OTHER})
    assert out["source"] == "course_cache"
    assert [c["course_name"] for c in out["courses"]] == ["图论"]
    assert out["semester"] == label


def test_demo_student_reads_the_same_fixture_as_the_schedule_page(monkeypatch) -> None:
    """演示学生：聊天工具必须与「课表」页面同源（`fixtures/demo/<学号>.json`）。"""
    db = _FakeDB(courses=[_course(DEMO, "力学B", "2026年春季学期")])
    _patch_tools(monkeypatch, db)
    out = query_schedule.invoke({"student_id": DEMO})
    assert out["source"] == "demo_fixture"
    assert out["count"] > 0
    assert out["semester"] == _current_semester_zh()
    assert "力学B" not in [c["course_name"] for c in out["courses"]]
    assert "合成" in out["message"]


def test_schedule_day_filter_applies_to_local_source(monkeypatch) -> None:
    label = _current_semester_zh()
    db = _FakeDB(courses=[
        _course(OTHER, "图论", label, time="周二 1~15周 第6,7节"),
        _course(OTHER, "游泳", label, time="周一 1~18周 第3,4节"),
    ])
    _patch_tools(monkeypatch, db)
    out = query_schedule.invoke({"student_id": OTHER, "day": "周一"})
    assert [c["course_name"] for c in out["courses"]] == ["游泳"]


# ---------- 3. get_week_view 要能"按日期排序" ----------

def test_week_view_lists_events_with_dates(monkeypatch) -> None:
    db = _FakeDB(events=[
        _event(OTHER, "小组会", "2026-09-16T14:00:00", "2026-09-16T18:00:00"),
        _event(OTHER, "组会", "2026-09-17T14:00:00", "2026-09-17T16:00:00"),
    ])
    _patch_tools(monkeypatch, db)
    out = get_week_view.invoke({"student_id": OTHER, "start_date": "2026-09-14"})
    assert out["week_start"] == "2026-09-14" and out["week_end"] == "2026-09-20"
    assert out["total_events"] == 2
    thu = out["daily"]["周四"]
    assert thu["date"] == "2026-09-17" and thu["event_count"] == 1
    assert [e["title"] for e in thu["events"]] == ["组会"]
    assert thu["events"][0]["start_time"] == "14:00"
    assert out["daily"]["周二"]["events"] == []
    assert set(out["free_days"]) == {"周一", "周二", "周五", "周六", "周日"}


def test_week_view_keeps_imported_course_events(monkeypatch) -> None:
    """课表导入的事件（source=schedule_import，绝对日期）也要按日期进周视图。"""
    db = _FakeDB(events=[
        _event(OTHER, "电磁学C", "2026-09-16T07:50:00", "2026-09-16T09:25:00",
               etype="course", loc="3A313"),
    ])
    _patch_tools(monkeypatch, db)
    out = get_week_view.invoke({"student_id": OTHER, "start_date": "2026-09-14"})
    wed = out["daily"]["周三"]
    assert [e["title"] for e in wed["events"]] == ["电磁学C"]
    assert wed["events"][0]["type"] == "course"


# ---------- 3b. 周视图按教学周过滤课程（模型之前就是在这一步铺错的） ----------

def test_week_view_filters_courses_by_teaching_week(monkeypatch) -> None:
    label = _current_semester_zh()
    db = _FakeDB(courses=[
        _course(OTHER, "计算机网络", label, time="周一 1~15周 第1,2节 5203"),
        _course(OTHER, "模拟与数字电路实验", label, time="周四 5~14周 第6-9节 电三楼406"),
        _course(OTHER, "图论", label, time="周日 2周 第6,7节 3C301"),
    ])
    _patch_tools(monkeypatch, db)
    out = get_week_view.invoke({"student_id": OTHER, "start_date": "2026-09-14"})
    assert "计算机网络" in [e["title"] for e in out["daily"]["周一"]["events"]]
    assert out["daily"]["周四"]["events"] == [], "5~14 周的课不能出现在第 3 周"
    assert out["daily"]["周日"]["events"] == [], "只有第 2 周的课不能出现在第 3 周"
    assert set(out["free_days"]) == {"周二", "周三", "周四", "周五", "周六", "周日"}


def test_week_view_dedupes_injected_course_with_imported_event(monkeypatch) -> None:
    """课表算出来的课与 events 里导入的同名同刻事件只出现一次。"""
    label = _current_semester_zh()
    db = _FakeDB(
        courses=[_course(OTHER, "计算机网络", label, time="周一 1~15周 第1,2节 5203")],
        events=[_event(OTHER, "计算机网络", "2026-09-14T07:50:00", "2026-09-14T09:25:00",
                       etype="course", loc="5203")],
    )
    _patch_tools(monkeypatch, db)
    out = get_week_view.invoke({"student_id": OTHER, "start_date": "2026-09-14"})
    assert out["daily"]["周一"]["event_count"] == 1


# ---------- 4. 路由：周维度以周视图为准 ----------

def _route_state(query: str, *, student_id: str = DEMO) -> dict:
    return {
        "query": query,
        "intent": "日程查询",
        "student_id": student_id,
        "user_profile": {},
        "tool_results": [],
        "thought_log": [],
        "rounds": 0,
    }


def test_route_week_question_uses_week_view_only() -> None:
    """用户原话：'课程与个人日程' 周维度 → 只调周视图（它自己含课程+日程）。"""
    out = nodes._direct_tool_route(_route_state("请汇总我本周的课程与个人日程，并按日期排序。"))
    assert out is not None and out["decision"] == "call_tool"
    assert [c["tool"] for c in out["tool_calls"]] == ["get_week_view"]
    assert out["tool_calls"][0]["args"]["start_date"] == nodes._target_week_start("本周")


def test_route_week_agenda_without_courses() -> None:
    out = nodes._direct_tool_route(_route_state("我本周有什么安排"))
    assert [c["tool"] for c in out["tool_calls"]] == ["get_week_view"]


def test_route_week_schedule_no_longer_feeds_semester_list() -> None:
    """回归：'我这周课表' 也走周视图（整学期列表会让模型把非本周课铺进来）。"""
    out = nodes._direct_tool_route(_route_state("我这周课表"))
    assert [c["tool"] for c in out["tool_calls"]] == ["get_week_view"]


def test_route_semester_schedule_still_uses_query_schedule() -> None:
    """非周维度的"我的课表"仍看整学期列表。"""
    out = nodes._direct_tool_route(_route_state("我的课表"))
    assert [c["tool"] for c in out["tool_calls"]] == ["query_schedule"]


def test_route_next_week_offsets_the_start_date() -> None:
    out = nodes._direct_tool_route(_route_state("下周课表"))
    call = out["tool_calls"][0]
    assert call["tool"] == "get_week_view"
    assert call["args"]["start_date"] == nodes._target_week_start("下周")
    assert call["args"]["start_date"] > nodes._target_week_start("本周")


def test_route_does_not_hijack_course_recommendation() -> None:
    """'课程'单独出现（没有课表/周维度）不得抢走选课推荐类问句。"""
    out = nodes._direct_tool_route(_route_state("推荐一些课程"))
    tools = [c["tool"] for c in (out or {}).get("tool_calls") or []]
    assert "query_schedule" not in tools and "get_week_view" not in tools


def test_route_composes_once_week_view_done() -> None:
    state = _route_state("请汇总我本周的课程与个人日程，并按日期排序。")
    state["tool_results"] = [{"tool": "get_week_view", "status": "done"}]
    out = nodes._direct_tool_route(state)
    assert out["decision"] == "compose" and out["tool_calls"] == []


# ── 2026-09-22：日维度必须优先于「课表」（09-19 线上缺陷） ──────────────
# 症状：问「请查询我今天的课表…」被 `elif "课表"` 抢到 query_schedule（整学期），
# 模型把 5~14 周的课铺进本周，编出「图论 第2周」当成今天的课；
# 而 query_daily_schedule("今天") 实测 0 条（2026-09-19 周六、第 3 周）。

def test_route_today_schedule_uses_daily_not_semester() -> None:
    out = nodes._direct_tool_route(_route_state("请查询我今天的课表，按时间顺序列出。"))
    assert [c["tool"] for c in out["tool_calls"]] == ["query_daily_schedule"]
    assert out["tool_calls"][0]["args"]["date"] == "今天"


def test_route_tomorrow_schedule_uses_daily() -> None:
    out = nodes._direct_tool_route(_route_state("我明天的课"))
    assert [c["tool"] for c in out["tool_calls"]] == ["query_daily_schedule"]
    assert out["tool_calls"][0]["args"]["date"] == "明天"


def test_route_weekday_schedule_uses_daily() -> None:
    """周X 也是日维度（'我周四有什么课'），此前同样被整学期课表抢走。"""
    out = nodes._direct_tool_route(_route_state("我周四有什么课"))
    assert [c["tool"] for c in out["tool_calls"]] == ["query_daily_schedule"]
    assert out["tool_calls"][0]["args"]["date"] == "周四"


def test_route_day_agenda_uses_daily() -> None:
    out = nodes._direct_tool_route(_route_state("我今天的安排"))
    assert [c["tool"] for c in out["tool_calls"]] == ["query_daily_schedule"]


def test_route_week_word_still_wins_over_day_word() -> None:
    """边界：'本周' 仍归周视图（它按教学周过滤，是"本周"的唯一权威）。"""
    out = nodes._direct_tool_route(_route_state("我这周课表"))
    assert [c["tool"] for c in out["tool_calls"]] == ["get_week_view"]


def test_route_semester_schedule_unaffected_by_day_branch() -> None:
    """边界：没有日期词的"我的课表"仍看整学期列表。"""
    out = nodes._direct_tool_route(_route_state("我的课表"))
    assert [c["tool"] for c in out["tool_calls"]] == ["query_schedule"]
