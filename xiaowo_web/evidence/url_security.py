"""Canonical URL validation and DNS-based SSRF blocking."""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

from xiaowo_web.evidence.models import ValidatedUrl


Resolver = Callable[[str, int], Iterable[str]]
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_SENSITIVE_NAME = re.compile(
    r"(?:token|ticket|cookie|authorization|session|student.?id|student.?no|sid|password|passwd|secret)",
    re.IGNORECASE,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:\b(?:PB|SA|BA|BE|MG|UG)\d{8}\b|\bST-[A-Za-z0-9._~-]{6,}\b|Bearer\s+\S+)",
    re.IGNORECASE,
)
_TRACKING_NAMES = frozenset({"gclid", "fbclid", "yclid", "mc_cid", "mc_eid"})


class UrlSafetyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _system_resolver(host: str, port: int) -> Iterable[str]:
    return {item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}


class UrlGuard:
    def __init__(self, resolver: Resolver | None = None) -> None:
        self._resolver = resolver or _system_resolver

    def validate(self, value: str) -> ValidatedUrl:
        if not value or len(value) > 4096 or any(char in value for char in "\r\n\t"):
            raise UrlSafetyError("URL_INVALID", "URL 格式无效。")
        try:
            parts = urlsplit(value)
            port = parts.port or (443 if parts.scheme.casefold() == "https" else 80)
        except ValueError as exc:
            raise UrlSafetyError("URL_INVALID", "URL 端口或主机格式无效。") from exc
        scheme = parts.scheme.casefold()
        if scheme not in {"http", "https"} or not parts.hostname:
            raise UrlSafetyError("URL_SCHEME_BLOCKED", "只允许公开 HTTP/HTTPS URL。")
        if parts.username or parts.password:
            raise UrlSafetyError("URL_CREDENTIAL_BLOCKED", "URL 不得包含用户信息。")

        host = self._normalize_host(parts.hostname)
        self._validate_port(port)
        query = self._sanitize_query(parts.query)
        path = quote(unquote(parts.path or "/"), safe="/%:@-._~!$&'()*+,;=")
        netloc = self._netloc(host, port, scheme)
        normalized = urlunsplit((scheme, netloc, path, query, ""))
        approved_ips = self._resolve_public(host, port)
        return ValidatedUrl(
            normalized_url=normalized,
            scheme=scheme,
            host=host,
            port=port,
            path=path,
            approved_ips=approved_ips,
            ustc_domain=host == "ustc.edu.cn" or host.endswith(".ustc.edu.cn"),
        )

    @staticmethod
    def _normalize_host(raw_host: str) -> str:
        if "%" in raw_host:
            raise UrlSafetyError("URL_HOST_BLOCKED", "IPv6 zone 标识不允许。")
        host = raw_host.rstrip(".").casefold()
        if not host:
            raise UrlSafetyError("URL_HOST_BLOCKED", "URL 主机为空。")
        if host.isdigit() or host.startswith(("0x", "+", "-")):
            raise UrlSafetyError("URL_HOST_BLOCKED", "非标准 IP 表示法不允许。")
        if re.fullmatch(r"[0-9.]+", host):
            pieces = host.split(".")
            if len(pieces) != 4 or any(not piece or (len(piece) > 1 and piece.startswith("0")) for piece in pieces):
                raise UrlSafetyError("URL_HOST_BLOCKED", "非标准 IPv4 表示法不允许。")
        try:
            return str(ipaddress.ip_address(host))
        except ValueError:
            pass
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise UrlSafetyError("URL_HOST_BLOCKED", "主机名无法规范化。") from exc
        if len(ascii_host) > 253 or any(not _HOST_LABEL.fullmatch(label) for label in ascii_host.split(".")):
            raise UrlSafetyError("URL_HOST_BLOCKED", "主机名格式无效。")
        return ascii_host

    @staticmethod
    def _validate_port(port: int) -> None:
        if port < 1 or port > 65535:
            raise UrlSafetyError("URL_PORT_BLOCKED", "URL 端口无效。")

    @staticmethod
    def _sanitize_query(raw_query: str) -> str:
        cleaned: list[tuple[str, str]] = []
        for name, value in parse_qsl(raw_query, keep_blank_values=True, strict_parsing=False):
            lowered = name.casefold()
            if _SENSITIVE_NAME.search(name) or _SENSITIVE_VALUE.search(value):
                raise UrlSafetyError("URL_SENSITIVE_QUERY", "URL 查询参数包含敏感信息。")
            if lowered.startswith("utm_") or lowered in _TRACKING_NAMES:
                continue
            cleaned.append((name, value))
        return urlencode(cleaned, doseq=True)

    def _resolve_public(self, host: str, port: int) -> tuple[str, ...]:
        try:
            literal = ipaddress.ip_address(host)
            addresses = {literal}
        except ValueError:
            try:
                raw_addresses = tuple(self._resolver(host, port))
            except (OSError, ValueError) as exc:
                raise UrlSafetyError("URL_DNS_FAILED", "URL 主机无法安全解析。") from exc
            if not raw_addresses:
                raise UrlSafetyError("URL_DNS_FAILED", "URL 主机没有可用地址。")
            try:
                addresses = {ipaddress.ip_address(value) for value in raw_addresses}
            except ValueError as exc:
                raise UrlSafetyError("URL_DNS_FAILED", "DNS 返回了无效地址。") from exc
        normalized: list[str] = []
        for address in addresses:
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if not address.is_global:
                raise UrlSafetyError("URL_PRIVATE_TARGET", "URL 解析到非公开网络地址。")
            normalized.append(str(address))
        return tuple(sorted(set(normalized)))

    @staticmethod
    def _netloc(host: str, port: int, scheme: str) -> str:
        rendered_host = f"[{host}]" if ":" in host else host
        default = 443 if scheme == "https" else 80
        return rendered_host if port == default else f"{rendered_host}:{port}"
