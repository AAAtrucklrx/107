"""Thread-safe SQLite storage for Web sessions, runs, events, and history."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from xiaowo_web.auth.models import Principal
from xiaowo_web.settings import WebSettings
from xiaowo_web.storage.cipher import FieldCipher


TERMINAL_RUN_STATES = frozenset({"completed", "cancelled", "failed"})
RUN_STATES = frozenset({"queued", "running", *TERMINAL_RUN_STATES})
HISTORY_RETENTION_SECONDS = 90 * 24 * 60 * 60
RUN_TOMBSTONE_RETENTION_SECONDS = 24 * 60 * 60


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    owner_key: str
    mode: str
    status: str
    created_at: float
    updated_at: float
    expires_at: float
    cancel_requested: bool
    error_code: str | None


class WebStore:
    """Small single-node store with serialized writes and per-call connections."""

    def __init__(self, settings: WebSettings) -> None:
        self.settings = settings
        self.db_path = Path(settings.app_db_path)
        self.schema_path = Path(settings.schema_web_path)
        self._write_lock = threading.RLock()
        self._event_condition = threading.Condition()
        self._event_sequences: dict[str, int] = {}
        self._cipher = FieldCipher(settings.data_key)

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema = self.schema_path.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(schema)
            feedback_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(answer_feedback)").fetchall()
            }
            if "namespace" not in feedback_columns:
                conn.execute(
                    "ALTER TABLE answer_feedback ADD COLUMN namespace TEXT NOT NULL DEFAULT 'anonymous'"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_answer_feedback_namespace ON answer_feedback(namespace, created_at DESC, id DESC)"
            )
            conn.commit()
        self.prune_expired()

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
                row = conn.execute("SELECT 1 AS ok").fetchone()
            return bool(row and row["ok"] == 1)
        except sqlite3.Error:
            return False

    # Sessions -----------------------------------------------------------------

    def create_session(
        self,
        *,
        principal_id: str,
        auth_mode: str,
        profile: dict[str, Any],
        is_admin: bool,
        now: float | None = None,
    ) -> tuple[str, Principal]:
        timestamp = time.time() if now is None else now
        raw_token = secrets.token_urlsafe(32)
        raw_csrf = secrets.token_urlsafe(24)
        token_hash = _secret_hash(raw_token)
        if auth_mode == "anonymous":
            idle_expires_at = timestamp + self.settings.anonymous_session_seconds
            absolute_expires_at = idle_expires_at
        else:
            idle_expires_at = timestamp + self.settings.session_idle_seconds
            absolute_expires_at = timestamp + self.settings.session_absolute_seconds
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO web_sessions(
                    token_hash, principal_id, auth_mode, profile_json, is_admin,
                    csrf_hash, created_at, last_seen_at, idle_expires_at, absolute_expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    principal_id,
                    auth_mode,
                    json.dumps(profile, ensure_ascii=False, separators=(",", ":")),
                    int(is_admin),
                    _secret_hash(raw_csrf),
                    timestamp,
                    timestamp,
                    idle_expires_at,
                    absolute_expires_at,
                ),
            )
            conn.commit()
        return raw_token, Principal(
            principal_id=principal_id,
            auth_mode=auth_mode,
            profile=dict(profile),
            is_admin=is_admin,
            session_key=token_hash,
            csrf_token=raw_csrf,
        )

    def resolve_session(
        self,
        raw_token: str,
        *,
        now: float | None = None,
        touch: bool = True,
    ) -> Principal | None:
        if not raw_token:
            return None
        timestamp = time.time() if now is None else now
        token_hash = _secret_hash(raw_token)
        with self._write_lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM web_sessions WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if timestamp >= row["idle_expires_at"] or timestamp >= row["absolute_expires_at"]:
                conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (token_hash,))
                conn.commit()
                return None
            if touch:
                idle_expires_at = (
                    row["absolute_expires_at"]
                    if row["auth_mode"] == "anonymous"
                    else min(timestamp + self.settings.session_idle_seconds, row["absolute_expires_at"])
                )
                conn.execute(
                    "UPDATE web_sessions SET last_seen_at = ?, idle_expires_at = ? WHERE token_hash = ?",
                    (timestamp, idle_expires_at, token_hash),
                )
                conn.commit()
        return Principal(
            principal_id=row["principal_id"],
            auth_mode=row["auth_mode"],
            profile=json.loads(row["profile_json"] or "{}"),
            is_admin=bool(row["is_admin"]),
            session_key=token_hash,
        )

    def rotate_csrf(self, session_key: str) -> str:
        raw_csrf = secrets.token_urlsafe(24)
        with self._write_lock, self._connect() as conn:
            changed = conn.execute(
                "UPDATE web_sessions SET csrf_hash = ? WHERE token_hash = ?",
                (_secret_hash(raw_csrf), session_key),
            ).rowcount
            conn.commit()
        if changed != 1:
            raise KeyError("session not found")
        return raw_csrf

    def validate_csrf(self, session_key: str, raw_csrf: str) -> bool:
        if not raw_csrf:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT csrf_hash FROM web_sessions WHERE token_hash = ?",
                (session_key,),
            ).fetchone()
        return bool(row and hmac.compare_digest(row["csrf_hash"], _secret_hash(raw_csrf)))

    def delete_session(self, session_key: str) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("DELETE FROM web_sessions WHERE token_hash = ?", (session_key,))
            conn.commit()

    # Runs and events -----------------------------------------------------------

    def cancel_owner_runs(self, owner_key: str) -> None:
        timestamp = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE web_chat_runs
                SET cancel_requested = 1, updated_at = ?
                WHERE owner_key = ?
                  AND status NOT IN ('completed', 'cancelled', 'failed')
                """,
                (timestamp, owner_key),
            )
            conn.commit()

    def create_run(self, owner_key: str, mode: str, *, now: float | None = None) -> RunRecord:
        timestamp = time.time() if now is None else now
        run = RunRecord(
            run_id=secrets.token_urlsafe(18),
            owner_key=owner_key,
            mode=mode,
            status="queued",
            created_at=timestamp,
            updated_at=timestamp,
            expires_at=timestamp + self.settings.run_event_retention_seconds,
            cancel_requested=False,
            error_code=None,
        )
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO web_chat_runs(
                    run_id, owner_key, mode, status, created_at, updated_at,
                    expires_at, cancel_requested, error_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)
                """,
                (
                    run.run_id,
                    run.owner_key,
                    run.mode,
                    run.status,
                    run.created_at,
                    run.updated_at,
                    run.expires_at,
                ),
            )
            conn.commit()
        return run

    def get_run(self, run_id: str, owner_key: str | None = None) -> RunRecord | None:
        sql = "SELECT * FROM web_chat_runs WHERE run_id = ?"
        params: tuple[Any, ...] = (run_id,)
        if owner_key is not None:
            sql += " AND owner_key = ?"
            params = (run_id, owner_key)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return self._run_from_row(row) if row is not None else None

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=row["run_id"],
            owner_key=row["owner_key"],
            mode=row["mode"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            cancel_requested=bool(row["cancel_requested"]),
            error_code=row["error_code"],
        )

    def is_expired_run(self, run_id: str, owner_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM web_run_tombstones WHERE run_id = ? AND owner_key = ?",
                (run_id, owner_key),
            ).fetchone()
        return row is not None

    def transition_run(
        self,
        run_id: str,
        status: str,
        *,
        error_code: str | None = None,
        now: float | None = None,
    ) -> bool:
        if status not in RUN_STATES:
            raise ValueError(f"invalid run status: {status}")
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            current = conn.execute(
                "SELECT status FROM web_chat_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if current is None or current["status"] in TERMINAL_RUN_STATES:
                return False
            conn.execute(
                "UPDATE web_chat_runs SET status = ?, error_code = ?, updated_at = ? WHERE run_id = ?",
                (status, error_code, timestamp, run_id),
            )
            conn.commit()
        return True

    def request_cancel(self, run_id: str, owner_key: str) -> bool:
        with self._write_lock, self._connect() as conn:
            changed = conn.execute(
                """
                UPDATE web_chat_runs
                SET cancel_requested = 1, updated_at = ?
                WHERE run_id = ? AND owner_key = ?
                  AND status NOT IN ('completed', 'cancelled', 'failed')
                """,
                (time.time(), run_id, owner_key),
            ).rowcount
            conn.commit()
        return changed == 1

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        timestamp = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT status FROM web_chat_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None or run["status"] in TERMINAL_RUN_STATES:
                conn.rollback()
                raise KeyError("run is missing or terminal")
            sequence = self._next_sequence(conn, run_id)
            self._insert_event(conn, run_id, sequence, event_type, payload, timestamp)
            conn.commit()
        self._notify_event(run_id, sequence)
        return self._event_envelope(run_id, sequence, event_type, payload, timestamp)

    def finish_run(
        self,
        run_id: str,
        status: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        error_code: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in TERMINAL_RUN_STATES:
            raise ValueError("finish_run requires a terminal status")
        timestamp = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT status FROM web_chat_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if run is None or run["status"] in TERMINAL_RUN_STATES:
                conn.rollback()
                return None
            sequence = self._next_sequence(conn, run_id)
            self._insert_event(conn, run_id, sequence, event_type, payload, timestamp)
            conn.execute(
                """
                UPDATE web_chat_runs
                SET status = ?, error_code = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, error_code, timestamp, run_id),
            )
            conn.commit()
        self._notify_event(run_id, sequence)
        return self._event_envelope(run_id, sequence, event_type, payload, timestamp)

    def _notify_event(self, run_id: str, sequence: int) -> None:
        with self._event_condition:
            self._event_sequences[run_id] = max(
                sequence,
                self._event_sequences.get(run_id, 0),
            )
            self._event_condition.notify_all()

    def wait_for_event(
        self,
        run_id: str,
        after_sequence: int,
        timeout: float | None = None,
    ) -> bool:
        """Wait for an in-process event; timeout provides the SQLite fallback."""
        wait_seconds = (
            self.settings.event_wait_timeout_seconds
            if timeout is None
            else max(0.0, timeout)
        )
        with self._event_condition:
            return self._event_condition.wait_for(
                lambda: self._event_sequences.get(run_id, 0) > after_sequence,
                timeout=wait_seconds,
            )

    @staticmethod
    def _next_sequence(conn: sqlite3.Connection, run_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM web_chat_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(row["next_sequence"])

    @staticmethod
    def _insert_event(
        conn: sqlite3.Connection,
        run_id: str,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        timestamp: float,
    ) -> None:
        conn.execute(
            """
            INSERT INTO web_chat_events(run_id, sequence, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence,
                event_type,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                timestamp,
            ),
        )

    @staticmethod
    def _event_envelope(
        run_id: str,
        sequence: int,
        event_type: str,
        payload: dict[str, Any],
        timestamp: float,
    ) -> dict[str, Any]:
        return {
            "id": sequence,
            "run_id": run_id,
            "type": event_type,
            "at": _utc_iso(timestamp),
            "data": payload,
        }

    def poll_run(
        self, run_id: str, after_sequence: int = 0
    ) -> tuple[list[dict[str, Any]], RunRecord | None]:
        """SSE 流单次快照：同一连接/事务内读事件 + run 状态。

        _event_stream 每秒轮询一次；合并后每个 tick 只开一条 SQLite 连接，
        替代原先在线程上分开的 list_events + get_run 两次连接开销。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sequence, event_type, payload_json, created_at
                FROM web_chat_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (run_id, after_sequence),
            ).fetchall()
            row = conn.execute(
                "SELECT * FROM web_chat_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        events = [
            self._event_envelope(
                run_id,
                int(item["sequence"]),
                item["event_type"],
                json.loads(item["payload_json"] or "{}"),
                float(item["created_at"]),
            )
            for item in rows
        ]
        return events, self._run_from_row(row) if row is not None else None

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT sequence, event_type, payload_json, created_at
                FROM web_chat_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [
            self._event_envelope(
                run_id,
                int(row["sequence"]),
                row["event_type"],
                json.loads(row["payload_json"] or "{}"),
                float(row["created_at"]),
            )
            for row in rows
        ]

    def recover_interrupted_runs(self) -> int:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT run_id FROM web_chat_runs WHERE status IN ('queued', 'running')",
            ).fetchall()
        recovered = 0
        for row in rows:
            event = self.finish_run(
                row["run_id"],
                "failed",
                "run.failed",
                {"code": "INTERNAL_ERROR", "message": "服务重启，请重新提问。"},
                error_code="INTERNAL_ERROR",
            )
            recovered += int(event is not None)
        return recovered

    # Authenticated conversation history ---------------------------------------

    def create_conversation(
        self,
        owner_key: str,
        title: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        conversation_id = secrets.token_urlsafe(18)
        normalized_title = " ".join(title.split()).strip()[:80] or "新对话"
        with self._write_lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO web_conversations(
                    conversation_id, owner_key, title, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    owner_key,
                    normalized_title,
                    timestamp,
                    timestamp,
                    timestamp + HISTORY_RETENTION_SECONDS,
                ),
            )
            conn.commit()
        return {
            "conversation_id": conversation_id,
            "title": normalized_title,
            "created_at": _utc_iso(timestamp),
            "updated_at": _utc_iso(timestamp),
        }

    def conversation_belongs_to(self, conversation_id: str, owner_key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM web_conversations WHERE conversation_id = ? AND owner_key = ?",
                (conversation_id, owner_key),
            ).fetchone()
        return row is not None

    def append_exchange(
        self,
        *,
        conversation_id: str,
        owner_key: str,
        run_id: str,
        question: str,
        answer: str,
        metadata: dict[str, Any],
    ) -> None:
        timestamp = time.time()
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conversation = conn.execute(
                "SELECT 1 FROM web_conversations WHERE conversation_id = ? AND owner_key = ?",
                (conversation_id, owner_key),
            ).fetchone()
            if conversation is None:
                conn.rollback()
                raise KeyError("conversation not found")
            for offset, (role, content) in enumerate((("user", question), ("assistant", answer))):
                conn.execute(
                    """
                    INSERT INTO web_messages(
                        message_id, conversation_id, run_id, role, content_value,
                        metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        secrets.token_urlsafe(18),
                        conversation_id,
                        run_id,
                        role,
                        self._cipher.seal(content),
                        json.dumps(metadata if role == "assistant" else {}, ensure_ascii=False),
                        timestamp + offset * 0.000001,
                    ),
                )
            conn.execute(
                """
                UPDATE web_conversations
                SET updated_at = ?, expires_at = ?
                WHERE conversation_id = ?
                """,
                (timestamp, timestamp + HISTORY_RETENTION_SECONDS, conversation_id),
            )
            conn.commit()

    def list_conversations_page(
        self,
        owner_key: str,
        *,
        limit: int = 30,
        cursor: tuple[float, str] | None = None,
    ) -> tuple[list[dict[str, Any]], tuple[float, str] | None]:
        page_size = min(max(limit, 1), 100)
        sql = """
            SELECT conversation_id, title, created_at, updated_at
            FROM web_conversations
            WHERE owner_key = ?
        """
        params: list[Any] = [owner_key]
        if cursor is not None:
            sql += " AND (updated_at < ? OR (updated_at = ? AND conversation_id < ?))"
            params.extend((cursor[0], cursor[0], cursor[1]))
        sql += " ORDER BY updated_at DESC, conversation_id DESC LIMIT ?"
        params.append(page_size + 1)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        has_more = len(rows) > page_size
        page = rows[:page_size]
        items = [
            {
                "conversation_id": row["conversation_id"],
                "title": row["title"],
                "created_at": _utc_iso(row["created_at"]),
                "updated_at": _utc_iso(row["updated_at"]),
            }
            for row in page
        ]
        next_cursor = (
            (float(page[-1]["updated_at"]), str(page[-1]["conversation_id"]))
            if has_more and page else None
        )
        return items, next_cursor

    def list_conversations(self, owner_key: str, *, limit: int = 30) -> list[dict[str, Any]]:
        return self.list_conversations_page(owner_key, limit=limit)[0]

    def get_conversation(self, conversation_id: str, owner_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conversation = conn.execute(
                """
                SELECT conversation_id, title, created_at, updated_at
                FROM web_conversations
                WHERE conversation_id = ? AND owner_key = ?
                """,
                (conversation_id, owner_key),
            ).fetchone()
            if conversation is None:
                return None
            messages = conn.execute(
                """
                SELECT message_id, run_id, role, content_value, metadata_json, created_at
                FROM web_messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC, message_id ASC
                """,
                (conversation_id,),
            ).fetchall()
        return {
            "conversation_id": conversation["conversation_id"],
            "title": conversation["title"],
            "created_at": _utc_iso(conversation["created_at"]),
            "updated_at": _utc_iso(conversation["updated_at"]),
            "messages": [
                {
                    "message_id": row["message_id"],
                    "run_id": row["run_id"],
                    "role": row["role"],
                    "content": self._cipher.open(row["content_value"]),
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                    "created_at": _utc_iso(row["created_at"]),
                }
                for row in messages
            ],
        }

    def delete_conversation(self, conversation_id: str, owner_key: str) -> bool:
        with self._write_lock, self._connect() as conn:
            changed = conn.execute(
                "DELETE FROM web_conversations WHERE conversation_id = ? AND owner_key = ?",
                (conversation_id, owner_key),
            ).rowcount
            conn.commit()
        return changed == 1

    def delete_all_conversations(self, owner_key: str) -> int:
        with self._write_lock, self._connect() as conn:
            changed = conn.execute(
                "DELETE FROM web_conversations WHERE owner_key = ?",
                (owner_key,),
            ).rowcount
            conn.commit()
        return int(changed)

    def reset_demo_owner(self, principal: Principal) -> dict[str, int]:
        """Clear mutable data owned by one demo session, never another namespace."""
        if principal.auth_mode != "demo":
            raise ValueError("demo reset requires a demo principal")
        owner_key = principal.history_owner_key
        if owner_key is None:
            raise ValueError("demo principal does not have a history owner")
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run_rows = conn.execute(
                "SELECT run_id FROM web_chat_runs WHERE owner_key = ?",
                (principal.session_key,),
            ).fetchall()
            run_ids = [str(row["run_id"]) for row in run_rows]
            feedback_deleted = 0
            if run_ids:
                placeholders = ",".join("?" for _ in run_ids)
                feedback_deleted = conn.execute(
                    f"DELETE FROM answer_feedback WHERE run_id IN ({placeholders})",
                    tuple(run_ids),
                ).rowcount
            conversations_deleted = conn.execute(
                "DELETE FROM web_conversations WHERE owner_key = ?",
                (owner_key,),
            ).rowcount
            runs_deleted = conn.execute(
                "DELETE FROM web_chat_runs WHERE owner_key = ?",
                (principal.session_key,),
            ).rowcount
            conn.commit()
        return {
            "conversations": int(conversations_deleted),
            "runs": int(runs_deleted),
            "feedback": int(feedback_deleted),
        }

    # Answer feedback -----------------------------------------------------------

    def answer_belongs_to(self, answer_id: str, run_id: str, owner_key: str) -> bool:
        with self._connect() as conn:
            run = conn.execute(
                "SELECT 1 FROM web_chat_runs WHERE run_id = ? AND owner_key = ? AND status = 'completed'",
                (run_id, owner_key),
            ).fetchone()
            if run is None:
                return False
            rows = conn.execute(
                "SELECT payload_json FROM web_chat_events WHERE run_id = ? AND event_type = 'answer.completed'",
                (run_id,),
            ).fetchall()
        return any(
            str(json.loads(row["payload_json"] or "{}").get("answer_id") or "") == answer_id
            for row in rows
        )

    def create_feedback(
        self,
        *,
        answer_id: str,
        run_id: str,
        category: str,
        detail: str,
        namespace: str = "anonymous",
        now: float | None = None,
    ) -> int:
        timestamp = time.time() if now is None else now
        if namespace not in {"anonymous", "demo", "production"}:
            raise ValueError("invalid feedback namespace")
        if detail and not self._cipher.enabled:
            raise ValueError("feedback detail encryption is unavailable")
        sealed_detail = self._cipher.seal(detail) if detail else ""
        with self._write_lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO answer_feedback(
                    answer_id, run_id, namespace, category, detail, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    answer_id,
                    run_id,
                    namespace,
                    category,
                    sealed_detail,
                    timestamp,
                    timestamp + 30 * 24 * 60 * 60,
                ),
            )
            conn.commit()
        return int(cursor.lastrowid)

    def list_feedback_page(
        self,
        namespace: str,
        *,
        limit: int = 100,
        cursor: tuple[float, str] | None = None,
    ) -> tuple[list[dict[str, Any]], tuple[float, str] | None]:
        if namespace not in {"demo", "production"}:
            raise ValueError("invalid reviewer feedback namespace")
        page_size = min(max(limit, 1), 200)
        sql = """
            SELECT id, answer_id, run_id, category, detail, status, created_at
            FROM answer_feedback
            WHERE namespace = ?
        """
        params: list[Any] = [namespace]
        if cursor is not None:
            try:
                cursor_id = int(cursor[1])
            except ValueError as exc:
                raise ValueError("invalid feedback cursor") from exc
            sql += " AND (created_at < ? OR (created_at = ? AND id < ?))"
            params.extend((cursor[0], cursor[0], cursor_id))
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(page_size + 1)
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        has_more = len(rows) > page_size
        page = rows[:page_size]
        items = [
            {
                "id": row["id"],
                "answer_id": row["answer_id"],
                "run_id": row["run_id"],
                "category": row["category"],
                "detail": self._cipher.open(row["detail"]) if row["detail"] else "",
                "status": row["status"],
                "created_at": _utc_iso(row["created_at"]),
            }
            for row in page
        ]
        next_cursor = (
            (float(page[-1]["created_at"]), str(page[-1]["id"]))
            if has_more and page else None
        )
        return items, next_cursor

    def list_feedback(self, *, limit: int = 100, namespace: str = "production") -> list[dict[str, Any]]:
        return self.list_feedback_page(namespace, limit=limit)[0]

    # Retention ----------------------------------------------------------------

    def prune_expired(self, *, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        expired_run_ids: list[str] = []
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM web_sessions WHERE idle_expires_at <= ? OR absolute_expires_at <= ?",
                (timestamp, timestamp),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO web_run_tombstones(run_id, owner_key, expired_at, purge_at)
                SELECT run_id, owner_key, ?, ?
                FROM web_chat_runs
                WHERE expires_at <= ?
                """,
                (timestamp, timestamp + RUN_TOMBSTONE_RETENTION_SECONDS, timestamp),
            )
            expired_run_ids = [
                str(row["run_id"])
                for row in conn.execute(
                    "SELECT run_id FROM web_chat_runs WHERE expires_at <= ?",
                    (timestamp,),
                ).fetchall()
            ]
            conn.execute("DELETE FROM web_chat_runs WHERE expires_at <= ?", (timestamp,))
            conn.execute("DELETE FROM web_run_tombstones WHERE purge_at <= ?", (timestamp,))
            conn.execute("DELETE FROM web_conversations WHERE expires_at <= ?", (timestamp,))
            conn.execute("DELETE FROM answer_feedback WHERE expires_at <= ?", (timestamp,))
            conn.commit()
        if expired_run_ids:
            with self._event_condition:
                for run_id in expired_run_ids:
                    self._event_sequences.pop(run_id, None)
