"""FastAPI application factory for the Xiaowo Web workspace."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware

from xiaowo_web import __version__
from xiaowo_web.academic import AcademicService
from xiaowo_web.api.routers import academic, admin, auth, campus, chat, conversations, feedback, review, system
from xiaowo_web.auth.cas import CasProvider, ExistingCasProvider
from xiaowo_web.auth.service import AuthService
from xiaowo_web.auth.mobile import MOBILE_ORIGINS, MobileAuth, bearer_token
from xiaowo_web.api.routers import mobile_auth
from xiaowo_web.chat import ChatManager, LegacyQaRunner, QaRunner
from xiaowo_web.campus import CampusService, CampusToolStore
from xiaowo_web.campus.demo import ensure_demo_campus_tool_seed
from xiaowo_web.errors import ApiError, api_error_handler
from xiaowo_web.knowledge.semantic_cache import SemanticCache
from xiaowo_web.evidence.clients import (BaiduSearchClient, BochaWebSearchClient, Crawl4AiClient, SearxngClient, SidecarHealthProvider)
from xiaowo_web.evidence.extractor import StructuredClaimExtractor
from xiaowo_web.evidence.pipeline import EvidencePipeline
from xiaowo_web.evidence.rewrite import QueryRewriter
from xiaowo_web.evidence.wechat import WechatClient
from xiaowo_web.evidence.runner import EvidenceAwareRunner
from xiaowo_web.knowledge import ApprovedKnowledgeRetriever
from xiaowo_web.settings import PROJECT_ROOT, WebSettings
from xiaowo_web.storage import WebStore
from xiaowo_web.review import ReviewStore
from xiaowo_web.review.demo import ensure_demo_review_seed
from xiaowo_web.settings import AuthMode
from xiaowo_web.review.sink import ReviewIngestionSink


def create_app(
    settings: WebSettings | None = None,
    *,
    runner: QaRunner | None = None,
    cas_provider: CasProvider | None = None,
    sidecar_health_provider: Any | None = None,
    academic_service: AcademicService | None = None,
    campus_service: CampusService | None = None,
    campus_tool_store: CampusToolStore | None = None,
    review_store: ReviewStore | None = None,
) -> FastAPI:
    resolved_settings = settings or WebSettings.from_env()
    store = WebStore(resolved_settings)
    resolved_review_store = review_store or ReviewStore(resolved_settings)
    # 语义缓存：每 app 独立实例（路径随 settings，测试天然隔离）；发布钩子定向失效
    semantic_cache = SemanticCache(resolved_settings.review_db_path.parent / "semantic_cache.db")
    resolved_review_store.attach_semantic_cache(semantic_cache)
    resolved_campus_tool_store = campus_tool_store or CampusToolStore(resolved_settings)
    auth_service = AuthService(resolved_settings, store)
    mobile_service = MobileAuth(resolved_settings) if resolved_settings.mobile_auth_enabled else None
    resolved_health_provider = sidecar_health_provider
    evidence_pipeline = None
    resolved_runner = runner
    approved_retriever = ApprovedKnowledgeRetriever(
        resolved_review_store,
        resolved_settings,
    )
    if resolved_runner is None:
        # 执行池容量与准入并发对齐（或其合理有界子集）：信号量放行 30 个 run
        # 而池只有 4 线程时，LLM 变慢会让后续 run 在队列里空等到超时——可用性塔缩
        local_runner = LegacyQaRunner(
            approved_retriever=approved_retriever,
            max_workers=min(resolved_settings.max_concurrent_runs, 16),
            semantic_cache=semantic_cache,
            # 会被 EvidenceAwareRunner 包住时，缓存写入交给外层（外层才知道最终答案）
            defer_cache_write=bool(resolved_settings.web_search_enabled),
        )
        if resolved_settings.web_search_enabled:
            if resolved_settings.search_provider == "bocha":
                search_client = BochaWebSearchClient(
                    resolved_settings.bocha_api_key,
                    base_url=resolved_settings.bocha_base_url,
                    timeout=resolved_settings.search_timeout_seconds,
                )
            elif resolved_settings.search_provider == "baidu":
                search_client = BaiduSearchClient(
                    resolved_settings.baidu_api_key,
                    base_url=resolved_settings.baidu_base_url,
                    timeout=resolved_settings.search_timeout_seconds,
                )
            else:
                search_client = SearxngClient(
                    resolved_settings.searxng_url,
                    timeout=resolved_settings.search_timeout_seconds,
                )
            crawl_client = Crawl4AiClient(
                resolved_settings.crawl4ai_url,
                timeout=max(
                    0.1,
                    resolved_settings.evidence_timeout_seconds
                    - resolved_settings.search_timeout_seconds,
                ),
            )
            evidence_pipeline = EvidencePipeline(
                resolved_settings,
                search_client,
                crawl_client,
                extractor=StructuredClaimExtractor(
                    model_name=resolved_settings.evidence_extractor_model,
                    enabled=resolved_settings.evidence_extractor_enabled,
                    probe_timeout_seconds=resolved_settings.evidence_extractor_probe_timeout_seconds,
                ),
                rewriter=QueryRewriter(),
                wechat=WechatClient(),
            )
            shadow_comparer = None
            if resolved_settings.shadow_compare_enabled:
                # Phase 2 影子对比（2026-09-17）：独立 sqlite，默认关
                from xiaowo_web.evidence.shadow import ShadowComparer, ShadowStore

                shadow_store = ShadowStore(
                    resolved_settings.review_db_path.parent / "shadow_compare.db"
                )
                shadow_store.initialize()
                shadow_comparer = ShadowComparer(shadow_store, search_client)
            resolved_runner = EvidenceAwareRunner(
                local_runner, evidence_pipeline, shadow=shadow_comparer,
                semantic_cache=semantic_cache,
            )
            if resolved_health_provider is None:
                resolved_health_provider = SidecarHealthProvider(search_client, crawl_client)
        else:
            resolved_runner = local_runner
    # 审核入库 sink：manager（经典链路候选）与 pipeline（联网引用抓整页）共用
    ingestion_sink = ReviewIngestionSink(resolved_review_store)
    chat_manager = ChatManager(
        resolved_settings,
        store,
        resolved_runner,
        ingestion_sink=ingestion_sink,
        page_fetcher=(evidence_pipeline.fetch_candidates_for_ingestion
                      if evidence_pipeline is not None else None),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.initialize()
        if mobile_service is not None:
            mobile_service.initialize()
        resolved_review_store.initialize()
        resolved_campus_tool_store.initialize()
        if resolved_settings.auth_mode is AuthMode.DEMO:
            ensure_demo_campus_tool_seed(resolved_campus_tool_store)
        if (
            resolved_settings.auth_mode is AuthMode.DEMO
            and "PB25111691" in resolved_settings.admin_ids
        ):
            ensure_demo_review_seed(resolved_review_store)
        store.recover_interrupted_runs()
        async def prune_mobile_credentials():
            while True:
                await asyncio.sleep(60)
                await asyncio.to_thread(mobile_service.prune)
        mobile_cleanup = asyncio.create_task(prune_mobile_credentials()) if mobile_service is not None else None
        try:
            yield
        finally:
            if mobile_cleanup is not None:
                mobile_cleanup.cancel()
                with suppress(asyncio.CancelledError):
                    await mobile_cleanup
            await chat_manager.close()

    app = FastAPI(
        title="小蜗 Web API",
        version=__version__,
        docs_url="/api/docs" if resolved_settings.environment.value == "development" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.store = store
    app.state.review_store = resolved_review_store
    app.state.auth_service = auth_service
    app.state.mobile_auth = mobile_service
    app.state.chat_manager = chat_manager
    app.state.cas_provider = cas_provider or ExistingCasProvider()
    app.state.sidecar_health_provider = resolved_health_provider
    app.state.evidence_pipeline = evidence_pipeline
    app.state.approved_retriever = approved_retriever
    app.state.academic_service = academic_service or AcademicService()
    app.state.campus_service = campus_service or CampusService()
    app.state.campus_tool_store = resolved_campus_tool_store

    app.add_exception_handler(ApiError, api_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            ".".join(str(part) for part in error.get("loc", ())[1:])
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "请求参数不符合接口约束。",
                    "fields": sorted({field for field in fields if field}),
                }
            },
        )

    # 方案 B：按请求解包浏览器 cookie 里的教务会话，供工具层 _cas() 使用（用完即弃）。
    @app.middleware("http")
    async def inject_jw_session(request, call_next):
        token = None
        guard_token = None
        try:
            if request.url.path.startswith("/api/") and request.headers.get("authorization") is not None:
                # Any Authorization header selects the bearer branch, even if invalid.
                # Cookie-based credentials must not repair a malformed/expired token.
                from services.jw_session import client_from_verified_payload, set_client, set_access_guard
                try:
                    raw = bearer_token(request)
                except ApiError:
                    raw = None
                resolved = mobile_service.resolve_with_payload(raw) if mobile_service and raw else None
                request.state.mobile_principal = resolved[0] if resolved else None
                request.state.mobile_token = raw

                def require_live_mobile_session():
                    if not mobile_service or not raw or mobile_service.resolve(raw) is None:
                        raise ApiError(401, "AUTH_REQUIRED", "登录凭据已失效，请重新登录。")

                guard_token = set_access_guard(require_live_mobile_session)
                client = client_from_verified_payload(resolved[1]) if resolved else None
                request.state.jw_identity = resolved[0].profile if client is not None else {}
                request.state.jw_session = client is not None
                token = set_client(client)
            elif request.url.path.startswith("/api/") and request.cookies.get("xiaowo_jw"):
                from services.jw_session import client_from_request, set_client

                client = client_from_request(request)
                if client is not None:
                    token = set_client(client)
                    try:
                        import asyncio as _asyncio
                        from services.jw_session import identity_for_client as _identity, bind_verified_identity

                        request.state.jw_identity = await _asyncio.to_thread(_identity, client)
                        bind_verified_identity(client, request.state.jw_identity)
                    except Exception:  # noqa: BLE001
                        request.state.jw_identity = {}
            return await call_next(request)
        finally:
            if token is not None:
                from services.jw_session import reset_client

                reset_client(token)
            if guard_token is not None:
                from services.jw_session import reset_access_guard

                reset_access_guard(guard_token)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    if mobile_service is not None:
        app.add_middleware(
            CORSMiddleware, allow_origins=list(MOBILE_ORIGINS), allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
            allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "Accept"],
            max_age=600,
        )

    prefix = "/api/v1"
    app.include_router(system.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(mobile_auth.router, prefix=prefix)
    app.include_router(conversations.router, prefix=prefix)
    app.include_router(chat.router, prefix=prefix)
    app.include_router(academic.router, prefix=prefix)
    app.include_router(campus.router, prefix=prefix)
    app.include_router(review.router, prefix=prefix)
    app.include_router(feedback.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)

    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    assets_dir = frontend_dist / "assets"
    if (frontend_dist / "index.html").is_file():
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

        @app.get("/{spa_path:path}", include_in_schema=False)
        async def spa_fallback(spa_path: str):
            if spa_path == "api" or spa_path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content={"error": {"code": "NOT_FOUND", "message": "接口不存在。"}},
                )
            requested = (frontend_dist / spa_path).resolve()
            if requested.is_relative_to(frontend_dist.resolve()) and requested.is_file():
                return FileResponse(requested)
            # index.html 不缓存:保证前端更新后浏览器拉到新 hash 的 JS/CSS
            return FileResponse(
                frontend_dist / "index.html",
                headers={"Cache-Control": "no-cache"},
            )
    return app


app = create_app()
