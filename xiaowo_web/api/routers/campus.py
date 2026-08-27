"""Public, source-labelled campus service endpoints."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Request


router = APIRouter(prefix="/campus", tags=["campus"])


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
