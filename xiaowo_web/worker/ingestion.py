"""Lease-based cleaner that creates review drafts without changing source facts."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

from xiaowo_web.evidence.privacy import contains_sensitive_text
from xiaowo_web.review import IngestionJob, ReviewStore


_PROMPT_INJECTION = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"忽略.{0,8}(?:之前|以上).{0,8}(?:指令|提示)|调用.{0,6}(?:工具|function)|"
    r"泄露.{0,8}(?:提示词|密钥|cookie|token))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CleanDraft:
    title: str
    scope: str
    category: str
    content: str
    chunks: list[str]


class Cleaner(Protocol):
    def clean(self, snapshot_text: str, metadata: dict[str, Any]) -> CleanDraft: ...


class DeterministicCleaner:
    """Safe fallback: normalize and chunk only; never add or rewrite facts."""

    def clean(self, snapshot_text: str, metadata: dict[str, Any]) -> CleanDraft:
        normalized = re.sub(r"[ \t]+", " ", snapshot_text.replace("\r\n", "\n"))
        paragraphs = [re.sub(r"\s+", " ", part).strip() for part in re.split(r"\n{2,}", normalized)]
        paragraphs = [part for part in paragraphs if len(part) >= 20]
        chunks: list[str] = []
        buffer = ""
        for paragraph in paragraphs:
            candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
            if len(candidate) <= 1200:
                buffer = candidate
            else:
                if buffer:
                    chunks.append(buffer)
                buffer = paragraph[:1200]
        if buffer:
            chunks.append(buffer)
        if not chunks and snapshot_text.strip():
            chunks = [snapshot_text.strip()[:1200]]
        title = str(metadata.get("title") or "公开网页资料").strip()[:200]
        text_hint = f"{title} {snapshot_text[:500]}"
        if any(word in text_hint for word in ("公告", "通知", "公示")):
            category = "announcement"
        elif any(word in text_hint for word in ("办理", "申请", "开放时间", "服务")):
            category = "dynamic_service"
        elif any(word in text_hint for word in ("办法", "规定", "政策", "制度")):
            category = "policy"
        else:
            category = "stable_general"
        host = (urlsplit(str(metadata.get("normalized_url") or "")).hostname or "").casefold()
        scope = "campus" if host == "ustc.edu.cn" or host.endswith(".ustc.edu.cn") else "general"
        return CleanDraft(
            title=title or "公开网页资料",
            scope=scope,
            category=category,
            content="\n\n".join(chunks),
            chunks=chunks,
        )


class IngestionWorker:
    def __init__(
        self,
        store: ReviewStore,
        cleaner: Cleaner | None = None,
        *,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.cleaner = cleaner or DeterministicCleaner()
        self.worker_id = worker_id or f"worker-{secrets.token_urlsafe(8)}"

    def run_once(self, *, now: float | None = None) -> str | None:
        job = self.store.claim_job(self.worker_id, now=now)
        if job is None:
            return None
        try:
            snapshot = self.store.read_snapshot(job.payload["content_path"])
            if contains_sensitive_text(snapshot):
                self.store.fail_job(job, "SENSITIVE_CONTENT", permanent=True, now=now)
                return "dead"
            if _PROMPT_INJECTION.search(snapshot):
                self.store.fail_job(job, "PROMPT_INJECTION", permanent=True, now=now)
                return "dead"
            draft = self.cleaner.clean(snapshot, job.payload)
            self.store.create_draft(
                job,
                title=draft.title,
                scope=draft.scope,
                category=draft.category,
                chunks=draft.chunks,
                model_text=draft.content,
                actor_key=self.worker_id,
            )
            return "done"
        except Exception:
            return self.store.fail_job(job, "CLEANING_FAILED", now=now)
