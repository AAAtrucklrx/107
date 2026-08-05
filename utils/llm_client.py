"""
小蜗 — LLM 客户端封装
对接校内 OpenAI 兼容 API
"""

from langchain_openai import ChatOpenAI
from config import LLM_CONFIG


def create_llm(temperature: float = None) -> ChatOpenAI:
    """
    创建 LLM 实例，对接校内平台。

    Args:
        temperature: 温度参数，不指定则使用默认配置
    """
    temp = temperature if temperature is not None else LLM_CONFIG["temperature"]
    return ChatOpenAI(
        base_url=LLM_CONFIG["base_url"],
        api_key=LLM_CONFIG["api_key"],
        model=LLM_CONFIG["model"],
        temperature=temp,
        max_tokens=LLM_CONFIG["max_tokens"],
        timeout=LLM_CONFIG["timeout"],
    )