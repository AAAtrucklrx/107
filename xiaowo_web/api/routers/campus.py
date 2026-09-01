"""Public, source-labelled campus service endpoints."""

from __future__ import annotations

import asyncio
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from xiaowo_web.api.dependencies import (
    optional_principal,
    require_authenticated,
    require_authenticated_mutation,
)
from xiaowo_web.api.schemas import CampusToolApplicationCreate
from xiaowo_web.auth.models import Principal
from xiaowo_web.campus.tool_store import CampusToolError, TOOL_CATEGORIES
from xiaowo_web.errors import ApiError
from xiaowo_web.evidence.url_security import UrlSafetyError


router = APIRouter(prefix="/campus", tags=["campus"])


def _campus_namespace(principal: Principal | None) -> str:
    return "demo" if principal is not None and principal.auth_mode == "demo" else "production"


def _request_id(value: str | None) -> str:
    return (value or f"req-{secrets.token_urlsafe(12)}")[:128]


def _raise_tool_error(exc: CampusToolError | UrlSafetyError) -> None:
    if isinstance(exc, CampusToolError):
        raise ApiError(exc.status_code, exc.code, exc.message) from exc
    raise ApiError(422, exc.code, exc.message) from exc


@router.get("/services")
async def services(
    request: Request,
    query: Annotated[str, Query(max_length=100)] = "",
    category: Annotated[str, Query(max_length=40)] = "",
) -> dict:
    return await asyncio.to_thread(request.app.state.campus_service.services, query, category)


@router.get("/activities")
async def activities(
    request: Request,
    query: Annotated[str, Query(max_length=100)] = "",
    category: Annotated[str, Query(max_length=40)] = "",
    limit: Annotated[int, Query(ge=1, le=20)] = 12,
) -> dict:
    return await asyncio.to_thread(
        request.app.state.campus_service.activities,
        query,
        category,
        limit,
    )


@router.get("/tools")
async def tools(
    request: Request,
    principal: Annotated[Principal | None, Depends(optional_principal)],
    query: Annotated[str, Query(max_length=100)] = "",
    category: Annotated[str, Query(max_length=40)] = "",
) -> dict:
    try:
        items = await asyncio.to_thread(
            request.app.state.campus_tool_store.list_public_tools,
            _campus_namespace(principal),
            query=query,
            category=category,
        )
    except CampusToolError as exc:
        _raise_tool_error(exc)
    demo = _campus_namespace(principal) == "demo"
    return {
        "items": items,
        "categories": list(TOOL_CATEGORIES),
        "source": {
            "kind": "demo_fixture" if demo else "approved_community",
            "label": "合成演示申请" if demo else "社区提交 · 管理员审核",
            "demo": demo,
            "stale": False,
        },
    }


@router.post("/tools/applications", status_code=201)
async def submit_tool_application(
    payload: CampusToolApplicationCreate,
    request: Request,
    principal: Annotated[Principal, Depends(require_authenticated_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    profile = principal.profile or {}
    try:
        return await asyncio.to_thread(
            request.app.state.campus_tool_store.submit_application,
            namespace=_campus_namespace(principal),
            applicant_principal_id=principal.principal_id,
            applicant_auth_mode=principal.auth_mode,
            applicant_name=str(profile.get("name") or ""),
            name=payload.name,
            description=payload.description,
            category=payload.category,
            url=payload.url,
            request_id=_request_id(request_id),
        )
    except (CampusToolError, UrlSafetyError) as exc:
        _raise_tool_error(exc)


@router.get("/tools/applications/mine")
async def my_tool_applications(
    request: Request,
    principal: Annotated[Principal, Depends(require_authenticated)],
    status: Annotated[str, Query(max_length=20)] = "",
) -> dict:
    try:
        result = await asyncio.to_thread(
            request.app.state.campus_tool_store.list_owner_applications,
            _campus_namespace(principal),
            principal.principal_id,
            status=status,
        )
    except CampusToolError as exc:
        _raise_tool_error(exc)
    return {**result, "namespace": _campus_namespace(principal)}


@router.get("/tools/notifications")
async def tool_notifications(
    request: Request,
    principal: Annotated[Principal, Depends(require_authenticated)],
    unread_only: bool = False,
) -> dict:
    items = await asyncio.to_thread(
        request.app.state.campus_tool_store.list_notifications,
        _campus_namespace(principal),
        principal.principal_id,
        unread_only=unread_only,
    )
    return {"items": items, "namespace": _campus_namespace(principal)}


@router.post("/tools/notifications/{notification_id}/read")
async def mark_tool_notification_read(
    notification_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_authenticated_mutation)],
) -> dict:
    try:
        return await asyncio.to_thread(
            request.app.state.campus_tool_store.mark_notification_read,
            _campus_namespace(principal),
            principal.principal_id,
            notification_id,
        )
    except KeyError as exc:
        raise ApiError(404, "TOOL_NOTIFICATION_NOT_FOUND", "没有找到该通知。") from exc
