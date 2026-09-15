# -*- coding: utf-8 -*-
"""学期本地时间 —— 后端「今天/现在」的唯一起点。

## 为什么需要本模块（2026-09-15）

服务器 `TZ` 未设置 ⇒ `time.tzname == ('UTC','UTC')`，而学期属于科大（Asia/Shanghai，
UTC+8，中国自 1991 年起无夏令时）。直接使用 `date.today()` / `datetime.now()` 会在
**北京时间 00:00–08:00** 得到「昨天」，后果：

- 教学周推算落后一周（周一 00:00 恰为周次切换点）→ 今日弹窗/日课表按错周筛课
- 注入 LLM 的「今天是几号 / 星期几」偏一天 → 时间感知类回答跟着错
- 自然语言「明天/后天」、日课表默认日期、晨报「今日考试」全部偏一天

## 设计约束：返回**朴素**（naive）时间

刻意**不**返回 tz-aware 对象。库内既有代码大量把时间与**朴素** datetime 比较
（例如活动的 `apply_deadline`），一旦混用 aware/naive 直接抛 `TypeError`。
本模块返回的是「已换算到学期时区的墙上时间」——语义修正，零破坏。
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone

_DEFAULT_OFFSET_HOURS = 8
_WEEKDAY_ZH = ("一", "二", "三", "四", "五", "六", "日")


def tz_offset_hours() -> int:
    """学期时区相对 UTC 的小时偏移。

    单一来源：`config.SEMESTER["tz_offset_hours"]`（其默认值来自 env
    `XIAOWO_TZ_OFFSET_HOURS`）；配置不可用时退回 +8。
    """
    try:
        from config import SEMESTER

        value = SEMESTER.get("tz_offset_hours")
        if value is not None:
            return int(value)
    except Exception:  # noqa: BLE001 — 配置缺失不应让调用方崩溃
        pass
    try:
        return int(os.getenv("XIAOWO_TZ_OFFSET_HOURS") or _DEFAULT_OFFSET_HOURS)
    except (TypeError, ValueError):
        return _DEFAULT_OFFSET_HOURS


def _tz() -> timezone:
    return timezone(timedelta(hours=tz_offset_hours()))


def semester_now() -> datetime:
    """学期时区的当前墙上时间（naive）。"""
    return datetime.now(_tz()).replace(tzinfo=None)


def semester_today() -> date:
    """学期时区的今天。"""
    return semester_now().date()


def semester_weekday() -> int:
    """学期时区的星期：**1=周一 … 7=周日**（与前端 `meetings[].weekday` 同基准）。"""
    return semester_now().isoweekday()


def semester_date_text() -> str:
    """注入 LLM 的中文日期，如 `2026年09月15日`。"""
    return semester_now().strftime("%Y年%m月%d日")


def semester_weekday_text() -> str:
    """注入 LLM 的中文星期，如 `星期二`。"""
    return "星期" + _WEEKDAY_ZH[semester_weekday() - 1]
