"""Render reviewer source-trust proposals as a Git-reviewable YAML diff."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import yaml


def build_source_trust_diff(
    config_path: Path,
    proposals: list[dict[str, Any]],
) -> tuple[str, int]:
    original = config_path.read_text(encoding="utf-8")
    payload = yaml.safe_load(original) or {}
    rules = list(payload.get("rules") or [])
    existing = {
        (
            str(rule.get("scheme") or "https").casefold(),
            str(rule.get("host") or "").casefold(),
            str(rule.get("path_prefix") or "/"),
        )
        for rule in rules
        if isinstance(rule, dict)
    }
    added = 0
    for record in proposals:
        proposal = dict(record.get("proposal") or {})
        identity = (
            "https",
            str(proposal.get("host") or "").casefold(),
            str(proposal.get("path_prefix") or "/"),
        )
        if identity in existing:
            continue
        existing.add(identity)
        rules.append({
            "scheme": "https",
            "host": identity[1],
            "path_prefix": identity[2],
            "level": proposal["level"],
            "institution": proposal["institution"],
            "effective_from": str(proposal["effective_from"]),
            "rationale": proposal["rationale"],
        })
        added += 1
    updated_payload = {**payload, "rules": rules}
    updated = yaml.safe_dump(
        updated_payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    diff = "\n".join(difflib.unified_diff(
        original.splitlines(),
        updated.splitlines(),
        fromfile="a/config/source_trust.yaml",
        tofile="b/config/source_trust.yaml",
        lineterm="",
    ))
    return diff, added
