"""Curated official links and public activity data with explicit provenance."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import yaml

from xiaowo_web.settings import PROJECT_ROOT


class CampusService:
    def __init__(self, activity_provider: Callable[..., dict[str, Any]] | None = None) -> None:
        self._activity_provider = activity_provider
        self._links: list[dict[str, Any]] | None = None

    def services(self, query: str = "", category: str = "") -> dict[str, Any]:
        links = self._load_links()
        needle = query.strip().casefold()
        category = category.strip()
        items = []
        for item in links:
            if category and item.get("category") != category:
                continue
            haystack = " ".join(
                [
                    str(item.get("name") or ""),
                    str(item.get("description") or ""),
                    " ".join(str(value) for value in item.get("scene") or []),
                ]
            ).casefold()
            if needle and needle not in haystack:
                continue
            items.append({
                "name": item.get("name") or "",
                "url": item.get("url") or "",
                "description": item.get("description") or "",
                "category": item.get("category") or "",
            })
        categories = sorted({str(item.get("category") or "") for item in links if item.get("category")})
        return {
            "items": items,
            "categories": categories,
            "source": {"kind": "curated_config", "label": "仓库审核官方入口", "stale": False},
        }

    def activities(self, query: str = "", category: str = "", limit: int = 12) -> dict[str, Any]:
        if self._activity_provider is not None:
            result = self._activity_provider(
                keyword=query,
                category=category,
                time_window="",
                limit=limit,
                student_id="",
            )
        else:
            from tools.activity_tools import query_activities

            result = query_activities.invoke({
                "keyword": query,
                "category": category,
                "time_window": "",
                "limit": limit,
                "student_id": "",
            })
        if result.get("error"):
            return {
                "items": [],
                "source": {"kind": "unavailable", "label": "青春科大暂不可用", "stale": False},
                "limitations": [str(result["error"])],
            }
        source_text = str(result.get("source") or "")
        stale = "缓存" in source_text or "快照" in source_text
        return {
            "items": result.get("activities") or [],
            "fetched_at": result.get("fetched_at"),
            "source": {
                "kind": "young_snapshot" if stale else "young_live",
                "label": source_text or "青春科大公开活动",
                "stale": stale,
            },
            "limitations": (["活动来自历史快照，请在报名前前往官方页面核验。"] if stale else []),
        }

    def _load_links(self) -> list[dict[str, Any]]:
        if self._links is None:
            path = PROJECT_ROOT / "config" / "links.yaml"
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            links = payload.get("links") or []
            self._links = [dict(item) for item in links if isinstance(item, dict)]
        return self._links

