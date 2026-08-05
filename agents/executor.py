"""
小蜗 — Step 执行器（Executor）
逐步执行 Planner 生成的计划，管理 ReAct 循环
"""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from agents.context import Context
from agents.planner import Plan, PlanStep
from utils.llm_client import create_llm
from utils.logger import get_logger

log = get_logger("xiaowo.executor")


# ── Tool 注册表 ─────────────────────────────────────

def _build_tool_registry() -> dict:
    """
    构建 tool_name → tool函数 的映射。
    延迟导入避免循环依赖。
    """
    from tools.faq_tools import search_faq, get_faq_categories
    from tools.course_tools import (
        query_schedule, query_daily_schedule, find_empty_room, query_grade, calc_gpa, query_exam,
        search_courses, get_semester_list,
        query_course_selection, query_program,
    )
    from tools.advisor_tools import (
        collect_preferences, recommend_courses, compare_courses, analyze_teacher,
    )
    from tools.schedule_tools import (
        add_event, get_day_view, get_week_view, check_conflict, import_schedule,
    )

    registry = {
        "search_faq": search_faq,
        "get_faq_categories": get_faq_categories,
        "query_schedule": query_schedule,
        "query_daily_schedule": query_daily_schedule,
        "find_empty_room": find_empty_room,
        "query_grade": query_grade,
        "calc_gpa": calc_gpa,
        "query_exam": query_exam,
        "search_courses": search_courses,
        "get_semester_list": get_semester_list,
        "query_course_selection": query_course_selection,
        "query_program": query_program,
        "collect_preferences": collect_preferences,
        "recommend_courses": recommend_courses,
        "compare_courses": compare_courses,
        "analyze_teacher": analyze_teacher,
        "add_event": add_event,
        "get_day_view": get_day_view,
        "get_week_view": get_week_view,
        "check_conflict": check_conflict,
        "import_schedule": import_schedule,
    }
    return registry


# ── 综合回答 Prompt ─────────────────────────────────

SYNTHESIZE_PROMPT = """你是小蜗，科大校园智能助手。
用户提出了以下问题，系统已经通过多个步骤收集了相关信息。
请根据所有步骤的执行结果，生成一个完整、准确、友好的回答。

## 用户原始问题
{query}

## 执行计划与结果
{context_summary}

## 回答要求
1. 直接回答用户的问题，不要重复用户的提问
2. 使用中文，语气亲切自然
3. 如果某些步骤失败了，说明原因并尝试给出已有信息
4. 如果结果中包含数据表格，用 Markdown 格式展示
5. 回答要简洁有条理，不要冗余信息
"""


class Executor:
    """
    逐步执行 Plan，管理 ReAct 循环。

    执行流程：
    1. Think: 读取 context，分析当前状态
    2. Act: 调用 step 指定的 Tool（替换占位符为实际值）
    3. Observe: 将 Tool 返回结果存入 context
    4. Decide: 判断是否需要调整后续 plan
    5. Synthesize: 整合所有结果，生成最终回答
    """

    def __init__(self) -> None:
        self.tool_registry: dict = _build_tool_registry()

    def execute(self, plan: Plan, context: Context) -> str:
        """
        执行完整计划并返回最终回答。

        Args:
            plan: Planner 生成的执行计划
            context: 跨 Step 共享的上下文

        Returns:
            最终回答文本
        """
        log.info(f"Executor 开始执行计划: {len(plan.steps)} 步")
        context.reset_plan_context()

        for step in plan.steps:
            result = self._execute_step(step, context)
            step.status = "failed" if isinstance(result, dict) and result.get("error") else "done"

            # 存入 Context
            context.add_step_result(step.step_id, result if isinstance(result, dict) else {"output": str(result)})

            log.info(f"  Step {step.step_id} [{step.status}]: {step.description}")

        # 整合结果，生成最终回答
        return self._synthesize(plan, context)

    def _execute_step(self, step: PlanStep, context: Context) -> dict:
        """执行单个 Step"""
        step.status = "running"

        # 1. 解析依赖占位符
        resolved_args = context.resolve_args(step.tool_args) if step.tool_args else {}

        # 2. 如果无 tool_name（纯推理步骤），跳过
        if not step.tool_name:
            log.debug(f"  Step {step.step_id}: 纯推理步骤，跳过 Tool 调用")
            return {"description": step.description, "args": resolved_args}

        # 3. 查找 Tool
        tool_func = self.tool_registry.get(step.tool_name)
        if not tool_func:
            error_msg = f"未知工具: {step.tool_name}"
            log.error(f"  Step {step.step_id}: {error_msg}")
            return {"error": error_msg}

        # 4. 调用 Tool
        try:
            log.debug(f"  Step {step.step_id}: 调用 {step.tool_name}({resolved_args})")
            result = tool_func.invoke(resolved_args) if hasattr(tool_func, 'invoke') else tool_func(**resolved_args)
            return result if isinstance(result, dict) else {"output": str(result)}
        except Exception as e:
            log.error(f"  Step {step.step_id}: Tool {step.tool_name} 执行失败: {e}")
            return {"error": f"工具 {step.tool_name} 执行失败: {str(e)}"}

    def _synthesize(self, plan: Plan, context: Context) -> str:
        """
        整合所有 Step 结果，调用 LLM 生成最终回答。

        如果计划只有 1 步且成功，直接返回 Tool 结果的文本表示（避免多余 LLM 调用）。
        """
        # 快速通道：单步计划直接返回
        if len(plan.steps) == 1:
            step = plan.steps[0]
            result = context.get_step_result(step.step_id)
            if result and not result.get("error"):
                return self._format_single_result(step, result)

        # 多步计划：调用 LLM 综合回答
        context_summary = context.get_summary()

        try:
            llm = create_llm(temperature=0.3)
            prompt = ChatPromptTemplate.from_messages([
                ("system", SYNTHESIZE_PROMPT),
            ])
            chain = prompt | llm
            response = chain.invoke({
                "query": plan.original_query,
                "context_summary": context_summary,
            })
            return response.content
        except Exception as e:
            log.error(f"综合回答生成失败: {e}")
            # 降级：拼接所有 Step 结果
            return self._fallback_synthesize(plan, context)

    def _format_single_result(self, step: PlanStep, result: dict) -> str:
        """格式化单步结果为自然语言"""
        # 尝试从结果中提取有意义的信息
        if "output" in result:
            return str(result["output"])
        if "answer" in result:
            return str(result["answer"])
        if "found" in result and result.get("results"):
            # FAQ 搜索结果
            return result["results"][0].get("content", str(result))
        # 通用格式化
        parts = []
        for key, value in result.items():
            if isinstance(value, (str, int, float, bool)):
                parts.append(f"{key}: {value}")
        return "\n".join(parts) if parts else str(result)

    def _fallback_synthesize(self, plan: Plan, context: Context) -> str:
        """LLM 综合失败时的降级回答：拼接各步骤结果"""
        lines = [f"关于「{plan.original_query}」的执行结果：\n"]
        for step in plan.steps:
            result = context.get_step_result(step.step_id)
            if not result:
                continue
            lines.append(f"**{step.description}**")
            if result.get("error"):
                lines.append(f"  ⚠️ {result['error']}")
            else:
                for k, v in result.items():
                    if isinstance(v, (str, int, float, bool)):
                        lines.append(f"  - {k}: {v}")
            lines.append("")
        return "\n".join(lines)
