# -*- coding: utf-8 -*-
"""请求级教务会话（方案 B，2026-10-23）。

问题：`ServiceContainer().cas_client` 是**进程级共享**的单例，只适合"服务端自己登录一次"
的旧模型；方案 B 下会话在**用户浏览器 cookie**里，必须**每请求解包、互不共享**。

做法：用 ContextVar 承载"当前请求的 CASClient"：
  - 请求进来：`client_from_request(request)` 解包 cookie → 构造 CASClient → 塞进 ContextVar
  - 工具调用：`_cas()` 优先取 ContextVar 里的客户端；没有则回退到旧单例（不破坏原行为）
  - 请求结束：`reset()` 清掉（不泄漏到下一个请求）

注意：这里**不做网络探测**（每次请求探测会给工具链加 100~300ms）；
会话是否有效由 `GET /auth/session`（UI 状态）按需探测，或由工具调用失败自然暴露。
"""

from __future__ import annotations

import contextvars
import copy
from collections.abc import Callable
from typing import Any

from services.session_vault import COOKIE_NAME, SessionVault, apply_cookies

_current: contextvars.ContextVar[Any] = contextvars.ContextVar("xiaowo_jw_client", default=None)
_NO_CLIENT = object()
_access_guard: contextvars.ContextVar[Callable[[], None] | None] = contextvars.ContextVar(
    "xiaowo_jw_access_guard", default=None,
)


def set_access_guard(guard: Callable[[], None]) -> contextvars.Token:
    """Carry live authorization into copied contexts, not just a login snapshot."""
    return _access_guard.set(guard)


def reset_access_guard(token: contextvars.Token) -> None:
    _access_guard.reset(token)


def _guard_client(client: Any) -> None:
    guard = _access_guard.get()
    if guard is None or getattr(client.session, "_xiaowo_access_guard", None) is guard:
        return
    # A retained client must remain guarded even after its parent context resets.
    send = getattr(client.session, "_xiaowo_unguarded_send", client.session.send)

    def guarded_send(*args, **kwargs):
        guard()
        return send(*args, **kwargs)

    client.session._xiaowo_unguarded_send = send
    client.session._xiaowo_access_guard = guard
    client.session.send = guarded_send


def current_client() -> Any:
    """当前请求的教务会话（无则 None）。"""
    client = _current.get()
    return None if client is _NO_CLIENT else client


def set_client(client: Any) -> contextvars.Token:
    if client is not None:
        _guard_client(client)
    return _current.set(_NO_CLIENT if client is None else client)


def request_context_active() -> bool:
    """Explicitly anonymous mobile contexts may never fall back to a global jar."""
    return _current.get() is not None


def bind_verified_identity(client: Any, identity: dict) -> Any:
    """Bind only a school-verified snapshot, never identity from request JSON."""
    sid = str(identity.get("student_id") or identity.get("id") or "").strip().upper()
    if not sid:
        return client
    client._student_id = sid
    client._student_profile = dict(identity)
    return client


def client_from_verified_payload(payload: dict) -> Any:
    if payload.get("auth_mode") != "cas":
        return None
    data_id = payload.get("student_data_id")
    if type(data_id) is not int or data_id <= 0:
        return None
    from services.cas_client import CASClient
    client = CASClient()
    if not apply_cookies(client.session, payload.get("cookies") or []):
        return None
    client._logged_in = True
    client._student_data_id = data_id
    return bind_verified_identity(client, payload["profile"])


def in_thread_context(call):
    """Capture at submission; each execution owns its own mutable HTTP session."""
    context = contextvars.copy_context()
    parent = current_client()
    active = request_context_active()
    # Snapshot before the parent request can finish; no live session crosses threads.
    jar = copy.deepcopy(parent.session.cookies) if parent is not None else None
    identity = {"id": parent.student_id, **copy.deepcopy(getattr(parent, "_student_profile", {}))} if parent is not None else {}
    logged_in = bool(parent.is_logged_in) if parent is not None else False
    data_id = getattr(parent, "student_data_id", None)

    def run(*args, **kwargs):
        def invoke():
            guard = _access_guard.get()
            if guard is not None:
                guard()
            token = None
            client = None
            if active:
                if jar is not None:
                    from services.cas_client import CASClient
                    client = CASClient()
                    client.session.cookies.update(copy.deepcopy(jar))
                    client._logged_in = logged_in
                    client._student_data_id = data_id
                    bind_verified_identity(client, identity)
                token = set_client(client)
            try:
                return call(*args, **kwargs)
            finally:
                if token is not None:
                    reset_client(token)
                if client is not None:
                    client.session.close()
        return context.run(invoke)
    return run


def reset_client(token: contextvars.Token) -> None:
    try:
        _current.reset(token)
    except Exception:  # noqa: BLE001
        pass


_identity_cache: dict[str, tuple[float, dict, int | None]] = {}
_IDENTITY_TTL = 600  # 10 分钟


def identity_for_client(client: Any, *, now: float | None = None) -> dict:
    """用 vault 会话从教务取真实身份（学号/姓名/学院/专业），按 cookie 指纹缓存 10 分钟。"""
    import hashlib as _hashlib
    import time as _time

    if client is None:
        return {}
    cookies = sorted((str(c.domain), str(c.path), str(c.name), str(c.value)) for c in client.session.cookies)
    key = _hashlib.sha1(repr(cookies).encode("utf-8")).hexdigest()
    now = _time.time() if now is None else now
    hit = _identity_cache.get(key)
    if hit and hit[0] > now:
        client._student_data_id = hit[2]
        return dict(hit[1])
    info: dict[str, Any] = {}
    data_id = None
    try:
        data_id = client._resolve_data_id_from_grades()
        if type(data_id) is int and data_id > 0:
            client._student_data_id = data_id
            page = client.get_student_info_page(data_id) or {}
            prof = page.get("profile") or {}
            info = {
                "student_id": prof.get("学号") or "",
                "name": prof.get("姓名") or "",
                "college": prof.get("所在学院") or "",
                "major": prof.get("专业") or prof.get("所修专业") or "",
                "grade": prof.get("年级") or "",
            }
            # 姓名/学号不在档案页，取成绩档案的 stdGradeRank
            try:
                gp = client._fetch_grade_profile() or {}
                rank = (gp.get("stdGradeRank") or {}) if isinstance(gp, dict) else {}
                info["name"] = info["name"] or str(rank.get("studentName") or "")
                info["student_id"] = info["student_id"] or str(rank.get("studentCode") or "")
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001 — 身份拿不到不影响会话
        info = {}
    info = {k: v for k, v in info.items() if v}
    _identity_cache[key] = (now + _IDENTITY_TTL, info, data_id)
    return dict(info)


def client_from_cookie_blob(blob: str | None, *, settings: Any, probe: bool = False) -> Any:
    """cookie → CASClient（不触网，除非 probe=True）。"""
    if not blob:
        return None
    vault = SessionVault(settings.session_vault_key, ttl_seconds=settings.session_vault_ttl_seconds)
    if not vault.enabled:
        return None
    cookies = vault.open(blob)
    if not cookies:
        return None
    from services.cas_client import CASClient

    client = CASClient()
    if apply_cookies(client.session, cookies) == 0:
        return None
    client._logged_in = True if not probe else client.probe_jw_session()
    return client


def client_from_request(request: Any, *, probe: bool = False) -> Any:
    """从请求 cookie 取会话（`xiaowo_jw`）。"""
    settings = request.app.state.settings
    blob = request.cookies.get(COOKIE_NAME)
    return client_from_cookie_blob(blob, settings=settings, probe=probe)
