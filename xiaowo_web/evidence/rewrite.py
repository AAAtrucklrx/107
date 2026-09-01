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

# 校内事务 → 官方站点限定查询（证据面扩展：site 查询提高官方一手命中率）
_OFFICIAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "教务处": ("教务处", "选课", "成绩", "课表", "校历", "考试", "评教", "补办", "转专业", "缓考"),
    "研究生院": ("研究生", "招生", "复试", "学位", "导师"),
    "图书馆": ("图书馆", "借阅", "馆藏", "研讨室"),
    "学生工作部": ("学工", "奖助", "勤工", "宿舍", "离校"),
    "校医院": ("校医院", "医保", "体检"),
    "财务处": ("学费", "缴费", "报销", "发票"),
    "就业指导中心": ("就业", "招聘", "实习"),
    "本科招生": ("招生办", "本科招生"),
}
# 问题里显式年份/相对年份
_YEAR_RE = re.compile(r"(20\d{2})\s*年|今年|明年|本学期|下学年", re.IGNORECASE)


def temporal_anchor(question: str) -> str | None:
    """提取问题中的年份锚（今年/明年/20XX年），用于把时效信息并入查询词。"""
    from datetime import date

    text = question.strip()
    if re.search(r"20\d{2}", text):
        match = re.search(r"(20\d{2})", text)
        return match.group(1)
    now = date.today().year
    if "明年" in text:
        return str(now + 1)
    if "今年" in text or "本学期" in text or "下学年" in text:
        return str(now)
    return None


def official_site_query(question: str) -> str | None:
    """若问题涉及校内事务，返回 site 限定的官方站点查询（如 site:ustc.edu.cn 教务处）。"""
    text = question.strip()
    for org, hints in _OFFICIAL_KEYWORDS.items():
        if any(hint in text for hint in hints):
            return f"site:ustc.edu.cn {org}"
    return None


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
        year = temporal_anchor(text)
        if year:
            prompt += f"（问题涉及 {year} 年/当前学年，查询词应包含对应年份。）"
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
        # 确定性：问题含年份锚且改写词没有年份时，把年份并入（防模型忽略提示）
        if year and queries:
            queries = [
                (f"{q} {year}" if year not in q else q)[: QUERY_MAX_CHARS + 5]
                for q in queries
            ]
        return queries or None

    def _invoke_default_model(self, prompt: str) -> Any:
        from utils.llm_client import create_llm

        model = create_llm(temperature=0)
        structured = model.with_structured_output(_RewritePayload, method="json_mode")
        return structured.invoke(prompt)
