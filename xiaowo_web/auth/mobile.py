"""Isolated mobile credentials: opaque tokens, atomic PKCE grants, encrypted jars.

No browser credentials are imported into this store implicitly. The only trust
boundary for a new identity is a successful school probe and profile lookup.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict, deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError

MOBILE_ORIGINS = ("https://localhost", "capacitor://localhost")
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_ALLOWED_COOKIES = {
    "id.ustc.edu.cn": {"SOURCEID_TGC", "SVRNAME", "rememberToken", "deviceID", "device"},
    "jw.ustc.edu.cn": {"SESSION", "SVRNAME", "rememberToken", "deviceID", "device"},
}


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bearer_token(request) -> str | None:
    """None means no header; malformed headers must never fall back to cookies."""
    header = request.headers.get("authorization")
    if header is None:
        return None
    parts = header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not _TOKEN.fullmatch(parts[1]):
        raise ApiError(401, "AUTH_REQUIRED", "登录凭据已失效，请重新登录。")
    return parts[1]


def require_mobile_origin(request) -> None:
    if request.headers.get("origin", "") not in MOBILE_ORIGINS:
        raise ApiError(403, "ORIGIN_MISMATCH", "请求来源校验失败。")


def require_json(request) -> None:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise ApiError(415, "JSON_REQUIRED", "请使用 JSON 请求。")


class MobileAuth:
    def __init__(self, settings) -> None:
        self.settings = settings
        self.path = Path(settings.mobile_auth_db_path)
        self._cipher = AESGCM(hashlib.sha256(settings.mobile_vault_key.encode()).digest()) if settings.mobile_vault_key else None
        self._lock = threading.RLock()
        self._limits: OrderedDict[str, deque[float]] = OrderedDict()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL avoids changing permissions on another unrelated file on creation.
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        os.chmod(self.path, 0o600)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS mobile_grants (
                    code_hash TEXT PRIMARY KEY, challenge TEXT NOT NULL,
                    sealed TEXT NOT NULL, expires_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS mobile_sessions (
                    token_hash TEXT PRIMARY KEY, session_id TEXT UNIQUE NOT NULL,
                    sealed TEXT, expires_at REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS mobile_session_expiry ON mobile_sessions(expires_at);
            """)
        self.prune()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        # DELETE journal avoids retaining old credential pages in a long-lived WAL.
        conn.execute("PRAGMA secure_delete=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _seal(self, value: dict, binding: str) -> str:
        if self._cipher is None:
            raise ApiError(503, "MOBILE_AUTH_DISABLED", "移动认证未配置。")
        nonce = os.urandom(12)
        raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(nonce + self._cipher.encrypt(nonce, raw, binding.encode())).decode()

    def _open(self, value: str, binding: str) -> dict | None:
        try:
            raw = base64.urlsafe_b64decode(value)
            return json.loads(self._cipher.decrypt(raw[:12], raw[12:], binding.encode()))
        except Exception:
            return None

    def limit(self, key: str) -> None:
        """Bounded process-local abuse control; proxy addresses are not trusted."""
        now = time.monotonic()
        with self._lock:
            queue = self._limits.setdefault(key, deque())
            self._limits.move_to_end(key)
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if len(queue) >= 20:
                raise ApiError(429, "AUTH_RATE_LIMITED", "登录尝试过于频繁，请稍后重试。")
            queue.append(now)
            while len(self._limits) > 4096:
                self._limits.popitem(last=False)

    def prune(self) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM mobile_grants WHERE expires_at <= ?", (now,))
            conn.execute("UPDATE mobile_sessions SET sealed = NULL, revoked = 1 WHERE expires_at <= ?", (now,))
            # Retain only non-sensitive revocation tombstones for repeat logout.
            conn.execute("DELETE FROM mobile_sessions WHERE expires_at <= ?", (now - 86400,))

    @staticmethod
    def validate_cookies(cookies: Any) -> list[dict]:
        if not isinstance(cookies, list) or not 1 <= len(cookies) <= 20:
            raise ApiError(400, "INVALID_COOKIES", "学校会话格式不正确。")
        clean = []
        seen = set()
        for cookie in cookies:
            if not isinstance(cookie, dict) or set(cookie) - {"name", "value", "domain", "path"}:
                raise ApiError(400, "INVALID_COOKIES", "学校会话格式不正确。")
            name, value, domain, path = (cookie.get(k, "/" if k == "path" else "") for k in ("name", "value", "domain", "path"))
            if not all(isinstance(v, str) for v in (name, value, domain, path)):
                raise ApiError(400, "INVALID_COOKIES", "学校会话格式不正确。")
            domain = domain.lstrip(".")
            if (domain not in _ALLOWED_COOKIES or name not in _ALLOWED_COOKIES[domain]
                    or not value or len(value) > 4096 or path != "/"
                    or any(ord(c) < 32 or ord(c) == 127 for c in value)
                    or (domain, name) in seen):
                raise ApiError(400, "INVALID_COOKIES", "学校会话包含不允许的项目。")
            seen.add((domain, name))
            clean.append({"name": name, "value": value, "domain": domain, "path": path})
        if ("id.ustc.edu.cn", "SOURCEID_TGC") not in seen:
            raise ApiError(400, "MISSING_TGC", "缺少学校登录会话，请重新登录。")
        return clean

    def authorize(self, cookies: list[dict], challenge: str) -> dict:
        from services.cas_client import CASClient
        from services.jw_session import identity_for_client
        from services.session_vault import apply_cookies
        clean = self.validate_cookies(cookies)
        client = CASClient()
        try:
            apply_cookies(client.session, clean)
            if not client.probe_jw_session():
                if getattr(client, "_probe_unavailable", False):
                    raise ApiError(503, "SCHOOL_UNAVAILABLE", "暂时无法连接教务系统，请稍后重试。")
                raise ApiError(401, "SCHOOL_SESSION_INVALID", "学校会话已失效，请重新登录。")
            # The probe verifies cookies but does not update CASClient's API gate.
            client._logged_in = True
            identity = identity_for_client(client)
            sid = str(identity.get("student_id") or "").strip().upper()
            name = str(identity.get("name") or "").strip()
            data_id = client.student_data_id
            if (not re.fullmatch(r"[A-Z0-9_-]{4,32}", sid) or not name
                    or type(data_id) is not int or data_id <= 0):
                raise ApiError(401, "SCHOOL_IDENTITY_UNAVAILABLE", "暂时无法核验学校身份，请重新登录。")
            profile = {k: str(identity.get(k) or "") for k in ("name", "major", "grade", "college")}
            profile.update(id=sid, student_id=sid, logged_in=True, profile_source="mobile_school_verified")
            # Probe/profile requests may rotate SESSION; persist only the necessary jar.
            jar = [{"name": c.name, "value": c.value, "domain": c.domain.lstrip("."), "path": c.path}
                   for c in client.session.cookies
                   if c.name in _ALLOWED_COOKIES.get(c.domain.lstrip("."), set()) and c.path == "/"]
            payload = {"principal_id": sid, "profile": profile, "cookies": self.validate_cookies(jar),
                       "auth_mode": "cas", "student_data_id": data_id}
        finally:
            client.session.close()
        self.prune()
        code = secrets.token_urlsafe(32)
        digest = token_hash(code)
        with self._lock, self._connect() as conn:
            conn.execute("INSERT INTO mobile_grants VALUES (?, ?, ?, ?)",
                         (digest, challenge, self._seal(payload, "grant:" + digest), time.time() + 60))
        return {"code": code, "expires_in": 60}

    def _insert_session(self, conn, payload: dict, *, expires_at: float) -> dict:
        raw = secrets.token_urlsafe(32)
        session_id = "mobile:" + secrets.token_urlsafe(24)
        conn.execute("INSERT INTO mobile_sessions VALUES (?, ?, ?, ?, 0)",
                     (token_hash(raw), session_id, self._seal(payload, "session:" + session_id), expires_at))
        return {"access_token": raw, "token_type": "Bearer", "expires_in": max(0, int(expires_at - time.time()))}

    def bootstrap(self) -> dict:
        self.prune()
        with self._lock, self._connect() as conn:
            return self._insert_session(conn, {"principal_id": "anon:" + secrets.token_urlsafe(12), "auth_mode": "anonymous", "profile": {"logged_in": False}, "cookies": []},
                                        expires_at=time.time() + self.settings.mobile_token_seconds)

    def exchange(self, code: str, verifier: str) -> dict:
        invalid = ApiError(400, "MOBILE_CODE_INVALID", "登录授权已失效，请重新登录。")
        if not _TOKEN.fullmatch(code) or not _VERIFIER.fullmatch(verifier):
            raise invalid
        self.prune()
        digest = token_hash(code)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM mobile_grants WHERE code_hash=?", (digest,)).fetchone()
            if row is None or row["expires_at"] <= time.time() or not hmac.compare_digest(row["challenge"], challenge):
                raise invalid
            payload = self._open(row["sealed"], "grant:" + digest)
            if payload is None:
                raise invalid
            conn.execute("DELETE FROM mobile_grants WHERE code_hash=?", (digest,))
            return self._insert_session(conn, payload, expires_at=time.time() + self.settings.mobile_token_seconds)

    def resolve(self, token: str) -> Principal | None:
        resolved = self.resolve_with_payload(token)
        return resolved[0] if resolved else None

    def resolve_with_payload(self, token: str) -> tuple[Principal, dict] | None:
        if not _TOKEN.fullmatch(token):
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM mobile_sessions WHERE token_hash=?", (token_hash(token),)).fetchone()
            if row is None or row["revoked"]:
                return None
            if row["expires_at"] <= time.time():
                conn.execute("UPDATE mobile_sessions SET sealed=NULL, revoked=1 WHERE token_hash=?", (token_hash(token),))
                return None
            payload = self._open(row["sealed"], "session:" + row["session_id"])
            if payload is None:
                return None
            if payload.get("auth_mode") == "cas":
                data_id = payload.get("student_data_id")
                if type(data_id) is not int or data_id <= 0:
                    # Pre-release sessions without the verified school ID must reauthenticate.
                    conn.execute("UPDATE mobile_sessions SET sealed=NULL, revoked=1 WHERE token_hash=?", (token_hash(token),))
                    return None
        principal = Principal(payload["principal_id"], payload["auth_mode"], payload["profile"],
                              payload["auth_mode"] == "cas" and payload["principal_id"] in self.settings.admin_ids,
                              row["session_id"])
        return principal, payload

    def revoke(self, token: str) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT session_id FROM mobile_sessions WHERE token_hash=?", (token_hash(token),)).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE mobile_sessions SET sealed=NULL, revoked=1 WHERE token_hash=?", (token_hash(token),))
            return row["session_id"]
