"""SQLite lease queue and immutable review/version storage."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xiaowo_web.settings import WebSettings


_NAMESPACES = frozenset({"demo", "production"})
_TTL_LIMITS = {
    "announcement": 7,
    "dynamic_service": 30,
    "policy": 90,
    "stable_general": 180,
}


def _digest(value: str | bytes) -> str:
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, slots=True)
class IngestionJob:
    job_id: str
    namespace: str
    snapshot_hash: str
    evidence_span_hash: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    lease_owner: str
    lease_expires_at: float


@dataclass(frozen=True, slots=True)
class PublishJob:
    job_id: str
    namespace: str
    generation_id: str
    reason: str
    attempts: int
    max_attempts: int
    lease_owner: str
    lease_expires_at: float


@dataclass(frozen=True, slots=True)
class RefetchJob:
    job_id: str
    namespace: str
    item_id: str
    source_url: str
    original_snapshot_hash: str
    title: str
    attempts: int
    max_attempts: int
    lease_owner: str
    lease_expires_at: float


class ReviewStore:
    def __init__(self, settings: WebSettings) -> None:
        self.settings = settings
        self.db_path = Path(settings.review_db_path)
        self.schema_path = Path(settings.schema_review_path)
        self.data_dir = Path(settings.web_evidence_dir)
        self._write_lock = threading.RLock()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "raw").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "approved").mkdir(parents=True, exist_ok=True)
        schema = self.schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(schema)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def healthcheck(self) -> bool:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT 1").fetchone()
            return row is not None
        except sqlite3.Error:
            return False

    def reset_demo_namespace(self) -> None:
        """Reset only synthetic review state; production is structurally out of scope."""
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            generation_ids = [
                str(row["generation_id"])
                for row in conn.execute(
                    "SELECT generation_id FROM publish_generations WHERE namespace = 'demo'"
                ).fetchall()
            ]
            item_rows = conn.execute(
                "SELECT item_id, snapshot_id FROM review_items WHERE namespace = 'demo'"
            ).fetchall()
            item_ids = [str(row["item_id"]) for row in item_rows]
            snapshot_ids = [str(row["snapshot_id"]) for row in item_rows]
            conn.execute("DELETE FROM active_index_state WHERE namespace = 'demo'")
            conn.execute("DELETE FROM publish_jobs WHERE namespace = 'demo'")
            if generation_ids:
                placeholders = ",".join("?" for _ in generation_ids)
                conn.execute(
                    f"DELETE FROM publish_documents WHERE generation_id IN ({placeholders})",
                    tuple(generation_ids),
                )
                conn.execute(
                    f"DELETE FROM publish_generations WHERE generation_id IN ({placeholders})",
                    tuple(generation_ids),
                )
            conn.execute("DELETE FROM source_trust_proposals WHERE namespace = 'demo'")
            conn.execute("DELETE FROM review_audit WHERE namespace = 'demo'")
            if item_ids:
                placeholders = ",".join("?" for _ in item_ids)
                conn.execute(
                    f"DELETE FROM review_items WHERE item_id IN ({placeholders})",
                    tuple(item_ids),
                )
            if snapshot_ids:
                placeholders = ",".join("?" for _ in snapshot_ids)
                conn.execute(
                    f"DELETE FROM web_snapshots WHERE snapshot_id IN ({placeholders})",
                    tuple(snapshot_ids),
                )
            conn.execute("DELETE FROM ingestion_jobs WHERE namespace = 'demo'")
            conn.commit()

    # Queue --------------------------------------------------------------------

    def queue_refetch(
        self,
        namespace: str,
        item_id: str,
        actor_key: str,
        request_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = conn.execute(
                """
                SELECT ri.item_id, ri.title, ws.normalized_url, ws.snapshot_hash
                FROM review_items ri
                JOIN web_snapshots ws ON ws.snapshot_id = ri.snapshot_id
                WHERE ri.namespace = ? AND ri.item_id = ?
                """,
                (namespace, item_id),
            ).fetchone()
            if item is None:
                conn.rollback()
                raise KeyError("review item not found")
            existing = conn.execute(
                """
                SELECT job_id, status FROM refetch_jobs
                WHERE namespace = ? AND item_id = ?
                  AND status IN ('queued', 'leased', 'retry')
                ORDER BY created_at DESC LIMIT 1
                """,
                (namespace, item_id),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return {
                    "job_id": str(existing["job_id"]),
                    "status": str(existing["status"]),
                    "created": False,
                }
            job_id = "refetch-" + secrets.token_urlsafe(16)
            conn.execute(
                """
                INSERT INTO refetch_jobs(
                    job_id, namespace, item_id, source_url, original_snapshot_hash,
                    title, status, attempts, max_attempts, available_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, 5, ?, ?, ?)
                """,
                (
                    job_id,
                    namespace,
                    item_id,
                    item["normalized_url"],
                    item["snapshot_hash"],
                    item["title"],
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="refetch_queued",
                object_type="review_item",
                object_id=item_id,
                request_id=request_id,
                now=timestamp,
            )
            conn.commit()
        return {"job_id": job_id, "status": "queued", "created": True}

    def claim_refetch_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: float | None = None,
    ) -> RefetchJob | None:
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE refetch_jobs
                SET status = 'retry', lease_owner = NULL, lease_expires_at = NULL,
                    available_at = ?, updated_at = ?, last_error_code = 'LEASE_EXPIRED'
                WHERE status = 'leased' AND lease_expires_at <= ?
                """,
                (timestamp, timestamp, timestamp),
            )
            row = conn.execute(
                """
                SELECT * FROM refetch_jobs
                WHERE status IN ('queued', 'retry') AND available_at <= ?
                ORDER BY created_at ASC, job_id ASC LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            lease_expires = timestamp + max(10, lease_seconds)
            attempts = int(row["attempts"]) + 1
            conn.execute(
                """
                UPDATE refetch_jobs
                SET status = 'leased', attempts = ?, lease_owner = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (attempts, worker_id, lease_expires, timestamp, row["job_id"]),
            )
            conn.commit()
        return RefetchJob(
            job_id=str(row["job_id"]),
            namespace=str(row["namespace"]),
            item_id=str(row["item_id"]),
            source_url=str(row["source_url"]),
            original_snapshot_hash=str(row["original_snapshot_hash"]),
            title=str(row["title"]),
            attempts=attempts,
            max_attempts=int(row["max_attempts"]),
            lease_owner=worker_id,
            lease_expires_at=lease_expires,
        )

    def complete_refetch_job(
        self,
        job: RefetchJob,
        outcome: str,
        *,
        now: float | None = None,
    ) -> None:
        if outcome not in {"unchanged", "ingestion_queued"}:
            raise ValueError("invalid refetch outcome")
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            changed = conn.execute(
                """
                UPDATE refetch_jobs
                SET status = 'done', outcome = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error_code = NULL, updated_at = ?
                WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (outcome, timestamp, job.job_id, job.lease_owner),
            ).rowcount
            conn.commit()
        if changed != 1:
            raise RuntimeError("refetch job lease no longer belongs to worker")

    def fail_refetch_job(
        self,
        job: RefetchJob,
        error_code: str,
        *,
        now: float | None = None,
        permanent: bool = False,
    ) -> str:
        timestamp = time.time() if now is None else now
        dead = permanent or job.attempts >= job.max_attempts
        status = "dead" if dead else "retry"
        available_at = timestamp if dead else timestamp + min(3600, 2 ** job.attempts)
        with self._write_lock, self._connect() as conn:
            changed = conn.execute(
                """
                UPDATE refetch_jobs
                SET status = ?, available_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error_code = ?, updated_at = ?
                WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (
                    status,
                    available_at,
                    error_code[:80],
                    timestamp,
                    job.job_id,
                    job.lease_owner,
                ),
            ).rowcount
            conn.commit()
        if changed != 1:
            raise RuntimeError("refetch job lease no longer belongs to worker")
        return status

    def enqueue_candidate(
        self,
        namespace: str,
        candidate: dict[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        snapshot_text = str(candidate.get("snapshot_text") or "")
        evidence_span_hash = str(candidate.get("evidence_span_hash") or "")
        if not snapshot_text or not evidence_span_hash:
            raise ValueError("candidate requires snapshot_text and evidence_span_hash")
        snapshot_hash = _digest(snapshot_text)
        snapshot_id = "snap-" + _digest(f"{namespace}:{snapshot_hash}")[:24]
        relative_path = self._write_immutable_snapshot(snapshot_hash, snapshot_text)
        timestamp = time.time() if now is None else now
        public_payload = {
            "snapshot_id": snapshot_id,
            "source_id": str(candidate.get("source_id") or ""),
            "normalized_url": str(candidate.get("normalized_url") or ""),
            "final_url": str(candidate.get("final_url") or candidate.get("normalized_url") or ""),
            "title": str(candidate.get("title") or ""),
            "institution": str(candidate.get("institution") or ""),
            "level": str(candidate.get("level") or "unverified"),
            "fetched_at": candidate.get("fetched_at"),
            "content_type": str(candidate.get("content_type") or "text/html"),
            "content_path": relative_path,
        }
        idempotency_key = _digest(f"{namespace}:{snapshot_hash}:{evidence_span_hash}")
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT OR IGNORE INTO web_snapshots(
                    snapshot_id, namespace, normalized_url, final_url, snapshot_hash,
                    content_path, content_type, fetched_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    namespace,
                    public_payload["normalized_url"],
                    public_payload["final_url"],
                    snapshot_hash,
                    relative_path,
                    public_payload["content_type"],
                    public_payload["fetched_at"],
                    timestamp,
                ),
            )
            existing = conn.execute(
                "SELECT job_id, status FROM ingestion_jobs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                conn.commit()
                return {"job_id": existing["job_id"], "status": existing["status"], "created": False}
            rejected = conn.execute(
                """
                SELECT ri.item_id
                FROM review_items ri
                JOIN web_snapshots ws ON ws.snapshot_id = ri.snapshot_id
                WHERE ws.namespace = ? AND ws.snapshot_hash = ? AND ri.status = 'rejected'
                LIMIT 1
                """,
                (namespace, snapshot_hash),
            ).fetchone()
            if rejected is not None:
                conn.commit()
                return {"item_id": rejected["item_id"], "status": "rejected", "created": False}
            job_id = "job-" + secrets.token_urlsafe(16)
            conn.execute(
                """
                INSERT INTO ingestion_jobs(
                    job_id, namespace, idempotency_key, snapshot_hash, evidence_span_hash,
                    payload_json, status, attempts, max_attempts, available_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', 0, 5, ?, ?, ?)
                """,
                (
                    job_id,
                    namespace,
                    idempotency_key,
                    snapshot_hash,
                    evidence_span_hash,
                    json.dumps(public_payload, ensure_ascii=False, separators=(",", ":")),
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            conn.commit()
        return {"job_id": job_id, "status": "queued", "created": True}

    def claim_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        now: float | None = None,
    ) -> IngestionJob | None:
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'retry', lease_owner = NULL, lease_expires_at = NULL,
                    available_at = ?, updated_at = ?, last_error_code = 'LEASE_EXPIRED'
                WHERE status = 'leased' AND lease_expires_at <= ?
                """,
                (timestamp, timestamp, timestamp),
            )
            row = conn.execute(
                """
                SELECT * FROM ingestion_jobs
                WHERE status IN ('queued', 'retry') AND available_at <= ?
                ORDER BY created_at ASC, job_id ASC
                LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            lease_expires = timestamp + max(10, lease_seconds)
            conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'leased', attempts = attempts + 1, lease_owner = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (worker_id, lease_expires, timestamp, row["job_id"]),
            )
            conn.commit()
        return IngestionJob(
            job_id=row["job_id"],
            namespace=row["namespace"],
            snapshot_hash=row["snapshot_hash"],
            evidence_span_hash=row["evidence_span_hash"],
            payload=json.loads(row["payload_json"] or "{}"),
            attempts=int(row["attempts"]) + 1,
            max_attempts=int(row["max_attempts"]),
            lease_owner=worker_id,
            lease_expires_at=lease_expires,
        )

    def fail_job(
        self,
        job: IngestionJob,
        error_code: str,
        *,
        now: float | None = None,
        permanent: bool = False,
    ) -> str:
        timestamp = time.time() if now is None else now
        dead = permanent or job.attempts >= job.max_attempts
        status = "dead" if dead else "retry"
        available_at = timestamp if dead else timestamp + min(3600, 2 ** job.attempts)
        with self._write_lock, self._connect() as conn:
            changed = conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = ?, available_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error_code = ?, updated_at = ?
                WHERE job_id = ? AND status = 'leased' AND lease_owner = ?
                """,
                (status, available_at, error_code, timestamp, job.job_id, job.lease_owner),
            ).rowcount
            conn.commit()
        if changed != 1:
            raise RuntimeError("job lease no longer belongs to worker")
        return status

    # Draft creation ------------------------------------------------------------

    def create_draft(
        self,
        job: IngestionJob,
        *,
        title: str,
        scope: str,
        category: str,
        chunks: list[str],
        model_text: str,
        actor_key: str,
    ) -> str:
        self._validate_category(category)
        if scope not in {"campus", "general"}:
            raise ValueError("invalid review scope")
        clean_chunks = [text.strip() for text in chunks if text and text.strip()]
        if not clean_chunks:
            raise ValueError("draft requires at least one chunk")
        now = time.time()
        item_id = "item-" + secrets.token_urlsafe(16)
        raw_text = self.read_snapshot(job.payload["content_path"])
        snapshot_id = str(job.payload["snapshot_id"])
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            lease = conn.execute(
                "SELECT status, lease_owner FROM ingestion_jobs WHERE job_id = ?",
                (job.job_id,),
            ).fetchone()
            if lease is None or lease["status"] != "leased" or lease["lease_owner"] != job.lease_owner:
                conn.rollback()
                raise RuntimeError("job lease no longer belongs to worker")
            existing = conn.execute(
                "SELECT item_id FROM review_items WHERE snapshot_id = ? LIMIT 1",
                (snapshot_id,),
            ).fetchone()
            if existing is not None:
                conn.execute(
                    "UPDATE ingestion_jobs SET status = 'done', lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                    (now, job.job_id),
                )
                conn.commit()
                return existing["item_id"]
            conn.execute(
                """
                INSERT INTO review_items(
                    item_id, namespace, snapshot_id, title, scope, category,
                    ttl_days, status, current_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', 1, ?, ?)
                """,
                (
                    item_id,
                    job.namespace,
                    snapshot_id,
                    title.strip()[:200] or "未命名公开资料",
                    scope,
                    category,
                    _TTL_LIMITS[category],
                    now,
                    now,
                ),
            )
            self._insert_version(conn, item_id, 1, "raw", raw_text, actor_key, now)
            model_version = self._insert_version(conn, item_id, 1, "model", model_text, actor_key, now)
            self._insert_chunks(conn, item_id, model_version, clean_chunks)
            conn.execute(
                """
                UPDATE ingestion_jobs
                SET status = 'done', lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (now, job.job_id),
            )
            self._audit(
                conn,
                namespace=job.namespace,
                actor_key=actor_key,
                action="draft_created",
                object_type="review_item",
                object_id=item_id,
                after_hash=_digest(model_text),
                request_id=job.job_id,
                now=now,
            )
            conn.commit()
        return item_id

    # Reviewer operations -------------------------------------------------------

    def list_items_page(
        self,
        namespace: str,
        *,
        status: str = "",
        limit: int = 50,
        cursor: tuple[float, str] | None = None,
    ) -> tuple[list[dict[str, Any]], tuple[float, str] | None]:
        self._validate_namespace(namespace)
        page_size = min(max(limit, 1), 100)
        sql = """
            SELECT ri.*, ws.normalized_url, ws.fetched_at
            FROM review_items ri
            JOIN web_snapshots ws ON ws.snapshot_id = ri.snapshot_id
            WHERE ri.namespace = ?
        """
        params: list[Any] = [namespace]
        if status:
            sql += " AND ri.status = ?"
            params.append(status)
        if cursor is not None:
            sql += " AND (ri.updated_at < ? OR (ri.updated_at = ? AND ri.item_id < ?))"
            params.extend((cursor[0], cursor[0], cursor[1]))
        sql += " ORDER BY ri.updated_at DESC, ri.item_id DESC LIMIT ?"
        params.append(page_size + 1)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        has_more = len(rows) > page_size
        page = rows[:page_size]
        next_cursor = (
            (float(page[-1]["updated_at"]), str(page[-1]["item_id"]))
            if has_more and page else None
        )
        return [dict(row) for row in page], next_cursor

    def list_items(self, namespace: str, *, status: str = "", limit: int = 50) -> list[dict[str, Any]]:
        return self.list_items_page(namespace, status=status, limit=limit)[0]

    def get_item(self, namespace: str, item_id: str) -> dict[str, Any] | None:
        self._validate_namespace(namespace)
        with self._connect() as conn:
            item = conn.execute(
                """
                SELECT ri.*, ws.normalized_url, ws.final_url, ws.content_path,
                       ws.content_type, ws.fetched_at, ws.snapshot_hash
                FROM review_items ri
                JOIN web_snapshots ws ON ws.snapshot_id = ri.snapshot_id
                WHERE ri.namespace = ? AND ri.item_id = ?
                """,
                (namespace, item_id),
            ).fetchone()
            if item is None:
                return None
            versions = conn.execute(
                """
                SELECT version_id, version_number, kind, content_text, content_hash, actor_key, created_at
                FROM review_versions WHERE item_id = ?
                ORDER BY version_number ASC, kind ASC
                """,
                (item_id,),
            ).fetchall()
            chunks = conn.execute(
                """
                SELECT chunk_id, version_id, position, content_text, content_hash,
                       approved, approved_by, approved_at, expires_at
                FROM review_chunks WHERE item_id = ?
                ORDER BY position ASC
                """,
                (item_id,),
            ).fetchall()
        payload = dict(item)
        payload["raw_snapshot"] = self.read_snapshot(payload["content_path"])
        payload["versions"] = [dict(row) for row in versions]
        payload["chunks"] = [dict(row) for row in chunks]
        payload.pop("content_path", None)
        return payload

    def start_review(self, namespace: str, item_id: str, actor_key: str, request_id: str) -> None:
        self._transition(namespace, item_id, {"draft"}, "in_review", actor_key, request_id)

    def edit_item(
        self,
        namespace: str,
        item_id: str,
        *,
        content: str,
        chunks: list[str],
        actor_key: str,
        request_id: str,
    ) -> int:
        clean_chunks = [value.strip() for value in chunks if value and value.strip()]
        if not content.strip() or not clean_chunks:
            raise ValueError("edited review version requires content and chunks")
        now = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = self._owned_item(conn, namespace, item_id)
            if item["status"] not in {"draft", "in_review"}:
                conn.rollback()
                raise ValueError("item cannot be edited in current state")
            version_number = int(item["current_version"]) + 1
            version_id = self._insert_version(
                conn,
                item_id,
                version_number,
                "human",
                content.strip(),
                actor_key,
                now,
            )
            self._insert_chunks(conn, item_id, version_id, clean_chunks)
            conn.execute(
                "UPDATE review_items SET current_version = ?, status = 'in_review', updated_at = ? WHERE item_id = ?",
                (version_number, now, item_id),
            )
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="version_edited",
                object_type="review_item",
                object_id=item_id,
                before_hash=None,
                after_hash=_digest(content.strip()),
                request_id=request_id,
                now=now,
            )
            conn.commit()
        return version_number

    def set_chunk_approval(
        self,
        namespace: str,
        item_id: str,
        chunk_id: str,
        approved: bool,
        actor_key: str,
        request_id: str,
    ) -> None:
        now = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = self._owned_item(conn, namespace, item_id)
            if item["status"] not in {"draft", "in_review"}:
                conn.rollback()
                raise ValueError("item cannot be reviewed in current state")
            chunk = conn.execute(
                """
                SELECT rc.chunk_id FROM review_chunks rc
                JOIN review_versions rv ON rv.version_id = rc.version_id
                WHERE rc.item_id = ? AND rc.chunk_id = ? AND rv.version_number = ?
                """,
                (item_id, chunk_id, item["current_version"]),
            ).fetchone()
            if chunk is None:
                conn.rollback()
                raise KeyError("chunk not found in current version")
            conn.execute(
                """
                UPDATE review_chunks
                SET approved = ?, approved_by = ?, approved_at = ?
                WHERE chunk_id = ?
                """,
                (int(approved), actor_key if approved else None, now if approved else None, chunk_id),
            )
            conn.execute("UPDATE review_items SET status = 'in_review', updated_at = ? WHERE item_id = ?", (now, item_id))
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="chunk_approved" if approved else "chunk_unapproved",
                object_type="review_chunk",
                object_id=chunk_id,
                after_hash=str(int(approved)),
                request_id=request_id,
                now=now,
            )
            conn.commit()

    def approve_item(
        self,
        namespace: str,
        item_id: str,
        *,
        category: str,
        ttl_days: int,
        actor_key: str,
        request_id: str,
    ) -> None:
        self._validate_category(category)
        if ttl_days < 1 or ttl_days > _TTL_LIMITS[category]:
            raise ValueError("ttl exceeds category maximum")
        now = time.time()
        expires_at = now + ttl_days * 24 * 60 * 60
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = self._owned_item(conn, namespace, item_id)
            if item["status"] not in {"draft", "in_review"}:
                conn.rollback()
                raise ValueError("item cannot be approved in current state")
            rows = conn.execute(
                """
                SELECT rc.chunk_id, rc.content_text
                FROM review_chunks rc
                JOIN review_versions rv ON rv.version_id = rc.version_id
                WHERE rc.item_id = ? AND rv.version_number = ? AND rc.approved = 1
                ORDER BY rc.position
                """,
                (item_id, item["current_version"]),
            ).fetchall()
            if not rows:
                conn.rollback()
                raise ValueError("at least one current chunk must be approved")
            approved_text = "\n\n".join(row["content_text"] for row in rows)
            self._insert_version(
                conn,
                item_id,
                int(item["current_version"]),
                "approved",
                approved_text,
                actor_key,
                now,
            )
            conn.execute(
                "UPDATE review_chunks SET expires_at = ? WHERE chunk_id IN ({})".format(
                    ",".join("?" for _ in rows),
                ),
                (expires_at, *(row["chunk_id"] for row in rows)),
            )
            conn.execute(
                """
                UPDATE review_items
                SET status = 'pending_publish', category = ?, ttl_days = ?, updated_at = ?
                WHERE item_id = ?
                """,
                (category, ttl_days, now, item_id),
            )
            self._queue_publish_locked(
                conn,
                namespace=namespace,
                reason=f"item_approved:{item_id}:v{item['current_version']}",
                now=now,
            )
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="item_approved",
                object_type="review_item",
                object_id=item_id,
                before_hash=None,
                after_hash=_digest(approved_text),
                request_id=request_id,
                now=now,
            )
            conn.commit()

    def reject_item(self, namespace: str, item_id: str, actor_key: str, request_id: str) -> None:
        self._transition(
            namespace,
            item_id,
            {"draft", "in_review"},
            "rejected",
            actor_key,
            request_id,
        )

    def revoke_item(
        self,
        namespace: str,
        item_id: str,
        actor_key: str,
        request_id: str,
    ) -> None:
        self._validate_namespace(namespace)
        now = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = self._owned_item(conn, namespace, item_id)
            if item["status"] not in {"active", "pending_publish", "publish_failed"}:
                conn.rollback()
                raise ValueError("item cannot be revoked in current state")
            conn.execute(
                "UPDATE review_items SET status = 'revoked', active_generation_id = NULL, updated_at = ? WHERE item_id = ?",
                (now, item_id),
            )
            self._queue_publish_locked(
                conn,
                namespace=namespace,
                reason=f"item_revoked:{item_id}",
                now=now,
            )
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="item_revoked",
                object_type="review_item",
                object_id=item_id,
                before_hash=item["active_generation_id"],
                after_hash=None,
                request_id=request_id,
                now=now,
            )
            conn.commit()

    def retry_publish(
        self,
        namespace: str,
        item_id: str,
        actor_key: str,
        request_id: str,
    ) -> None:
        self._validate_namespace(namespace)
        now = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = self._owned_item(conn, namespace, item_id)
            if item["status"] != "publish_failed":
                conn.rollback()
                raise ValueError("item is not in publish_failed state")
            job = conn.execute(
                """
                SELECT job_id, status FROM publish_jobs
                WHERE namespace = ? AND status IN ('retry', 'dead')
                ORDER BY created_at DESC LIMIT 1
                """,
                (namespace,),
            ).fetchone()
            if job and job["status"] == "retry":
                conn.execute(
                    "UPDATE publish_jobs SET status = 'queued', available_at = ?, updated_at = ? WHERE job_id = ?",
                    (now, now, job["job_id"]),
                )
            else:
                self._queue_publish_locked(
                    conn,
                    namespace=namespace,
                    reason=f"manual_publish_retry:{item_id}",
                    now=now,
                )
            conn.execute(
                "UPDATE review_items SET status = 'pending_publish', updated_at = ? WHERE item_id = ?",
                (now, item_id),
            )
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="publish_retried",
                object_type="review_item",
                object_id=item_id,
                before_hash=None,
                after_hash=None,
                request_id=request_id,
                now=now,
            )
            conn.commit()

    # Atomic index publishing -------------------------------------------------

    @staticmethod
    def _queue_publish_locked(
        conn: sqlite3.Connection,
        *,
        namespace: str,
        reason: str,
        now: float,
    ) -> PublishJob:
        generation_id = "gen-" + secrets.token_urlsafe(16)
        job_id = "pub-" + secrets.token_urlsafe(16)
        conn.execute(
            """
            INSERT INTO publish_generations(generation_id, namespace, status, created_at)
            VALUES (?, ?, 'building', ?)
            """,
            (generation_id, namespace, now),
        )
        conn.execute(
            """
            INSERT INTO publish_jobs(
                job_id, namespace, generation_id, reason, status, attempts,
                max_attempts, available_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'queued', 0, 5, ?, ?, ?)
            """,
            (job_id, namespace, generation_id, reason[:200], now, now, now),
        )
        return PublishJob(
            job_id=job_id,
            namespace=namespace,
            generation_id=generation_id,
            reason=reason[:200],
            attempts=0,
            max_attempts=5,
            lease_owner="",
            lease_expires_at=0,
        )

    def claim_publish_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 120,
        now: float | None = None,
    ) -> PublishJob | None:
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'retry', lease_owner = NULL, lease_expires_at = NULL,
                    available_at = ?, updated_at = ?
                WHERE status = 'leased' AND lease_expires_at <= ?
                """,
                (timestamp, timestamp, timestamp),
            )
            row = conn.execute(
                """
                SELECT pj.* FROM publish_jobs pj
                WHERE pj.status IN ('queued', 'retry') AND pj.available_at <= ?
                  AND NOT EXISTS (
                    SELECT 1 FROM publish_jobs active
                    WHERE active.namespace = pj.namespace AND active.status = 'leased'
                  )
                ORDER BY pj.created_at ASC, pj.job_id ASC
                LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                conn.commit()
                return None
            lease_expires = timestamp + max(30, lease_seconds)
            attempts = int(row["attempts"]) + 1
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'leased', attempts = ?, lease_owner = ?,
                    lease_expires_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (attempts, worker_id, lease_expires, timestamp, row["job_id"]),
            )
            conn.execute(
                "UPDATE publish_generations SET status = 'building' WHERE generation_id = ?",
                (row["generation_id"],),
            )
            conn.execute(
                """
                UPDATE review_items SET status = 'pending_publish', updated_at = ?
                WHERE namespace = ? AND status = 'publish_failed'
                """,
                (timestamp, row["namespace"]),
            )
            conn.commit()
        return PublishJob(
            job_id=str(row["job_id"]),
            namespace=str(row["namespace"]),
            generation_id=str(row["generation_id"]),
            reason=str(row["reason"]),
            attempts=attempts,
            max_attempts=int(row["max_attempts"]),
            lease_owner=worker_id,
            lease_expires_at=lease_expires,
        )

    def materialize_publish_documents(
        self,
        job: PublishJob,
        *,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_publish_lease(conn, job)
            existing = conn.execute(
                """
                SELECT document_id, item_id, chunk_id, content_text, content_hash,
                       metadata_json, expires_at
                FROM publish_documents WHERE generation_id = ? ORDER BY document_id
                """,
                (job.generation_id,),
            ).fetchall()
            if not existing:
                rows = conn.execute(
                    """
                    SELECT ri.item_id, ri.title, ri.scope, ri.category, ri.ttl_days,
                           ws.normalized_url, ws.fetched_at, rc.chunk_id, rc.content_text,
                           rc.content_hash, rc.expires_at
                    FROM review_items ri
                    JOIN web_snapshots ws ON ws.snapshot_id = ri.snapshot_id
                    JOIN review_versions rv
                      ON rv.item_id = ri.item_id AND rv.version_number = ri.current_version
                     AND rv.kind IN ('model', 'human')
                    JOIN review_chunks rc
                      ON rc.version_id = rv.version_id AND rc.approved = 1
                    WHERE ri.namespace = ?
                      AND ri.status IN ('active', 'pending_publish', 'publish_failed')
                      AND rc.expires_at > ?
                    ORDER BY ri.item_id, rc.position
                    """,
                    (job.namespace, timestamp),
                ).fetchall()
                for row in rows:
                    document_id = f"{row['item_id']}:{row['chunk_id']}"
                    metadata = {
                        "item_id": row["item_id"],
                        "chunk_id": row["chunk_id"],
                        "title": row["title"],
                        "scope": row["scope"],
                        "category": row["category"],
                        "ttl_days": int(row["ttl_days"]),
                        "source": row["normalized_url"],
                        "fetched_at": row["fetched_at"],
                        "namespace": job.namespace,
                        "generation_id": job.generation_id,
                    }
                    conn.execute(
                        """
                        INSERT INTO publish_documents(
                            generation_id, document_id, item_id, chunk_id,
                            content_text, content_hash, metadata_json, expires_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            job.generation_id,
                            document_id,
                            row["item_id"],
                            row["chunk_id"],
                            row["content_text"],
                            row["content_hash"],
                            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                            row["expires_at"],
                        ),
                    )
                existing = conn.execute(
                    """
                    SELECT document_id, item_id, chunk_id, content_text, content_hash,
                           metadata_json, expires_at
                    FROM publish_documents WHERE generation_id = ? ORDER BY document_id
                    """,
                    (job.generation_id,),
                ).fetchall()
            conn.commit()
        return [
            {
                "document_id": row["document_id"],
                "item_id": row["item_id"],
                "chunk_id": row["chunk_id"],
                "content": row["content_text"],
                "content_hash": row["content_hash"],
                "metadata": json.loads(row["metadata_json"]),
                "expires_at": float(row["expires_at"]),
            }
            for row in existing
        ]

    def activate_publish_job(
        self,
        job: PublishJob,
        *,
        manifest_path: str,
        manifest_hash: str,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_publish_lease(conn, job)
            generation = conn.execute(
                "SELECT created_at FROM publish_generations WHERE generation_id = ?",
                (job.generation_id,),
            ).fetchone()
            active = conn.execute(
                """
                SELECT ais.generation_id, pg.created_at
                FROM active_index_state ais
                JOIN publish_generations pg ON pg.generation_id = ais.generation_id
                WHERE ais.namespace = ?
                """,
                (job.namespace,),
            ).fetchone()
            if active and float(active["created_at"]) > float(generation["created_at"]):
                conn.execute(
                    "UPDATE publish_generations SET status = 'orphan' WHERE generation_id = ?",
                    (job.generation_id,),
                )
                conn.execute(
                    "UPDATE publish_jobs SET status = 'done', lease_owner = NULL, lease_expires_at = NULL, updated_at = ? WHERE job_id = ?",
                    (timestamp, job.job_id),
                )
                conn.commit()
                return False
            previous = str(active["generation_id"]) if active else None
            if previous and previous != job.generation_id:
                conn.execute(
                    "UPDATE publish_generations SET status = 'verified' WHERE generation_id = ?",
                    (previous,),
                )
            conn.execute(
                """
                UPDATE publish_generations
                SET status = 'active', manifest_path = ?, manifest_hash = ?, activated_at = ?
                WHERE generation_id = ?
                """,
                (manifest_path, manifest_hash, timestamp, job.generation_id),
            )
            conn.execute(
                """
                INSERT INTO active_index_state(namespace, generation_id, previous_generation_id, activated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace) DO UPDATE SET
                    generation_id = excluded.generation_id,
                    previous_generation_id = excluded.previous_generation_id,
                    activated_at = excluded.activated_at
                """,
                (job.namespace, job.generation_id, previous, timestamp),
            )
            included_rows = conn.execute(
                "SELECT DISTINCT item_id FROM publish_documents WHERE generation_id = ?",
                (job.generation_id,),
            ).fetchall()
            included = [str(row["item_id"]) for row in included_rows]
            conn.execute(
                """
                UPDATE review_items
                SET status = 'expired', active_generation_id = NULL, updated_at = ?
                WHERE namespace = ? AND status IN ('active', 'pending_publish', 'publish_failed')
                """,
                (timestamp, job.namespace),
            )
            if included:
                placeholders = ",".join("?" for _ in included)
                conn.execute(
                    f"""
                    UPDATE review_items
                    SET status = 'active', active_generation_id = ?, updated_at = ?
                    WHERE namespace = ? AND item_id IN ({placeholders})
                    """,
                    (job.generation_id, timestamp, job.namespace, *included),
                )
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = 'done', lease_owner = NULL, lease_expires_at = NULL,
                    last_error_code = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (timestamp, job.job_id),
            )
            conn.commit()
        return True

    def fail_publish_job(
        self,
        job: PublishJob,
        error_code: str,
        *,
        now: float | None = None,
    ) -> str:
        timestamp = time.time() if now is None else now
        permanent = job.attempts >= job.max_attempts
        status = "dead" if permanent else "retry"
        delay = 0 if permanent else min(300, 2 ** min(job.attempts, 8))
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._assert_publish_lease(conn, job)
            conn.execute(
                """
                UPDATE publish_jobs
                SET status = ?, available_at = ?, lease_owner = NULL,
                    lease_expires_at = NULL, last_error_code = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (status, timestamp + delay, error_code[:80], timestamp, job.job_id),
            )
            conn.execute(
                "UPDATE publish_generations SET status = 'failed' WHERE generation_id = ?",
                (job.generation_id,),
            )
            conn.execute(
                """
                UPDATE review_items SET status = 'publish_failed', updated_at = ?
                WHERE namespace = ? AND status = 'pending_publish'
                """,
                (timestamp, job.namespace),
            )
            conn.commit()
        return status

    def expire_due_chunks(self, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            namespaces: list[str] = []
            for namespace in sorted(_NAMESPACES):
                changed = conn.execute(
                    """
                    UPDATE review_items
                    SET status = 'expired', active_generation_id = NULL, updated_at = ?
                    WHERE namespace = ? AND status IN ('active', 'pending_publish', 'publish_failed')
                      AND NOT EXISTS (
                        SELECT 1 FROM review_chunks rc
                        JOIN review_versions rv ON rv.version_id = rc.version_id
                        WHERE rc.item_id = review_items.item_id
                          AND rv.version_number = review_items.current_version
                          AND rc.approved = 1 AND rc.expires_at > ?
                      )
                    """,
                    (timestamp, namespace, timestamp),
                ).rowcount
                if changed:
                    namespaces.append(namespace)
                    self._queue_publish_locked(
                        conn,
                        namespace=namespace,
                        reason="approved_chunks_expired",
                        now=timestamp,
                    )
            conn.commit()
        return len(namespaces)

    def get_active_generation(self, namespace: str) -> dict[str, Any] | None:
        self._validate_namespace(namespace)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ais.namespace, ais.generation_id, ais.previous_generation_id,
                       ais.activated_at, pg.manifest_path, pg.manifest_hash
                FROM active_index_state ais
                JOIN publish_generations pg ON pg.generation_id = ais.generation_id
                WHERE ais.namespace = ? AND pg.status = 'active'
                """,
                (namespace,),
            ).fetchone()
        return dict(row) if row else None

    def get_generation(self, namespace: str, generation_id: str) -> dict[str, Any] | None:
        self._validate_namespace(namespace)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM publish_generations WHERE namespace = ? AND generation_id = ?",
                (namespace, generation_id),
            ).fetchone()
        return dict(row) if row else None

    def get_generation_state(self, namespace: str) -> dict[str, Any]:
        self._validate_namespace(namespace)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT ais.generation_id, ais.previous_generation_id, ais.activated_at,
                       current.status AS active_status, previous.status AS previous_status
                FROM active_index_state ais
                JOIN publish_generations current ON current.generation_id = ais.generation_id
                LEFT JOIN publish_generations previous
                  ON previous.generation_id = ais.previous_generation_id
                WHERE ais.namespace = ?
                """,
                (namespace,),
            ).fetchone()
            busy = conn.execute(
                """
                SELECT 1 FROM publish_jobs
                WHERE namespace = ? AND status IN ('queued', 'leased', 'retry') LIMIT 1
                """,
                (namespace,),
            ).fetchone()
        if row is None:
            return {
                "namespace": namespace,
                "active_generation_id": None,
                "previous_generation_id": None,
                "activated_at": None,
                "can_rollback": False,
                "publish_busy": bool(busy),
            }
        return {
            "namespace": namespace,
            "active_generation_id": str(row["generation_id"]),
            "previous_generation_id": (
                str(row["previous_generation_id"])
                if row["previous_generation_id"] is not None else None
            ),
            "activated_at": float(row["activated_at"]),
            "can_rollback": bool(
                row["previous_generation_id"]
                and row["previous_status"] == "verified"
                and not busy
            ),
            "publish_busy": bool(busy),
        }

    def rollback_active_generation(
        self,
        namespace: str,
        actor_key: str,
        request_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, str]:
        self._validate_namespace(namespace)
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            state = conn.execute(
                """
                SELECT ais.generation_id, ais.previous_generation_id, previous.status AS previous_status
                FROM active_index_state ais
                LEFT JOIN publish_generations previous
                  ON previous.generation_id = ais.previous_generation_id
                WHERE ais.namespace = ?
                """,
                (namespace,),
            ).fetchone()
            if (
                state is None
                or not state["previous_generation_id"]
                or state["previous_status"] != "verified"
            ):
                conn.rollback()
                raise ValueError("no verified previous generation")
            busy = conn.execute(
                """
                SELECT 1 FROM publish_jobs
                WHERE namespace = ? AND status IN ('queued', 'leased', 'retry') LIMIT 1
                """,
                (namespace,),
            ).fetchone()
            if busy is not None:
                conn.rollback()
                raise RuntimeError("publication is busy")
            current_id = str(state["generation_id"])
            previous_id = str(state["previous_generation_id"])
            expired = conn.execute(
                """
                SELECT 1 FROM publish_documents
                WHERE generation_id = ? AND expires_at <= ? LIMIT 1
                """,
                (previous_id, timestamp),
            ).fetchone()
            if expired is not None:
                conn.rollback()
                raise ValueError("previous generation contains expired documents")
            included = [
                str(row["item_id"])
                for row in conn.execute(
                    "SELECT DISTINCT item_id FROM publish_documents WHERE generation_id = ?",
                    (previous_id,),
                ).fetchall()
            ]
            conn.execute(
                "UPDATE publish_generations SET status = 'verified' WHERE generation_id = ?",
                (current_id,),
            )
            conn.execute(
                "UPDATE publish_generations SET status = 'active', activated_at = ? WHERE generation_id = ?",
                (timestamp, previous_id),
            )
            conn.execute(
                """
                UPDATE active_index_state
                SET generation_id = ?, previous_generation_id = ?, activated_at = ?
                WHERE namespace = ?
                """,
                (previous_id, current_id, timestamp, namespace),
            )
            conn.execute(
                """
                UPDATE review_items
                SET status = 'expired', active_generation_id = NULL, updated_at = ?
                WHERE namespace = ? AND status = 'active'
                """,
                (timestamp, namespace),
            )
            if included:
                placeholders = ",".join("?" for _ in included)
                conn.execute(
                    f"""
                    UPDATE review_items
                    SET status = 'active', active_generation_id = ?, updated_at = ?
                    WHERE namespace = ? AND item_id IN ({placeholders})
                    """,
                    (previous_id, timestamp, namespace, *included),
                )
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="generation_rolled_back",
                object_type="publish_generation",
                object_id=previous_id,
                before_hash=current_id,
                after_hash=previous_id,
                request_id=request_id,
                now=timestamp,
            )
            conn.commit()
        return {"generation_id": previous_id, "previous_generation_id": current_id}

    # Source trust proposals ---------------------------------------------------

    def create_source_trust_proposal(
        self,
        namespace: str,
        item_id: str,
        proposal: dict[str, Any],
        actor_key: str,
        request_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        timestamp = time.time() if now is None else now
        proposal_id = "proposal-" + secrets.token_urlsafe(16)
        serialized = json.dumps(proposal, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._owned_item(conn, namespace, item_id)
            conn.execute(
                """
                INSERT INTO source_trust_proposals(
                    proposal_id, namespace, item_id, proposal_json, status,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, ?)
                """,
                (proposal_id, namespace, item_id, serialized, actor_key, timestamp),
            )
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="source_trust_proposed",
                object_type="source_trust_proposal",
                object_id=proposal_id,
                after_hash=_digest(serialized),
                request_id=request_id,
                now=timestamp,
            )
            conn.commit()
        return {
            "proposal_id": proposal_id,
            "namespace": namespace,
            "item_id": item_id,
            "proposal": proposal,
            "status": "draft",
            "created_at": timestamp,
        }

    def list_source_trust_proposals(
        self,
        namespace: str,
        *,
        status: str = "draft",
    ) -> list[dict[str, Any]]:
        self._validate_namespace(namespace)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT proposal_id, namespace, item_id, proposal_json, status,
                       created_by, created_at
                FROM source_trust_proposals
                WHERE namespace = ? AND (? = '' OR status = ?)
                ORDER BY created_at ASC, proposal_id ASC
                """,
                (namespace, status, status),
            ).fetchall()
        return [
            {
                **dict(row),
                "proposal": json.loads(row["proposal_json"]),
            }
            for row in rows
        ]

    def mark_source_trust_proposals_exported(
        self,
        namespace: str,
        proposal_ids: list[str],
        actor_key: str,
        request_id: str,
        *,
        now: float | None = None,
    ) -> None:
        self._validate_namespace(namespace)
        if not proposal_ids:
            raise ValueError("no proposals selected")
        timestamp = time.time() if now is None else now
        placeholders = ",".join("?" for _ in proposal_ids)
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = conn.execute(
                f"""
                UPDATE source_trust_proposals SET status = 'exported'
                WHERE namespace = ? AND status = 'draft'
                  AND proposal_id IN ({placeholders})
                """,
                (namespace, *proposal_ids),
            ).rowcount
            if changed != len(set(proposal_ids)):
                conn.rollback()
                raise ValueError("proposal export set changed")
            for proposal_id in proposal_ids:
                self._audit(
                    conn,
                    namespace=namespace,
                    actor_key=actor_key,
                    action="source_trust_exported",
                    object_type="source_trust_proposal",
                    object_id=proposal_id,
                    request_id=request_id,
                    now=timestamp,
                )
            conn.commit()

    @staticmethod
    def _assert_publish_lease(conn: sqlite3.Connection, job: PublishJob) -> None:
        row = conn.execute(
            "SELECT status, lease_owner FROM publish_jobs WHERE job_id = ? AND generation_id = ?",
            (job.job_id, job.generation_id),
        ).fetchone()
        if row is None or row["status"] != "leased" or row["lease_owner"] != job.lease_owner:
            raise ValueError("publish job lease is no longer owned")

    # Snapshot and helpers ------------------------------------------------------

    def read_snapshot(self, relative_path: str) -> str:
        path = (self.data_dir / relative_path).resolve()
        if not path.is_relative_to(self.data_dir.resolve()):
            raise ValueError("snapshot path escapes evidence data directory")
        return path.read_text(encoding="utf-8")

    def _write_immutable_snapshot(self, snapshot_hash: str, content: str) -> str:
        relative = Path("raw") / snapshot_hash[:2] / f"{snapshot_hash}.txt"
        target = (self.data_dir / relative).resolve()
        if not target.is_relative_to(self.data_dir.resolve()):
            raise ValueError("snapshot path escapes evidence data directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if _digest(target.read_bytes()) != snapshot_hash:
                raise RuntimeError("immutable snapshot hash collision")
            return relative.as_posix()
        temporary = target.with_name(f".{target.name}.{secrets.token_hex(6)}.tmp")
        with temporary.open("xb") as handle:
            data = content.encode("utf-8")
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        return relative.as_posix()

    @staticmethod
    def _insert_version(
        conn: sqlite3.Connection,
        item_id: str,
        version_number: int,
        kind: str,
        content: str,
        actor_key: str,
        now: float,
    ) -> str:
        version_id = "ver-" + secrets.token_urlsafe(16)
        conn.execute(
            """
            INSERT INTO review_versions(
                version_id, item_id, version_number, kind, content_text,
                content_hash, actor_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (version_id, item_id, version_number, kind, content, _digest(content), actor_key, now),
        )
        return version_id

    @staticmethod
    def _insert_chunks(
        conn: sqlite3.Connection,
        item_id: str,
        version_id: str,
        chunks: list[str],
    ) -> None:
        for position, content in enumerate(chunks):
            conn.execute(
                """
                INSERT INTO review_chunks(
                    chunk_id, item_id, version_id, position, content_text, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "chunk-" + secrets.token_urlsafe(16),
                    item_id,
                    version_id,
                    position,
                    content,
                    _digest(content),
                ),
            )

    def _transition(
        self,
        namespace: str,
        item_id: str,
        allowed: set[str],
        target: str,
        actor_key: str,
        request_id: str,
    ) -> None:
        now = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            item = self._owned_item(conn, namespace, item_id)
            if item["status"] not in allowed:
                conn.rollback()
                raise ValueError("invalid review state transition")
            conn.execute("UPDATE review_items SET status = ?, updated_at = ? WHERE item_id = ?", (target, now, item_id))
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action=f"status_{target}",
                object_type="review_item",
                object_id=item_id,
                before_hash=item["status"],
                after_hash=target,
                request_id=request_id,
                now=now,
            )
            conn.commit()

    @staticmethod
    def _owned_item(conn: sqlite3.Connection, namespace: str, item_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM review_items WHERE namespace = ? AND item_id = ?",
            (namespace, item_id),
        ).fetchone()
        if row is None:
            raise KeyError("review item not found")
        return row

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        *,
        namespace: str,
        actor_key: str,
        action: str,
        object_type: str,
        object_id: str,
        request_id: str,
        now: float,
        before_hash: str | None = None,
        after_hash: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO review_audit(
                audit_id, namespace, actor_key, action, object_type, object_id,
                before_hash, after_hash, request_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "audit-" + secrets.token_urlsafe(16),
                namespace,
                actor_key,
                action,
                object_type,
                object_id,
                before_hash,
                after_hash,
                request_id,
                now,
            ),
        )

    @staticmethod
    def _validate_namespace(namespace: str) -> None:
        if namespace not in _NAMESPACES:
            raise ValueError("invalid review namespace")

    @staticmethod
    def _validate_category(category: str) -> None:
        if category not in _TTL_LIMITS:
            raise ValueError("invalid review category")
