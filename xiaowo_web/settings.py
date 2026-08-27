"""Web 应用的安全运行配置。"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from xiaowo_web import __version__


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


class SettingsError(ValueError):
    """配置组合不满足安全约束。"""


class AppEnvironment(StrEnum):
    DEVELOPMENT = "development"
    COMPETITION = "competition"
    PRODUCTION = "production"


class AuthMode(StrEnum):
    ANONYMOUS = "anonymous"
    DEMO = "demo"
    CAS = "cas"


DEMO_STUDENT_ID = "PB25111691"
DEFAULT_SIDECAR_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "searxng", "crawl4ai"})


def _env_bool(source: dict[str, str], name: str, default: bool = False) -> bool:
    value = source.get(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise SettingsError(f"{name} 必须为 true 或 false")


def _enum_value(enum_type, source: dict[str, str], name: str, default: str):
    raw = source.get(name, default).strip().casefold()
    try:
        return enum_type(raw)
    except ValueError as exc:
        choices = ", ".join(item.value for item in enum_type)
        raise SettingsError(f"{name} 必须是以下值之一: {choices}") from exc


def _split_csv(source: dict[str, str], name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in source.get(name, "").split(",") if value.strip())


def _origin_tuple(value: str, name: str) -> tuple[str, str, int]:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SettingsError(f"{name} 必须是完整的 http/https 来源")
    if parsed.username or parsed.password:
        raise SettingsError(f"{name} 禁止包含用户信息")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise SettingsError(f"{name} 不能包含路径、查询或 fragment")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise SettingsError(f"{name} 端口无效") from exc
    return parsed.scheme.casefold(), parsed.hostname.casefold(), port


def _service_origin(value: str, name: str) -> tuple[str, str, int]:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SettingsError(f"{name} 必须是完整的 http/https URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise SettingsError(f"{name} 包含不允许的 URL 部分")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise SettingsError(f"{name} 端口无效") from exc
    return parsed.scheme.casefold(), parsed.hostname.casefold(), port


@dataclass(frozen=True, slots=True)
class WebSettings:
    environment: AppEnvironment
    auth_mode: AuthMode
    public_origin: str
    app_db_path: Path
    schema_web_path: Path
    review_db_path: Path
    schema_review_path: Path
    web_evidence_dir: Path
    published_chroma_dir: Path
    published_bm25_dir: Path
    admin_ids: frozenset[str]
    data_key: str
    session_secret: str
    trusted_proxy_cidrs: tuple[str, ...]
    sidecar_allowed_hosts: frozenset[str]
    cas_service_url: str
    web_search_enabled: bool
    searxng_url: str
    crawl4ai_url: str
    ingestion_worker_enabled: bool
    cookie_name: str = "xiaowo_session"
    csrf_cookie_name: str = "xiaowo_csrf"
    cas_state_cookie_name: str = "xiaowo_cas_state"
    anonymous_session_seconds: int = 24 * 60 * 60
    session_idle_seconds: int = 12 * 60 * 60
    session_absolute_seconds: int = 7 * 24 * 60 * 60
    run_event_retention_seconds: int = 60 * 60
    search_timeout_seconds: float = 4.0
    evidence_timeout_seconds: float = 12.0
    generation_timeout_seconds: float = 18.0
    run_timeout_seconds: float = 20.0
    max_question_chars: int = 8_000
    max_concurrent_runs: int = 30
    max_queued_runs: int = 30
    local_relevance_min: float = 0.60
    version: str = __version__

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "WebSettings":
        source = dict(os.environ if environ is None else environ)
        environment = _enum_value(
            AppEnvironment, source, "XIAOWO_ENV", AppEnvironment.DEVELOPMENT.value,
        )
        auth_mode = _enum_value(
            AuthMode, source, "XIAOWO_AUTH_MODE", AuthMode.ANONYMOUS.value,
        )
        public_origin = source.get("XIAOWO_PUBLIC_ORIGIN", "http://localhost:8000").strip().rstrip("/")
        app_db_path = Path(
            source.get("XIAOWO_APP_DB_PATH", str(PROJECT_ROOT / "database" / "xiaowo.db")),
        ).resolve()
        raw_admin_ids = source.get("XIAOWO_ADMIN_IDS", "")
        admin_ids = frozenset(
            value.strip().upper() for value in raw_admin_ids.split(",") if value.strip()
        )
        try:
            settings = cls(
                environment=environment,
                auth_mode=auth_mode,
                public_origin=public_origin,
                app_db_path=app_db_path,
                schema_web_path=PROJECT_ROOT / "database" / "schema_web.sql",
                review_db_path=Path(
                    source.get("XIAOWO_REVIEW_DB_PATH", str(PROJECT_ROOT / "data" / "review.db")),
                ).resolve(),
                schema_review_path=PROJECT_ROOT / "database" / "schema_review.sql",
                web_evidence_dir=Path(
                    source.get("XIAOWO_WEB_EVIDENCE_DIR", str(PROJECT_ROOT / "data" / "web_evidence")),
                ).resolve(),
                published_chroma_dir=Path(
                    source.get(
                        "XIAOWO_PUBLISHED_CHROMA_DIR",
                        str(PROJECT_ROOT / "knowledge" / "chroma_db" / "web_approved"),
                    ),
                ).resolve(),
                published_bm25_dir=Path(
                    source.get(
                        "XIAOWO_PUBLISHED_BM25_DIR",
                        str(PROJECT_ROOT / "data" / "web_evidence" / "approved" / "bm25"),
                    ),
                ).resolve(),
                admin_ids=admin_ids,
                data_key=source.get("XIAOWO_DATA_KEY", "").strip(),
                session_secret=source.get("XIAOWO_SESSION_SECRET", "").strip(),
                trusted_proxy_cidrs=_split_csv(source, "XIAOWO_TRUSTED_PROXY_CIDRS"),
                sidecar_allowed_hosts=frozenset(
                    value.casefold() for value in (
                        _split_csv(source, "XIAOWO_SIDECAR_ALLOWED_HOSTS")
                        or tuple(DEFAULT_SIDECAR_HOSTS)
                    )
                ),
                cas_service_url=source.get("CAS_SERVICE_URL", "").strip(),
                web_search_enabled=_env_bool(source, "XIAOWO_WEB_SEARCH_ENABLED"),
                searxng_url=source.get("XIAOWO_SEARXNG_URL", "http://127.0.0.1:8080").rstrip("/"),
                crawl4ai_url=source.get("XIAOWO_CRAWL4AI_URL", "http://127.0.0.1:11235").rstrip("/"),
                ingestion_worker_enabled=_env_bool(source, "XIAOWO_INGESTION_WORKER_ENABLED"),
                max_question_chars=int(source.get("XIAOWO_MAX_QUESTION_CHARS", "8000")),
                max_concurrent_runs=int(source.get("XIAOWO_MAX_CONCURRENT_RUNS", "30")),
                max_queued_runs=int(source.get("XIAOWO_MAX_QUEUED_RUNS", "30")),
                local_relevance_min=float(source.get("XIAOWO_LOCAL_RELEVANCE_MIN", "0.60")),
            )
        except ValueError as exc:
            if isinstance(exc, SettingsError):
                raise
            raise SettingsError("Web 数值配置格式无效") from exc
        settings.validate()
        return settings

    @property
    def cookie_secure(self) -> bool:
        return urlparse(self.public_origin).scheme.casefold() == "https"

    def validate(self) -> None:
        public_origin = _origin_tuple(self.public_origin, "XIAOWO_PUBLIC_ORIGIN")
        if self.environment is AppEnvironment.PRODUCTION and self.auth_mode is AuthMode.DEMO:
            raise SettingsError("production 环境禁止 demo 认证")
        if self.auth_mode is AuthMode.ANONYMOUS and self.admin_ids:
            raise SettingsError("anonymous 模式禁止配置管理员 ID")
        if self.auth_mode is AuthMode.DEMO and self.admin_ids - {DEMO_STUDENT_ID}:
            raise SettingsError("demo 模式只能把 PB25111691 配置为演示管理员")
        if self.auth_mode is AuthMode.CAS:
            if not self.cookie_secure:
                raise SettingsError("CAS 模式必须使用 HTTPS public origin")
            cas_url = urlparse(self.cas_service_url)
            if cas_url.scheme != "https" or not cas_url.hostname:
                raise SettingsError("CAS 模式必须配置 HTTPS CAS_SERVICE_URL")
            if _service_origin(self.cas_service_url, "CAS_SERVICE_URL") != public_origin:
                raise SettingsError("CAS_SERVICE_URL 必须属于 XIAOWO_PUBLIC_ORIGIN")
            if not self.data_key:
                raise SettingsError("CAS 模式必须配置 XIAOWO_DATA_KEY")
            if len(self.session_secret.encode("utf-8")) < 32:
                raise SettingsError("CAS 模式必须配置至少 32 字节的 XIAOWO_SESSION_SECRET")
        for value in self.trusted_proxy_cidrs:
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise SettingsError(f"XIAOWO_TRUSTED_PROXY_CIDRS 包含无效网段: {value}") from exc
        if not 0 < self.local_relevance_min <= 1:
            raise SettingsError("XIAOWO_LOCAL_RELEVANCE_MIN 必须在 (0, 1] 范围内")
        if self.max_question_chars < 1 or self.max_question_chars > 50_000:
            raise SettingsError("XIAOWO_MAX_QUESTION_CHARS 必须在 1 到 50000 之间")
        if self.max_concurrent_runs < 1 or self.max_concurrent_runs > 500:
            raise SettingsError("XIAOWO_MAX_CONCURRENT_RUNS 必须在 1 到 500 之间")
        if self.max_queued_runs < 0 or self.max_queued_runs > 1000:
            raise SettingsError("XIAOWO_MAX_QUEUED_RUNS 必须在 0 到 1000 之间")
        if self.web_search_enabled:
            self._validate_sidecar_url(self.searxng_url, "XIAOWO_SEARXNG_URL")
            self._validate_sidecar_url(self.crawl4ai_url, "XIAOWO_CRAWL4AI_URL")
        elif self.ingestion_worker_enabled:
            self._validate_sidecar_url(self.crawl4ai_url, "XIAOWO_CRAWL4AI_URL")

    def _validate_sidecar_url(self, value: str, name: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise SettingsError(f"{name} 必须是完整的 http/https URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SettingsError(f"{name} 包含不允许的 URL 部分")
        if parsed.path not in {"", "/"}:
            raise SettingsError(f"{name} 不能包含路径")
        host = parsed.hostname.casefold()
        if host not in self.sidecar_allowed_hosts:
            raise SettingsError(f"{name} 主机不在 XIAOWO_SIDECAR_ALLOWED_HOSTS 中")
