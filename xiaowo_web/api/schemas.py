"""Pydantic request contracts shared by API routers."""

from __future__ import annotations

import ipaddress
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ChatRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=50_000)
    mode: Literal["auto", "web", "local"] = "auto"
    conversation_id: str | None = Field(default=None, max_length=128)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question must not be blank")
        return normalized


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="新对话", max_length=80)


class ReviewEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=500_000)
    chunks: list[str] = Field(min_length=1, max_length=200)


class ChunkApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool | None = None
    approval_status: Literal["pending", "approved", "rejected"] | None = None

    @model_validator(mode="after")
    def require_consistent_decision(self) -> "ChunkApproval":
        if self.approved is None and self.approval_status is None:
            raise ValueError("chunk approval decision is required")
        if self.approved is not None and self.approval_status is not None:
            legacy_status = "approved" if self.approved else "pending"
            if legacy_status != self.approval_status:
                raise ValueError("approved and approval_status disagree")
        return self


class ReviewApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["announcement", "dynamic_service", "policy", "stable_general"]
    ttl_days: int = Field(ge=1, le=180)


class AnswerFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=8, max_length=128)
    category: Literal["helpful", "incorrect", "outdated", "source_issue", "other"]
    detail: str = Field(default="", max_length=1000)


class SourceTrustProposalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=253)
    path_prefix: str = Field(default="/", min_length=1, max_length=500)
    level: Literal["official_primary", "reliable_independent"]
    institution: str = Field(min_length=2, max_length=200)
    effective_from: date = Field(default_factory=date.today)
    rationale: str = Field(min_length=10, max_length=1000)

    @field_validator("host")
    @classmethod
    def validate_exact_host(cls, value: str) -> str:
        host = value.strip().casefold().rstrip(".")
        if (
            not host
            or "*" in host
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
            or ".." in host
        ):
            raise ValueError("host must be an exact DNS hostname")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return host
        raise ValueError("host must not be an IP address")

    @field_validator("path_prefix")
    @classmethod
    def validate_path_prefix(cls, value: str) -> str:
        prefix = value.strip()
        if not prefix.startswith("/") or "?" in prefix or "#" in prefix or "\\" in prefix:
            raise ValueError("path_prefix must be an absolute URL path")
        return prefix.rstrip("/") or "/"
