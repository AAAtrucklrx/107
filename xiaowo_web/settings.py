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
    # ingest 清洗器：true（默认）= LLM 语义清洗（失败自动回退确定性）；false = 仅确定性归一化
    ingest_llm_clean: bool = True
    # Web evidence extraction is capability-gated: a configured model must
    # pass the runtime probe before the web gate opens.
    evidence_extractor_enabled: bool = True
    evidence_extractor_model: str = ""
    evidence_extractor_probe_timeout_seconds: float = 4.0
    # 联网检索增强：查询改写（长问句 → 1-2 个关键词查询）与有限加轮（1..3）
    web_query_rewrite: bool = True
    web_search_max_rounds: int = 2
    # 微信公众号通道（科大相关问题优先，受熔断保护；由 XIAOWO_WECHAT_ENABLED 控制）
    wechat_enabled: bool = True
    # 联网搜索源："searxng"（自托管 sidecar）或 "bocha"（博查 Web Search API，国内直连免 sidecar）
    search_provider: str = "searxng"
    bocha_api_key: str = ""
    bocha_base_url: str = "https://api.bochaai.com"
    # 演示重置保护（2026-09-03 事故加固）：默认关闭端点；开启需额外密钥头
    demo_reset_enabled: bool = False
    demo_reset_key: str = ""
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
    event_wait_timeout_seconds: float = 1.0
    job_done_retention_seconds: int = 7 * 24 * 60 * 60
    job_dead_retention_seconds: int = 90 * 24 * 60 * 60
    orphan_generation_retention_seconds: int = 7 * 24 * 60 * 60
    review_cleanup_interval_seconds: int = 5 * 60
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
                        # 中文路径下 chromadb Rust 端无法落盘 hnsw,发布索引也放英文物理路径
                        r"C:\xiaowo_kb\web_approved",
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
                search_provider=source.get("XIAOWO_SEARCH_PROVIDER", "searxng").strip().casefold(),
                bocha_api_key=source.get("XIAOWO_BOCHA_API_KEY", "").strip(),
                bocha_base_url=source.get("XIAOWO_BOCHA_BASE_URL", "https://api.bochaai.com").rstrip("/"),
                searxng_url=source.get("XIAOWO_SEARXNG_URL", "http://127.0.0.1:8080").rstrip("/"),
                crawl4ai_url=source.get("XIAOWO_CRAWL4AI_URL", "http://127.0.0.1:11235").rstrip("/"),
                ingestion_worker_enabled=_env_bool(source, "XIAOWO_INGESTION_WORKER_ENABLED"),
                ingest_llm_clean=_env_bool(source, "XIAOWO_INGEST_LLM_CLEAN", default=True),
                evidence_extractor_enabled=_env_bool(
                    source, "XIAOWO_EVIDENCE_EXTRACTOR_ENABLED", default=True,
                ),
                evidence_extractor_model=source.get(
                    "XIAOWO_EVIDENCE_EXTRACTOR_MODEL",
                    source.get("LLM_MODEL", "deepseek-v4-flash"),
                ).strip(),
                evidence_extractor_probe_timeout_seconds=float(source.get(
                    "XIAOWO_EVIDENCE_EXTRACTOR_PROBE_TIMEOUT_SECONDS", "4",
                )),
                web_query_rewrite=_env_bool(source, "XIAOWO_WEB_QUERY_REWRITE", default=True),
                web_search_max_rounds=max(1, min(3, int(source.get("XIAOWO_WEB_SEARCH_ROUNDS", "2")))),
                wechat_enabled=_env_bool(source, "XIAOWO_WECHAT_ENABLED", default=True),
                demo_reset_enabled=_env_bool(source, "XIAOWO_DEMO_RESET_ENABLED", default=False),
                demo_reset_key=source.get("XIAOWO_DEMO_RESET_KEY", "").strip(),
                max_question_chars=int(source.get("XIAOWO_MAX_QUESTION_CHARS", "8000")),
                max_concurrent_runs=int(source.get("XIAOWO_MAX_CONCURRENT_RUNS", "30")),
                max_queued_runs=int(source.get("XIAOWO_MAX_QUEUED_RUNS", "30")),
                local_relevance_min=float(source.get("XIAOWO_LOCAL_RELEVANCE_MIN", "0.60")),
                search_timeout_seconds=float(source.get("XIAOWO_SEARCH_TIMEOUT_SECONDS", "4.0")),
                evidence_timeout_seconds=float(source.get("XIAOWO_EVIDENCE_TIMEOUT_SECONDS", "12.0")),
                generation_timeout_seconds=float(source.get("XIAOWO_GENERATION_TIMEOUT_SECONDS", "18.0")),
                run_timeout_seconds=float(source.get("XIAOWO_RUN_TIMEOUT_SECONDS", "20.0")),
                run_event_retention_seconds=int(source.get(
                    "XIAOWO_RUN_EVENT_RETENTION_SECONDS", str(60 * 60),
                )),
                event_wait_timeout_seconds=float(source.get(
                    "XIAOWO_EVENT_WAIT_TIMEOUT_SECONDS", "1",
                )),
                job_done_retention_seconds=int(source.get(
                    "XIAOWO_JOB_DONE_RETENTION_SECONDS", str(7 * 24 * 60 * 60),
                )),
                job_dead_retention_seconds=int(source.get(
                    "XIAOWO_JOB_DEAD_RETENTION_SECONDS", str(90 * 24 * 60 * 60),
                )),
                orphan_generation_retention_seconds=int(source.get(
                    "XIAOWO_ORPHAN_GENERATION_RETENTION_SECONDS", str(7 * 24 * 60 * 60),
                )),
                review_cleanup_interval_seconds=int(source.get(
                    "XIAOWO_REVIEW_CLEANUP_INTERVAL_SECONDS", str(5 * 60),
                )),
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
        if self.demo_reset_enabled and len(self.demo_reset_key) < 16:
            raise SettingsError("启用演示重置必须配置至少 16 字符的 XIAOWO_DEMO_RESET_KEY")
        if self.max_question_chars < 1 or self.max_question_chars > 50_000:
            raise SettingsError("XIAOWO_MAX_QUESTION_CHARS 必须在 1 到 50000 之间")
        if self.max_concurrent_runs < 1 or self.max_concurrent_runs > 500:
            raise SettingsError("XIAOWO_MAX_CONCURRENT_RUNS 必须在 1 到 500 之间")
        if self.max_queued_runs < 0 or self.max_queued_runs > 1000:
            raise SettingsError("XIAOWO_MAX_QUEUED_RUNS 必须在 0 到 1000 之间")
        if self.evidence_extractor_probe_timeout_seconds <= 0 or self.evidence_extractor_probe_timeout_seconds > 30:
            raise SettingsError("XIAOWO_EVIDENCE_EXTRACTOR_PROBE_TIMEOUT_SECONDS 必须在 (0, 30] 范围内")
        if self.run_event_retention_seconds < 5 * 60 or self.run_event_retention_seconds > 7 * 24 * 60 * 60:
            raise SettingsError("XIAOWO_RUN_EVENT_RETENTION_SECONDS 必须在 300 到 604800 秒之间")
        if self.event_wait_timeout_seconds <= 0 or self.event_wait_timeout_seconds > 10:
            raise SettingsError("XIAOWO_EVENT_WAIT_TIMEOUT_SECONDS 必须在 (0, 10] 范围内")
        if self.job_done_retention_seconds < 24 * 60 * 60:
            raise SettingsError("XIAOWO_JOB_DONE_RETENTION_SECONDS 不能少于 1 天")
        if self.job_dead_retention_seconds < self.job_done_retention_seconds:
            raise SettingsError("dead 任务保留期不能短于 done 任务保留期")
        if self.orphan_generation_retention_seconds < 24 * 60 * 60:
            raise SettingsError("孤儿 generation 保留期不能少于 1 天")
        if self.review_cleanup_interval_seconds < 30:
            raise SettingsError("XIAOWO_REVIEW_CLEANUP_INTERVAL_SECONDS 不能少于 30 秒")
        if self.web_search_enabled:
            if self.search_provider == "bocha":
                if not self.bocha_api_key:
                    raise SettingsError(
                        "XIAOWO_SEARCH_PROVIDER=bocha 时必须配置 XIAOWO_BOCHA_API_KEY"
                    )
            else:
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
