"""Non-blocking chat-to-review queue boundary."""

from __future__ import annotations

from xiaowo_web.review.store import ReviewStore


class ReviewIngestionSink:
    def __init__(self, store: ReviewStore) -> None:
        self.store = store

    def enqueue(self, namespace: str, candidates: list[dict]) -> list[dict]:
        return [self.store.enqueue_candidate(namespace, candidate) for candidate in candidates]
