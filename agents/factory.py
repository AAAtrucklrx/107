"""
小蜗 — Agent 工厂
统一的 Agent 创建逻辑，消除各子 Agent 模块的重复代码

v2.1: 适配 langchain 1.3+ 新版 create_agent API
"""

from typing import Callable

from langchain.agents import create_agent

from utils.llm_client import create_llm
from utils.logger import get_logger

log = get_logger("xiaowo.agent")


def build_agent(
    system_prompt: str,
    tools: list[Callable],
    temperature: float = 0.3,
    name: str = "agent",
):
    """
    构建 LangChain Tool-Calling Agent（v2.1 新版 API）。

    Args:
        system_prompt: 系统提示词
        tools: Agent 可使用的工具列表
        temperature: LLM 温度参数
        name: Agent 名称（用于日志）

    Returns:
        CompiledStateGraph 实例（通过 .invoke() 调用）
    """
    llm = create_llm(temperature=temperature)
    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=system_prompt,
        name=name,
    )
    log.debug(f"创建 Agent: {name} (temperature={temperature}, tools={[t.name for t in tools]})")
    return agent


def invoke_agent(agent, user_input: str) -> str:
    """
    统一调用 Agent 的入口函数。

    适配新版 create_agent 返回的 CompiledStateGraph：
    - 输入: {"messages": [("user", input_text)]}
    - 输出: {"messages": [...]} → 提取最后一条 AI 消息

    Args:
        agent: create_agent 返回的 CompiledStateGraph
        user_input: 用户输入文本

    Returns:
        AI 回复文本
    """
    result = agent.invoke({"messages": [("user", user_input)]})
    messages = result.get("messages", [])
    if not messages:
        return "抱歉，我暂时无法回答这个问题。"
    # 最后一条消息是 AI 回复
    last_msg = messages[-1]
    if hasattr(last_msg, "content"):
        return last_msg.content
    return str(last_msg)
