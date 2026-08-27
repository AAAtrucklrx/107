"""Reviewer-only operational lists outside individual review items."""

from __future__ import annotations

import secrets
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request

from xiaowo_web.api.dependencies import require_reviewer, require_reviewer_mutation
from xiaowo_web.api.pagination import decode_cursor, encode_cursor
from xiaowo_web.auth.models import Principal
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
