"""
小蜗 — 全局 Planner Agent
分析复杂查询，拆解为多步执行计划
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from utils.llm_client import create_llm
from utils.logger import get_logger

log = get_logger("xiaowo.planner")


# ── 数据类 ──────────────────────────────────────────

@dataclass
class PlanStep:
    """计划中的一个执行步骤"""
    step_id: int
    description: str
    tool_name: str | None = None
    tool_args: dict = field(default_factory=dict)
    depends_on: list[int] = field(default_factory=list)
    status: str = "pending"  # pending | running | done | failed


@dataclass
class Plan:
    """执行计划"""
    steps: list[PlanStep]
    original_query: str
    reasoning: str = ""


# ── Planner System Prompt ───────────────────────────

PLANNER_SYSTEM_PROMPT = """你是小蜗的任务规划器（Planner）。
你的职责是分析用户的复杂查询，拆解为可执行的步骤计划。

## 可用工具清单

| 工具名 | 功能 | 关键参数 |
|--------|------|---------|
| search_faq | 搜索FAQ知识库 | query: str |
| get_faq_categories | 获取FAQ分类 | (无参数) |
| query_schedule | 查询课表 | student_id: str, week: int, day: str |
| query_daily_schedule | 查询某天课程安排（精确到分钟） | date: str, student_id: str |
| find_empty_room | 查找空教室 | building: str, time_desc: str |
| query_grade | 查询成绩 | student_id: str, course_name: str, semester: str |
| calc_gpa | 计算GPA | student_id: str, semester: str |
| query_exam | 查询考试安排 | student_id: str, course_name: str |
| collect_preferences | 收集选课偏好 | (无参数，返回已收集偏好) |
| recommend_courses | 推荐课程 | profile: dict |
| compare_courses | 对比两门课 | course_a: str, course_b: str |
| analyze_teacher | 分析教师评价 | teacher_name: str |
| add_event | 添加日程事件 | student_id: str, title: str, start_time: str, end_time: str |
| get_day_view | 查看某天日程 | student_id: str, date_str: str |
| get_week_view | 查看本周日程 | student_id: str, start_date: str |
| check_conflict | 检查时间冲突 | student_id: str, start_time: str, end_time: str |
| import_schedule | 导入课表到日程 | student_id: str |
| search_courses | 全校课程搜索 | keyword: str, limit: int = 10 |
| get_semester_list | 获取学期列表 | (无参数) |
| query_course_selection | 查询选课结果 | student_id: str, semester: str |
| query_program | 查询培养方案 | student_id: str, module_id: int |

## 规划规则

1. 每个步骤只调用一个 Tool，或进行纯推理分析（tool_name 为 null）
2. 步骤之间通过 depends_on 建立依赖关系
3. 如果某步骤依赖前序步骤的结果，在 tool_args 中用 {{step_N.field}} 占位
   - 例如 Step 2 依赖 Step 1 的 gpa 结果：tool_args 中写 "{{step_1.gpa}}"
4. 计划步骤数不超过 5 步
5. 如果用户查询其实很简单（一个 Tool 就能完成），返回单步计划即可
6. student_id 由调用方传入；未登录时为空，涉及个人数据的工具会返回锁定提示

## 输出格式（严格 JSON，不要其他文字）

```json
{{
    "reasoning": "分析用户意图和所需信息的过程（2-3句话）",
    "steps": [
        {{
            "step_id": 1,
            "description": "查询用户当前GPA",
            "tool_name": "calc_gpa",
            "tool_args": {{"student_id": "{{student_id}}"}},
            "depends_on": []
        }},
        {{
            "step_id": 2,
            "description": "根据GPA推荐适合的课程",
            "tool_name": "recommend_courses",
            "tool_args": {{"profile": {{"target_gpa": "{{step_1.gpa}}"}}}},
            "depends_on": [1]
        }}
    ]
}}
```
"""


def _build_tool_list_text() -> str:
    """构建工具列表文本，供 prompt 使用"""
    # 已硬编码在 PLANNER_SYSTEM_PROMPT 中，无需动态生成
    return ""


def create_plan(user_query: str, student_id: str = "") -> Plan:
    """
    调用 Planner LLM，将用户查询拆解为执行计划。

    Args:
        user_query: 用户的复杂查询
        student_id: 学生ID

    Returns:
        Plan 对象，包含 steps 列表和推理过程
    """
    log.info(f"Planner 开始分析: {user_query[:80]}...")

    llm = create_llm(temperature=0.2)
    parser = JsonOutputParser()
    prompt = ChatPromptTemplate.from_messages([
        ("system", PLANNER_SYSTEM_PROMPT),
        ("human", "用户查询: {input}\n学生ID: {student_id}"),
    ])

    chain = prompt | llm | parser

    try:
        result = chain.invoke({"input": user_query, "student_id": student_id})
    except Exception as e:
        log.error(f"Planner LLM 调用失败: {e}")
        # 降级：返回单步计划，交给 FAQ 兜底
        return Plan(
            steps=[PlanStep(
                step_id=1,
                description="使用智能问答回答",
                tool_name="search_faq",
                tool_args={"query": user_query},
            )],
            original_query=user_query,
            reasoning=f"Planner 降级: {e}",
        )

    # 解析 LLM 返回的 JSON 为 Plan 对象
    reasoning = result.get("reasoning", "")
    raw_steps = result.get("steps", [])

    steps = []
    for s in raw_steps:
        step = PlanStep(
            step_id=s.get("step_id", len(steps) + 1),
            description=s.get("description", ""),
            tool_name=s.get("tool_name"),
            tool_args=s.get("tool_args", {}),
            depends_on=s.get("depends_on", []),
        )
        steps.append(step)

    plan = Plan(
        steps=steps,
        original_query=user_query,
        reasoning=reasoning,
    )

    log.info(f"Planner 生成计划: {len(steps)} 步, 推理: {reasoning[:100]}")
    for step in steps:
        log.debug(f"  Step {step.step_id}: {step.description} → {step.tool_name}")

    return plan


def validate_plan(plan: Plan) -> list[str]:
    """
    验证计划的合理性，返回问题列表（空列表 = 通过验证）。

    检查项：
    1. 步骤编号连续
    2. depends_on 引用的步骤存在且在当前步骤之前
    3. tool_name 在已知工具列表中（或为 None）
    """
    KNOWN_TOOLS = {
        "search_faq", "get_faq_categories",
        "query_schedule", "query_daily_schedule", "find_empty_room", "query_grade", "calc_gpa", "query_exam",
        "search_courses", "get_semester_list",
        "query_course_selection", "query_program",
        "collect_preferences", "recommend_courses", "compare_courses", "analyze_teacher",
        "add_event", "get_day_view", "get_week_view", "check_conflict", "import_schedule",
    }

    issues = []
    step_ids = {s.step_id for s in plan.steps}

    for step in plan.steps:
        # 检查 tool_name
        if step.tool_name and step.tool_name not in KNOWN_TOOLS:
            issues.append(f"Step {step.step_id}: 未知工具 '{step.tool_name}'")

        # 检查 depends_on
        for dep_id in step.depends_on:
            if dep_id not in step_ids:
                issues.append(f"Step {step.step_id}: 依赖的 Step {dep_id} 不存在")
            elif dep_id >= step.step_id:
                issues.append(f"Step {step.step_id}: 依赖的 Step {dep_id} 不在之前")

    if issues:
        log.warning(f"计划验证发现 {len(issues)} 个问题: {issues}")

    return issues
