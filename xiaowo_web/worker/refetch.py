"""Lease-based public refetch worker that only queues changed snapshots."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
from typing import Protocol

from xiaowo_web.evidence.clients import SidecarContractError
from xiaowo_web.evidence.models import CrawledPage
from xiaowo_web.evidence.trust import SourceTrustStore
from xiaowo_web.evidence.url_security import UrlGuard, UrlSafetyError
from xiaowo_web.review import RefetchJob, ReviewStore


class RefetchCrawler(Protocol):
    async def health(self) -> bool: ...
    async def crawl(self, url: str) -> CrawledPage: ...


class RefetchWorker:
    def __init__(
        self,
        store: ReviewStore,
        crawler: RefetchCrawler,
        *,
        url_guard: UrlGuard | None = None,
        trust_store: SourceTrustStore | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.store = store
        self.crawler = crawler
        self.url_guard = url_guard or UrlGuard()
        self.trust_store = trust_store or SourceTrustStore()
        self.worker_id = worker_id or f"refetcher-{secrets.token_urlsafe(8)}"

    async def run_once(self, *, now: float | None = None) -> str | None:
        job = self.store.claim_refetch_job(self.worker_id, now=now)
        if job is None:
            return None
        try:
            if not await self.crawler.health():
                return self.store.fail_refetch_job(job, "CRAWL_BLOCKED", now=now)
            requested = await asyncio.to_thread(self.url_guard.validate, job.source_url)
            page = await self.crawler.crawl(requested.normalized_url)
            final = await asyncio.to_thread(self.url_guard.validate, page.final_url)
            if not page.robots_allowed or not page.peer_ip_verified:
                raise SidecarContractError("refetch response lacks safety proof")
            content_hash = hashlib.sha256(page.markdown.encode("utf-8")).hexdigest()
            if content_hash == job.original_snapshot_hash:
                self.store.complete_refetch_job(job, "unchanged", now=now)
                return "unchanged"
            trust = self.trust_store.classify(final)
            self.store.enqueue_candidate(job.namespace, {
                "source_id": "refetch-" + hashlib.sha256(
                    final.normalized_url.encode("utf-8")
                ).hexdigest()[:16],
                "normalized_url": final.normalized_url,
                "final_url": final.normalized_url,
                "title": page.title or job.title,
                "institution": trust.institution,
                "level": trust.level,
                "fetched_at": page.fetched_at,
                "published_at": page.published_at,
                "content_type": page.content_type,
                "snapshot_text": page.markdown,
                "evidence_span_hash": f"refetch:{content_hash}",
            }, now=now)
            self.store.complete_refetch_job(job, "ingestion_queued", now=now)
            return "ingestion_queued"
        except asyncio.CancelledError:
            raise
        except UrlSafetyError:
            return self.store.fail_refetch_job(
                job,
                "REFETCH_URL_BLOCKED",
                now=now,
                permanent=True,
            )
        except SidecarContractError:
            return self.store.fail_refetch_job(job, "REFETCH_CONTRACT", now=now)
        except TimeoutError:
            return self.store.fail_refetch_job(job, "REFETCH_TIMEOUT", now=now)
        except Exception:
            return self.store.fail_refetch_job(job, "REFETCH_FAILED", now=now)
