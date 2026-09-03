"""Liveness, readiness, and public configuration."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from xiaowo_web.settings import PROJECT_ROOT


router = APIRouter(tags=["system"])


@router.get("/health/live")
async def live(request: Request) -> dict:
    return {"status": "ok", "version": request.app.state.settings.version}


def _approved_index_ok(request: Request) -> bool:
    """活性发布索引：任一 namespace 有 active 指针且 manifest/bm25 文件在盘。"""
    try:
        settings = request.app.state.settings
        for namespace in ("demo", "production"):
            active = request.app.state.review_store.get_active_generation(namespace)
            if active:
                generation_id = str(active.get("generation_id") or "")
                manifest = (
                    Path(settings.web_evidence_dir) / "approved" / "manifests"
                    / namespace / f"{generation_id}.json"
                )
                bm25 = Path(settings.published_bm25_dir) / namespace / f"{generation_id}.json"
                if manifest.is_file() and bm25.is_file():
                    return True
        return False
    except Exception:  # noqa: BLE001
        return False


def _search_quality_ok() -> bool:
    """哨兵最近一次搜索结果（>0 命中）与索引检查；无哨兵/数据过期视为未知（不误报）。"""
    try:
        state_path = os.environ.get("XIAOWO_SENTINEL_STATE_PATH") or str(
            PROJECT_ROOT / "deploy" / "server" / "run" / "sentinel_state.json")
        state_file = Path(state_path)
        if not state_file.exists():
            return True
        state = json.loads(state_file.read_text(encoding="utf-8"))
        age = time.time() - float(state.get("checked_at") or 0)
        if age > 600:
            return True  # 哨兵数据过期：按未知处理，避免冻结告警
        return bool(state.get("search_ok", True))
    except Exception:  # noqa: BLE001
        return True


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    checks = {
        "database": request.app.state.store.healthcheck(),
        "review_database": request.app.state.review_store.healthcheck(),
        # 软状态（2026-09-03 增补）：不影响整体 ready 判定，仅作可观测性透出
        "approved_index": _approved_index_ok(request),
        "search_quality": _search_quality_ok(),
    }
    if settings.web_search_enabled:
        provider = request.app.state.sidecar_health_provider
        checks["web_evidence"] = bool(provider and await provider.ready())
        pipeline = request.app.state.evidence_pipeline
        checks["evidence_extractor"] = bool(
            pipeline is not None and await pipeline.extractor.ready()
        )
    # 核心门槛保持原语义（数据/侧车/证据抽取）；approved_index/search_quality 只观测
    core_checks = {
        key: value for key, value in checks.items()
        if key in {"database", "review_database", "web_evidence", "evidence_extractor"}
    }
    status = "ready" if all(core_checks.values()) else "not_ready"
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
            "demo_reset_enabled": settings.demo_reset_enabled,
        },
        "time_budget_seconds": {
            "search": settings.search_timeout_seconds,
            "evidence": settings.evidence_timeout_seconds,
            "generation": settings.generation_timeout_seconds,
            "total": settings.run_timeout_seconds,
        },
    }
