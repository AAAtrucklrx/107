"""Synthetic demo review fixture lifecycle."""

from __future__ import annotations

import json

from xiaowo_web.review.store import ReviewStore
from xiaowo_web.settings import PROJECT_ROOT
from xiaowo_web.worker.ingestion import IngestionWorker


DEMO_REVIEW_FIXTURE = PROJECT_ROOT / "fixtures" / "demo" / "review_seed.json"


def ensure_demo_review_seed(store: ReviewStore) -> str | None:
    existing = store.list_items("demo", limit=1)
    if existing:
        return str(existing[0]["item_id"])
    payload = json.loads(DEMO_REVIEW_FIXTURE.read_text(encoding="utf-8"))
    store.enqueue_candidate("demo", payload)
    result = IngestionWorker(store, worker_id="demo-fixture-loader").run_once()
    if result != "done":
        return None
    items = store.list_items("demo", limit=1)
    return str(items[0]["item_id"]) if items else None
