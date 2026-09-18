"""活动推荐的时间窗一致性与卡片标签（2026-09-18 用户实测）。

症状：问「这周日有什么活动推荐」→ 卡片「活动推荐」给了 8 行**周六**活动
（理由是"明天截止报名、与你的空闲时间高度匹配"），正文却说"周日暂时没有"。

两个根因：
1. `活动推荐确定性路由` 只填 keyword，**从不传 time_window** → 工具返回全部报名中活动；
2. `_in_window` 认不出"周日"，而且 `"这周" in "这周日"` 为真 → "这周日" 走"本周"分支，
   整周活动都算命中（周六的自然在内）。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import agents.qa.nodes as nodes
import tools.activity_tools as activity_tools
from services.young_client import YoungActivity
from tools.activity_tools import _activity_window_label, _in_window, query_activities

NOW = datetime(2026, 9, 18, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))  # 周五


def _act(name: str, day: str, *, apply_end: str = "") -> YoungActivity:
    return YoungActivity(
        name=name,
        organizer="某社团",
        start_time=f"{day} 14:00",
        end_time=f"{day} 17:00",
        apply_end=apply_end or f"{day} 13:30",
        people_num=3,
        service_hour="2",
        category="单次项目",
        description="简介",
    )


SAT = _act("哲学社新生活力课程", "2026-09-19")
SUN = _act("周日的活动", "2026-09-20")


# ---------- 1. 时间窗判定 ----------

def test_specific_weekday_beats_this_week() -> None:
    """核心回归："这周日" 不能被当成"这一周"。"""
    assert _in_window(SAT, "这周日", NOW) is False
    assert _in_window(SUN, "这周日", NOW) is True
    assert _in_window(SAT, "周日", NOW) is False
    assert _in_window(SAT, "周六", NOW) is True
    assert _in_window(SUN, "周六", NOW) is False


def test_relative_days() -> None:
    assert _in_window(SAT, "明天", NOW) is True   # 09-18 的明天 = 09-19
    assert _in_window(SUN, "明天", NOW) is False
    assert _in_window(SAT, "今天", NOW) is False
    assert _in_window(SUN, "后天", NOW) is True


def test_wide_windows_still_work() -> None:
    assert _in_window(SAT, "周末", NOW) is True
    assert _in_window(SUN, "周末", NOW) is True
    assert _in_window(SAT, "本周", NOW) is True
    assert _in_window(SAT, "", NOW) is None, "没有窗口就不过滤"


def test_next_week_weekday_is_exclusive() -> None:
    assert _in_window(SAT, "下周六", NOW) is False
    next_sat = _act("下周六的活动", "2026-09-26")
    assert _in_window(next_sat, "下周六", NOW) is True


def test_window_labels() -> None:
    assert _activity_window_label("这周日", NOW) == "周日（2026-09-20）"
    assert _activity_window_label("周六", NOW) == "周六（2026-09-19）"
    assert _activity_window_label("明天", NOW) == "明天（2026-09-19）"
    assert _activity_window_label("", NOW) == ""


# ---------- 2. 路由把时间窗传下去 ----------

def _route_state(query: str) -> dict:
    return {
        "query": query,
        "intent": "活动推荐",
        "student_id": "PB25111691",
        "user_profile": {"major": "计算机科学与技术", "grade": "2025级"},
        "tool_results": [],
        "thought_log": [],
        "rounds": 0,
    }


def test_activity_time_window_extraction() -> None:
    assert nodes._activity_time_window("这周日有什么活动推荐") == "周日"
    assert nodes._activity_time_window("本周六有讲座吗") == "周六"
    assert nodes._activity_time_window("明天有什么活动") == "明天"
    assert nodes._activity_time_window("本周末有活动吗") == "本周末"
    assert nodes._activity_time_window("有什么活动推荐") == ""


def test_activity_route_passes_time_window() -> None:
    out = nodes._direct_tool_route(_route_state("这周日有什么活动推荐"))
    assert out is not None and out["decision"] == "call_tool"
    call = out["tool_calls"][0]
    assert call["tool"] == "query_activities"
    assert call["args"]["time_window"] == "周日", "问句里的时间窗必须传给工具"


def test_activity_route_keeps_keyword() -> None:
    out = nodes._direct_tool_route(_route_state("明天有什么讲座"))
    args = out["tool_calls"][0]["args"]
    assert args["time_window"] == "明天" and args["keyword"] == "讲座"


# ---------- 3. 窗口无命中：不要塞别天的活动 ----------

def test_query_activities_window_no_match_returns_empty(monkeypatch) -> None:
    monkeypatch.setattr(activity_tools, "semester_now", lambda: NOW)
    monkeypatch.setattr(activity_tools, "_fetch_enrolment_cached", lambda: ([SAT], None))
    out = query_activities.invoke({"time_window": "周日"})
    assert out["count"] == 0
    assert out["activities"] == [], "用户问周日时，卡片不能出现周六的活动"
    assert out["window_label"] == "周日（2026-09-20）"
    assert "没有正在报名的活动" in out["message"]


def test_query_activities_window_match_keeps_label(monkeypatch) -> None:
    monkeypatch.setattr(activity_tools, "semester_now", lambda: NOW)
    monkeypatch.setattr(activity_tools, "_fetch_enrolment_cached", lambda: ([SAT, SUN], "缓存回退"))
    out = query_activities.invoke({"time_window": "周六"})
    assert out["count"] == 1
    assert out["activities"][0]["name"] == "哲学社新生活力课程"
    assert out["window_label"] == "周六（2026-09-19）"


def test_activity_card_title_shows_window() -> None:
    tables = nodes._tool_to_structured([{
        "tool": "query_activities", "status": "done",
        "result": {"window_label": "周日（2026-09-20）", "activities": [
            {"name": "周日的活动", "organizer": "某社团", "start": "2026-09-20 14:00",
             "place": "5107", "apply_end": "2026-09-20 13:30", "reason": "空闲匹配"},
        ]},
    }])
    assert tables and tables[0]["title"] == "活动推荐（周日（2026-09-20））"

# ---------- 4. 深层语义：活动在目标当天"进行中" ----------

def test_multiday_activity_counts_on_target_day() -> None:
    """跨天活动（09-06~10-11 摄影大赛）在 09-20 当天仍可参加 → 必须命中。"""
    contest = YoungActivity(name="摄影大赛", organizer="全媒体中心",
                            start_time="2026-09-06 00:00", end_time="2026-10-11 04:00",
                            apply_end="2026-10-10 23:00")
    assert _in_window(contest, "周日", NOW) is True


def test_ended_activity_is_excluded() -> None:
    """09-13~09-19 的活动在 09-20 已经结束 → 不能进"周日"推荐。"""
    ended = YoungActivity(name="武协新生活力课程", organizer="武协",
                          start_time="2026-09-13 12:00", end_time="2026-09-19 12:00")
    assert _in_window(ended, "周日", NOW) is False


def test_next_week_sunday_is_excluded_from_this_sunday() -> None:
    next_sun = _act("平面设计公开课", "2026-09-27")
    assert _in_window(next_sun, "周日", NOW) is False, "下周日不属于'这周日'"
    assert _in_window(next_sun, "下周日", NOW) is True
    assert _activity_window_label("下周日", NOW) == "下周日（2026-09-27）"


def test_past_weekday_rolls_to_next_week() -> None:
    """周五问"周三" → 最近的周三是下周三（09-23），而不是已经过去的 09-16。"""
    assert nodes._activity_time_window("周三有什么活动") == "周三"
    wed_next = _act("下周三活动", "2026-09-23")
    wed_past = _act("上周三活动", "2026-09-16")
    assert _in_window(wed_next, "周三", NOW) is True
    assert _in_window(wed_past, "周三", NOW) is False

def test_next_week_sunday_window_is_kept() -> None:
    """'下周日' 不能丢成 '周日'（否则卡片会回到本周日）。"""
    assert nodes._activity_time_window("下周日有什么活动") == "下周日"
    assert nodes._activity_time_window("这周日有什么活动推荐") == "周日"
    out = nodes._direct_tool_route(_route_state("下周日有什么活动"))
    assert out["tool_calls"][0]["args"]["time_window"] == "下周日"
