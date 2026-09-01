"""Persistent campus-tool applications, publication, audit, and notifications."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from xiaowo_web.evidence.url_security import UrlGuard, UrlSafetyError
from xiaowo_web.settings import WebSettings


TOOL_CATEGORIES = ("study", "life", "information", "community", "other")
APPLICATION_STATUSES = ("pending", "approved", "rejected")
TOOL_STATUSES = ("active", "unpublished")


class CampusToolError(ValueError):
    """Domain error with a stable API code and HTTP status."""

    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class CampusToolStore:
    def __init__(self, settings: WebSettings, *, url_guard: UrlGuard | None = None) -> None:
        self.db_path = Path(settings.review_db_path)
        self.schema_path = Path(settings.schema_review_path)
        self._url_guard = url_guard or UrlGuard()
        self._write_lock = threading.RLock()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
                return conn.execute("SELECT 1").fetchone() is not None
        except sqlite3.Error:
            return False

    def reset_demo_namespace(self) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM user_notifications WHERE namespace = 'demo'")
            conn.execute("DELETE FROM campus_tool_audit WHERE namespace = 'demo'")
            conn.execute("DELETE FROM campus_tools WHERE namespace = 'demo'")
            conn.execute("DELETE FROM campus_tool_applications WHERE namespace = 'demo'")
            conn.commit()

    def has_applications(self, namespace: str) -> bool:
        self._validate_namespace(namespace)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM campus_tool_applications WHERE namespace = ? LIMIT 1",
                (namespace,),
            ).fetchone()
        return row is not None

    def submit_application(
        self,
        *,
        namespace: str,
        applicant_principal_id: str,
        applicant_auth_mode: str,
        applicant_name: str,
        name: str,
        description: str,
        category: str,
        url: str,
        request_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        validated = self._url_guard.validate(url.strip())
        if validated.scheme != "https":
            raise CampusToolError("TOOL_URL_HTTPS_REQUIRED", "校园工具只接受公开 HTTPS 链接。", 422)
        return self._submit_normalized(
            namespace=namespace,
            applicant_principal_id=applicant_principal_id,
            applicant_auth_mode=applicant_auth_mode,
            applicant_name=applicant_name,
            name=name,
            description=description,
            category=category,
            submitted_url=url.strip(),
            normalized_url=validated.normalized_url,
            request_id=request_id,
            now=now,
        )

    def submit_demo_seed(
        self,
        *,
        name: str,
        description: str,
        category: str,
        url: str,
        request_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Insert a tracked synthetic fixture without relying on live DNS."""
        normalized = self._normalize_trusted_demo_url(url)
        return self._submit_normalized(
            namespace="demo",
            applicant_principal_id="PB25111691",
            applicant_auth_mode="demo",
            applicant_name="测试",
            name=name,
            description=description,
            category=category,
            submitted_url=url,
            normalized_url=normalized,
            request_id=request_id,
            now=now,
        )

    def _submit_normalized(
        self,
        *,
        namespace: str,
        applicant_principal_id: str,
        applicant_auth_mode: str,
        applicant_name: str,
        name: str,
        description: str,
        category: str,
        submitted_url: str,
        normalized_url: str,
        request_id: str,
        now: float | None,
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        self._validate_category(category)
        if applicant_auth_mode not in {"demo", "cas"}:
            raise CampusToolError("AUTH_REQUIRED", "此功能需要登录。", 401)
        clean_name = self._clean_required(name, "工具名称", 80)
        clean_description = self._clean_optional(description, 240)
        clean_applicant = self._clean_required(applicant_principal_id, "申请人", 64)
        clean_display_name = self._clean_optional(applicant_name, 80) or "未取得姓名"
        clean_request_id = self._clean_required(request_id, "请求编号", 128)
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                """
                SELECT application_id FROM campus_tool_applications
                WHERE namespace = ? AND applicant_principal_id = ? AND request_id = ?
                """,
                (namespace, clean_applicant, clean_request_id),
            ).fetchone()
            if existing is not None:
                row = self._application_row(conn, namespace, str(existing["application_id"]))
                conn.commit()
                return self._application_dict(row)
            duplicate = conn.execute(
                """
                SELECT application_id AS object_id, 'pending' AS duplicate_kind
                FROM campus_tool_applications
                WHERE namespace = ? AND normalized_url = ? AND status = 'pending'
                UNION ALL
                SELECT tool_id AS object_id, 'active' AS duplicate_kind
                FROM campus_tools
                WHERE namespace = ? AND normalized_url = ? AND status = 'active'
                LIMIT 1
                """,
                (namespace, normalized_url, namespace, normalized_url),
            ).fetchone()
            if duplicate is not None:
                conn.rollback()
                label = "待审核申请" if duplicate["duplicate_kind"] == "pending" else "已上架工具"
                raise CampusToolError("TOOL_URL_DUPLICATE", f"该链接已有{label}，不能重复提交。")
            application_id = "tool-app-" + secrets.token_urlsafe(16)
            conn.execute(
                """
                INSERT INTO campus_tool_applications(
                    application_id, namespace, applicant_principal_id, applicant_auth_mode,
                    applicant_name_snapshot, name, description, category, submitted_url,
                    normalized_url, status, request_id, version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, 1, ?, ?)
                """,
                (
                    application_id,
                    namespace,
                    clean_applicant,
                    applicant_auth_mode,
                    clean_display_name,
                    clean_name,
                    clean_description,
                    category,
                    submitted_url,
                    normalized_url,
                    clean_request_id,
                    timestamp,
                    timestamp,
                ),
            )
            after = self._application_row(conn, namespace, application_id)
            self._audit(
                conn,
                namespace=namespace,
                actor_key=clean_applicant,
                action="application_submitted",
                object_type="application",
                object_id=application_id,
                before=None,
                after=dict(after),
                reason=None,
                request_id=clean_request_id,
                now=timestamp,
            )
            conn.commit()
        return self._application_dict(after)

    def list_public_tools(self, namespace: str, *, query: str = "", category: str = "") -> list[dict[str, Any]]:
        self._validate_namespace(namespace)
        if category:
            self._validate_category(category)
        clauses = ["namespace = ?", "status = 'active'"]
        params: list[Any] = [namespace]
        if category:
            clauses.append("category = ?")
            params.append(category)
        if query.strip():
            pattern = f"%{self._escape_like(query.strip())}%"
            clauses.append("(name LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\' OR normalized_url LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern, pattern])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT tool_id, application_id, name, description, category, url,
                       normalized_url, status, published_at, version
                FROM campus_tools
                WHERE {' AND '.join(clauses)}
                ORDER BY published_at DESC, name COLLATE NOCASE, tool_id DESC
                """,
                tuple(params),
            ).fetchall()
        return [self._tool_dict(row) for row in rows]

    def list_owner_applications(
        self,
        namespace: str,
        applicant_principal_id: str,
        *,
        status: str = "",
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        if status and status not in APPLICATION_STATUSES:
            raise CampusToolError("TOOL_STATUS_INVALID", "申请状态筛选无效。", 422)
        clauses = ["a.namespace = ?", "a.applicant_principal_id = ?"]
        params: list[Any] = [namespace, applicant_principal_id]
        if status:
            clauses.append("a.status = ?")
            params.append(status)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT a.*, t.tool_id, t.status AS tool_status,
                       t.unpublish_reason, t.unpublished_at,
                       (
                           SELECT n.notification_id FROM user_notifications n
                           WHERE n.namespace = a.namespace
                             AND n.recipient_principal_id = a.applicant_principal_id
                             AND n.application_id = a.application_id
                             AND n.read_at IS NULL
                           ORDER BY n.created_at DESC LIMIT 1
                       ) AS unread_notification_id
                FROM campus_tool_applications a
                LEFT JOIN campus_tools t ON t.application_id = a.application_id
                WHERE {' AND '.join(clauses)}
                ORDER BY a.created_at DESC, a.application_id DESC
                """,
                tuple(params),
            ).fetchall()
            unread = conn.execute(
                """
                SELECT COUNT(*) AS count FROM user_notifications
                WHERE namespace = ? AND recipient_principal_id = ? AND read_at IS NULL
                """,
                (namespace, applicant_principal_id),
            ).fetchone()
        return {
            "items": [self._application_dict(row) for row in rows],
            "unread_count": int(unread["count"] if unread else 0),
        }

    def list_notifications(
        self,
        namespace: str,
        recipient_principal_id: str,
        *,
        unread_only: bool = False,
    ) -> list[dict[str, Any]]:
        self._validate_namespace(namespace)
        clause = " AND read_at IS NULL" if unread_only else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM user_notifications
                WHERE namespace = ? AND recipient_principal_id = ?{clause}
                ORDER BY created_at DESC, notification_id DESC
                LIMIT 100
                """,
                (namespace, recipient_principal_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_notification_read(
        self,
        namespace: str,
        recipient_principal_id: str,
        notification_id: str,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM user_notifications
                WHERE namespace = ? AND recipient_principal_id = ? AND notification_id = ?
                """,
                (namespace, recipient_principal_id, notification_id),
            ).fetchone()
            if row is None:
                conn.rollback()
                raise KeyError("notification not found")
            if row["read_at"] is None:
                conn.execute(
                    "UPDATE user_notifications SET read_at = ? WHERE notification_id = ?",
                    (timestamp, notification_id),
                )
            updated = conn.execute(
                "SELECT * FROM user_notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
            conn.commit()
        return dict(updated)

    def list_admin_applications(
        self,
        namespace: str,
        *,
        status: str = "pending",
        query: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._validate_namespace(namespace)
        if status and status not in APPLICATION_STATUSES:
            raise CampusToolError("TOOL_STATUS_INVALID", "申请状态筛选无效。", 422)
        clauses = ["a.namespace = ?"]
        params: list[Any] = [namespace]
        if status:
            clauses.append("a.status = ?")
            params.append(status)
        if query.strip():
            pattern = f"%{self._escape_like(query.strip())}%"
            clauses.append(
                "(a.name LIKE ? ESCAPE '\\' OR a.description LIKE ? ESCAPE '\\' "
                "OR a.normalized_url LIKE ? ESCAPE '\\' OR a.applicant_principal_id LIKE ? ESCAPE '\\' "
                "OR a.applicant_name_snapshot LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern] * 5)
        params.append(max(1, min(int(limit), 100)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT a.*, t.tool_id, t.status AS tool_status,
                       t.unpublish_reason, t.unpublished_at
                FROM campus_tool_applications a
                LEFT JOIN campus_tools t ON t.application_id = a.application_id
                WHERE {' AND '.join(clauses)}
                ORDER BY CASE a.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                         a.updated_at DESC, a.application_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [self._application_dict(row) for row in rows]

    def get_admin_application(self, namespace: str, application_id: str) -> dict[str, Any] | None:
        self._validate_namespace(namespace)
        with self._connect() as conn:
            row = self._application_row(conn, namespace, application_id)
        return self._application_dict(row) if row is not None else None

    def approve_application(
        self,
        namespace: str,
        application_id: str,
        *,
        expected_version: int,
        actor_key: str,
        request_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            repeated = self._repeated_request(conn, namespace, actor_key, request_id)
            if repeated is not None:
                self._assert_repeated_request_target(
                    repeated,
                    action="application_approved",
                    object_type="application",
                    object_id=application_id,
                )
                row = self._application_row(conn, namespace, application_id)
                conn.commit()
                if row is None:
                    raise KeyError("application not found")
                return self._application_dict(row)
            before = self._application_row(conn, namespace, application_id)
            if before is None:
                conn.rollback()
                raise KeyError("application not found")
            self._assert_pending_version(before, expected_version)
            duplicate = conn.execute(
                """
                SELECT tool_id FROM campus_tools
                WHERE namespace = ? AND normalized_url = ? AND status = 'active'
                LIMIT 1
                """,
                (namespace, before["normalized_url"]),
            ).fetchone()
            if duplicate is not None:
                conn.rollback()
                raise CampusToolError("TOOL_URL_DUPLICATE", "该链接已有已上架工具，不能重复通过。")
            tool_id = "campus-tool-" + secrets.token_urlsafe(16)
            conn.execute(
                """
                INSERT INTO campus_tools(
                    tool_id, namespace, application_id, name, description, category,
                    url, normalized_url, status, published_by, published_at,
                    version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, 1, ?, ?)
                """,
                (
                    tool_id,
                    namespace,
                    application_id,
                    before["name"],
                    before["description"],
                    before["category"],
                    before["normalized_url"],
                    before["normalized_url"],
                    actor_key,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            conn.execute(
                """
                UPDATE campus_tool_applications
                SET status = 'approved', decision_reason = NULL, reviewed_by = ?,
                    reviewed_at = ?, version = version + 1, updated_at = ?
                WHERE namespace = ? AND application_id = ?
                """,
                (actor_key, timestamp, timestamp, namespace, application_id),
            )
            self._notify(
                conn,
                namespace=namespace,
                recipient=str(before["applicant_principal_id"]),
                notification_type="tool_approved",
                title="校园工具申请已通过",
                body=f"“{before['name']}”已通过审核并面向全校上架。",
                application_id=application_id,
                tool_id=tool_id,
                now=timestamp,
            )
            after = self._application_row(conn, namespace, application_id)
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="application_approved",
                object_type="application",
                object_id=application_id,
                before=dict(before),
                after=dict(after),
                reason=None,
                request_id=request_id,
                now=timestamp,
            )
            conn.commit()
        return self._application_dict(after)

    def reject_application(
        self,
        namespace: str,
        application_id: str,
        *,
        expected_version: int,
        reason: str,
        actor_key: str,
        request_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        clean_reason = self._clean_required(reason, "驳回原因", 500)
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            repeated = self._repeated_request(conn, namespace, actor_key, request_id)
            if repeated is not None:
                self._assert_repeated_request_target(
                    repeated,
                    action="application_rejected",
                    object_type="application",
                    object_id=application_id,
                )
                row = self._application_row(conn, namespace, application_id)
                conn.commit()
                if row is None:
                    raise KeyError("application not found")
                return self._application_dict(row)
            before = self._application_row(conn, namespace, application_id)
            if before is None:
                conn.rollback()
                raise KeyError("application not found")
            self._assert_pending_version(before, expected_version)
            conn.execute(
                """
                UPDATE campus_tool_applications
                SET status = 'rejected', decision_reason = ?, reviewed_by = ?,
                    reviewed_at = ?, version = version + 1, updated_at = ?
                WHERE namespace = ? AND application_id = ?
                """,
                (clean_reason, actor_key, timestamp, timestamp, namespace, application_id),
            )
            self._notify(
                conn,
                namespace=namespace,
                recipient=str(before["applicant_principal_id"]),
                notification_type="tool_rejected",
                title="校园工具申请未通过",
                body=f"“{before['name']}”未通过审核：{clean_reason}",
                application_id=application_id,
                tool_id=None,
                now=timestamp,
            )
            after = self._application_row(conn, namespace, application_id)
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="application_rejected",
                object_type="application",
                object_id=application_id,
                before=dict(before),
                after=dict(after),
                reason=clean_reason,
                request_id=request_id,
                now=timestamp,
            )
            conn.commit()
        return self._application_dict(after)

    def list_admin_tools(
        self,
        namespace: str,
        *,
        status: str = "active",
        query: str = "",
    ) -> list[dict[str, Any]]:
        self._validate_namespace(namespace)
        if status and status not in TOOL_STATUSES:
            raise CampusToolError("TOOL_STATUS_INVALID", "工具状态筛选无效。", 422)
        clauses = ["t.namespace = ?"]
        params: list[Any] = [namespace]
        if status:
            clauses.append("t.status = ?")
            params.append(status)
        if query.strip():
            pattern = f"%{self._escape_like(query.strip())}%"
            clauses.append("(t.name LIKE ? ESCAPE '\\' OR t.normalized_url LIKE ? ESCAPE '\\')")
            params.extend([pattern, pattern])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT t.*, a.applicant_principal_id, a.applicant_name_snapshot
                FROM campus_tools t
                JOIN campus_tool_applications a ON a.application_id = t.application_id
                WHERE {' AND '.join(clauses)}
                ORDER BY t.updated_at DESC, t.tool_id DESC
                """,
                tuple(params),
            ).fetchall()
        return [self._tool_dict(row) for row in rows]

    def unpublish_tool(
        self,
        namespace: str,
        tool_id: str,
        *,
        expected_version: int,
        reason: str,
        actor_key: str,
        request_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        self._validate_namespace(namespace)
        clean_reason = self._clean_required(reason, "下架原因", 500)
        timestamp = time.time() if now is None else now
        with self._write_lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            repeated = self._repeated_request(conn, namespace, actor_key, request_id)
            if repeated is not None:
                self._assert_repeated_request_target(
                    repeated,
                    action="tool_unpublished",
                    object_type="tool",
                    object_id=tool_id,
                )
                row = conn.execute(
                    "SELECT * FROM campus_tools WHERE namespace = ? AND tool_id = ?",
                    (namespace, tool_id),
                ).fetchone()
                conn.commit()
                if row is None:
                    raise KeyError("tool not found")
                return self._tool_dict(row)
            before = conn.execute(
                """
                SELECT t.*, a.applicant_principal_id, a.name AS application_name
                FROM campus_tools t
                JOIN campus_tool_applications a ON a.application_id = t.application_id
                WHERE t.namespace = ? AND t.tool_id = ?
                """,
                (namespace, tool_id),
            ).fetchone()
            if before is None:
                conn.rollback()
                raise KeyError("tool not found")
            if before["status"] != "active":
                conn.rollback()
                raise CampusToolError("TOOL_STATE_INVALID", "当前工具已经下架。")
            if int(before["version"]) != int(expected_version):
                conn.rollback()
                raise CampusToolError("TOOL_VERSION_CONFLICT", "工具状态已变化，请刷新后重试。")
            conn.execute(
                """
                UPDATE campus_tools
                SET status = 'unpublished', unpublished_by = ?, unpublished_at = ?,
                    unpublish_reason = ?, version = version + 1, updated_at = ?
                WHERE namespace = ? AND tool_id = ?
                """,
                (actor_key, timestamp, clean_reason, timestamp, namespace, tool_id),
            )
            self._notify(
                conn,
                namespace=namespace,
                recipient=str(before["applicant_principal_id"]),
                notification_type="tool_unpublished",
                title="校园工具已下架",
                body=f"“{before['application_name']}”已下架：{clean_reason}",
                application_id=str(before["application_id"]),
                tool_id=tool_id,
                now=timestamp,
            )
            after = conn.execute(
                "SELECT * FROM campus_tools WHERE namespace = ? AND tool_id = ?",
                (namespace, tool_id),
            ).fetchone()
            self._audit(
                conn,
                namespace=namespace,
                actor_key=actor_key,
                action="tool_unpublished",
                object_type="tool",
                object_id=tool_id,
                before=dict(before),
                after=dict(after),
                reason=clean_reason,
                request_id=request_id,
                now=timestamp,
            )
            conn.commit()
        return self._tool_dict(after)

    def list_audit(
        self,
        namespace: str,
        *,
        query: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self._validate_namespace(namespace)
        clauses = ["namespace = ?"]
        params: list[Any] = [namespace]
        if query.strip():
            pattern = f"%{self._escape_like(query.strip())}%"
            clauses.append(
                "(actor_key LIKE ? ESCAPE '\\' OR action LIKE ? ESCAPE '\\' "
                "OR object_id LIKE ? ESCAPE '\\' OR COALESCE(reason, '') LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern] * 4)
        params.append(max(1, min(int(limit), 100)))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT audit_id, actor_key, action, object_type, object_id,
                       before_json, after_json, reason, request_id, created_at
                FROM campus_tool_audit
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC, audit_id DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            for field in ("before_json", "after_json"):
                raw = item.pop(field, None)
                try:
                    item[field.removesuffix("_json")] = json.loads(raw) if raw else None
                except (TypeError, ValueError):
                    item[field.removesuffix("_json")] = None
            result.append(item)
        return result

    @staticmethod
    def _application_row(
        conn: sqlite3.Connection,
        namespace: str,
        application_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT a.*, t.tool_id, t.status AS tool_status,
                   t.unpublish_reason, t.unpublished_at
            FROM campus_tool_applications a
            LEFT JOIN campus_tools t ON t.application_id = a.application_id
            WHERE a.namespace = ? AND a.application_id = ?
            """,
            (namespace, application_id),
        ).fetchone()

    @staticmethod
    def _application_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["description"] = str(result.get("description") or "")
        result["display_description"] = result["description"] or "暂无补充说明"
        result["unread"] = bool(result.get("unread_notification_id"))
        return result

    @staticmethod
    def _tool_dict(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["description"] = str(result.get("description") or "")
        result["display_description"] = result["description"] or "暂无补充说明"
        return result

    @staticmethod
    def _assert_pending_version(row: sqlite3.Row, expected_version: int) -> None:
        if row["status"] != "pending":
            raise CampusToolError("TOOL_APPLICATION_STATE_INVALID", "该申请已经完成审核。")
        if int(row["version"]) != int(expected_version):
            raise CampusToolError("TOOL_APPLICATION_VERSION_CONFLICT", "申请状态已变化，请刷新后重试。")

    @staticmethod
    def _notify(
        conn: sqlite3.Connection,
        *,
        namespace: str,
        recipient: str,
        notification_type: str,
        title: str,
        body: str,
        application_id: str,
        tool_id: str | None,
        now: float,
    ) -> str:
        notification_id = "notification-" + secrets.token_urlsafe(16)
        conn.execute(
            """
            INSERT INTO user_notifications(
                notification_id, namespace, recipient_principal_id, notification_type,
                title, body, application_id, tool_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                namespace,
                recipient,
                notification_type,
                title,
                body,
                application_id,
                tool_id,
                now,
            ),
        )
        return notification_id

    @staticmethod
    def _audit(
        conn: sqlite3.Connection,
        *,
        namespace: str,
        actor_key: str,
        action: str,
        object_type: str,
        object_id: str,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
        reason: str | None,
        request_id: str,
        now: float,
    ) -> None:
        conn.execute(
            """
            INSERT INTO campus_tool_audit(
                audit_id, namespace, actor_key, action, object_type, object_id,
                before_json, after_json, reason, request_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "tool-audit-" + secrets.token_urlsafe(16),
                namespace,
                actor_key,
                action,
                object_type,
                object_id,
                json.dumps(before, ensure_ascii=False, sort_keys=True, default=str) if before is not None else None,
                json.dumps(after, ensure_ascii=False, sort_keys=True, default=str) if after is not None else None,
                reason,
                request_id[:128],
                now,
            ),
        )

    @staticmethod
    def _repeated_request(
        conn: sqlite3.Connection,
        namespace: str,
        actor_key: str,
        request_id: str,
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM campus_tool_audit
            WHERE namespace = ? AND actor_key = ? AND request_id = ?
            """,
            (namespace, actor_key, request_id[:128]),
        ).fetchone()

    @staticmethod
    def _assert_repeated_request_target(
        row: sqlite3.Row,
        *,
        action: str,
        object_type: str,
        object_id: str,
    ) -> None:
        if (
            row["action"] != action
            or row["object_type"] != object_type
            or row["object_id"] != object_id
        ):
            raise CampusToolError(
                "TOOL_REQUEST_ID_CONFLICT",
                "该请求编号已用于其他审核操作，请刷新后重试。",
            )

    @staticmethod
    def _clean_required(value: str, label: str, max_length: int) -> str:
        cleaned = " ".join(str(value or "").split())
        if not cleaned:
            raise CampusToolError("TOOL_FIELD_REQUIRED", f"{label}不能为空。", 422)
        if len(cleaned) > max_length:
            raise CampusToolError("TOOL_FIELD_TOO_LONG", f"{label}超过长度限制。", 422)
        return cleaned

    @staticmethod
    def _clean_optional(value: str, max_length: int) -> str:
        cleaned = " ".join(str(value or "").split())
        if len(cleaned) > max_length:
            raise CampusToolError("TOOL_FIELD_TOO_LONG", "字段超过长度限制。", 422)
        return cleaned

    @staticmethod
    def _normalize_trusted_demo_url(value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme.casefold() != "https" or not parts.hostname or parts.username or parts.password:
            raise CampusToolError("DEMO_TOOL_URL_INVALID", "演示工具链接无效。", 500)
        host = parts.hostname.casefold().rstrip(".")
        port = parts.port
        netloc = host if port in {None, 443} else f"{host}:{port}"
        path = quote(unquote(parts.path or "/"), safe="/%:@-._~!$&'()*+,;=")
        return urlunsplit(("https", netloc, path, parts.query, ""))

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    @staticmethod
    def _validate_namespace(namespace: str) -> None:
        if namespace not in {"demo", "production"}:
            raise ValueError("invalid campus tool namespace")

    @staticmethod
    def _validate_category(category: str) -> None:
        if category not in TOOL_CATEGORIES:
            raise CampusToolError("TOOL_CATEGORY_INVALID", "校园工具类别无效。", 422)


__all__ = [
    "APPLICATION_STATUSES",
    "CampusToolError",
    "CampusToolStore",
    "TOOL_CATEGORIES",
    "TOOL_STATUSES",
    "UrlSafetyError",
]
