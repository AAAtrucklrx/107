"""
小蜗 — QA LangGraph 状态定义
统一问答流程在节点间传递的状态
"""

from typing import TypedDict


class QaState(TypedDict):
    """统一问答流程状态"""

    query: str  # 用户原始问题
    module_signal: str  # 侧边栏模块信号（"自动判断" 或模块名，仅作软提示）
    intent: str  # embedding 意图分类结果（参考信号）
    intent_top3: list[dict]  # 意图 Top3（含分数）
    candidates: list[dict]  # 知识库候选召回片段（embedding_parse 与 retrieve 填充）
    candidates_found: bool  # 候选召回是否达到阈值
    student_id: str  # 学号（登录用户；未登录为空）
    user_profile: dict  # 用户信息（姓名/专业/年级/登录状态等）
    chat_history: list[dict]  # 最近对话历史（多轮指代理解，保留最近20条）
    decision: str  # think 决策：clarify / retrieve / call_tool / compose
    retrieve_query: str  # retrieve 决策时改写后的检索词
    sub_queries: list[str]  # retrieve 决策时的并列子检索词(查询发散, 可空)
    retrieval_log: list[dict]  # 检索过程记录 [{round, decision, reason}] → thought.step 上屏
    tool_calls: list[dict]  # call_tool 决策的工具调用 [{tool, args}]
    tool_results: list[dict]  # 工具执行结果 [{tool, result, status}]
    rounds: int  # 已执行工具轮次（上限 4）
    thought_log: list[dict]  # think 决策记录 [{round, decision, reason}]
    llm_down: bool  # P3-2 熔断标记：本轮内 LLM 已失败，后续轮次直接确定性规则
    clarify_question: str  # clarify 决策时向用户提出的追问
    answer: str  # 最终回答
    truncated: bool  # compose 时 LLM 输出触顶(finish_reason=length), 前端展示"继续生成"
    error: str  # 异常/错误信息（如 LLM API 不可用）
