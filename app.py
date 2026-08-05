"""
小蜗 — 科大校园全能智能助手
Streamlit 主应用入口

运行: streamlit run app.py

v2.0: 支持 Plan-and-Execute 架构
- 简单查询 → Router → 子Agent → Tool → 回答
- 复杂查询 → Router → Planner → Executor → 多步Tool → 综合回答
"""

import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

from config import (DATABASE_PATH, SCHEMA_PATH, KNOWLEDGE_DATA_DIR, DEMO_STUDENT,
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


def get_agent(agent_type: str):
    """根据路由结果创建对应的子 Agent（按类型缓存）"""
    if agent_type not in st.session_state:
        if agent_type == "faq":
            from agents.faq_agent import create_faq_agent
            st.session_state[agent_type] = create_faq_agent()
        elif agent_type == "course":
            from agents.course_agent import create_course_agent
            st.session_state[agent_type] = create_course_agent()
        elif agent_type == "advisor":
            from agents.advisor_agent import create_advisor_agent
            st.session_state[agent_type] = create_advisor_agent()
        elif agent_type == "schedule":
            from agents.schedule_agent import create_schedule_agent
            st.session_state[agent_type] = create_schedule_agent()
    return st.session_state.get(agent_type)


def get_context():
    """获取会话级 Context（跨多次查询保持）"""
    from agents.context import Context
    if "_context" not in st.session_state:
        st.session_state["_context"] = Context()
    return st.session_state["_context"]


def process_query(user_input: str, selected_module: str) -> str:
    """
    处理用户查询的核心流程（v2.1）：
    1. Router 判断路由 + 复杂度
    2. simple → 子Agent直接处理
    3. complex → Planner生成计划 → Executor逐步执行
    """
    from agents.router import route_query
    from agents.factory import invoke_agent

    route = route_query(user_input, selected_module)
    agent_type = route.get("agent", "faq")
    complexity = route.get("complexity", "simple")
    rewritten = route.get("rewritten_query", user_input)

    student_id = DEMO_STUDENT["id"]

    # ── 登录用户优先：用真实学号替换演示账号 ──
    user = st.session_state.get("user")
    if user:
        student_id = user["id"]

    # ── 复杂查询：Plan-and-Execute ──
    if agent_type == "planner" or complexity == "complex":
        return _process_complex(rewritten, student_id)

    # ── 简单查询：直接路由到子Agent ──
    full_query = f"当前学生ID: {student_id}\n"
    if user:
        full_query += f"学生姓名: {user.get('name', '')}\n"
        full_query += f"登录状态: 已连接教务系统，可以查询个人课表、成绩等\n"
    else:
        full_query += f"登录状态: 未登录，仅可查询公共数据\n"
    full_query += f"\n{rewritten}"

    try:
        agent = get_agent(agent_type)
        if agent is None:
            log.warning(f"Agent '{agent_type}' 未找到，降级到 FAQ")
            agent = get_agent("faq")
    except Exception as e:
        log.error(f"Agent 初始化失败: {e}")
        return f"❌ Agent 初始化失败: {str(e)}\n\n请检查 LLM API 配置（config.py）是否正确。"

    try:
        return invoke_agent(agent, full_query)
    except Exception as e:
        error_msg = str(e)
        log.error(f"处理请求失败: {error_msg}")
        if "api_key" in error_msg.lower() or "auth" in error_msg.lower():
            return f"""⚠️ LLM API 认证失败。请检查：

1. 在 `config.py` 中设置正确的 `api_key`
2. 确认校内 LLM 平台 (llm.ustc.edu.cn) 的 API 密钥有效
3. 设置环境变量: `export LLM_API_KEY="your-key"`

错误详情: {error_msg}"""
        return f"❌ 处理请求时出错: {error_msg}\n\n请稍后重试。"


def _process_complex(user_query: str, student_id: str) -> str:
    """
    Plan-and-Execute 流程：
    1. Planner 生成执行计划
    2. Executor 逐步执行
    3. 返回综合回答
    """
    from agents.planner import create_plan, validate_plan
    from agents.executor import Executor

    try:
        # 1. 生成计划
        plan = create_plan(user_query, student_id)

        # 2. 验证计划
        issues = validate_plan(plan)
        if issues:
            log.warning(f"计划验证未通过: {issues}，降级到 FAQ")
            # 降级到 FAQ
            from agents.factory import invoke_agent
            agent = get_agent("faq")
            if agent:
                return invoke_agent(agent, f"学生ID: {student_id}\n\n{user_query}")
            return "抱歉，我暂时无法处理这个复杂问题，请尝试拆分后逐个提问。"

        # 3. 执行计划
        context = get_context()
        executor = Executor()
        answer = executor.execute(plan, context)

        # 4. 记录对话历史
        context.add_chat_history("user", user_query)
        context.add_chat_history("assistant", answer)

        return answer

    except Exception as e:
        log.error(f"Plan-and-Execute 失败: {e}")
        return f"❌ 处理复杂查询时出错: {str(e)}\n\n请尝试将问题拆分为更简单的部分逐个提问。"


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
                "李教授教书怎么样？",
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

        # v2.0 复杂查询示例
        st.markdown("---")
        st.markdown("**🧠 试试复杂查询（Plan-and-Execute）**")
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
        status_text = f"知识库: {faq_count} 篇文档 | 已登录: {user['name']} ({user['id']}) | 架构: Plan-and-Execute v2.1"
    else:
        status_text = f"知识库: {faq_count} 篇文档 | 未登录（演示学生: {DEMO_STUDENT['id']}） | 架构: Plan-and-Execute v2.1"
    st.caption(status_text)


if __name__ == "__main__":
    main()
