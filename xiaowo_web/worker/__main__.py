"""Run the independent SQLite ingestion worker."""

from __future__ import annotations

import asyncio

from xiaowo_web.evidence.clients import Crawl4AiClient
from xiaowo_web.knowledge.approved import ApprovedKnowledgeRetriever
from xiaowo_web.review import ReviewStore
from xiaowo_web.settings import WebSettings
from xiaowo_web.worker import IngestionWorker, PublicationWorker, RefetchWorker
from xiaowo_web.worker.ingestion import LlmCleaner
from xiaowo_web.worker.pre_review import PreReviewer


def _build_pre_reviewer(settings: WebSettings):
    """进料预审（默认开）：判定不出来一律 unknown，资料照常进人工队列。"""
    if not settings.review_pre_review or not settings.evidence_extractor_model.strip():
        return None
    return PreReviewer(settings.evidence_extractor_model)


def _build_cleaner(settings: WebSettings):
    """按配置选择 ingest 清洗器：LLM 语义清洗（失败自动回退确定性）或纯确定性。"""
    if settings.ingest_llm_clean and settings.evidence_extractor_model.strip():
        return LlmCleaner(settings.evidence_extractor_model)
    return None


async def _run() -> None:
    settings = WebSettings.from_env()
    if not settings.ingestion_worker_enabled:
        raise SystemExit("XIAOWO_INGESTION_WORKER_ENABLED=false; worker 未启动")
    store = ReviewStore(settings)
    store.initialize()
    ingestion = IngestionWorker(
        store,
        cleaner=_build_cleaner(settings),
        pre_reviewer=_build_pre_reviewer(settings),
        auto_approve=settings.review_auto_approve,
        retriever=ApprovedKnowledgeRetriever(store, settings),
    )
    publisher = PublicationWorker(store, settings)
    crawler = Crawl4AiClient(
        settings.crawl4ai_url,
        timeout=max(0.1, settings.evidence_timeout_seconds - settings.search_timeout_seconds),
    )
    refetch = RefetchWorker(store, crawler)
    try:
        while True:
            await asyncio.to_thread(store.cleanup_retained_state)
            result = await asyncio.to_thread(ingestion.run_once)
            if result is None:
                result = await refetch.run_once()
            if result is None:
                result = await asyncio.to_thread(publisher.run_once)
            if result is None:
                await asyncio.sleep(1.0)
    finally:
        await crawler.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
