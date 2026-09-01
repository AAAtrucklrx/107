"""Search query rewriting for the web evidence pipeline.

The pipeline searches with the user's question verbatim; long natural-language
questions degrade engine recall on campus-reachable engines (baidu/360/chinaso
measured 0 hits in tests).  This module rewrites a question into 1-2 compact
keyword queries, mirroring the query/sub_queries pattern already used by the
local RAG path (``agents/qa/nodes.py`` THINK rules).

Privacy: rewriting happens AFTER ``sanitize_public_query`` and never adds data;
the original question is the fallback on any failure (no degradation to error).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# 超过该长度的自然问句才需要改写（短查询直接原样使用，避免无谓 LLM 调用）
REWRITE_MIN_CHARS = 30
# 单条查询词长度上限（避免把改写结果又变成一个长句子）
QUERY_MAX_CHARS = 40
MAX_QUERIES = 2


class _RewritePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 上限宽松以容忍模型多给；去重/长度校验在 rewrite() 内完成并截断到 MAX_QUERIES
    queries: list[str] = Field(min_length=1, max_length=6)


_SYSTEM_PROMPT = (
    "你是搜索关键词改写器。把用户问题改写成 1 到 2 个适合中文搜索引擎的简短关键词查询，"
    "每条不超过 32 个字，不含问句语气词。若问题已经是简短关键词（≤30 字），只返回原词。"
    "只返回 JSON：{\"queries\": [\"关键词1\", \"关键词2\"]}。"
)


class QueryRewriter:
    """Bound query rewriting; returns None on any failure (caller falls back)."""

    def __init__(
        self,
        invoke: Any | None = None,
        *,
        min_chars: int = REWRITE_MIN_CHARS,
        max_queries: int = MAX_QUERIES,
    ) -> None:
        self._invoke = invoke or self._invoke_default_model
        self._injected = invoke is not None
        self.min_chars = min_chars
        self.max_queries = max_queries
        self.last_error: str | None = None

    @property
    def available(self) -> bool:
        return self._injected or True

    def wants_rewrite(self, question: str) -> bool:
        return len(question.strip()) > self.min_chars

    async def rewrite(self, question: str, *, short_hint: bool = False) -> list[str] | None:
        """Return 1-2 keyword queries or None to keep the original question."""
        text = question.strip()
        if not self.wants_rewrite(text):
            return None
        prompt = _SYSTEM_PROMPT
        if short_hint:
            prompt += "（请比上一轮更简短，使用不同关键词组合。）"
        try:
            import asyncio

            raw = await asyncio.to_thread(self._invoke, prompt + "\n\n用户问题：" + text)
            payload = _RewritePayload.model_validate(raw)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            return None
        queries: list[str] = []
        normalized_text = text.strip(" \t。！？!?，,；;")
        for item in payload.queries:
            cleaned = re.sub(r"\s+", " ", str(item)).strip(" \t。！？!?，,;；")
            if not cleaned:
                continue
            if cleaned == normalized_text or cleaned in queries:
                continue
            queries.append(cleaned[:QUERY_MAX_CHARS])
            if len(queries) >= self.max_queries:
                break
        return queries or None

    def _invoke_default_model(self, prompt: str) -> Any:
        from utils.llm_client import create_llm

        model = create_llm(temperature=0)
        structured = model.with_structured_output(_RewritePayload, method="json_mode")
        return structured.invoke(prompt)
