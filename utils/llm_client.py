"""
小蜗 — LLM 客户端封装
对接校内 OpenAI 兼容 API
"""

import time

import httpx
from typing import Any

from langchain_openai import ChatOpenAI
from config import LLM_CONFIG

# P3-2 进程级熔断窗：任一调用方发现平台不可用后，窗内 create_llm 直接快速失败，
# 避免每问重复撞防火墙丢包的 connect 超时（实测每次 ~21s）
_LLM_DOWN_UNTIL = 0.0
_LLM_DOWN_WINDOW = 600.0  # 秒


def mark_llm_down(seconds: float = _LLM_DOWN_WINDOW) -> None:
    """标记 LLM 平台近期不可用（embedding 探测失败/调用超时等场景调用）。"""
    global _LLM_DOWN_UNTIL
    _LLM_DOWN_UNTIL = max(_LLM_DOWN_UNTIL, time.time() + seconds)


def llm_circuit_open() -> bool:
    return time.time() < _LLM_DOWN_UNTIL


def mark_llm_down_if_unreachable(exc: Exception) -> bool:
    """连接级失败（平台不可达：ConnectionError/connect refused）才开熔断窗。

    读超时（Request timed out，平台慢/瞬时限流）属瞬时失败，开窗会把后续
    正常请求也拖进降级（实测批量回归中一次超时殃及全部后续用例）。
    """
    text = f"{type(exc).__name__} {exc}".lower()
    if ("connection" in text or "connect" in text
            or isinstance(exc, (ConnectionError, LLMUnavailableError))):
        mark_llm_down()
        return True
    return False


class LLMUnavailableError(ConnectionError):
    """熔断窗内快速失败（区别于真实网络异常，供调用方直接走降级）。"""


def llm_content(response: Any) -> str:
    """提取模型回复文本。DeepSeek 官网 V4 系列偶发 content 为空、
    推理内容落在 additional_kwargs.reasoning_content——作为最后兜底使用
    （正常时与 response.content 一致，不会改变现有行为）。"""
    if response is None:
        return ""
    try:
        text = str(getattr(response, "content", None) or "").strip()
        if text:
            return text
        kwargs = getattr(response, "additional_kwargs", None) or {}
        return str(kwargs.get("reasoning_content") or "").strip()
    except Exception:
        return ""


def create_llm(temperature: float = None, model: str | None = None) -> ChatOpenAI:
    """
    创建 LLM 实例，对接校内平台。

    Args:
        temperature: 温度参数，不指定则使用默认配置
    """
    if llm_circuit_open():
        raise LLMUnavailableError("LLM 平台近期不可用（熔断窗内快速失败）")
    temp = temperature if temperature is not None else LLM_CONFIG["temperature"]
    return ChatOpenAI(
        base_url=LLM_CONFIG["base_url"],
        api_key=LLM_CONFIG["api_key"],
        model=model or LLM_CONFIG["model"],
        temperature=temp,
        max_tokens=LLM_CONFIG["max_tokens"],
        # connect 单独 5s：防火墙丢包型故障快速失败；读超时保持全局配置
        timeout=httpx.Timeout(LLM_CONFIG["timeout"], connect=5.0),
        # 平台不可用时快速失败（默认 1 次重试＝最多 2 次尝试），
        # 避免 langchain 默认 2 次重试 × 30s 超时把断网拖成分钟级假死
        max_retries=int(LLM_CONFIG.get("max_retries", 1)),
    )
