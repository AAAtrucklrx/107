"""
小蜗 — 科大校园全能智能助手（专门测试版）
Streamlit 测试入口：内置模拟登录（绕过 CAS），启动即已登录个人测试账号。

运行: streamlit run app_test.py

与正式版 app.py 的唯一差异：main() 中内嵌「测试模式」代码块，
自动加载 %TEMP%\\xiaowo_personal\\data.json（个人数据爬取备份）并模拟登录。
正式功能代码与 app.py 完全一致。
"""

import sys
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


def process_query(user_input: str, selected_module: str) -> str:
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
    return (result.get("answer") or result.get("clarify_question")
            or "抱歉，我暂时无法回答这个问题。")


def maybe_show_activity_recommendation():
    """
    登录用户：拉取校团委活动 → 推荐引擎（课表空闲匹配 + MMR）→ 每天最多一次弹窗。
    未配置 YOUNG_TOKEN 或未登录时静默跳过。
    """
    user = st.session_state.get("user")
    if not user or not YOUNG_TOKEN:
        return
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
        recs = recommend(activities, matcher=matcher)
        mark_shown(user["id"])
        show_activity_dialog(recs, activities)
    except Exception as e:
        log.warning(f"校团委活动推荐失败: {e}")


def _init_test_mode(container: ServiceContainer) -> None:
    """测试模式：加载个人数据爬取备份，模拟登录（绕过 CAS），仅测试版使用"""
    import os as _os
    import json as _json
    import time as _time

    _test_path = Path(_os.environ.get("TEMP", "")) / "xiaowo_personal" / "data.json"
    if not _test_path.exists():
        st.error(f"测试数据缺失: {_test_path}\n请先运行 scripts/crawl_personal.py 生成备份后再启动测试版。")
        return
    try:
        _td = _json.loads(_test_path.read_text(encoding="utf-8"))
        if st.session_state.get("user") is None:
            _u = dict(_td["user"])
            _u.setdefault("logged_in_at", _time.time())  # 避免侧边栏误判登录过期
            st.session_state["user"] = _u
        if container._cas_client is None:
            container.init_cas_client()
        _c = container.cas_client
        _c._logged_in = True  # 伪登录：解锁 has_cas() 判定的个人数据查询
        _c._student_id = _td["user"]["id"]
        import services.cas_client as _cc
        _tree = _td["program_tree"]
        _cc.CASClient.get_my_program_tree = lambda self, _t=_tree: _t  # 注入个人方案树
        # 强制个人数据查询走 SQLite 降级（避免真发请求到 jw 造成超时等待）
        import tools.course_tools as _ct
        _ct._fetch_real_grades = lambda cas: None
        _ct._fetch_real_schedule = lambda cas, sem_id: None
        # 个人数据 seed 闭环：库中无该生成绩/课表时用爬取备份写入（幂等，不覆盖已有数据）
        _sid = _td["user"]["id"]
        if not (container.db.query_one("SELECT COUNT(*) AS cnt FROM student_grades WHERE student_id = ?", (_sid,)) or {}).get("cnt"):
            _ct._sync_grades_to_db(_sid, _td.get("grades") or [])
        if not (container.db.query_one("SELECT COUNT(*) AS cnt FROM student_courses WHERE student_id = ?", (_sid,)) or {}).get("cnt"):
            _ct._sync_courses_to_db(_sid, _td.get("courses") or [], _td.get("semester") or "")
        st.sidebar.caption(f"🧪 测试模式 | 数据: {_test_path.name}")
    except Exception as _e:
        log.warning(f"测试模式初始化失败: {_e}")
        st.error(f"测试模式初始化失败: {_e}")


# ============================================================
# Streamlit 主页面
# ============================================================
def main():
    from ui.chat import init_page, apply_custom_css, render_sidebar, render_chat_area, add_assistant_message, check_cas_callback

    init_page()
    apply_custom_css()

    # 初始化服务
    container = init_services()

    # ── CAS 重定向回调处理：检查 URL 中是否有 ticket 参数 ──
    check_cas_callback()

    # ── 测试模式（仅测试版）：模拟登录，绕过 CAS ──
    _init_test_mode(container)

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

    # 对话区域
    prompt = render_chat_area()

    # 处理用户输入
    if prompt:
        with st.spinner("小蜗正在思考..."):
            response = process_query(prompt, selected_module)
        add_assistant_message(response)
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
