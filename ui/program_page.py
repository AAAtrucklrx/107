# -*- coding: utf-8 -*-
"""
program_page.py — 培养方案三合一页面（我的方案 / 学期规划 / 进度概览）

数据源（tools/program_tools.py 三工具，均支持 personal_tree 参数）：
- get_my_program       → 我的方案（按模块分组的课程清单）
- plan_semester        → 学期规划（按方案 term 排出第 N 学年课程）
- get_program_progress → 进度概览（已修必修/缺口/模块进度）

数据准备：
- 已登录：从 st.session_state.user 取 major/grade，读 student_grades 已修课程，
  并经 ServiceContainer.cas_client.get_my_program_tree() 拉取个人方案树传给三工具；
  拉取失败/未登录自动降级到全量库。
- 未登录：用画像 major/grade（若会话有）+ 全量库方案；进度区提示「登录后查看个人进度」。

页面使用 Streamlit 原生组件，不引入外部依赖。
"""

from __future__ import annotations

import streamlit as st

from tools.program_tools import get_my_program, get_program_progress, plan_semester

_LOGIN_PROMPT = "🔒 先登录或填写专业年级后查看个人培养方案"


def _get_user_context():
    """从 session 取用户上下文，返回 (major, grade, taken_courses, taken_credits, is_logged_in)。

    已登录：major/grade 取用户资料，已修课程读 student_grades 表；
    未登录：仅取画像 major/grade（可能为空），无已修课程。
    """
    user = st.session_state.get("user")
    if user:
        major = user.get("major", "")
        grade = user.get("grade", "")
        taken_courses, taken_credits = _load_taken(user.get("id", ""))
        return major, grade, taken_courses, taken_credits, True
    return "", "", [], [], False


def _load_taken(student_id: str):
    """从 student_grades 表读取已修课程（课程名 + 学分），失败返回空。"""
    if not student_id:
        return [], []
    try:
        from services.service_container import ServiceContainer
        rows = ServiceContainer().db.query(
            "SELECT course_name, credits FROM student_grades WHERE student_id = ?",
            (student_id,),
        )
        courses = [r["course_name"] for r in rows if r.get("course_name")]
        credits = [r.get("credits") or 0.0 for r in rows if r.get("course_name")]
        return courses, credits
    except Exception:
        return [], []


def _pull_personal_tree(container) -> dict | list | None:
    """登录后经 ServiceContainer 拉取个人方案树；未登录/失败返回 None（降级全量库）。"""
    try:
        if not container.has_cas():
            return None
        tree = container.cas_client.get_my_program_tree()
        if isinstance(tree, dict) and "error" in tree:
            return None
        return tree
    except Exception:
        return None


def _major_grade_from_profile() -> tuple:
    """未登录降级：尝试从会话画像（如 selectbox 等已填画像）取 major/grade。"""
    major = st.session_state.get("profile_major", "")
    grade = st.session_state.get("profile_grade", "")
    return major, grade


def _program_options() -> tuple:
    """从全量库读专业/年级选项（programs 表去重），失败返回空列表。"""
    try:
        from tools.program_tools import COURSE_DB
        import sqlite3
        conn = sqlite3.connect(str(COURSE_DB), timeout=5)
        try:
            majors = [r[0] for r in conn.execute(
                "SELECT DISTINCT name FROM programs WHERE name != '' ORDER BY name")]
            grades = [r[0] for r in conn.execute(
                "SELECT DISTINCT grade FROM programs WHERE grade != '' ORDER BY grade DESC")]
            return majors, grades
        finally:
            conn.close()
    except Exception:
        return [], []


def _term_sort_key(c):
    """缺口清单按学期紧迫度排序：无前缀（未标注）置前 → 带学年前缀的按学年号升序。"""
    import re
    m = re.match(r"\s*(\d)", c.get("term") or "")
    return (0, 0) if not m else (1, int(m.group(1)))


# ── 各子页 ─────────────────────────────────────────

def _render_my_program(program: dict):
    """我的方案：方案名 + 模块构成 + 按模块分组的课程表。"""
    if not program or not program.get("courses"):
        st.info("未能定位到培养方案。")
        return

    st.markdown(f"### 📜 {program.get('name') or '个人培养方案'}")
    meta = []
    if program.get("college"):
        meta.append(f"{program['college']}")
    if program.get("grade"):
        meta.append(f"{program['grade']}")
    if program.get("totalCredits") is not None:
        meta.append(f"总学分 {program['totalCredits']}")
    if meta:
        st.caption(" · ".join(meta))

    # 模块构成
    if program.get("modules"):
        with st.expander("📂 模块构成", expanded=False):
            cols = st.columns(min(4, max(1, len(program["modules"]))))
            for i, m in enumerate(program["modules"]):
                with cols[i % len(cols)]:
                    st.markdown(f"**{m['category'] or '未分类'}**")
                    st.caption(f"{m['course_count']} 门 · {m['required_credits']} 学分")

    # 按模块分组课程表
    by_cat: dict[str, list] = {}
    for c in program["courses"]:
        by_cat.setdefault(c.get("category") or "未分类", []).append(c)
    for cat, courses in sorted(by_cat.items()):
        st.markdown(f"#### {cat}")
        st.dataframe(
            [{
                "课程名": c["name"],
                "代码": c["code"],
                "学分": c["credit"] if c["credit"] is not None else "",
                "必修": c["required"],
                "学期": c["term"],
            } for c in courses],
            use_container_width=True,
            hide_index=True,
        )


def _render_semester_plan(program: dict, major: str, grade: str, tree, year_index: int):
    """学期规划：选第 N 学年，展示高中秋/春课程表 + 总学分；未标注课程单独列出。"""
    try:
        plan = plan_semester.invoke(
            {"major": major, "grade": grade, "year_index": year_index,
             "personal_tree": tree},
        )
    except Exception:
        st.error("学期规划加载失败，请稍后重试。")
        return

    if not plan.get("terms"):
        st.info(f"第 {year_index} 学年暂无课程安排。")
        return

    st.markdown(f"### 🗓 第 {year_index} 学年培养计划")
    st.caption(f"总学分 {plan.get('total_credits', 0)}（先修要求以培养方案为准）")

    for t in plan["terms"]:
        courses = t["courses"]
        tag = t["term"]
        label = {"未标注": "📌 未标注学期的课程"}.get(tag, f"**{tag}** 学期")
        with st.expander(f"{label}（{len(courses)} 门）", expanded=True):
            st.dataframe(
                [{
                    "课程名": c["name"],
                    "代码": c["code"],
                    "学分": c["credit"] if c["credit"] is not None else "",
                    "必修": c["required"],
                    "模块": c["category"],
                } for c in courses],
                use_container_width=True,
                hide_index=True,
            )


def _render_progress(progress: dict, is_logged_in: bool):
    """进度概览：已修必修/总数、缺口清单、各模块进度。"""
    if not is_logged_in:
        st.info("🔒 登录后查看个人进度。")
        return
    if not progress:
        st.info("暂无进度数据。")
        return

    # 顶部总进度条
    credits_taken = progress.get("credits_taken", 0)
    credits_required = progress.get("credits_required", 0)
    percent = progress.get("percent", 0)
    st.markdown(f"### 📊 培养进度")
    st.caption(
        f"已修必修 **{progress.get('required_taken', 0)}**/{progress['required_total']} 门"
        f"（{credits_taken}/{credits_required} 学分）"
    )
    st.progress(min(max(percent, 0.0), 100.0) / 100.0, text=f"{percent}%")

    remaining = progress.get("required_remaining") or []

    col1, col2 = st.columns([3, 2])
    with col1:
        st.markdown("#### ⚠️ 必修缺口")
        if remaining:
            remaining_sorted = sorted(remaining, key=_term_sort_key)
            st.dataframe(
                [{
                    "课程名": c["name"],
                    "代码": c["code"],
                    "学分": c["credit"] if c["credit"] is not None else "",
                    "学期": c["term"],
                    "模块": c["category"],
                } for c in remaining_sorted],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.success("🎉 必修课程已全部修完！")

    with col2:
        st.markdown("#### 📦 模块进度")
        for m in progress.get("modules_progress") or []:
            total = m["total"]
            taken = m["taken"]
            pct = round(taken / total * 100) if total else 0
            st.markdown(f"**{m['category'] or '未分类'}**（{taken}/{total}）")
            st.progress(pct / 100.0)


# ── 主入口 ─────────────────────────────────────────

def render_program_page(container) -> None:
    """渲染培养方案三合一页面（由 app.py 在选中「培养方案」模块时调用）。"""
    major, grade, taken_courses, taken_credits, is_logged_in = _get_user_context()

    # 未登录降级：用画像 major/grade（若会话有），否则提供全量库专业/年级选择器
    if not major:
        pm, pg = _major_grade_from_profile()
        major, grade = pm, pg
    if not major:
        majors, grades = _program_options()
        if not majors:
            st.info(_LOGIN_PROMPT)
            return
        st.caption("🔒 未登录：展示全量库培养方案预览（登录后自动切换为个人方案）")
        col_m, col_g = st.columns([3, 1])
        with col_m:
            major = st.selectbox("专业", majors, key="profile_major")
        with col_g:
            grade = st.selectbox("年级", grades,
                                 index=min(2, len(grades) - 1) if grades else 0,
                                 key="profile_grade")

    # 拉取个人方案树（登录/未登录均可能为 None → 降级全量库）
    tree = _pull_personal_tree(container) if is_logged_in else None

    # 顶部方案名 + 进度概览条
    try:
        program = get_my_program.invoke(
            {"major": major, "grade": grade, "personal_tree": tree})
    except Exception:
        st.error("培养方案加载失败，请稍后重试。")
        return

    if not program or not program.get("courses"):
        st.info("未能定位到培养方案，请确认专业名称后重试。")
        return

    # 顶部：方案名 + 年级 + 进度条
    st.markdown(f"### 📜 {program.get('name') or '个人培养方案'}")
    meta = [x for x in [program.get("college"), program.get("grade")] if x]
    if meta:
        st.caption(" · ".join(meta))

    try:
        progress = get_program_progress.invoke(
            {"major": major, "grade": grade, "taken_courses": taken_courses,
             "taken_credits": taken_credits, "personal_tree": tree},
        )
    except Exception:
        progress = None

    if progress and progress.get("credits_required"):
        percent = progress["percent"]
        st.progress(min(max(percent, 0.0), 100.0) / 100.0,
                    text=f"已获学分 {progress['credits_taken']}/{progress['credits_required']}"
                         f"（{percent}%）")
    elif not is_logged_in:
        st.info("🔒 登录后可查看个人培养进度。")

    tabs = st.tabs(["我的方案", "学期规划", "进度概览"])
    with tabs[0]:
        _render_my_program(program)
    with tabs[1]:
        years = min(6, max(1, _estimate_years(program.get("courses") or [])))
        year_index = st.selectbox("选择学年", list(range(1, years + 1)),
                                  format_func=lambda y: {1: "大一", 2: "大二", 3: "大三",
                                                        4: "大四", 5: "大五", 6: "大六"}.get(y, f"第{y}年"),
                                  key="program_year_select")
        _render_semester_plan(program, major, grade, tree, int(year_index))
    with tabs[2]:
        _render_progress(progress, is_logged_in)


def _estimate_years(courses: list) -> int:
    """按方案课程里最大学年号估算总学年数（1-4+），缺省 4。"""
    import re
    max_year = 0
    for c in courses:
        m = re.match(r"\s*(\d)", c.get("term") or "")
        if m:
            max_year = max(max_year, int(m.group(1)))
    return max(4, max_year)