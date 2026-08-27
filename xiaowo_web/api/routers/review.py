"""Reviewer queue fixed to the authenticated principal's namespace."""

from __future__ import annotations

import difflib
import secrets
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Query, Request

from xiaowo_web.api.dependencies import require_reviewer, require_reviewer_mutation
from xiaowo_web.api.schemas import (
    ChunkApproval,
    ReviewApproval,
    ReviewEdit,
    SourceTrustProposalCreate,
)
from xiaowo_web.api.pagination import decode_cursor, encode_cursor
from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError


router = APIRouter(prefix="/admin/review-items", tags=["review"])


def _request_id(value: str | None) -> str:
    return (value or f"req-{secrets.token_urlsafe(12)}")[:128]


def _namespace(principal: Principal) -> str:
    namespace = principal.review_namespace
    if namespace is None:
        raise ApiError(403, "FORBIDDEN", "当前账号没有审核命名空间。")
    return namespace


def _detail_or_404(request: Request, principal: Principal, item_id: str) -> dict:
    detail = request.app.state.review_store.get_item(_namespace(principal), item_id)
    if detail is None:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "没有找到该审核条目。")
    raw = detail.get("raw_snapshot") or ""
    candidates = [
        version
        for version in detail.get("versions") or []
        if version.get("kind") in {"model", "human"}
    ]
    current = candidates[-1]["content_text"] if candidates else ""
    detail["diff"] = "\n".join(difflib.unified_diff(
        raw.splitlines(),
        current.splitlines(),
        fromfile="原文",
        tofile="当前清洗稿",
        lineterm="",
    ))
    return detail


@router.get("")
async def list_items(
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer)],
    status: Annotated[str, Query(max_length=30)] = "",
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str, Query(max_length=512)] = "",
) -> dict:
    items, next_anchor = request.app.state.review_store.list_items_page(
        _namespace(principal),
        status=status,
        limit=limit,
        cursor=decode_cursor(cursor),
    )
    return {
        "items": items,
        "next_cursor": encode_cursor(*next_anchor) if next_anchor else None,
        "namespace": _namespace(principal),
    }


@router.get("/{item_id}")
async def detail(
    item_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer)],
) -> dict:
    return _detail_or_404(request, principal, item_id)


@router.post("/{item_id}/review")
async def start_review(
    item_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        request.app.state.review_store.start_review(
            _namespace(principal), item_id, principal.principal_id, _request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "没有找到该审核条目。") from exc
    except ValueError as exc:
        raise ApiError(409, "REVIEW_STATE_INVALID", "当前状态不能开始审核。") from exc
    return {"item_id": item_id, "status": "in_review"}


@router.post("/{item_id}/versions")
async def edit_version(
    item_id: str,
    payload: ReviewEdit,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        version = request.app.state.review_store.edit_item(
            _namespace(principal),
            item_id,
            content=payload.content,
            chunks=payload.chunks,
            actor_key=principal.principal_id,
            request_id=_request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "没有找到该审核条目。") from exc
    except ValueError as exc:
        raise ApiError(409, "REVIEW_STATE_INVALID", "当前审核版本不能编辑。") from exc
    return {"item_id": item_id, "version": version, "status": "in_review"}


@router.post("/{item_id}/chunks/{chunk_id}")
async def approve_chunk(
    item_id: str,
    chunk_id: str,
    payload: ChunkApproval,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        request.app.state.review_store.set_chunk_approval(
            _namespace(principal),
            item_id,
            chunk_id,
            payload.approved,
            principal.principal_id,
            _request_id(request_id),
            approval_status=payload.approval_status,
        )
    except KeyError as exc:
        raise ApiError(404, "REVIEW_CHUNK_NOT_FOUND", "没有找到当前版本中的分块。") from exc
    except ValueError as exc:
        raise ApiError(409, "REVIEW_STATE_INVALID", "当前状态不能修改分块审核。") from exc
    resolved_status = payload.approval_status or ("approved" if payload.approved else "pending")
    return {
        "chunk_id": chunk_id,
        "approval_status": resolved_status,
        "approved": resolved_status == "approved",
    }


@router.post("/{item_id}/approve")
async def approve_item(
    item_id: str,
    payload: ReviewApproval,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        request.app.state.review_store.approve_item(
            _namespace(principal),
            item_id,
            category=payload.category,
            ttl_days=payload.ttl_days,
            actor_key=principal.principal_id,
            request_id=_request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "没有找到该审核条目。") from exc
    except ValueError as exc:
        raise ApiError(
            409,
            "REVIEW_APPROVAL_INVALID",
            "请先逐块完成批准或排除，并使用分类允许的有效期。",
        ) from exc
    return {"item_id": item_id, "status": "approved"}


@router.post("/{item_id}/reject")
async def reject_item(
    item_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        request.app.state.review_store.reject_item(
            _namespace(principal), item_id, principal.principal_id, _request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "没有找到该审核条目。") from exc
    except ValueError as exc:
        raise ApiError(409, "REVIEW_STATE_INVALID", "当前状态不能拒绝。") from exc
    return {"item_id": item_id, "status": "rejected"}


@router.post("/{item_id}/revoke")
async def revoke_item(
    item_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        request.app.state.review_store.revoke_item(
            _namespace(principal), item_id, principal.principal_id, _request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "没有找到该审核条目。") from exc
    except ValueError as exc:
        raise ApiError(409, "REVIEW_STATE_INVALID", "当前状态不能撤回。") from exc
    return {"item_id": item_id, "status": "revoked"}


@router.post("/{item_id}/publish/retry")
async def retry_publish(
    item_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        request.app.state.review_store.retry_publish(
            _namespace(principal), item_id, principal.principal_id, _request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "没有找到该审核条目。") from exc
    except ValueError as exc:
        raise ApiError(409, "REVIEW_STATE_INVALID", "当前条目没有可重试的发布任务。") from exc
    return {"item_id": item_id, "status": "pending_publish"}


@router.post("/{item_id}/refetch", status_code=202)
async def refetch_item(
    item_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    try:
        queued = request.app.state.review_store.queue_refetch(
            _namespace(principal),
            item_id,
            principal.principal_id,
            _request_id(request_id),
        )
    except KeyError as exc:
        raise ApiError(404, "REVIEW_ITEM_NOT_FOUND", "没有找到该审核条目。") from exc
    return {"item_id": item_id, **queued}


@router.post("/{item_id}/source-trust-proposals", status_code=201)
async def propose_source_trust(
    item_id: str,
    payload: SourceTrustProposalCreate,
    request: Request,
    principal: Annotated[Principal, Depends(require_reviewer_mutation)],
    request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    detail = _detail_or_404(request, principal, item_id)
    source = urlsplit(str(detail.get("normalized_url") or ""))
    if (source.hostname or "").casefold().rstrip(".") != payload.host:
        raise ApiError(422, "SOURCE_PROPOSAL_MISMATCH", "建议 host 必须与当前审核来源完全一致。")
    source_path = source.path or "/"
    prefix = payload.path_prefix
    if prefix != "/" and source_path != prefix and not source_path.startswith(f"{prefix}/"):
        raise ApiError(422, "SOURCE_PROPOSAL_MISMATCH", "建议栏目路径必须覆盖当前审核来源。")
    proposal = payload.model_dump(mode="json")
    created = request.app.state.review_store.create_source_trust_proposal(
        _namespace(principal),
        item_id,
        proposal,
        principal.principal_id,
        _request_id(request_id),
    )
    return created
