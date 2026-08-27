"""Git-reviewed source trust rules and conservative domain classification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from xiaowo_web.evidence.models import TrustDecision, ValidatedUrl
from xiaowo_web.settings import PROJECT_ROOT


_MULTI_LABEL_SUFFIXES = frozenset({"edu.cn", "gov.cn", "com.cn", "net.cn", "org.cn", "ac.cn"})


def registered_domain(host: str) -> str:
    labels = host.casefold().rstrip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in _MULTI_LABEL_SUFFIXES else suffix


@dataclass(frozen=True, slots=True)
class _Rule:
    rule_id: str
    scheme: str
    host: str
    path_prefix: str
    level: str
    institution: str


class SourceTrustStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PROJECT_ROOT / "config" / "source_trust.yaml"
        payload = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        rules = payload.get("rules") or []
        self.version = int(payload.get("version") or 0)
        self._rules = tuple(
            _Rule(
                rule_id=f"rule-{index + 1}",
                scheme=str(item.get("scheme") or "https").casefold(),
                host=str(item.get("host") or "").casefold().rstrip("."),
                path_prefix=self._normalize_prefix(str(item.get("path_prefix") or "/")),
                level=str(item.get("level") or "unverified"),
                institution=str(item.get("institution") or ""),
            )
            for index, item in enumerate(rules)
            if isinstance(item, dict) and item.get("host")
        )

    def classify(self, url: ValidatedUrl) -> TrustDecision:
        for rule in self._rules:
            if rule.scheme != url.scheme or rule.host != url.host:
                continue
            if not self._path_matches(url.path, rule.path_prefix):
                continue
            level = rule.level if rule.level in {"official_primary", "reliable_independent"} else "unverified"
            tags = ("ustc_domain",) if url.ustc_domain else ()
            return TrustDecision(
                level=level,
                institution=rule.institution,
                tags=tags,
                rule_id=rule.rule_id,
            )
        if url.ustc_domain:
            return TrustDecision(
                level="general",
                institution="科大域名来源（未审核栏目）",
                tags=("ustc_domain",),
            )
        return TrustDecision(level="general", institution=registered_domain(url.host))

    @staticmethod
    def _normalize_prefix(value: str) -> str:
        prefix = value if value.startswith("/") else f"/{value}"
        return prefix.rstrip("/") or "/"

    @staticmethod
    def _path_matches(path: str, prefix: str) -> bool:
        if prefix == "/":
            return True
        return path == prefix or path.startswith(f"{prefix}/")

    def classify_url_without_dns(self, value: str) -> TrustDecision:
        """Ranking hint only; final classification must use a ValidatedUrl."""

        parts = urlsplit(value)
        host = (parts.hostname or "").casefold().rstrip(".")
        path = parts.path or "/"
        for rule in self._rules:
            if parts.scheme.casefold() == rule.scheme and host == rule.host and self._path_matches(path, rule.path_prefix):
                return TrustDecision(rule.level, rule.institution, (), rule.rule_id)
        return TrustDecision("general", registered_domain(host) if host else "")
