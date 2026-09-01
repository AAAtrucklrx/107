"""
小蜗 — 科大官方节次时间表
来源：教务处官网《2026年春、夏季学期》校历附《上课时间表》
https://www.teach.ustc.edu.cn/calendar/19714.html
用于把 jw 课表的节次编号转换为精确到分钟的起止时间。
"""

# 节次编号 → (开始时间, 结束时间)，格式 HH:MM
# 上午：第1~5小节（课间 09:25~09:45 休息20分钟）
# 下午：第6~10小节（课间 15:35~15:55 休息20分钟）
# 晚上：第11~13小节
import re


PERIOD_TIMES = {
    1: ("07:50", "08:35"),
    2: ("08:40", "09:25"),
    3: ("09:45", "10:30"),
    4: ("10:35", "11:20"),
    5: ("11:25", "12:10"),
    6: ("14:00", "14:45"),
    7: ("14:50", "15:35"),
    8: ("15:55", "16:40"),
    9: ("16:45", "17:30"),
    10: ("17:35", "18:20"),
    11: ("19:30", "20:15"),
    12: ("20:20", "21:05"),
    13: ("21:10", "21:55"),
}


def parse_periods(period_str: str) -> list[int]:
    """
    解析节次字符串，如 "3,4"、"6,7,8,9" → [3, 4] / [6, 7, 8, 9]。
    无法解析时返回空列表。
    """
    if not period_str:
        return []
    nums: list[int] = []
    normalized = str(period_str).replace("，", ",")
    for part in re.split(r"[,\s]+", normalized):
        token = part.strip()
        if not token:
            continue
        bounds = re.split(r"[-~—至]", token, maxsplit=1)
        if len(bounds) == 2 and all(value.strip().isdigit() for value in bounds):
            start, end = (int(value.strip()) for value in bounds)
            if start <= end:
                nums.extend(range(start, end + 1))
        elif token.isdigit():
            nums.append(int(token))
    return sorted({value for value in nums if 1 <= value <= 13})


def periods_to_range(periods: list[int]) -> dict | None:
    """
    给定节次列表，返回精确到分钟的时间范围。
    如 [3, 4] → {"start": "09:45", "end": "11:20", "periods_text": "第3-4节"}
    节次不连续时取最早开始、最晚结束。
    """
    if not periods:
        return None
    times = [PERIOD_TIMES.get(p) for p in periods]
    times = [t for t in times if t]
    if not times:
        return None
    start = min(t[0] for t in times)
    end = max(t[1] for t in times)
    if len(periods) == 1:
        text = f"第{periods[0]}节"
    else:
        text = f"第{periods[0]}-{periods[-1]}节"
    return {"start": start, "end": end, "periods_text": text}
