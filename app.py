"""
小蜗 — 科大校园全能智能助手
Streamlit 主应用入口

运行: streamlit run app.py

v3.0: 统一 QA LangGraph 架构
- 消息处理 → run_qa: 意图识别 → 工具调用 → 综合回答
"""

import sys
from datetime import date
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

from config import (DATABASE_PATH, SCHEMA_PATH, KNOWLEDGE_DATA_DIR,
                   CHROMA_PERSIST_DIR, YOUNG_TOKEN, YOUNG_PAGE_SIZE)
from database.seed_data import SEED_SQL
from services.service_container import ServiceContainer
from utils.logger import get_logger

log = get_logger("xiaowo.app")


# ── 初始化（Streamlit 缓存） ────────────────────────

@st.cache_resource
def init_services() -> ServiceContainer:
    """初始化全局服务容器（数据库 + 知识库）"""
    container = ServiceContainer()
    container.init_database(DATABASE_PATH, SCHEMA_PATH, seed_sql=SEED_SQL)
    container.init_vector_store(CHROMA_PERSIST_DIR, knowledge_data_dir=KNOWLEDGE_DATA_DIR)
    return container


def process_query(user_input: str, selected_module: str) -> dict:
    """
    处理用户查询的核心流程（v3.0）：
    统一 QA LangGraph — 意图识别 → 工具调用 → 综合回答
    """
    student_id = ""
    user_profile = {"logged_in": False}

    user = st.session_state.get("user")
    if user:
        student_id = user["id"]
        user_profile = {
            "name": user.get("name", ""),
            "major": user.get("major", ""),
            "grade": user.get("grade", ""),
            "logged_in": True,
        }

    from agents.qa.graph import run_qa
    # 多轮记忆：把最近对话历史传给 QA 链路（含当前问题），供指代理解
    chat_history = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                    for m in (st.session_state.get("messages") or [])][-20:]
    result = run_qa(user_input, module_signal=selected_module,
                    student_id=student_id, user_profile=user_profile,
                    chat_history=chat_history)
    if result.get("error"):
        log.warning(f"QA 流程提示: {result['error']}")
    answer = (result.get("answer") or result.get("clarify_question")
              or "抱歉，我暂时无法回答这个问题。")
    # P5-1 工具结果卡片：把原始 tool_results 透传给 UI，由 ui/chat 按工具抽卡片渲染（与 LLM 摘要解耦）
    return {"answer": answer, "tool_results": result.get("tool_results") or []}


def maybe_show_activity_recommendation():
    """
    登录用户：拉取校团委活动 → 推荐引擎（课表空闲匹配 + MMR）→ 每天最多一次弹窗。
    未配置 YOUNG_TOKEN 或未登录时静默跳过。
    """
    user = st.session_state.get("user")
    if not user or not YOUNG_TOKEN:
        return
    attempt_key = (user["id"], date.today().isoformat())
    if st.session_state.get("_activity_recommendation_attempt") == attempt_key:
        return
    st.session_state["_activity_recommendation_attempt"] = attempt_key
    from ui.activity_dialog import shown_today, mark_shown, show_activity_dialog
    if shown_today(user["id"]):
        return
    try:
        from services.young_client import YoungService
        from services.activity_recommender import recommend, FreeTimeMatcher
        container = init_services()
        activities = YoungService.from_token(YOUNG_TOKEN).fetch_enrolment_activities(
            page_size=YOUNG_PAGE_SIZE)
        if not activities:
            return
        matcher = FreeTimeMatcher.from_db(container.db, user["id"])
        # P4-C：四因子个性化推荐（平台标签冷启动 + 行为权重）+ shown 埋点
        from services.activity_profile import behavior_weights, get_profile, record_interaction
        profile = get_profile(container.db, user["id"])
        pw = behavior_weights(container.db, user["id"])
        recs = recommend(activities, matcher=matcher,
                         personal_profile=profile, personal_weights=pw)
        for r_ in recs:
            try:
                record_interaction(container.db, user["id"], r_["activity"], "shown")
            except Exception:
                pass
        mark_shown(user["id"])
        show_activity_dialog(recs, activities)
    except Exception as e:
        log.warning(f"校团委活动推荐失败: {e}")


def maybe_show_morning_brief():
    """
    P5-2 晨报：登录用户每日首次打开时聚合今日日程/今日考试/近期待办并弹窗一次。
    独立于活动弹窗去重；任何异常只降级记日志，不打断主流程。
    """
    user = st.session_state.get("user")
    if not user:
        return
    try:
        from ui.activity_dialog import (
            morning_brief_shown_today, mark_morning_brief_shown, show_morning_brief_dialog)
        from services.morning_brief import build_morning_brief
        if morning_brief_shown_today(user["id"]):
            return
        brief = build_morning_brief(user["id"])
        if not (brief.get("sections") or []):
            mark_morning_brief_shown(user["id"])
            return
        mark_morning_brief_shown(user["id"])
        show_morning_brief_dialog(brief)
    except Exception as e:
        log.warning(f"晨报展示失败: {e}")


# ============================================================
# Streamlit 主页面
# ============================================================
def main(startup_hook=None):
    """渲染应用；测试入口可通过 startup_hook 注入离线登录数据。"""
    from ui.chat import init_page, apply_custom_css, render_sidebar, render_chat_area, add_assistant_message, check_cas_callback

    init_page()
    apply_custom_css()

    # 初始化服务
    container = init_services()

    # ── CAS 重定向回调处理：检查 URL 中是否有 ticket 参数 ──
    check_cas_callback()

    if startup_hook is not None:
        startup_hook(container)

    # 页面标题
    st.markdown(
        '<div style="text-align:center; padding:10px;">'
        '<h1 style="color:#003D7C; margin:0;">🐌 小蜗 · 科大校园智能助手</h1>'
        '<p style="color:#666; margin:0;">你好，我是小蜗 🐌，今天有什么可以帮你？</p>'
        '</div>',
        unsafe_allow_html=True,
    )

    # 系统状态
    faq_count = container.faq_store.count()
    course_count = container.db.query_one("SELECT COUNT(*) as cnt FROM student_courses") or {"cnt": 0}

    # 侧边栏
    selected_module = render_sidebar()

    # ── 培养方案模块：主区渲染三合一页面（不走聊天流程） ──
    if selected_module == "培养方案":
        from ui.program_page import render_program_page
        render_program_page(container)
        maybe_show_activity_recommendation()
        user = st.session_state.get("user")
        faq_count = container.faq_store.count()
        if user:
            status_text = f"知识库: {faq_count} 篇文档 | 已登录: {user['name']} ({user['id']}) | 架构: QA LangGraph v3.0"
        else:
            status_text = f"知识库: {faq_count} 篇文档 | 未登录 | 架构: QA LangGraph v3.0"
        st.markdown("---")
        st.caption(status_text)
        return

    # ── 校园导航模块：官方工具/网站跳转一览（不走聊天流程） ──
    if selected_module == "校园导航":
        from ui.links_page import render_links_page
        render_links_page()
        return

    # 对话区域
    prompt = render_chat_area()

    # ── 培养方案页「问问小蜗」联动：pending_query 作为普通用户提问处理 ──
    pending = st.session_state.pop("pending_query", None)
    if pending:
        from datetime import datetime
        st.session_state.messages.append({
            "role": "user", "content": pending,
            "timestamp": datetime.now().strftime("%H:%M"),
        })
        with st.spinner("小蜗正在思考..."):
            _r = process_query(pending, selected_module)
        add_assistant_message(_r["answer"], tool_results=_r["tool_results"])
        st.rerun()

    # 处理用户输入
    if prompt:
        with st.spinner("小蜗正在思考..."):
            _r = process_query(prompt, selected_module)
        add_assistant_message(_r["answer"], tool_results=_r["tool_results"])
        st.rerun()

    # 空状态引导
    if not st.session_state.get("messages"):
        st.markdown("---")
        st.markdown("### 💡 你可以这样问我：")

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("**📚 智能问答**")
            for example in [
                "学生证丢了怎么补办？",
                "图书馆几点关门？",
                "大研计划什么时候申请？",
            ]:
                st.caption(f"  • {example}")

            st.markdown("**📊 课业助手**")
            for example in [
                "我这周有什么课？",
                "帮我算一下上学期GPA",
                "今天下午三教有空教室吗？",
            ]:
                st.caption(f"  • {example}")

        with col2:
            st.markdown("**🔍 选课顾问**")
            for example in [
                "推荐几门适合大二的AI方向选修课",
                "邵帅老师教课怎么样？",
                "对比一下机器学习和深度学习",
            ]:
                st.caption(f"  • {example}")

            st.markdown("**📅 日程管理**")
            for example in [
                "今天有什么事？",
                "下周三下午3点开组会",
                "这周忙不忙？",
            ]:
                st.caption(f"  • {example}")

        # 复杂查询示例
        st.markdown("---")
        st.markdown("**🧠 试试复杂查询**")
        for example in [
            "帮我查一下GPA，然后根据我的成绩推荐适合的课程",
            "我的课表怎么样，帮我分析一下选课策略",
        ]:
            st.caption(f"  • {example}")

    # ── P5-2 晨报弹窗（每天最多一次，独立于活动弹窗） ──
    maybe_show_morning_brief()

    # ── 校团委活动推荐弹窗（每天最多一次） ──
    maybe_show_activity_recommendation()

    # 状态栏
    st.markdown("---")
    user = st.session_state.get("user")
    if user:
        status_text = f"知识库: {faq_count} 篇文档 | 已登录: {user['name']} ({user['id']}) | 架构: QA LangGraph v3.0"
    else:
        status_text = f"知识库: {faq_count} 篇文档 | 未登录 | 架构: QA LangGraph v3.0"
    st.caption(status_text)


if __name__ == "__main__":
    main()
