"""
小蜗 — 自然语言时间解析工具
将"今天下午""下周三""第5周"等自然语言表达转换为具体日期
"""

import re
from datetime import date, datetime, time, timedelta


# 星期的中文映射
_WEEKDAY_MAP = {
    "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
    "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3, "星期五": 4, "星期六": 5, "星期日": 6,
}

_CLOCK_RE = re.compile(
    r"(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上|今晚|夜里)?"
    r"(?P<hour>\d{1,2})"
    r"(?:(?::(?P<colon_minute>\d{1,2}))|"
    r"[点时](?:(?P<half>半)|(?P<cn_minute>\d{1,2})分?)?)"
)

_PERIOD_RANGES = (
    (("凌晨",), "00:00", "06:00"),
    (("早上", "上午"), "08:00", "12:00"),
    (("中午",), "11:00", "14:00"),
    (("下午",), "14:00", "18:00"),
    (("傍晚", "晚上", "今晚", "夜里"), "19:00", "22:00"),
)


def parse_natural_time(text: str, reference_date: date = None) -> dict:
    """
    解析自然语言时间表达。

    Args:
        text: 自然语言文本，如 "明天下午"、"周三上午"、"下周三3-4节"
        reference_date: 参考日期，默认今天

    Returns:
        {
            "date": date(2026, 7, 25),       # 解析出的日期
            "day_of_week": "周三",             # 星期几
            "period": "下午",                  # 时段: 上午/下午/晚上
            "period_start": "14:00",           # 时段开始
            "period_end": "18:00",             # 时段结束
            "sections": "3-4节",               # 节次（如有）
        }
    """
    ref = reference_date or date.today()

    result = {
        "date": ref,
        "day_of_week": _weekday_to_chinese(ref.weekday()),
        "period": None,
        "period_start": "08:00",
        "period_end": "18:00",
        "sections": None,
    }

    # 解析 ISO 日期 (YYYY-MM-DD / YYYY/MM/DD / YYYY年M月D日)
    m_iso = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", text)
    has_explicit_date = False
    if m_iso:
        try:
            result["date"] = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            pass
        else:
            has_explicit_date = True

    # 解析日期偏移: 今天/明天/后天/昨天
    if not has_explicit_date and "今天" in text:
        result["date"] = ref
    elif not has_explicit_date and "明天" in text:
        result["date"] = ref + timedelta(days=1)
    elif not has_explicit_date and "后天" in text:
        result["date"] = ref + timedelta(days=2)
    elif not has_explicit_date and "昨天" in text:
        result["date"] = ref + timedelta(days=-1)

    # 解析 "下周X"
    m_next_week = re.search(r"下周([一二三四五六日天])", text)
    if m_next_week and not has_explicit_date:
        target_wd = _WEEKDAY_MAP.get(f"周{m_next_week.group(1)}", 0)
        days_ahead = 7 - ref.weekday() + target_wd
        result["date"] = ref + timedelta(days=days_ahead)

    # 解析 "周X"
    m_this_week = re.search(r"(?:这周)?周([一二三四五六日天])(?!期)", text)
    if m_this_week and "下周" not in text and not has_explicit_date:
        target_wd = _WEEKDAY_MAP.get(f"周{m_this_week.group(1)}", 0)
        days_ahead = target_wd - ref.weekday()
        if days_ahead < 0:
            days_ahead += 7
        result["date"] = ref + timedelta(days=days_ahead)

    # 解析时段
    for names, period_start, period_end in _PERIOD_RANGES:
        matched = next((name for name in names if name in text), None)
        if matched:
            result["period"] = matched
            result["period_start"] = period_start
            result["period_end"] = period_end
            break

    # 解析节次
    m_section = re.search(r"(\d+-\d+)节", text)
    if m_section:
        result["sections"] = m_section.group(1) + "节"

    # 更新星期名称
    result["day_of_week"] = _weekday_to_chinese(result["date"].weekday())

    return result


def _clock_parts(match: re.Match, inherited_period: str = None) -> tuple[int, int, str]:
    """将一个中文或 24 小时时钟表达转换为 hour/minute。"""
    period = match.group("period") or inherited_period
    hour = int(match.group("hour"))
    minute_text = match.group("colon_minute") or match.group("cn_minute")
    minute = 30 if match.group("half") else int(minute_text or 0)

    if minute > 59:
        raise ValueError("分钟必须在 0 到 59 之间")
    if period:
        if not 0 <= hour <= 12:
            raise ValueError("带时段的小时必须在 0 到 12 之间")
        if period in ("凌晨", "早上", "上午"):
            hour = 0 if hour == 12 else hour
        elif period == "中午":
            if hour < 11:
                hour += 12
        elif period in ("下午", "傍晚", "晚上", "今晚", "夜里") and hour < 12:
            hour += 12
    elif not 0 <= hour <= 23:
        raise ValueError("小时必须在 0 到 23 之间")
    return hour, minute, period


def parse_event_time_range(text: str, reference_date: date = None) -> tuple[datetime, datetime]:
    """解析事件的完整起止时间；仅给出一个时钟时默认持续一小时。"""
    parsed = parse_natural_time(text, reference_date=reference_date)
    target_date = parsed["date"]
    clocks = list(_CLOCK_RE.finditer(text))

    if not clocks:
        start = datetime.combine(target_date, time.fromisoformat(parsed["period_start"]))
        end = datetime.combine(target_date, time.fromisoformat(parsed["period_end"]))
        return start, end

    start_hour, start_minute, start_period = _clock_parts(clocks[0])
    start = datetime.combine(target_date, time(start_hour, start_minute))
    if len(clocks) == 1:
        return start, start + timedelta(hours=1)

    end_match = clocks[1]
    end_raw_hour = int(end_match.group("hour"))
    inherited_period = start_period
    if (not end_match.group("period") and
            start_period in ("傍晚", "晚上", "今晚", "夜里") and
            end_raw_hour < int(clocks[0].group("hour"))):
        inherited_period = None
    end_hour, end_minute, _ = _clock_parts(end_match, inherited_period)
    end = datetime.combine(target_date, time(end_hour, end_minute))
    if end <= start:
        end += timedelta(days=1)
    return start, end


def is_iso_datetime(value) -> bool:
    """判断值是否为 datetime.fromisoformat 可接受的字符串。"""
    try:
        datetime.fromisoformat(str(value))
        return True
    except (TypeError, ValueError):
        return False


def enrich_event_time_args(args: dict, text: str, reference_date: date = None) -> bool:
    """只补齐 args 中缺失或非法的事件时间，返回是否发生修改。"""
    start_bad = not is_iso_datetime(args.get("start_time"))
    end_bad = not is_iso_datetime(args.get("end_time"))
    if not (start_bad or end_bad):
        return False

    start, end = parse_event_time_range(text, reference_date=reference_date)
    if start_bad:
        args["start_time"] = start.isoformat()
    if end_bad:
        args["end_time"] = end.isoformat()
    return True


def _weekday_to_chinese(wd: int) -> str:
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return names[wd] if 0 <= wd <= 6 else "未知"


def iso_format(d: date, time_str: str = "08:00") -> str:
    """生成 ISO 格式日期时间字符串"""
    return f"{d.isoformat()}T{time_str}:00"


def format_date_cn(d: date) -> str:
    """格式化为中文日期，如 '7月25日 周三'"""
    wd = _weekday_to_chinese(d.weekday())
    return f"{d.month}月{d.day}日 {wd}"
