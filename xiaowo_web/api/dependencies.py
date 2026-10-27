"""Request-scoped session, cookie, CSRF, and authorization helpers."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Request, Response

from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError
from xiaowo_web.settings import WebSettings


def get_settings(request: Request) -> WebSettings:
    return request.app.state.settings


def optional_principal(request: Request) -> Principal | None:
    if request.headers.get("authorization") is not None:
        from xiaowo_web.auth.mobile import bearer_token
        token = bearer_token(request)
        service = getattr(request.app.state, "mobile_auth", None)
        # Middleware may have resolved this token before an awaited request body.
        # Recheck here so concurrent logout cannot authorize a new mutation/run.
        principal = service.resolve(token) if service is not None else None
        if principal is None:
            raise ApiError(401, "AUTH_REQUIRED", "登录凭据已失效，请重新登录。")
        return principal
    settings: WebSettings = request.app.state.settings
    raw_token = request.cookies.get(settings.cookie_name, "")
    return request.app.state.auth_service.resolve(raw_token)


def set_session_cookies(
    response: Response,
    raw_token: str,
    principal: Principal,
    settings: WebSettings,
) -> None:
    max_age = (
        settings.anonymous_session_seconds
        if principal.auth_mode == "anonymous"
        else settings.session_absolute_seconds
    )
    response.set_cookie(
        settings.cookie_name,
        raw_token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name,
        principal.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )


def clear_session_cookies(response: Response, settings: WebSettings) -> None:
    response.delete_cookie(settings.cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


def _promote_with_jw_identity(request: Request, principal: Principal) -> Principal:
    """方案 B：有有效教务会话时，把匿名主体提升为正式登录态（2026-10-23）。"""
    if principal.is_authenticated or request.headers.get("authorization") is not None:
        return principal
    jw = getattr(request.state, "jw_identity", None) or {}
    student_id = str(jw.get("student_id") or "")
    name = str(jw.get("name") or "")
    if not student_id or not name:
        return principal
    settings = request.app.state.settings
    profile = {
        **(principal.profile or {}),
        "id": student_id,
        "student_id": student_id,
        "name": name,
        "major": __import__("re").sub(r"^\d+", "", str(jw.get("major") or "")),
        "grade": str(jw.get("grade") or ""),
        "college": str(jw.get("college") or ""),
    }
    admins = str(getattr(settings, "admin_ids", "") or "")
    is_admin = student_id in {a.strip() for a in admins.split(",") if a.strip()}
    return Principal(
        principal_id=student_id,
        auth_mode="cas",
        profile=profile,
        is_admin=is_admin,
        session_key=principal.session_key,
        csrf_token=principal.csrf_token,
    )


def ensure_principal(request: Request, response: Response) -> Principal:
    principal = optional_principal(request)
    if principal is not None:
        return _promote_with_jw_identity(request, principal)
    raw_token, principal = request.app.state.auth_service.create_anonymous()
    set_session_cookies(response, raw_token, principal, request.app.state.settings)
    return _promote_with_jw_identity(request, principal)


def require_principal(request: Request) -> Principal:
    principal = optional_principal(request)
    if principal is None:
        raise ApiError(401, "AUTH_REQUIRED", "会话已失效，请刷新页面。")
    return _promote_with_jw_identity(request, principal)


def require_mutation(request: Request) -> Principal:
    settings: WebSettings = request.app.state.settings
    principal = require_principal(request)
    if request.headers.get("authorization") is not None:
        from xiaowo_web.auth.mobile import require_json, require_mobile_origin
        require_mobile_origin(request)
        if request.method not in {"DELETE", "GET", "HEAD"}:
            require_json(request)
        return principal
    origin = request.headers.get("origin", "").rstrip("/")
    if not origin or not hmac.compare_digest(origin, settings.public_origin):
        raise ApiError(403, "ORIGIN_MISMATCH", "请求来源校验失败。")
    csrf_header = request.headers.get("x-csrf-token", "")
    csrf_cookie = request.cookies.get(settings.csrf_cookie_name, "")
    if not csrf_header or not csrf_cookie or not hmac.compare_digest(csrf_header, csrf_cookie):
        raise ApiError(403, "CSRF_INVALID", "安全令牌无效，请刷新页面。")
    if not request.app.state.store.validate_csrf(principal.session_key, csrf_header):
        raise ApiError(403, "CSRF_INVALID", "安全令牌已失效，请刷新页面。")
    return principal


def require_authenticated(
    principal: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    if not principal.is_authenticated:
        raise ApiError(401, "AUTH_REQUIRED", "此功能需要登录。")
    return principal


def require_authenticated_mutation(request: Request) -> Principal:
    principal = require_mutation(request)
    if not principal.is_authenticated:
        raise ApiError(401, "AUTH_REQUIRED", "此功能需要登录。")
    return principal


def require_reviewer(
    principal: Annotated[Principal, Depends(require_principal)],
) -> Principal:
    if not principal.is_authenticated:
        raise ApiError(401, "AUTH_REQUIRED", "此功能需要登录。")
    if not principal.is_admin or principal.review_namespace is None:
        raise ApiError(403, "FORBIDDEN", "当前账号没有审核权限。")
    return principal


def require_reviewer_mutation(request: Request) -> Principal:
    principal = require_mutation(request)
    if not principal.is_authenticated:
        raise ApiError(401, "AUTH_REQUIRED", "此功能需要登录。")
    if not principal.is_admin or principal.review_namespace is None:
        raise ApiError(403, "FORBIDDEN", "当前账号没有审核权限。")
    return principal
