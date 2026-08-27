"""Authenticated conversation history; anonymous history never reaches this store."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from xiaowo_web.api.dependencies import require_mutation, require_principal
from xiaowo_web.api.schemas import ConversationCreate
from xiaowo_web.api.pagination import decode_cursor, encode_cursor
from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError


router = APIRouter(prefix="/conversations", tags=["conversations"])


def _history_owner(principal: Principal) -> str:
    if principal.history_owner_key is None:
        raise ApiError(401, "AUTH_REQUIRED", "匿名历史只保存在当前浏览器。")
    return principal.history_owner_key


@router.get("")
async def list_conversations(
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    cursor: Annotated[str, Query(max_length=512)] = "",
) -> dict:
    items, next_anchor = request.app.state.store.list_conversations_page(
        _history_owner(principal),
        limit=limit,
        cursor=decode_cursor(cursor),
    )
    return {
        "items": items,
        "next_cursor": encode_cursor(*next_anchor) if next_anchor else None,
    }


@router.post("")
async def create_conversation(
    payload: ConversationCreate,
    request: Request,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    return request.app.state.store.create_conversation(_history_owner(principal), payload.title)


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
) -> dict:
    result = request.app.state.store.get_conversation(conversation_id, _history_owner(principal))
    if result is None:
        raise ApiError(404, "CONVERSATION_NOT_FOUND", "没有找到该会话。")
    return result


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    if not request.app.state.store.delete_conversation(conversation_id, _history_owner(principal)):
        raise ApiError(404, "CONVERSATION_NOT_FOUND", "没有找到该会话。")
    return {"deleted": True}


@router.delete("")
async def delete_all_conversations(
    request: Request,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    count = request.app.state.store.delete_all_conversations(_history_owner(principal))
    return {"deleted": count}
