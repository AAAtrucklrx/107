"""小蜗开发流水线 — LLM 封装（中科大 deepseek-v4-flash）。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SEC


def get_llm(temperature: float = 0.2) -> ChatOpenAI:
    """创建中科大 dsV4flash 实例（OpenAI 兼容 chat 端点）。"""
    if not LLM_API_KEY:
        raise RuntimeError("缺少 LLM API key：请设置 USTC_API_KEY 或 LLM_API_KEY 环境变量")
    return ChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        model=LLM_MODEL,
        temperature=temperature,
        timeout=LLM_TIMEOUT_SEC,
        max_retries=2,
    )


def ask(system: str, user: str, temperature: float = 0.2) -> str:
    """单轮 LLM 调用，返回文本。"""
    llm = get_llm(temperature)
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    return (resp.content or "").strip()
