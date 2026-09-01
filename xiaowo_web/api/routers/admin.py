"""Reviewer-only operational lists outside individual review items."""

from __future__ import annotations

import asyncio
import secrets
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from xiaowo_web.api.dependencies import require_reviewer, require_reviewer_mutation
from xiaowo_web.api.pagination import decode_cursor, encode_cursor
from xiaowo_web.api.schemas import CampusToolApproval, CampusToolRejection, CampusToolUnpublish
from xiaowo_web.auth.models import Principal
from xiaowo_web.campus.tool_store import CampusToolError
from xiaowo_web.errors import ApiError
from xiaowo_web.review.trust_proposals import build_source_trust_diff
from xiaowo_web.settings import PROJECT_ROOT


router = APIRouter(prefix="/admin", tags=["admin"])


def _namespace(principal: Principal) -> str:
    namespace = principal.review_namespace
    if namespace is None:
        raise ApiError(403, "FORBIDDEN", "当前账号没有审核命名空间。")
    return namespace


def _request_id(value: str | None) -> str:
    return (value or f"req-{secrets.token_urlsafe(12)}")[:128]


def _raise_campus_tool_error(exc: CampusToolError) -> None:
    raise ApiError(exc.status_code, exc.code, exc.message) from exc


@router.get("/campus-tool-applications")
async def list_campus_tool_applications(
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer)],
    status: Annotated[str, Query(max_length=20)] = "pending",
    query: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict:
    namespace = _namespace(principal)
    try:
        items = await asyncio.to_thread(
            request.app.state.campus_tool_store.list_admin_applications,
            namespace,
            status=status,
            query=query,
            limit=limit,
        )
    except CampusToolError as exc:
        _raise_campus_tool_error(exc)
    return {"items": items, "namespace": namespace}


@router.get("/campus-tool-applications/{application_id}")
async def campus_tool_application_detail(
    application_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer)],
) -> dict:
    item = await asyncio.to_thread(
        request.app.state.campus_tool_store.get_admin_application,
        _namespace(principal),
        application_id,
    )
    if item is None:
        raise ApiError(404, "TOOL_APPLICATION_NOT_FOUND", "没有找到该工具申请。")
    return item


@router.post("/campus-tool-applications/{application_id}/approve")
async def approve_campus_tool_application(
    application_id: str,
    payload: CampusToolApproval,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        return await asyncio.to_thread(
            request.app.state.campus_tool_store.approve_application,
            _namespace(principal),
            application_id,
            expected_version=payload.expected_version,
            actor_key=principal.principal_id,
            request_id=_request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "TOOL_APPLICATION_NOT_FOUND", "没有找到该工具申请。") from exc
    except CampusToolError as exc:
        _raise_campus_tool_error(exc)


@router.post("/campus-tool-applications/{application_id}/reject")
async def reject_campus_tool_application(
    application_id: str,
    payload: CampusToolRejection,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        return await asyncio.to_thread(
            request.app.state.campus_tool_store.reject_application,
            _namespace(principal),
            application_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
            actor_key=principal.principal_id,
            request_id=_request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "TOOL_APPLICATION_NOT_FOUND", "没有找到该工具申请。") from exc
    except CampusToolError as exc:
        _raise_campus_tool_error(exc)


@router.get("/campus-tools")
async def list_managed_campus_tools(
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer)],
    status: Annotated[str, Query(max_length=20)] = "active",
    query: Annotated[str, Query(max_length=100)] = "",
) -> dict:
    namespace = _namespace(principal)
    try:
        items = await asyncio.to_thread(
            request.app.state.campus_tool_store.list_admin_tools,
            namespace,
            status=status,
            query=query,
        )
    except CampusToolError as exc:
        _raise_campus_tool_error(exc)
    return {"items": items, "namespace": namespace}


@router.post("/campus-tools/{tool_id}/unpublish")
async def unpublish_campus_tool(
    tool_id: str,
    payload: CampusToolUnpublish,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        return await asyncio.to_thread(
            request.app.state.campus_tool_store.unpublish_tool,
            _namespace(principal),
            tool_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
            actor_key=principal.principal_id,
            request_id=_request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "CAMPUS_TOOL_NOT_FOUND", "没有找到该校园工具。") from exc
    except CampusToolError as exc:
        _raise_campus_tool_error(exc)


@router.get("/campus-tool-audit")
async def list_campus_tool_audit(
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer)],
    query: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> dict:
    namespace = _namespace(principal)
    items = await asyncio.to_thread(
        request.app.state.campus_tool_store.list_audit,
        namespace,
        query=query,
        limit=limit,
    )
    return {"items": items, "namespace": namespace}


@router.get("/feedback")
async def list_feedback(
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str, Query(max_length=512)] = "",
) -> dict:
    namespace = _namespace(principal)
    try:
        items, next_anchor = request.app.state.store.list_feedback_page(
            namespace,
            limit=limit,
            cursor=decode_cursor(cursor),
        )
    except ValueError as exc:
        raise ApiError(400, "CURSOR_INVALID", "分页游标无效，请从第一页重新加载。") from exc
    return {
        "items": items,
        "next_cursor": encode_cursor(*next_anchor) if next_anchor else None,
        "namespace": namespace,
    }


@router.get("/generations")
async def generation_state(
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer)],
) -> dict:
    return request.app.state.review_store.get_generation_state(_namespace(principal))


@router.post("/generations/rollback")
async def rollback_generation(
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    namespace = _namespace(principal)
    state = request.app.state.review_store.get_generation_state(namespace)
    previous_id = state.get("previous_generation_id")
    if not previous_id:
        raise ApiError(409, "GENERATION_ROLLBACK_UNAVAILABLE", "当前没有可回滚的完整 generation。")
    if state.get("publish_busy"):
        raise ApiError(409, "GENERATION_ROLLBACK_BUSY", "发布任务进行中，暂不能回滚。")
    if not request.app.state.approved_retriever.validate_generation(namespace, previous_id):
        raise ApiError(409, "GENERATION_INTEGRITY_INVALID", "上一 generation 未通过完整性校验。")
    try:
        rolled_back = request.app.state.review_store.rollback_active_generation(
            namespace,
            principal.principal_id,
            _request_id(request_id),
        )
    except RuntimeError as exc:
        raise ApiError(409, "GENERATION_ROLLBACK_BUSY", "发布任务进行中，暂不能回滚。") from exc
    except ValueError as exc:
        raise ApiError(409, "GENERATION_ROLLBACK_UNAVAILABLE", "上一 generation 已过期或不可回滚。") from exc
    return {"namespace": namespace, "status": "active", **rolled_back}


@router.post("/source-trust-proposals/export")
async def export_source_trust_proposals(
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    namespace = _namespace(principal)
    proposals = request.app.state.review_store.list_source_trust_proposals(namespace)
    if not proposals:
        raise ApiError(409, "SOURCE_PROPOSAL_EMPTY", "当前没有待导出的来源白名单建议。")
    diff, added = build_source_trust_diff(PROJECT_ROOT / "config" / "source_trust.yaml", proposals)
    if not diff or added == 0:
        raise ApiError(409, "SOURCE_PROPOSAL_DUPLICATE", "建议规则已存在于当前白名单。")
    proposal_ids = [str(item["proposal_id"]) for item in proposals]
    request.app.state.review_store.mark_source_trust_proposals_exported(
        namespace,
        proposal_ids,
        principal.principal_id,
        _request_id(request_id),
    )
    return {
        "namespace": namespace,
        "filename": f"source-trust-{namespace}-{date.today().isoformat()}.diff",
        "proposal_ids": proposal_ids,
        "diff": diff,
    }
