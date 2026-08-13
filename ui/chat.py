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
            ["自动判断", "智能问答", "课业助手", "选课顾问", "日程管理", "培养方案"],
            index=0,
            help="仅作参考，小蜗会自主判断你的问题；培养方案为独立页面",
        )

        st.markdown("---")

        if st.button("🗑️ 清空对话", use_container_width=True):
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
            if st.button("🔄 重新登录", use_container_width=True):
                _logout()
                st.rerun()
            return

        st.success(f"👤 {user.get('name', user['id'])}")
        st.caption(f"学号: {user['id']}")
        if user.get("major"):
            st.caption(f"专业: {user['major']}")
        st.caption("🟢 已连接教务系统")
        if st.button("🔓 退出登录", use_container_width=True):
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


def _do_login(student_id: str, password: str):
    """执行 CAS 登录"""
    from services.service_container import ServiceContainer

    sc = ServiceContainer()
    with st.spinner("正在登录教务系统..."):
        try:
            success = sc.login(student_id, password)
            if success:
                # 登录成功 → 获取学生基本信息
                info = sc.cas_client.get_student_info() or {}
                st.session_state["user"] = {
                    "id": student_id,
                    "name": info.get("name", student_id),
                    "major": info.get("major", ""),
                    "grade": info.get("grade", ""),
                    "logged_in_at": time.time(),
                }
                st.toast(f"✅ 登录成功！欢迎 {info.get('name', student_id)}")
                st.rerun()
            else:
                st.error("❌ 学号或密码错误，请重试")
        except Exception as e:
            st.error(f"❌ 登录失败: {e}")


def _logout():
    """退出登录"""
    from services.service_container import ServiceContainer

    st.session_state.pop("user", None)
    ServiceContainer.reset()
    # 清除 Agent 缓存（因为登录后 Agent 可能持有旧 session）
    for key in ["faq", "course", "advisor", "schedule"]:
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
        success = sc.login_with_ticket(ticket)
        if success:
            info = sc.cas_client.get_student_info() or {}
            username = sc.cas_client.student_id or ""
            st.session_state["user"] = {
                "id": username,
                "name": info.get("name", username),
                "major": info.get("major", ""),
                "grade": info.get("grade", ""),
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


def add_assistant_message(content: str):
    """添加助手消息到对话历史"""
    now = datetime.now().strftime("%H:%M")
    st.session_state.messages.append({
        "role": "assistant",
        "content": content,
        "timestamp": now,
    })


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