"""Opaque, validated keyset pagination cursors."""

from __future__ import annotations

import base64
import json
from typing import Any

from xiaowo_web.errors import ApiError


def encode_cursor(timestamp: float, identifier: str) -> str:
    payload = json.dumps(
        {"v": 1, "t": timestamp, "id": identifier},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> tuple[float, str] | None:
    if not value:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        payload: Any = json.loads(base64.urlsafe_b64decode(value + padding))
        timestamp = float(payload["t"])
        identifier = str(payload["id"])
        if payload.get("v") != 1 or not identifier or len(identifier) > 200:
            raise ValueError
    except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(400, "CURSOR_INVALID", "分页游标无效，请从第一页重新加载。") from exc
    return timestamp, identifier
