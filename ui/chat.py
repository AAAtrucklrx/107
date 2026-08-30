"""
小蜗 — Streamlit UI 组件
侧边栏、对话界面、自定义样式

v2.1: 新增登录面板，支持 CAS 统一认证
"""

import time
import streamlit as st
from datetime import datetime


def init_page():
    """初始化页面配置"""
    st.set_page_config(
        page_title="小蜗 - 科大校园智能助手",
        page_icon="🐌",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def apply_custom_css():
    """注入自定义 CSS 样式"""
    st.markdown("""
    <style>
    /* 主色调 */
    :root {
        --ustc-blue: #003D7C;
        --light-blue: #E8F0FE;
        --green: #2E7D32;
        --orange: #ED6C02;
        --red: #D32F2F;
    }

    /* 页面标题 */
    .main-header {
        color: #003D7C;
        font-size: 28px;
        font-weight: bold;
        text-align: center;
        padding: 10px 0;
    }

    /* 用户消息气泡（右侧浅蓝） */
    [data-testid="stChatMessage"] [data-testid="stChatMessageContent"]:has(+ .user) {
        background-color: #E8F0FE;
    }

    /* 时间戳 */
    .timestamp {
        color: #999;
        font-size: 12px;
        margin-top: 4px;
    }

    /* 自定义侧边栏 */
    .sidebar-footer {
        position: fixed;
        bottom: 10px;
        font-size: 12px;
        color: #999;
    }
    </style>
    """, unsafe_allow_html=True)


def render_sidebar() -> str:
    """
    渲染侧边栏，返回用户选择的模块。

    Returns:
        模块名: "自动判断" | "智能问答" | "课业助手" | "选课顾问" | "日程管理" | "培养方案"
    """
    with st.sidebar:
        st.markdown("### 🐌 小蜗")

        # ── 登录状态面板 ──
        _render_login_panel()

        st.markdown("---")

        module = st.radio(
            "模块切换",
            ["自动判断", "智能问答", "课业助手", "选课顾问", "日程管理", "培养方案", "校园导航"],
            index=0,
            help="仅作参考，小蜗会自主判断你的问题；培养方案/校园导航为独立页面",
            key="module_switch",  # 固定 key：支持培养方案页「问问小蜗」按钮程序化切模块
        )

        st.markdown("---")

        if st.button("🗑️ 清空对话", width="stretch"):
            st.session_state.messages = []
            st.rerun()

        st.markdown("---")

        # 对话历史简要
        st.caption("最近对话")
        if "messages" in st.session_state and st.session_state.messages:
            for i, msg in enumerate(st.session_state.messages[-6:]):
                if msg["role"] == "user":
                    preview = msg["content"][:20] + ("..." if len(msg["content"]) > 20 else "")
                    st.caption(f"💬 {preview}")

        st.markdown("---")
        user = st.session_state.get("user")
        if user:
            st.caption(f"版本: v2.1 | 已登录: {user['id']}")
        else:
            st.caption(f"版本: v2.1 | 未登录")

    return module


def _render_login_panel():
    """登录/用户状态面板 — 支持 CAS 重定向登录"""
    user = st.session_state.get("user")

    if user:
        # ── 已登录：显示用户信息 + 登出按钮 ──
        elapsed = time.time() - user.get("logged_in_at", 0)
        if elapsed > 3600:
            st.warning("⏰ 登录已过期，请重新登录")
            if st.button("🔄 重新登录", width="stretch"):
                _logout()
                st.rerun()
            return

        st.success(f"👤 {user.get('name', user['id'])}")
        st.caption(f"学号: {user['id']}")
        if user.get("major"):
            st.caption(f"专业: {user['major']}")
        if user.get("grade"):
            st.caption(f"年级: {user['grade']}")
        st.caption("🟢 已连接教务系统")
        if st.button("🔓 退出登录", width="stretch"):
            _logout()
            st.rerun()
    else:
        # ── 未登录：CAS 统一认证跳转 ──
        st.info("🔒 登录后可查看个人课表、成绩等")

        from services.cas_client import CASClient
        cas = CASClient()
        login_url = cas.get_login_url()
        st.markdown(
            f'<a href="{login_url}" target="_self" '
            f'style="display:block; text-align:center; padding:12px 16px; '
            f'background:#003D7C; color:white; border-radius:8px; '
            f'text-decoration:none; font-weight:bold; margin:8px 0;">'
            f'🔑 科大统一身份认证登录</a>',
            unsafe_allow_html=True,
        )
        st.caption("点击后跳转到科大 CAS 认证页面 (id.ustc.edu.cn)")


def _clear_program_preview_state() -> None:
    """清理匿名预览选择，防止其被下一位登录用户继承。"""
    for key in ("profile_major", "profile_grade", "program_year_select"):
        st.session_state.pop(key, None)


def _logout():
    """退出登录"""
    from services.service_container import ServiceContainer

    user = st.session_state.pop("user", None) or {}
    student_id = user.get("id")
    ServiceContainer().logout(student_id)
    try:
        from agents.qa.nodes import clear_personal_tree_cache
        clear_personal_tree_cache(student_id)
    except Exception:
        pass
    _clear_program_preview_state()
    for key in (
        "messages",
        "pending_query",
        "_activity_recommendation_attempt",
        "_cas_ticket_processed",
    ):
        st.session_state.pop(key, None)
    # 清除 URL 中可能残留的 ticket 参数
    try:
        st.query_params.clear()
    except Exception:
        pass
    st.toast("👋 已退出登录")


def check_cas_callback():
    """
    检查 CAS 重定向回调：如果 URL 中有 ticket 参数，尝试自动登录。
    应在 main() 启动时调用。
    """
    try:
        ticket = st.query_params.get("ticket")
    except Exception:
        return

    if not ticket:
        return

    # 防止重复处理
    if st.session_state.get("_cas_ticket_processed") == ticket:
        return

    st.session_state["_cas_ticket_processed"] = ticket

    from services.service_container import ServiceContainer
    sc = ServiceContainer()
    try:
        client = sc.authenticate_ticket(ticket)
        if client is not None:
            info = client.get_student_info() or {}
            username = client.student_id or ""
            _clear_program_preview_state()
            st.session_state["user"] = {
                "id": username,
                "name": info.get("name", username),
                "major": info.get("major", ""),
                "grade": info.get("grade", ""),
                "profile_source": info.get("profile_source", "cas_authenticated"),
                "logged_in_at": time.time(),
            }
            # 清除 URL 中的 ticket 参数
            st.query_params.clear()
            st.toast(f"✅ 登录成功！欢迎 {info.get('name', username)}")
            st.rerun()
        else:
            st.query_params.clear()
            st.error("❌ CAS 认证失败，请重试")
    except Exception as e:
        st.query_params.clear()
        st.error(f"❌ CAS 认证异常: {e}")


def render_chat_area():
    """渲染对话区域和输入框"""
    # 问候语
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # 显示历史消息
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            for card in (msg.get("cards") or []):
                rendered = _render_card(card)
                if rendered:
                    st.markdown(rendered)
            st.markdown(msg["content"])
            if "timestamp" in msg:
                st.caption(msg["timestamp"])

    # 输入框
    if prompt := st.chat_input("💬 输入你的问题...", key="chat_input"):
        # 添加用户消息
        now = datetime.now().strftime("%H:%M")
        st.session_state.messages.append({
            "role": "user",
            "content": prompt,
            "timestamp": now,
        })
        return prompt

    return None


# ── P5-1 工具结果卡片：UI 层从 tool_results 数据通道渲染，与 LLM 摘要解耦 ──
_CARD_TOOLS = ("recommend_courses", "compare_courses", "check_course_conflict", "get_program_progress")


def _extract_cards(tool_results):
    """从 tool_results（[{tool,status,result}]）取最后一张完成的卡片工具。"""
    if not tool_results:
        return None
    for entry in reversed(tool_results):
        tool = entry.get("tool")
        if tool in _CARD_TOOLS and entry.get("status") == "done" and isinstance(entry.get("result"), dict):
            return [{"type": tool, "data": entry["result"]}]
    return None


def _render_card(card):
    """把结构化工具结果渲染为 markdown 卡片；异常/缺数据返回 None 走纯文本降级。"""
    t = card["type"]
    d = card["data"]
    try:
        if t == "compare_courses":
            # advisor_tools.compare_courses → {course_a, course_b, comparison:{...}}
            a, b = d.get("course_a"), d.get("course_b")
            cmp_ = d.get("comparison") or {}
            if not a or not b:
                return None
            lines = ["| 维度 | {a} | {b} |".format(a=a.get("name", ""), b=b.get("name", "")),
                     "|---|---|---|"]
            for label, x, y in (
                ("评分", a.get("rating_avg", ""), b.get("rating_avg", "")),
                ("评价数", a.get("rate_count", ""), b.get("rate_count", "")),
                ("学分", a.get("credit", ""), b.get("credit", "")),
                ("开课", "、".join(a.get("terms") or []), "、".join(b.get("terms") or [])),
            ):
                lines.append(f"| {label} | {x} | {y} |")
            head = "### ⚖️ 课程对比\n"
            if cmp_.get("suggestion"):
                head += f"\n*{cmp_['suggestion']}*\n"
            return head + "\n".join(lines)

        if t == "get_program_progress":
            # program_tools.get_program_progress →
            #   {required_total, required_taken, percent,
            #    required_remaining:[{code,name,credit,term,category}], modules_progress:[...]}
            total = d.get("required_total") or 0
            done = d.get("required_taken") or 0
            pct = d.get("percent")
            if pct is None:
                pct = round(done * 100 / total) if total else 0
            pct = int(round(pct))
            bar = "#" * min(20, pct // 5) + "-" * (20 - min(20, pct // 5))
            head = f"### 🎯 培养方案进度 {done}/{total}（{pct}%）\n`{bar}`"
            gaps = d.get("required_remaining") or []
            if gaps:
                lines = ["\n**缺口清单：**"]
                for g in gaps:
                    k = f"[{g.get('category','')}]" if g.get("category") else ""
                    lines.append(f"- {g.get('name', '')}（{g.get('credit', '')}学分·{g.get('term', '?')}{k}）")
                return head + "\n" + "\n".join(lines[:25])
            return head

        if t == "check_course_conflict":
            # selection_tools.check_course_conflict →
            #   {courses, conflicts:[{course_a,course_b,day,a_time,b_time,reason,weeks_unknown}], conflict_count}
            confs = d.get("conflicts") or []
            missing = d.get("missing") or []
            incomplete = d.get("time_incomplete") or []
            total = int(d.get("total") or 0)
            head = "### ⚠️ 选课冲突检查\n"
            if not confs:
                if total == 0:
                    detail = d.get("message") or "没有可用于检测的课程数据，暂时无法判断冲突。"
                    if missing and not all(name in detail for name in missing):
                        detail += f"\n\n未找到：{'、'.join(missing)}"
                    return head + detail
                detail = f"已检查 {total} 门课程，未发现时间冲突。"
                if total < 2:
                    detail += " 当前数据不足两门课程，无法进行课程间对比。"
                if missing:
                    detail += f"\n\n未找到：{'、'.join(missing)}"
                if incomplete:
                    detail += f"\n\n时间信息不完整：{'、'.join(incomplete)}"
                return head + detail
            lines = []
            for c in confs:
                wu = "（周次未知，保守判定）" if c.get("weeks_unknown") else ""
                lines.append(f"- **{c.get('course_a', '')}** ⚔️ **{c.get('course_b', '')}** "
                             f"`{c.get('day', '?')}` {c.get('reason', '时间重叠')}{wu}")
            return head + "\n".join(lines)

        if t == "recommend_courses":
            # advisor_tools.recommend_courses →
            #   {recommendations, groups:{required,elective,exploratory}, total_candidates}
            body = "### 📚 选课推荐\n"
            groups = d.get("groups") or {}
            if d.get("source") == "exact_course":
                group_labels = (("课程班级", "required"),)
            else:
                group_labels = (("必修", "required"), ("方案内选修", "elective"),
                                ("方向补充", "exploratory"))
            for label, key in group_labels:
                items = groups.get(key) or []
                if not items:
                    continue
                body += f"\n**{label}组（{len(items)}门）**\n"
                for it in items:
                    teachers = "、".join(x.get("name", "") for x in (it.get("teachers") or [])[:2]) or "未知"
                    hint = (it.get("program_hint") or {}).get("program", "")
                    line = (f"- **{it.get('name', '')}**（{it.get('credit', '')}学分）| {teachers} | "
                            f"{it.get('rating_avg', '')}分·{it.get('rate_count', '')}评论")
                    if hint:
                        line += f" | {hint[:30]}"
                    body += line + "\n"
            limitations = d.get("limitations") or []
            if limitations:
                body += "\n" + "\n".join(f"> {text}" for text in limitations) + "\n"
            found = d.get("total_candidates")
            body += f"\n> 候选 {found if found is not None else 'N/A'} 门（本卡仅结构化概览，细则见正文回答）"
            return body
    except Exception:
        return None
    return None


def add_assistant_message(content: str, tool_results=None):
    """添加助手消息到对话历史（可选携带 tool_results 以渲染工具结果卡片）"""
    now = datetime.now().strftime("%H:%M:%S")
    msg = {"role": "assistant", "content": content, "timestamp": now}
    cards = _extract_cards(tool_results)
    if cards:
        msg["cards"] = cards
    st.session_state.messages.append(msg)


def show_thinking_indicator():
    """显示思考动画"""
    return st.status("小蜗正在思考...", expanded=False)


def show_tool_call(tool_name: str, status: str = "running"):
    """显示工具调用状态"""
    emoji_map = {
        "search_faq": "🔍",
        "query_schedule": "📅",
        "find_empty_room": "🏫",
        "query_grade": "📊",
        "calc_gpa": "🧮",
        "query_exam": "📝",
        "recommend_courses": "📚",
        "compare_courses": "⚖️",
        "analyze_teacher": "👨‍🏫",
        "add_event": "➕",
        "get_day_view": "📋",
        "get_week_view": "📆",
        "check_conflict": "⚠️",
        "import_schedule": "📥",
        "collect_preferences": "📋",
        "get_faq_categories": "📂",
    }
    emoji = emoji_map.get(tool_name, "🔧")
    return f"{emoji} `{tool_name}`..."
