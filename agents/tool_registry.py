"""
小蜗 — Tool 注册表
tool_name → tool 函数映射（Phase 2b 起独立成模块，原寄居在 legacy agents/executor.py）。
延迟导入避免循环依赖。
"""
from __future__ import annotations


def _build_tool_registry() -> dict:
    from tools.faq_tools import search_faq, get_faq_categories
    from tools.course_tools import (
        query_schedule, query_daily_schedule, find_empty_room, query_grade, calc_gpa, query_exam,
        search_courses, get_semester_list,
        query_course_selection, query_program, search_all_lessons,
    )
    from tools.advisor_tools import (
        collect_preferences, recommend_courses, compare_courses, analyze_teacher,
    )
    from tools.program_tools import (
        get_my_program, get_program_progress, plan_semester,
    )
    from tools.schedule_tools import (
        add_event, get_day_view, get_week_view, check_conflict, import_schedule,
    )
    from tools.selection_tools import (
        check_course_conflict, evaluate_selection_pressure,
    )
    from tools.link_tools import render_link
    from tools.activity_tools import query_activities

    registry = {
        "search_faq": search_faq,
        "get_faq_categories": get_faq_categories,
        "query_schedule": query_schedule,
        "query_daily_schedule": query_daily_schedule,
        "find_empty_room": find_empty_room,
        "query_grade": query_grade,
        "calc_gpa": calc_gpa,
        "query_exam": query_exam,
        "search_all_lessons": search_all_lessons,
        "search_courses": search_courses,
        "get_semester_list": get_semester_list,
        "query_course_selection": query_course_selection,
        "query_program": query_program,
        "collect_preferences": collect_preferences,
        "recommend_courses": recommend_courses,
        "compare_courses": compare_courses,
        "analyze_teacher": analyze_teacher,
        "get_my_program": get_my_program,
        "get_program_progress": get_program_progress,
        "plan_semester": plan_semester,
        "add_event": add_event,
        "get_day_view": get_day_view,
        "get_week_view": get_week_view,
        "check_conflict": check_conflict,
        "import_schedule": import_schedule,
        "check_course_conflict": check_course_conflict,
        "evaluate_selection_pressure": evaluate_selection_pressure,
        "render_link": render_link,
        "query_activities": query_activities,
    }

    # P4-1 生态工具：Spec 驱动自动注册（tools/ecosystem/，eco: 前缀）；
    # 加载失败只拒载单个工具，不影响内置注册表
    try:
        from tools.ecosystem import load_ecosystem_tools
        registry.update(load_ecosystem_tools())
    except Exception as e:  # noqa: BLE001
        print(f"[tool_registry] 生态工具加载失败（忽略）: {e}")

    return registry
