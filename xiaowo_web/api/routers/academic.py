"""Authenticated, principal-bound academic workspace endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from xiaowo_web.api.dependencies import require_authenticated
from xiaowo_web.auth.models import Principal


router = APIRouter(prefix="/academic", tags=["academic"])


async def _call(request: Request, method: str, principal: Principal) -> dict:
    function = getattr(request.app.state.academic_service, method)
    return await asyncio.to_thread(function, principal)


@router.get("/overview")
async def overview(
    request: Request,
    principal: Annotated[Principal, Depends(require_authenticated)],
) -> dict:
    return await _call(request, "overview", principal)


@router.get("/program")
async def program(
    request: Request,
    principal: Annotated[Principal, Depends(require_authenticated)],
) -> dict:
    return await _call(request, "program", principal)


@router.get("/courses")
async def courses(
    request: Request,
    principal: Annotated[Principal, Depends(require_authenticated)],
) -> dict:
    return await _call(request, "courses", principal)


@router.get("/schedule")
async def schedule(
    request: Request,
    principal: Annotated[Principal, Depends(require_authenticated)],
) -> dict:
    return await _call(request, "schedule", principal)
