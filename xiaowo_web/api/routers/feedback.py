"""Owned answer feedback with sensitive-text scanning and bounded retention."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from xiaowo_web.api.dependencies import require_mutation
from xiaowo_web.api.schemas import AnswerFeedbackCreate
from xiaowo_web.auth.models import Principal
from xiaowo_web.errors import ApiError
from xiaowo_web.evidence.privacy import contains_sensitive_text


router = APIRouter(prefix="/answers", tags=["feedback"])


@router.post("/{answer_id}/feedback")
async def create_feedback(
    answer_id: str,
    payload: AnswerFeedbackCreate,
    request: Request,
    principal: Annotated[Principal, Depends(require_mutation)],
) -> dict:
    if not request.app.state.store.answer_belongs_to(
        answer_id,
        payload.run_id,
        principal.session_key,
    ):
        raise ApiError(404, "ANSWER_NOT_FOUND", "没有找到当前会话中的该回答。")
    detail = payload.detail.strip()
    profile_values = {
        str(value).strip()
        for value in principal.profile.values()
        if value is not None and len(str(value).strip()) >= 2
    }
    if detail and (
        contains_sensitive_text(detail)
        or any(value in detail for value in profile_values)
    ):
        raise ApiError(422, "FEEDBACK_SENSITIVE", "反馈说明包含个人或凭证信息，请删除后重试。")
    try:
        feedback_id = request.app.state.store.create_feedback(
            answer_id=answer_id,
            run_id=payload.run_id,
            category=payload.category,
            detail=detail,
            namespace=(
                "demo" if principal.auth_mode == "demo"
                else ("production" if principal.auth_mode == "cas" else "anonymous")
            ),
        )
    except ValueError as exc:
        raise ApiError(
            503,
            "FEEDBACK_DETAIL_DISABLED",
            "当前环境未配置反馈说明加密；可删除说明后仅提交分类。",
        ) from exc
    return {"feedback_id": feedback_id, "status": "open"}
