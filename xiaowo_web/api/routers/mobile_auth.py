"""Mobile authentication endpoints; no password handling or browser cookies."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

from xiaowo_web.auth.mobile import require_json, require_mobile_origin
from xiaowo_web.errors import ApiError

router = APIRouter(prefix="/auth/mobile", tags=["auth"])


class AuthorizationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookies: list[dict] = Field(min_length=1, max_length=20)
    code_challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    code_challenge_method: Literal["S256"]


class ExchangePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=43, max_length=43)
    code_verifier: str = Field(min_length=43, max_length=128)


async def _body(request: Request) -> dict:
    if not request.app.state.settings.mobile_auth_enabled:
        raise ApiError(404, "MOBILE_AUTH_DISABLED", "移动认证未启用。")
    require_mobile_origin(request)
    require_json(request)
    request.app.state.mobile_auth.limit(request.client.host if request.client else "unknown")
    size = 0
    chunks = []
    async for chunk in request.stream():
        size += len(chunk)
        if size > 32768:
            raise ApiError(413, "PAYLOAD_TOO_LARGE", "登录会话数据过大。")
        chunks.append(chunk)
    try:
        result = json.loads(b"".join(chunks))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (ValueError, UnicodeError):
        raise ApiError(400, "INVALID_JSON", "请求体不是合法 JSON。")


@router.post("/bootstrap")
async def bootstrap(request: Request) -> dict:
    if await _body(request):
        raise ApiError(422, "VALIDATION_ERROR", "匿名启动不接受额外参数。")
    return await asyncio.to_thread(request.app.state.mobile_auth.bootstrap)


@router.post("/authorize")
async def authorize(request: Request) -> dict:
    body = await _body(request)
    try:
        payload = AuthorizationPayload.model_validate(body)
    except ValidationError:
        raise ApiError(422, "VALIDATION_ERROR", "登录参数不符合接口约束。")
    return await asyncio.to_thread(request.app.state.mobile_auth.authorize, payload.cookies, payload.code_challenge)


@router.post("/exchange")
async def exchange(request: Request) -> dict:
    body = await _body(request)
    try:
        payload = ExchangePayload.model_validate(body)
    except ValidationError:
        raise ApiError(400, "MOBILE_CODE_INVALID", "登录授权已失效，请重新登录。")
    return await asyncio.to_thread(request.app.state.mobile_auth.exchange, payload.code, payload.code_verifier)
