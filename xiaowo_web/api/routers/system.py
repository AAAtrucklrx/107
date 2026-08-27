"""Liveness, readiness, and public configuration."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


router = APIRouter(tags=["system"])


@router.get("/health/live")
async def live(request: Request) -> dict:
    return {"status": "ok", "version": request.app.state.settings.version}


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    checks = {
        "database": request.app.state.store.healthcheck(),
        "review_database": request.app.state.review_store.healthcheck(),
    }
    if settings.web_search_enabled:
        provider = request.app.state.sidecar_health_provider
        checks["web_evidence"] = bool(provider and await provider.ready())
        pipeline = request.app.state.evidence_pipeline
        checks["evidence_extractor"] = bool(
            pipeline is not None and await pipeline.extractor.ready()
        )
    status = "ready" if all(checks.values()) else "not_ready"
    return JSONResponse(
        status_code=200 if status == "ready" else 503,
        content={"status": status, "checks": checks},
    )


@router.get("/config/public")
async def public_config(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "environment": settings.environment.value,
        "auth_mode": settings.auth_mode.value,
        "version": settings.version,
        "features": {
            "chat": True,
            "web_search": settings.web_search_enabled,
            "personal_workspace": settings.auth_mode.value in {"demo", "cas"},
            "review_workspace": settings.auth_mode.value in {"demo", "cas"},
            "ingestion_worker": settings.ingestion_worker_enabled,
        },
        "time_budget_seconds": {
            "search": settings.search_timeout_seconds,
            "evidence": settings.evidence_timeout_seconds,
            "generation": settings.generation_timeout_seconds,
            "total": settings.run_timeout_seconds,
        },
    }
