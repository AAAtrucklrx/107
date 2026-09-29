#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐个调用全部内置工具，检查"能不能接收参数、能不能正常返回"（2026-09-29）。

用法：
    .venv/bin/python scripts/probe_tools.py                # 全部 31 个工具
    .venv/bin/python scripts/probe_tools.py --only query_grade analyze_teacher
    .venv/bin/python scripts/probe_tools.py --timeout 60

判读：
    OK     正常返回（有内容）
    EMPTY  无异常但结果为空/明确"暂无"（多为演示数据本身没有，例如考试安排）
    SKIP   夹具缺必填参数（脚本问题，不是工具问题）
    ERR    抛异常或超时 —— **这是唯一需要修的状态**，脚本以退出码 1 结束

注意：个人类工具依赖 `services.session_ctx.set_student()` 注入的上下文（图里由
`agents/qa/graph.py` 设置），本脚本按同样方式注入演示学号；数据库初始化也走同一条路径。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import importlib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODULES = ["faq_tools", "schedule_tools", "selection_tools", "course_tools",
           "advisor_tools", "program_tools", "activity_tools", "link_tools"]

DEMO_STUDENT = "PB25111691"

# 夹具：覆盖各工具的必填参数（注意 query_schedule 的 day 是"星期几"而非日期）
FIXTURES: dict[str, object] = {
    "student_id": DEMO_STUDENT,
    "major": "计算机科学与技术", "grade": "2023",
    "course": "量子物理", "course_name": "量子物理",
    "course_a": "量子物理", "course_b": "电磁学",
    "teacher": "张辉", "teacher_name": "张辉",
    "major_a": "计算机科学与技术", "major_b": "物理学",
    "keyword": "人工智能", "scene": "退课",
    "building": "三教", "time_desc": "周三下午",
    "title": "小蜗工具自检", "start_time": "2026-09-30 14:00", "end_time": "2026-09-30 15:00",
    "location": "三教 101", "description": "自检用日程",
    "day": "周五", "week": 5, "date": "2026-09-29", "limit": 3, "n": 3,
    "category": "讲座", "time_window": "本周", "query": "选课",
    "question": "推荐几门通识课", "text": "考试安排",
    "profile": {"major": "计算机科学与技术", "grade": "2023"},
    "user_profile": {"major": "计算机科学与技术", "grade": "2023"},
    "preference_type": "通识", "course_scope": "通识课", "target_term": "2026秋",
}

_EMPTY_HINTS = ("暂无", "未找到", "不可用", "未查询", "查询失败", "没有正在")

# 某些工具需要特定组合才能走到 happy path（全局夹具会同时塞多个参数）
TOOL_ARGS_OVERRIDES: dict[str, dict] = {
    "analyze_teacher": {"course": "计算系统概论A"},          # 按课程列该课所有老师
    "get_course_reviews": {"course_name": "量子物理"},
    "query_grade": {"student_id": DEMO_STUDENT},
    "query_schedule": {"student_id": DEMO_STUDENT, "day": "周五"},
}


def _is_empty(result) -> bool:
    """结构化判空：不再靠关键词误伤有数据的返回（calc_gpa/query_program 曾被子串误判）。"""
    if result is None:
        return True
    if isinstance(result, (list, tuple, set)):
        return len(result) == 0
    if isinstance(result, dict):
        if result.get("error"):
            return True
        if result.get("found") is False:
            return True
        if not result:
            return True
        # 有实质内容（非空列表/字典字段）就不算空 —— 避免 count 字段 + 明细并存时误判
        containers = [v for v in result.values() if isinstance(v, (list, tuple, dict, set))]
        if any(len(v) > 0 for v in containers):
            return False
        if result.get("count") == 0:
            return True
        return any(hint in str(result.get("message", "")) for hint in _EMPTY_HINTS)
    if isinstance(result, str):
        return not result.strip()
    return False


def _bootstrap() -> None:
    """初始化业务库 / 评课库 / FAQ 知识库，并注入个人上下文。

    直接复用 `init_check.py`（它就是 app 启动时的初始化入口，同时会把 FAQ 知识库加载好——
    少了这一步，`search_faq` / `get_faq_categories` 会因为"知识库未初始化"而假空）。
    """
    import init_check  # noqa: F401 — 导入即完成初始化
    from services.session_ctx import set_student

    set_student(DEMO_STUDENT)


def _load_tools() -> list:
    from langchain_core.tools import BaseTool
    found: dict[str, object] = {}
    for mod_name in MODULES:
        mod = importlib.import_module(f"tools.{mod_name}")
        for value in vars(mod).values():
            if isinstance(value, BaseTool):
                found.setdefault(value.name, value)
    return [found[k] for k in sorted(found)]


def _args_for(tool) -> tuple[dict, list[str]]:
    props, required = {}, []
    try:
        schema = tool.args_schema.model_json_schema()
        props, required = schema.get("properties", {}), schema.get("required", [])
    except Exception:  # noqa: BLE001
        props = getattr(tool, "args", {}) or {}
        required = [k for k, v in props.items() if isinstance(v, dict) and v.get("required")]
    args, missing = {}, []
    override = TOOL_ARGS_OVERRIDES.get(getattr(tool, "name", ""), {})
    if override:
        return dict(override), []
    for name in props:
        if name in FIXTURES:
            args[name] = FIXTURES[name]
        elif name in required:
            missing.append(name)
    return args, missing


def main() -> int:
    ap = argparse.ArgumentParser(description="小蜗工具逐个自检")
    ap.add_argument("--only", nargs="*", default=[], help="只测这些工具名")
    ap.add_argument("--timeout", type=float, default=45.0, help="单个工具超时秒数")
    args = ap.parse_args()

    _bootstrap()
    tools = _load_tools()
    if args.only:
        tools = [t for t in tools if t.name in set(args.only)]
    print(f"[工具] 共 {len(tools)} 个 · 超时 {args.timeout:.0f}s · 演示学号 {DEMO_STUDENT}\n")

    rows = []
    for tool in tools:
        call_args, missing = _args_for(tool)
        if missing:
            rows.append((tool.name, "SKIP", 0, f"夹具缺必填: {', '.join(missing)}"))
            continue
        started = time.time()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(tool.invoke, call_args).result(timeout=args.timeout)
            cost = int((time.time() - started) * 1000)
            text = str(result).replace("\n", " ")
            rows.append((tool.name, "EMPTY" if _is_empty(result) else "OK", cost, text[:104]))
        except concurrent.futures.TimeoutError:
            rows.append((tool.name, "ERR", int((time.time() - started) * 1000), f"超时 >{args.timeout:.0f}s"))
        except Exception as exc:  # noqa: BLE001
            rows.append((tool.name, "ERR", int((time.time() - started) * 1000),
                         f"{type(exc).__name__}: {str(exc)[:88]}"))

    print(f"{'工具':<30}{'状态':<8}{'耗时':>8}  摘要")
    print("-" * 132)
    for name, state, cost, note in rows:
        print(f"{name:<30}{state:<8}{cost:>6}ms  {note}")
    stat: dict[str, int] = {}
    for _, state, _, _ in rows:
        stat[state] = stat.get(state, 0) + 1
    print(f"\n汇总: {stat}")
    if stat.get("ERR"):
        print("⚠️ 存在 ERR：需要修工具或夹具")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
