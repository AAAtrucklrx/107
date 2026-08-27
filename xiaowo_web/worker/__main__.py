"""Run the independent SQLite ingestion worker."""

from __future__ import annotations

import asyncio

from xiaowo_web.evidence.clients import Crawl4AiClient
from xiaowo_web.review import ReviewStore
from xiaowo_web.settings import WebSettings
from xiaowo_web.worker import IngestionWorker, PublicationWorker, RefetchWorker


async def _run() -> None:
    settings = WebSettings.from_env()
    if not settings.ingestion_worker_enabled:
        raise SystemExit("XIAOWO_INGESTION_WORKER_ENABLED=false; worker 未启动")
    store = ReviewStore(settings)
    store.initialize()
    ingestion = IngestionWorker(store)
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
