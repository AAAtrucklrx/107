"""Create, stream, resume, and cancel owned chat runs."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from xiaowo_web.api.dependencies import require_mutation, require_principal
from xiaowo_web.api.schemas import ChatRunCreate
from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError


router = APIRouter(prefix="/chat/runs", tags=["chat"])
_TERMINAL_STATES = frozenset({"completed", "cancelled", "failed"})


@router.post("")
async def create_run(
    payload: ChatRunCreate,
    request: Request,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    settings = request.app.state.settings
    if len(payload.question) > settings.max_question_chars:
        raise ApiError(422, "QUESTION_TOO_LONG", "问题长度超过当前限制。")
    return await request.app.state.chat_manager.create_run(
        principal=principal,
        question=payload.question,
        mode=payload.mode,
        conversation_id=payload.conversation_id,
    )


def _parse_last_event_id(raw_value: str | None) -> int:
    if raw_value in {None, ""}:
        return 0
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ApiError(400, "EVENT_CURSOR_INVALID", "Last-Event-ID 必须是非负整数。") from exc
    if value < 0:
        raise ApiError(400, "EVENT_CURSOR_INVALID", "Last-Event-ID 必须是非负整数。")
    return value


def _encode_sse(event: dict) -> str:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['id']}\nevent: {event['type']}\ndata: {payload}\n\n"


async def _event_stream(request: Request, run_id: str, after_sequence: int) -> AsyncIterator[str]:
    store = request.app.state.store
    cursor = after_sequence
    last_keepalive = time.monotonic()
    while True:
        if await request.is_disconnected():
            return
        events = store.list_events(run_id, cursor)
        for event in events:
            cursor = int(event["id"])
            yield _encode_sse(event)
        run = store.get_run(run_id)
        if run is None or run.status in _TERMINAL_STATES:
            # The terminal event and terminal run state commit atomically. A run
            # can finish between the first event read and this status read, so
            # drain once more before closing the stream.
            for event in store.list_events(run_id, cursor):
                cursor = int(event["id"])
                yield _encode_sse(event)
            return
        now = time.monotonic()
        if now - last_keepalive >= 15:
            yield ": keep-alive\n\n"
            last_keepalive = now
        until_keepalive = max(0.05, 15 - (time.monotonic() - last_keepalive))
        await asyncio.to_thread(
            store.wait_for_event,
            run_id,
            cursor,
            min(request.app.state.settings.event_wait_timeout_seconds, until_keepalive),
        )


@router.get("/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_principal)],
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    store = request.app.state.store
    store.prune_expired()
    run = store.get_run(run_id, principal.session_key)
    if run is None:
        if store.is_expired_run(run_id, principal.session_key):
            raise ApiError(410, "EVENT_CURSOR_EXPIRED", "事件保留期已结束，请重新提问。")
        raise ApiError(404, "RUN_NOT_FOUND", "没有找到该回答任务。")
    after_sequence = _parse_last_event_id(last_event_id)
    return StreamingResponse(
        _event_stream(request, run_id, after_sequence),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{run_id}/cancel")
async def cancel(
    run_id: str,
    request: Request,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    store = request.app.state.store
    run = store.get_run(run_id, principal.session_key)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "没有找到该回答任务。")
    if run.status in _TERMINAL_STATES:
        raise ApiError(409, "RUN_NOT_ACTIVE", "该回答任务已经结束。")
    if not request.app.state.chat_manager.cancel(run_id, principal):
        raise ApiError(409, "RUN_NOT_ACTIVE", "该回答任务已经结束。")
    return {"run_id": run_id, "cancel_requested": True}
