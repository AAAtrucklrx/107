#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地最小服务（L2）：隔离验证"方案 B 登录态"链路（2026-10-23）。

只挂登录态相关接口，不加载小蜗全量依赖（chromadb/langchain 等），用于本地实测：
  GET  /dev/login              → 造一个 demo 会话 cookie，返回 csrf
  POST /auth/sso_cookie        → 校验并加密导入 CookieJar（同真实端点逻辑）
  GET  /auth/session           → {"jw_session": bool, "student": ...}
  DELETE /auth/sso_cookie      → 清除
  GET  /dev/probe              → 用托管会话直接打教务 /home/menu
  GET  /dev/tool-probe         → 走 tools.course_tools._cas()（验证工具层注入）

启动：
  .venv-local/bin/uvicorn scripts.dev_auth_app:app --port 8799
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace

from fastapi import FastAPI, Header, HTTPException, Request, Response

from services.jw_session import client_from_cookie_blob, client_from_request
from services.session_vault import (
    COOKIE_NAME, CookiePayloadError, SessionVault, apply_cookies, validate_payload,
)

VAULT_KEY = "dev-local-vault-key-please-change"
SETTINGS = SimpleNamespace(session_vault_key=VAULT_KEY, session_vault_ttl_seconds=3600)
SESSION_COOKIE = "dev_session"
TOKENS: dict[str, str] = {}   # session -> csrf

app = FastAPI(title="xiaowo dev auth (L2)")
app.state.settings = SETTINGS   # 与真实 app 一致：端点从这里读配置


def _vault() -> SessionVault:
    return SessionVault(SETTINGS.session_vault_key, ttl_seconds=SETTINGS.session_vault_ttl_seconds)


def _guard(request: Request, csrf: str | None) -> None:
    sid = request.cookies.get(SESSION_COOKIE)
    if not sid or TOKENS.get(sid) != (csrf or ""):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")


@app.get("/dev/login")
def dev_login(response: Response) -> dict:
    sid = secrets.token_urlsafe(12)
    csrf = secrets.token_urlsafe(16)
    TOKENS[sid] = csrf
    response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax", path="/")
    return {"ok": True, "csrf": csrf}


@app.post("/auth/sso_cookie")
async def import_sso_cookie(request: Request, response: Response,
                            x_csrf_token: str | None = Header(default=None)) -> dict:
    _guard(request, x_csrf_token)
    vault = _vault()
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(400, detail="INVALID_JSON")
    try:
        essential = validate_payload((body or {}).get("cookies"))
    except CookiePayloadError as exc:
        raise HTTPException(400, detail=exc.code)
    # 用真实 CASClient 校验会话可用（只读 /home/menu）
    from services.cas_client import CASClient
    probe = CASClient()
    if apply_cookies(probe.session, essential) == 0 or not probe.probe_jw_session():
        raise HTTPException(400, detail="COOKIE_UNUSABLE")
    blob = vault.seal(essential)
    # 本地是 http://127.0.0.1，Secure cookie 不会被浏览器/curl 回传；仅调试用 secure=False
    response.set_cookie(COOKIE_NAME, blob, **vault.cookie_kwargs(secure=False))
    return {"ok": True, "size": len(blob), "items": len(essential)}


@app.get("/auth/session")
def session(request: Request) -> dict:
    client = client_from_request(request)
    return {"jw_session": client is not None, "vaulted": client is not None}


@app.delete("/auth/sso_cookie")
def clear_sso_cookie(request: Request, response: Response,
                     x_csrf_token: str | None = Header(default=None)) -> dict:
    _guard(request, x_csrf_token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/dev/probe")
def dev_probe(request: Request) -> dict:
    client = client_from_request(request)
    if client is None:
        return {"jw_session": False, "status": None}
    r = client.session.get("https://jw.ustc.edu.cn/home/menu", timeout=20)
    items = 0
    try:
        data = r.json()
        items = len(data) if isinstance(data, list) else len(data or {})
    except Exception:  # noqa: BLE001
        pass
    return {"jw_session": True, "status": r.status_code, "items": items}


@app.get("/dev/tool-probe")
def dev_tool_probe(request: Request) -> dict:
    """验证工具层 _cas() 是否拿到托管会话（不依赖数据库）。"""
    from services.jw_session import reset_client, set_client
    from tools.course_tools import _cas

    client = client_from_request(request)
    token = set_client(client)
    try:
        used = _cas()
        same = used is client and client is not None
        status = None
        if same:
            status = used.session.get("https://jw.ustc.edu.cn/home/menu", timeout=20).status_code
        return {"injected": same, "status": status}
    finally:
        reset_client(token)
