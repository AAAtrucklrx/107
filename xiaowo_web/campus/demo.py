"""Synthetic campus-tool fixture lifecycle for the isolated demo namespace."""

from __future__ import annotations

import json
import time

from xiaowo_web.campus.tool_store import CampusToolStore
from xiaowo_web.settings import PROJECT_ROOT


DEMO_CAMPUS_TOOL_FIXTURE = PROJECT_ROOT / "fixtures" / "demo" / "campus_tool_seed.json"


def ensure_demo_campus_tool_seed(store: CampusToolStore) -> None:
    if store.has_applications("demo"):
        return
    payload = json.loads(DEMO_CAMPUS_TOOL_FIXTURE.read_text(encoding="utf-8"))
    if not payload.get("synthetic"):
        raise ValueError("campus tool demo fixture must be synthetic")
    base_time = time.time() - 3 * 60 * 60
    for index, item in enumerate(payload.get("items") or []):
        created = store.submit_demo_seed(
            name=str(item.get("name") or ""),
            description=str(item.get("description") or ""),
            category=str(item.get("category") or ""),
            url=str(item.get("url") or ""),
            request_id=f"demo-campus-tool-seed-{index}",
            now=base_time + index * 60,
        )
        status = str(item.get("status") or "pending")
        if status == "approved":
            store.approve_application(
                "demo",
                str(created["application_id"]),
                expected_version=int(created["version"]),
                actor_key="demo-seed-admin",
                request_id=f"demo-campus-tool-approve-{index}",
                now=base_time + index * 60 + 10,
            )
        elif status == "rejected":
            store.reject_application(
                "demo",
                str(created["application_id"]),
                expected_version=int(created["version"]),
                reason=str(item.get("reason") or "演示记录：申请未通过。"),
                actor_key="demo-seed-admin",
                request_id=f"demo-campus-tool-reject-{index}",
                now=base_time + index * 60 + 10,
            )


__all__ = ["ensure_demo_campus_tool_seed"]
