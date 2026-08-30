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
    truncated: bool = False
