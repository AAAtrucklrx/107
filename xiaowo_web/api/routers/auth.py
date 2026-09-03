"""Anonymous bootstrap, demo login, CAS redirect, and logout."""

from __future__ import annotations

import asyncio
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from xiaowo_web.api.dependencies import (
    clear_session_cookies,
    ensure_principal,
    optional_principal,
    require_mutation,
    set_session_cookies,
)
from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError
from xiaowo_web.settings import AuthMode


router = APIRouter(prefix="/auth", tags=["auth"])


def _capabilities(principal: Principal) -> dict[str, bool]:
    return {
        "public_chat": True,
        "server_history": principal.history_owner_key is not None,
        "personal_academic": principal.is_authenticated,
        "admin_console": principal.is_admin and principal.review_namespace is not None,
        "knowledge_review": principal.is_admin and principal.review_namespace is not None,
        "production_publish": principal.auth_mode == "cas" and principal.is_admin,
    }


def _session_payload(principal: Principal, csrf_token: str) -> dict:
    return {
        "principal": {
            "id": principal.principal_id if principal.is_authenticated else None,
            "auth_mode": principal.auth_mode,
            "authenticated": principal.is_authenticated,
            "profile": principal.profile if principal.is_authenticated else None,
            "is_admin": principal.is_admin,
            "review_namespace": principal.review_namespace,
        },
        "capabilities": _capabilities(principal),
        "csrf_token": csrf_token,
    }


def _set_csrf_cookie(response: Response, request: Request, token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        settings.csrf_cookie_name,
        token,
        max_age=(
            settings.anonymous_session_seconds
            if optional_principal(request) is None
            else settings.session_absolute_seconds
        ),
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


@router.get("/session")
async def session(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(ensure_principal)],
) -> dict:
    csrf_token = principal.csrf_token or request.cookies.get(
        request.app.state.settings.csrf_cookie_name,
        "",
    )
    if not request.app.state.store.validate_csrf(principal.session_key, csrf_token):
        principal = request.app.state.auth_service.rotate_csrf(principal)
        csrf_token = principal.csrf_token
        _set_csrf_cookie(response, request, csrf_token)
    return _session_payload(principal, csrf_token)


@router.post("/demo")
async def login_demo(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    settings = request.app.state.settings
    if settings.auth_mode is not AuthMode.DEMO:
        raise ApiError(404, "AUTH_MODE_DISABLED", "演示登录在当前运行模式下未启用。")
    raw_token, demo_principal = request.app.state.auth_service.login_demo(principal)
    set_session_cookies(response, raw_token, demo_principal, settings)
    return _session_payload(demo_principal, demo_principal.csrf_token)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    request.app.state.auth_service.logout(principal)
    clear_session_cookies(response, request.app.state.settings)
    if principal.auth_mode == "cas":
        try:
            from services.service_container import ServiceContainer

            ServiceContainer().logout(principal.principal_id)
        except Exception:
            pass
    return {"logged_out": True}


@router.post("/demo/reset")
async def reset_demo(
    request: Request,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    settings = request.app.state.settings
    # 2026-09-03 事故加固：默认禁用（公网访客曾触发清空全部已批准知识）
    if not settings.demo_reset_enabled:
        raise ApiError(404, "AUTH_MODE_DISABLED", "演示恢复当前未启用（管理员需在 .env 设置 XIAOWO_DEMO_RESET_ENABLED=true）。")
    if settings.auth_mode is not AuthMode.DEMO or principal.auth_mode != "demo":
        raise ApiError(404, "AUTH_MODE_DISABLED", "演示恢复在当前运行模式下未启用。")
    import hmac

    provided_key = request.headers.get("x-demo-reset-key", "")
    if not provided_key or not hmac.compare_digest(provided_key, settings.demo_reset_key):
        _log_reset_attempt(request, "FORBIDDEN")
        raise ApiError(403, "RESET_FORBIDDEN", "重置密钥缺失或不正确（该操作已记录）。")
    if not await _export_demo_snapshot(request):
        _log_reset_attempt(request, "EXPORT_FAILED")
        raise ApiError(500, "RESET_EXPORT_FAILED", "重置前快照导出失败，已中止操作（数据未变）。")
    _log_reset_attempt(request, "GRANTED")
    request.app.state.chat_manager.cancel_owner(principal)
    deleted = request.app.state.store.reset_demo_owner(principal)
    from xiaowo_web.campus.demo import ensure_demo_campus_tool_seed

    request.app.state.campus_tool_store.reset_demo_namespace()
    ensure_demo_campus_tool_seed(request.app.state.campus_tool_store)
    review_reset = False
    if principal.is_admin:
        from xiaowo_web.review.demo import ensure_demo_review_seed

        request.app.state.review_store.reset_demo_namespace()
        ensure_demo_review_seed(request.app.state.review_store)
        review_reset = True
    return {
        "reset": True,
        "snapshot_exported": True,
        "profile_id": principal.principal_id,
        "deleted": deleted,
        "review_namespace": "demo" if principal.is_admin else None,
        "review_reset": review_reset,
        "campus_tools_reset": True,
    }


def _log_reset_attempt(request: Request, outcome: str) -> None:
    """审计：带来源 IP/UA 的服务器日志（reset 会清空 review_audit，故审计写日志）。"""
    from utils.logger import get_logger

    client_ip = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        client_ip = f"{client_ip} (via {forwarded.split(',')[0].strip()})"
    get_logger("xiaowo.auth").warning(
        f"demo/reset 尝试: outcome={outcome} ip={client_ip} ua={request.headers.get('user-agent', '')[:120]}"
    )


async def _export_demo_snapshot(request: Request) -> bool:
    """清空前导出 demo 全量（review + campus_tools → JSON 目录）。失败返回 False（中止 reset）。"""
    import json
    import sqlite3
    import time as _time
    from pathlib import Path

    try:
        settings = request.app.state.settings
        out_dir = Path(settings.review_db_path).parent / "backups" / f"demo_reset_{_time.strftime('%Y%m%d_%H%M%S')}"
        out_dir.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(settings.review_db_path)
        db.row_factory = sqlite3.Row

        # 直接带 namespace 列的表
        direct = ("review_items", "review_chunks", "review_versions", "web_snapshots",
                  "publish_generations", "active_index_state", "publish_documents",
                  "ingestion_jobs", "ingestion_tombstones", "review_audit",
                  "campus_tool_applications", "campus_tools", "campus_tool_audit",
                  "user_notifications", "source_trust_proposals", "publish_jobs", "publish_job_items")
        # 需经关联表过滤（无 namespace 列）的表 → 子查询映射
        join_map = {
            "review_versions": ("item_id", "SELECT item_id FROM review_items WHERE namespace='demo'"),
            "review_chunks": ("item_id", "SELECT item_id FROM review_items WHERE namespace='demo'"),
            "publish_documents": ("generation_id", "SELECT generation_id FROM publish_generations WHERE namespace='demo'"),
            "publish_job_items": ("job_id", "SELECT job_id FROM publish_jobs WHERE namespace='demo'"),
        }
        dump: dict[str, list[dict]] = {}
        for table in direct:
            if table in join_map:
                col, sub = join_map[table]
                rows = db.execute(
                    f"SELECT * FROM {table} WHERE {col} IN ({sub})"
                ).fetchall()
            else:
                rows = db.execute(f"SELECT * FROM {table} WHERE namespace = ?", ("demo",)).fetchall()
            if rows:
                dump[table] = [dict(row) for row in rows]
        db.close()
        (out_dir / "demo_review_dump.json").write_text(
            json.dumps(dump, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        (out_dir / "reset_meta.json").write_text(json.dumps({
            "exported_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
            "trigger_ip": request.client.host if request.client else "unknown",
            "user_agent": request.headers.get("user-agent", "")[:200],
            "trigger_id": request.headers.get("x-request-id", "")[:64],
            "note": "演示重置前自动导出快照（恢复：deploy/server/restore_review_data.py 或按 SOP 还原）",
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        return True
    except Exception:  # noqa: BLE001
        return False


def _service_with_state(service_url: str, state: str) -> str:
    parts = urlsplit(service_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["state"] = state
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


@router.get("/cas/login")
async def cas_login(request: Request) -> RedirectResponse:
    settings = request.app.state.settings
    if settings.auth_mode is not AuthMode.CAS:
        raise ApiError(404, "AUTH_MODE_DISABLED", "CAS 登录在当前运行模式下未启用。")
    state = request.app.state.auth_service.sign_cas_state()
    service_url = _service_with_state(settings.cas_service_url, state)
    login_url = request.app.state.cas_provider.login_url(service_url)
    response = RedirectResponse(login_url, status_code=302)
    response.set_cookie(
        settings.cas_state_cookie_name,
        state,
        max_age=600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/api/v1/auth/cas/callback",
    )
    return response


@router.get("/cas/callback")
async def cas_callback(
    request: Request,
    ticket: Annotated[str, Query(min_length=3, max_length=512)],
    state: Annotated[str, Query(min_length=20, max_length=512)],
) -> RedirectResponse:
    settings = request.app.state.settings
    if settings.auth_mode is not AuthMode.CAS:
        raise ApiError(404, "AUTH_MODE_DISABLED", "CAS 登录在当前运行模式下未启用。")
    cookie_state = request.cookies.get(settings.cas_state_cookie_name, "")
    if not request.app.state.auth_service.validate_cas_state(state, cookie_state):
        raise ApiError(403, "CAS_STATE_INVALID", "CAS 登录状态校验失败，请重新登录。")
    service_url = _service_with_state(settings.cas_service_url, state)
    identity = await asyncio.to_thread(
        request.app.state.cas_provider.authenticate,
        ticket,
        service_url,
    )
    if identity is None:
        raise ApiError(401, "CAS_AUTH_FAILED", "CAS 身份验证失败，请重新登录。")
    normalized_id = identity.student_id.strip().upper()
    returned_id = str(identity.profile.get("id") or "").strip().upper()
    if not normalized_id or (returned_id and returned_id != normalized_id):
        raise ApiError(403, "CAS_PROFILE_MISMATCH", "CAS 身份与返回档案不一致。")
    old_principal = optional_principal(request)
    raw_token, principal = request.app.state.auth_service.login_cas(
        normalized_id,
        identity.profile,
        old_principal,
    )
    response = RedirectResponse(f"{settings.public_origin}/academic", status_code=303)
    set_session_cookies(response, raw_token, principal, settings)
    response.delete_cookie(settings.cas_state_cookie_name, path="/api/v1/auth/cas/callback")
    return response
