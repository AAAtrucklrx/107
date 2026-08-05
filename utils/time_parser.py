"""
小蜗 — 自然语言时间解析工具
将"今天下午""下周三""第5周"等自然语言表达转换为具体日期
"""

import re
from datetime import date, datetime, timedelta


# 星期的中文映射
_WEEKDAY_MAP = {
    "周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
    "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3, "星期五": 4, "星期六": 5, "星期日": 6,
}


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
    if m_iso:
        try:
            result["date"] = date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError:
            pass
        else:
            result["day_of_week"] = _weekday_to_chinese(result["date"].weekday())
            return result

    # 解析日期偏移: 今天/明天/后天/昨天
    if "今天" in text:
        result["date"] = ref
    elif "明天" in text:
        result["date"] = ref + timedelta(days=1)
    elif "后天" in text:
        result["date"] = ref + timedelta(days=2)
    elif "昨天" in text:
        result["date"] = ref + timedelta(days=-1)

    # 解析 "下周X"
    m_next_week = re.search(r"下周([一二三四五六日天])", text)
    if m_next_week:
        target_wd = _WEEKDAY_MAP.get(f"周{m_next_week.group(1)}", 0)
        days_ahead = target_wd - ref.weekday()
        if days_ahead <= 0:
            days_ahead += 7
        days_ahead += 7  # "下周" => 加7天
        result["date"] = ref + timedelta(days=days_ahead)

    # 解析 "周X"
    m_this_week = re.search(r"(?:这周)?周([一二三四五六日天])(?!期)", text)
    if m_this_week and "下周" not in text:
        target_wd = _WEEKDAY_MAP.get(f"周{m_this_week.group(1)}", 0)
        days_ahead = target_wd - ref.weekday()
        if days_ahead < 0:
            days_ahead += 7
        result["date"] = ref + timedelta(days=days_ahead)

    # 解析时段
    if "上午" in text:
        result["period"] = "上午"
        result["period_start"] = "08:00"
        result["period_end"] = "12:00"
    elif "下午" in text:
        result["period"] = "下午"
        result["period_start"] = "14:00"
        result["period_end"] = "18:00"
    elif "晚上" in text:
        result["period"] = "晚上"
        result["period_start"] = "19:00"
        result["period_end"] = "22:00"

    # 解析节次
    m_section = re.search(r"(\d+-\d+)节", text)
    if m_section:
        result["sections"] = m_section.group(1) + "节"

    # 更新星期名称
    result["day_of_week"] = _weekday_to_chinese(result["date"].weekday())

    return result


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