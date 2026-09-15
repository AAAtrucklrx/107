# -*- coding: utf-8 -*-
"""学期时区「今天」回归测试。

对应 #4：服务器 TZ=UTC 时，`date.today()` 在北京时间 00:00–08:00 得到「昨天」，
而周一 00:00 恰是教学周切换点 → 教学周落后一周 → 今日弹窗/日课表按错周筛课。

冻结瞬间取「北京周一 00:30」（= UTC 周日 16:30），正是该缺陷的触发窗口。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

import utils.semester_time as st
from utils.schedule_parse import teaching_week

_BEIJING_MONDAY_EARLY = datetime(2026, 9, 13, 16, 30, tzinfo=timezone.utc)  # 北京 09-14 周一 00:30
_SEMESTER_START = date(2026, 8, 31)  # 该学期第一个周一


class _FrozenDatetime(datetime):
    """把 `datetime.now()` 冻结到指定 UTC 瞬间（供 monkeypatch 替换模块符号）。"""

    instant = _BEIJING_MONDAY_EARLY

    @classmethod
    def now(cls, tz=None):  # noqa: D102
        moment = cls.instant.astimezone(tz) if tz is not None else cls.instant.replace(tzinfo=None)
        return moment


@pytest.fixture()
def frozen(monkeypatch):
    monkeypatch.setattr(st, "datetime", _FrozenDatetime)
    return _FrozenDatetime


def test_semester_today_follows_semester_timezone(frozen):
    """学期时区（Asia/Shanghai=+8）已是周一，而 UTC 还在周日。"""
    assert frozen.instant.date() == date(2026, 9, 13)  # UTC 视角：周日
    assert st.semester_today() == date(2026, 9, 14)    # 学期时区视角：周一（正确）


def test_semester_weekday_is_iso_numbering(frozen):
    """1=周一 … 7=周日，与前端 meetings[].weekday 同基准。"""
    assert st.semester_weekday() == 1
    assert st.semester_weekday_text() == "星期一"


def test_semester_now_is_naive_to_avoid_aware_mixing(frozen):
    """刻意返回朴素时间：库内大量与朴素 datetime 比较，混用 aware/naive 会抛 TypeError。"""
    assert st.semester_now().tzinfo is None
    assert st.semester_date_text() == "2026年09月14日"


def test_week_boundary_uses_semester_timezone(frozen):
    """核心回归：北京周一凌晨必须算作新一周，否则今日弹窗按上一周筛课。"""
    assert teaching_week(st.semester_today(), _SEMESTER_START, 18) == 3
    # 旧行为（取 UTC 的 date.today()）会得到上一周
    assert teaching_week(frozen.instant.date(), _SEMESTER_START, 18) == 2


def test_tz_offset_defaults_to_east_eight():
    """学期时区默认 UTC+8（中国无夏令时；可在 config.SEMESTER 或 env 覆盖）。"""
    assert st.tz_offset_hours() == 8
    assert st._tz().utcoffset(None) == timedelta(hours=8)
