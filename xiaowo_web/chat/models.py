"""Publicly safe chat runner contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

from xiaowo_web.auth.models import Principal


@dataclass(frozen=True, slots=True)
class QaRunRequest:
    run_id: str
    question: str
    requested_mode: str
    effective_mode: str
    principal: Principal
    conversation_id: str | None
    chat_history: list[dict[str, str]] = field(default_factory=list)
    emit_stage: Callable[[str, str], None] | None = None
    # 阶段2：结构化卡片事件（工具完成即推，先于正文）
    emit_table: Callable[[dict], None] | None = None
    # compose 增量流式：正文 token 推流（answer.delta 事件，前端拼接显示）
    emit_delta: Callable[[str], None] | None = None


@dataclass(slots=True)
class AnswerBundle:
    markdown: str
    claims: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    terminal_reason: str = "completed"
    ingestion_candidates: list[dict[str, Any]] = field(default_factory=list)
    # B2: think 决策过程(前端折叠卡展示); B4: LLM 输出触顶截断标记(前端"继续生成")
    thoughts: list[dict[str, Any]] = field(default_factory=list)
    # 阶段1 结构化数据卡：工具结果表格（成绩/课表/考试/选课），不经 LLM 重述
    structured: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    # 本地链路的结构信号（2026-09-16）：供上层判断"本地到底有没有答出来"。
    # 字段：candidates_found / candidate_count / top_score / tool_used。
    # ⚠️ 实测只有 `candidates_found is False`（知识库压根没召回）是**可靠**判据；
    #    `top_score` **不能**用来区分"答得出/答不出"（实测两组分数几乎重叠：
    #    答得出 0.50~0.60，答不出 0.44~0.58），不要据此设阈值。
    retrieval: dict[str, Any] = field(default_factory=dict)
