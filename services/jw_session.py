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
from typing import Any

from services.session_vault import COOKIE_NAME, SessionVault, apply_cookies

_current: contextvars.ContextVar[Any] = contextvars.ContextVar("xiaowo_jw_client", default=None)


def current_client() -> Any:
    """当前请求的教务会话（无则 None）。"""
    return _current.get()


def set_client(client: Any) -> contextvars.Token:
    return _current.set(client)


def reset_client(token: contextvars.Token) -> None:
    try:
        _current.reset(token)
    except Exception:  # noqa: BLE001
        pass


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
