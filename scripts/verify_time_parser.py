# -*- coding: utf-8 -*-
"""自然语言事件时间与回答断言的纯逻辑回归。"""
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.qa_consistency import answer_matches
from utils.time_parser import enrich_event_time_args, parse_event_time_range, parse_natural_time


def check(name: str, condition: bool, detail="") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    print(f"[PASS] {name}")


def main() -> None:
    sunday = date(2026, 8, 23)
    check("周日的下周一", parse_natural_time("下周一", sunday)["date"] == date(2026, 8, 24))
    check("周一的下周一", parse_natural_time("下周一", date(2026, 8, 24))["date"] == date(2026, 8, 31))
    check("周四的下周三", parse_natural_time("下周三", date(2026, 8, 27))["date"] == date(2026, 9, 2))

    # "第N周"绝对周次（按 config.SEMESTER 开学日 2026-08-31 换算）
    check("第1周周一", parse_natural_time("第1周周一")["date"] == date(2026, 8, 31))
    check("第5周周四", parse_natural_time("第5周周四")["date"] == date(2026, 10, 1))
    check("第5周默认周一", parse_natural_time("第5周")["date"] == date(2026, 9, 28))
    check("第N周不受参考日影响", parse_natural_time("第2周", sunday)["date"] == date(2026, 9, 7))

    cases = [
        ("明天下午3点到4点开会", "2026-08-24T15:00:00", "2026-08-24T16:00:00"),
        ("明天下午3点半到4点半开会", "2026-08-24T15:30:00", "2026-08-24T16:30:00"),
        ("明天15:20至16:45开会", "2026-08-24T15:20:00", "2026-08-24T16:45:00"),
        ("明天晚上7点开会", "2026-08-24T19:00:00", "2026-08-24T20:00:00"),
        ("明天晚上11点到凌晨1点开会", "2026-08-24T23:00:00", "2026-08-25T01:00:00"),
        ("明天晚上11点到1点开会", "2026-08-24T23:00:00", "2026-08-25T01:00:00"),
    ]
    for query, expected_start, expected_end in cases:
        start, end = parse_event_time_range(query, sunday)
        check(query, start == datetime.fromisoformat(expected_start) and end == datetime.fromisoformat(expected_end),
              f"got {start.isoformat()} ~ {end.isoformat()}")

    args = {"start_time": "2026-08-24T15:30:00", "end_time": "坏值"}
    enrich_event_time_args(args, "明天下午3点半到4点半开会", sunday)
    check("保留合法开始时间", args["start_time"] == "2026-08-24T15:30:00", str(args))
    check("仅补非法结束时间", args["end_time"] == "2026-08-24T16:30:00", str(args))

    banned = re.compile(r"90.{0,6}(对应|是|→).{0,3}3\.7")
    check("拒绝错误 GPA 映射",
          not answer_matches("90分对应3.7，满绩为4.0", "4.0", banned, [], None))
    check("接受正确 GPA 对照表",
          answer_matches("90分对应4.0；85~89分对应3.7", "4.0", banned, [], None))

    print("\n结果: 全部通过")


if __name__ == "__main__":
    main()
