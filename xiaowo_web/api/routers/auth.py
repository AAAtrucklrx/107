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
    if settings.auth_mode is not AuthMode.DEMO or principal.auth_mode != "demo":
        raise ApiError(404, "AUTH_MODE_DISABLED", "演示恢复在当前运行模式下未启用。")
    request.app.state.chat_manager.cancel_owner(principal)
    deleted = request.app.state.store.reset_demo_owner(principal)
    review_reset = False
    if principal.is_admin:
        from xiaowo_web.review.demo import ensure_demo_review_seed

        request.app.state.review_store.reset_demo_namespace()
        ensure_demo_review_seed(request.app.state.review_store)
        review_reset = True
    return {
        "reset": True,
        "profile_id": principal.principal_id,
        "deleted": deleted,
        "review_namespace": "demo" if principal.is_admin else None,
        "review_reset": review_reset,
    }


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
