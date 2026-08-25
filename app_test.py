"""
小蜗 — Streamlit 测试入口。

运行: streamlit run app_test.py

页面与问答逻辑完全复用 app.py，仅在服务初始化后加载个人数据备份并模拟登录。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

from app import main
from services.service_container import ServiceContainer
from utils.logger import get_logger

log = get_logger("xiaowo.app_test")


def _init_test_mode(container: ServiceContainer) -> None:
    """加载个人数据备份并模拟登录，仅供测试入口使用。"""
    test_path = (
        Path(__file__).resolve().parent
        / "scripts"
        / "data"
        / "xiaowo_personal"
        / "data.json"
    )
    temp_dir = os.environ.get("TEMP")
    if not test_path.exists() and temp_dir:
        fallback = Path(temp_dir) / "xiaowo_personal" / "data.json"
        if fallback.exists():
            test_path = fallback
    if not test_path.exists():
        st.error(
            f"测试数据缺失: {test_path}\n"
            "请先运行 scripts/rebuild_testdata.py（或 crawl_personal.py）生成备份后再启动测试版。"
        )
        return

    try:
        test_data = json.loads(test_path.read_text(encoding="utf-8"))
        if st.session_state.get("user") is None:
            user = dict(test_data["user"])
            user.setdefault("logged_in_at", time.time())
            user.setdefault("profile_source", "test_backup")
            st.session_state["user"] = user
            for key in ("profile_major", "profile_grade", "program_year_select"):
                st.session_state.pop(key, None)

        student_id = test_data["user"]["id"]
        from services.session_ctx import reset_student, set_student
        from tools import course_tools

        token = set_student(student_id)
        try:
            client = container.cas_client
            client._logged_in = True
            client._student_id = student_id

            client.inject_program_tree(test_data["program_tree"])
            course_tools.set_offline_mode(True)
        finally:
            reset_student(token)

        has_grades = container.db.query_one(
            "SELECT COUNT(*) AS cnt FROM student_grades WHERE student_id = ?",
            (student_id,),
        ) or {}
        if not has_grades.get("cnt"):
            course_tools._sync_grades_to_db(student_id, test_data.get("grades") or [])

        has_courses = container.db.query_one(
            "SELECT COUNT(*) AS cnt FROM student_courses WHERE student_id = ?",
            (student_id,),
        ) or {}
        if not has_courses.get("cnt"):
            course_tools._sync_courses_to_db(
                student_id,
                test_data.get("courses") or [],
                test_data.get("semester") or "",
            )
        st.sidebar.caption(f"🧪 测试模式 | 数据: {test_path.name}")
    except Exception as exc:  # noqa: BLE001 - 测试入口须将任意初始化失败渲染到页面
        log.warning(f"测试模式初始化失败: {exc}")
        st.error(f"测试模式初始化失败: {exc}")


if __name__ == "__main__":
    main(startup_hook=_init_test_mode)
