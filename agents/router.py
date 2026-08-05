"""
小蜗 — 主路由 Agent
理解用户意图，路由到对应的子 Agent
支持：关键词快速匹配 → 复杂度判断 → LLM 兜底路由

v2.0 升级：新增 complexity 字段，区分简单/复杂查询
"""

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from utils.llm_client import create_llm
from utils.logger import get_logger

log = get_logger("xiaowo.router")

# ── 模块映射 ────────────────────────────────────────

MODULE_MAP = {
    "智能问答": "faq",
    "课业助手": "course",
    "选课顾问": "advisor",
    "日程管理": "schedule",
}

# ── 关键词路由表（优先级从高到低） ──────────────────

_KEYWORD_ROUTES: list[tuple[str, list[str]]] = [
    ("faq", [
        "是什么", "怎么办", "怎么申请", "流程", "规定", "政策", "几点", "在哪里",
        "怎么补", "能不能", "如何", "条件是什么", "有哪些食堂", "好不好吃",
    ]),
    ("schedule", [
        "日程", "提醒", "今天", "明天", "这周", "添加", "导入课表",
        "有什么事", "忙不忙", "有空吗", "组会",
    ]),
    ("course", [
        "课表", "成绩", "GPA", "空教室", "考试", "有什么课", "多少分",
        "学分", "绩点", "分数", "我的课",
    ]),
    ("advisor", [
        "推荐", "选课", "对比", "老师", "教授", "哪个好", "选修",
        "通识", "选什么",
    ]),
]

# ── 复杂查询检测 ────────────────────────────────────

# 多意图连接词
_MULTI_INTENT_KEYWORDS = [
    "然后", "并且", "还有", "另外", "同时", "再帮我", "顺便",
    "之后", "接着", "还要",
]

# 分析/规划型关键词（需要多步推理）
_ANALYSIS_KEYWORDS = [
    "分析", "规划", "安排", "策略", "计划",
    "适合我的", "根据我的", "帮我制定",
]

# 跨模块关键词组合（触发 Planner）
# 如果查询同时命中以下不同模块的关键词 → complex
_CROSS_MODULE_PAIRS = [
    ({"成绩", "GPA", "绩点"}, {"推荐", "选课", "选什么"}),
    ({"课表", "日程"}, {"空教室", "考试"}),
    ({"成绩", "GPA"}, {"对比", "老师", "哪个好"}),
]


def _detect_complexity(user_input: str) -> str:
    """
    检测查询复杂度。

    Returns:
        "simple" — 单一意图，直接路由
        "complex" — 多意图或分析型，交给 Planner
    """
    # 1. 多意图连接词检测
    intent_count = sum(1 for kw in _MULTI_INTENT_KEYWORDS if kw in user_input)
    if intent_count >= 1:
        # 进一步确认：连接词前后是否涉及不同模块
        for kw in _MULTI_INTENT_KEYWORDS:
            if kw in user_input:
                idx = user_input.index(kw)
                before = user_input[:idx]
                after = user_input[idx + len(kw):]
                before_modules = _get_matched_modules(before)
                after_modules = _get_matched_modules(after)
                if before_modules and after_modules and before_modules != after_modules:
                    return "complex"

    # 2. 分析/规划型关键词
    if any(kw in user_input for kw in _ANALYSIS_KEYWORDS):
        return "complex"

    # 3. 跨模块组合检测
    for group_a, group_b in _CROSS_MODULE_PAIRS:
        has_a = any(kw in user_input for kw in group_a)
        has_b = any(kw in user_input for kw in group_b)
        if has_a and has_b:
            return "complex"

    # 4. 逗号/分号分隔的多问题检测
    separators = ["，", ",", "；", ";"]
    for sep in separators:
        parts = [p.strip() for p in user_input.split(sep) if p.strip()]
        if len(parts) >= 2:
            modules_per_part = [_get_matched_modules(p) for p in parts]
            # 如果不同部分涉及不同模块
            all_modules = set()
            for mods in modules_per_part:
                all_modules.update(mods)
            if len(all_modules) >= 2:
                return "complex"

    return "simple"


def _get_matched_modules(text: str) -> set[str]:
    """返回文本命中的所有模块名集合"""
    matched = set()
    for agent, keywords in _KEYWORD_ROUTES:
        for kw in keywords:
            if kw in text:
                matched.add(agent)
                break
    return matched


# ── LLM 路由 Prompt ─────────────────────────────────

ROUTER_SYSTEM_PROMPT = """你是一个路由器Agent。根据用户输入，判断应该由哪个子Agent处理，以及查询复杂度。

路由规则：
1. 用户问"xxx是什么/怎么办/在哪里/流程/规定/政策/如何申请/什么时候/几点" → faq（智能问答）
2. 用户问"我的课表/成绩/GPA/空教室/考试/有什么课/分数" → course（课业助手）
3. 用户问"推荐课程/对比课程/老师怎么样/选课建议/通识课/选修课/哪个老师好" → advisor（选课顾问）
4. 用户问"日程/提醒/今天/明天/这周/添加事件/导入课表/有空吗/有什么事/忙不忙" → schedule（日程管理）
5. 用户输入模块名（"智能问答"、"课业助手"、"选课顾问"、"日程管理"）→ 直接路由到对应模块
6. 如果查询包含多个不同模块的意图（如"查GPA然后推荐课"），需要多步处理 → planner

复杂度判断：
- simple: 单一意图，一个模块能解决
- complex: 多意图、跨模块、需要分析/规划

输出格式（严格遵守，只输出JSON）：
{{"agent": "faq" | "course" | "advisor" | "schedule" | "planner", "complexity": "simple" | "complex", "reason": "简要说明路由原因", "rewritten_query": "如有必要，改写为用户意图更清晰的query，否则保持原样"}}
"""


def _keyword_route(user_input: str) -> dict | None:
    """尝试关键词路由匹配，返回 None 表示未命中"""
    for agent, keywords in _KEYWORD_ROUTES:
        for kw in keywords:
            if kw in user_input:
                return {
                    "agent": agent,
                    "reason": f"匹配关键词'{kw}'",
                    "rewritten_query": user_input,
                }
    return None


def route_query(user_input: str, selected_module: str = None) -> dict:
    """
    路由用户查询到对应的子 Agent。

    v2.0: 新增 complexity 字段，complex 查询路由到 Planner。

    Args:
        user_input: 用户输入
        selected_module: 用户在侧边栏手动选择的模块（可选）

    Returns:
        {
            "agent": "faq"|"course"|"advisor"|"schedule"|"planner",
            "complexity": "simple"|"complex",
            "reason": "...",
            "rewritten_query": "..."
        }
    """
    # 1. 手动选择模块 → 直接路由（simple）
    if selected_module and selected_module in MODULE_MAP:
        agent = MODULE_MAP[selected_module]
        log.debug(f"手动路由 → {agent} (用户选择 {selected_module})")
        return {
            "agent": agent,
            "complexity": "simple",
            "reason": f"用户手动选择了 {selected_module} 模块",
            "rewritten_query": user_input,
        }

    # 2. 复杂度检测（在关键词路由之前做，因为 complex 查询可能跨模块）
    complexity = _detect_complexity(user_input)
    if complexity == "complex":
        log.debug(f"复杂度检测 → complex: {user_input[:60]}")
        return {
            "agent": "planner",
            "complexity": "complex",
            "reason": "检测到多意图或跨模块查询",
            "rewritten_query": user_input,
        }

    # 3. 关键词快速匹配（simple）
    result = _keyword_route(user_input)
    if result:
        result["complexity"] = "simple"
        log.debug(f"关键词路由 → {result['agent']} ({result['reason']})")
        return result

    # 4. LLM 兜底路由
    try:
        llm = create_llm(temperature=0.1)
        parser = JsonOutputParser()
        prompt = ChatPromptTemplate.from_messages([
            ("system", ROUTER_SYSTEM_PROMPT),
            ("human", "{input}"),
        ])
        chain = prompt | llm | parser
        result = chain.invoke({"input": user_input})

        # 确保 complexity 字段存在
        if "complexity" not in result:
            result["complexity"] = "simple"
        # 如果 LLM 判断为 complex，路由到 planner
        if result.get("complexity") == "complex" and result.get("agent") != "planner":
            result["agent"] = "planner"

        log.debug(f"LLM 路由 → {result.get('agent')} ({result.get('reason')})")
        return result
    except Exception as e:
        log.warning(f"LLM 路由失败，兜底 FAQ: {e}")
        return {
            "agent": "faq",
            "complexity": "simple",
            "reason": "无法判断意图，兜底使用智能问答",
            "rewritten_query": user_input,
        }
