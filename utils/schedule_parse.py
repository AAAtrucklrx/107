"""
小蜗 — 课程时间解析与节次级冲突判定（选课 H 项）

时间字符串来源多样（实测样本）：
- jw 真实课表:   "周一 2~11周 第3,4节 09:45-11:20"
- 爬取备份:      "周二 11~12周 第3,4节"
- 实验课变体:    "周四 3~16周 第19:00~19:30节"（无节次号，只有时钟）
- 多段排课:      "周一第1-2节;周三第3-4节"（分号分隔）

冲突判定口径（与轮4 人工目测的差异点）：
- 周次不重叠 → 不算冲突（如 力学B 周二11-12周 vs 热学B 周二13-18周）
- 周次未知 → 保守按重叠处理（weeks_unknown=True，输出中注明）
- 节次优先：双方都有节次号时按节次交集判定；否则按时钟区间兜底（节次→时钟换算见
  utils/course_periods.PERIOD_TIMES）；两者皆无 → unknown（数据不足，如实说明）
"""

from __future__ import annotations

import re

from utils.course_periods import PERIOD_TIMES, parse_periods

_DAY_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_DAY_NUM_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}

_DAY_RE = re.compile(r"[周星期]([一二三四五六日天])")
_WEEKS_RE = re.compile(r"(\d+(?:\s*[~\-—至,，]\s*\d+)*)\s*周")
_PERIODS_RE = re.compile(r"第([0-9,，\-]+)\s*节")
_CLOCK_RE = re.compile(r"(\d{1,2}:\d{2})\s*[~\-—至]\s*(\d{1,2}:\d{2})")

# 无"第"前缀的节次写法（如"周一3-4节"）；lookbehind 排除已带"第"、时钟（含冒号）
# 与列表中段的数字，避免误改"第19:00~19:30节"等时钟变体
_ADD_DI_RE = re.compile(r"(?<![第:\d,，])(\d+(?:[-,，]\d+)*节)")


def normalize_time_str(time_str: str) -> str:
    """为无"第"前缀的节次写法补"第"（"周一3-4节"→"周一第3-4节"），供 parse_course_time 统一解析。"""
    if not time_str:
        return time_str
    return _ADD_DI_RE.sub(r"第\1", time_str)


def _to_minutes(hhmm: str) -> int:
    """'09:45' → 585"""
    try:
        h, m = hhmm.strip().split(":")
        return int(h) * 60 + int(m)
    except (ValueError, IndexError):
        return 0


def _expand_periods(raw: str) -> list[int]:
    """'3,4' / '8,9,10' / '1-2' / '3-5,7' → 节次号列表（1..13，去重排序）"""
    expanded: list[int] = []
    for tok in re.split(r"[,\s]", raw.replace("，", ",")):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            a, b = tok.split("-", 1)
            if a.isdigit() and b.isdigit():
                lo, hi = int(a), int(b)
                if lo <= hi:
                    expanded.extend(range(lo, hi + 1))
        elif tok.isdigit():
            expanded.append(int(tok))
    return sorted({p for p in expanded if 1 <= p <= 13})


def parse_course_time(time_str: str) -> list[dict]:
    """解析课程时间字符串 → 时间段列表（每段一个 dict）。

    每段字段：
      day        "周一".."周日" 或 None（无星期信息）
      day_num    1..7 或 None
      weeks      (start, end) 周次闭区间 或 None（无周次信息）
      weeks_raw  原始周次文本（如 '2~11周'），用于展示
      periods    节次号列表（[] 表示无节次信息）
      clock      (start_min, end_min) 或 None（无时钟信息）
      raw        原始分段文本

    无法解析出任何信息的字符串返回 []（调用方据此标记"时间不全"）。
    """
    if not time_str:
        return []
    slots = []
    for part in re.split(r"[;；]", time_str):
        part = part.strip()
        if not part:
            continue
        slot = {"day": None, "day_num": None, "weeks": None, "weeks_raw": "",
                "periods": [], "clock": None, "raw": part}
        m = _DAY_RE.search(part)
        if m:
            slot["day_num"] = _DAY_CN[m.group(1)]
            slot["day"] = _DAY_NUM_CN[slot["day_num"]]
        m = _WEEKS_RE.search(part)
        if m:
            nums = [int(x) for x in re.findall(r"\d+", m.group(1))]
            if nums:
                slot["weeks"] = (min(nums), max(nums))
                slot["weeks_raw"] = f"{m.group(1)}周"
        m = _PERIODS_RE.search(part)
        if m:
            slot["periods"] = _expand_periods(m.group(1))
        m = _CLOCK_RE.search(part)
        if m:
            s, e = _to_minutes(m.group(1)), _to_minutes(m.group(2))
            if e > s:
                slot["clock"] = (s, e)
        if slot["day_num"] or slot["weeks"] or slot["periods"] or slot["clock"]:
            slots.append(slot)
    return slots


def slot_clock_range(slot: dict) -> tuple[int, int] | None:
    """时间段 → 精确到分钟的起止区间：有时钟用时钟，否则用节次换算（PERIOD_TIMES）。"""
    if slot.get("clock"):
        return slot["clock"]
    if slot.get("periods"):
        times = [PERIOD_TIMES[p] for p in slot["periods"] if p in PERIOD_TIMES]
        if times:
            starts = [_to_minutes(t[0]) for t in times]
            ends = [_to_minutes(t[1]) for t in times]
            return (min(starts), max(ends))
    return None


def slots_overlap(a: dict, b: dict) -> tuple[str, dict]:
    """判定两个时间段是否冲突。

    返回 ("conflict" | "no_conflict" | "unknown", {reason, weeks_unknown})。
    - 星期不同 → 不冲突；缺星期信息 → unknown
    - 周次双方已知且不交叠 → 不冲突；任一未知 → 按重叠保守判定（weeks_unknown=True）
    - 双方都有节次号 → 按节次交集；否则按时钟区间（可换算）交叠
    - 节次与时钟皆缺 → unknown（数据不足，如实说明）
    """
    da, db = a.get("day_num"), b.get("day_num")
    if da is None or db is None:
        return ("unknown", {"reason": "缺少星期信息", "weeks_unknown": False})
    if da != db:
        return ("no_conflict", {"reason": "不同星期", "weeks_unknown": False})

    wa, wb = a.get("weeks"), b.get("weeks")
    weeks_unknown = False
    if wa and wb:
        if not (wa[0] <= wb[1] and wb[0] <= wa[1]):
            return ("no_conflict", {"reason": "周次不重叠", "weeks_unknown": False})
    else:
        weeks_unknown = True

    pa, pb = a.get("periods") or [], b.get("periods") or []
    if pa and pb:
        if set(pa) & set(pb):
            return ("conflict", {"reason": "节次重叠", "weeks_unknown": weeks_unknown})
        return ("no_conflict", {"reason": "节次不重叠", "weeks_unknown": weeks_unknown})

    ca, cb = slot_clock_range(a), slot_clock_range(b)
    if ca and cb:
        if ca[0] < cb[1] and cb[0] < ca[1]:
            return ("conflict", {"reason": "时间段重叠", "weeks_unknown": weeks_unknown})
        return ("no_conflict", {"reason": "时间段不重叠", "weeks_unknown": weeks_unknown})

    return ("unknown", {"reason": "缺少节次/时间数据", "weeks_unknown": weeks_unknown})
