# -*- coding: utf-8 -*-
"""会话保险箱（Session Vault）：把学校会话票据加密后交给**用户浏览器**保管（2026-10-23）。

方案 B（已定）：服务端**不落地凭据**。
  CookieJar（SOURCEID_TGC + jw SESSION）→ JSON → AES-256-GCM 加密 → base64url
  → 写入 **我们域**的 HttpOnly + Secure + SameSite=Lax cookie
  → 每次请求浏览器自动带回 → 服务端解包即用，用完即弃。

为什么不是"存密码"：TGT 是会话票据，可撤销、有有效期；密码一旦泄露不可挽回。
为什么不存学校域的 cookie：跨域禁止，`document.cookie` 只能写自己域。

依赖：仅 `cryptography`（AESGCM）+ 标准库，无项目其他依赖，便于单测与复用。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = b"v1"
COOKIE_NAME = "xiaowo_jw"
DEFAULT_TTL_SECONDS = 12 * 3600

# 只保留教务/认证必需条目：TGT 与 jw 会话（analytics 的 AG_session 约 1.4KB，用不到）
_ESSENTIAL_NAMES = {"SOURCEID_TGC", "SESSION", "SVRNAME", "rememberToken", "deviceID", "device"}
_KEEP_DOMAINS = ("id.ustc.edu.cn", "jw.ustc.edu.cn")


class SessionVault:
    """加解密「用户浏览器里的学校会话」。未配置密钥时禁用（fail-closed）。"""

    def __init__(self, secret: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> None:
        self._key = hashlib.sha256(secret.encode("utf-8")).digest() if secret else None
        self.ttl = max(60, int(ttl_seconds))

    @property
    def enabled(self) -> bool:
        return self._key is not None

    # ── 序列化 ──────────────────────────────────────────────
    @staticmethod
    def essential(cookies: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """挑出必要 cookie（体积约 600B），丢掉 analytics 等无关大条目。"""
        out = []
        for c in cookies or []:
            name = str(c.get("name") or "")
            domain = str(c.get("domain") or "").lstrip(".")
            if domain not in _KEEP_DOMAINS:
                continue
            if name not in _ESSENTIAL_NAMES:
                continue
            out.append({
                "name": name, "value": str(c.get("value") or ""), "domain": domain,
                "path": str(c.get("path") or "/"),
            })
        return out

    # ── 加密 / 解密 ─────────────────────────────────────────
    def seal(self, cookies: list[dict[str, Any]], *, now: float | None = None) -> str:
        if not self.enabled:
            raise RuntimeError("session vault 未配置密钥")
        payload = {"iat": int(now if now is not None else time.time()),
                   "cookies": self.essential(cookies)}
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, raw, VERSION)
        return base64.urlsafe_b64encode(VERSION + nonce + ciphertext).decode("ascii").rstrip("=")

    def open(self, blob: str, *, now: float | None = None) -> list[dict[str, Any]] | None:
        """解包；篡改、密钥不符、过期、格式错误一律返回 None（fail-closed）。"""
        if not self.enabled or not blob:
            return None
        try:
            data = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
            if len(data) < 2 + 12 + 16 or not data.startswith(VERSION):
                return None
            nonce, ciphertext = data[2:14], data[14:]
            raw = AESGCM(self._key).decrypt(nonce, ciphertext, VERSION)
            payload = json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001 — 任何异常都视为无效会话
            return None
        issued = int(payload.get("iat") or 0)
        current = now if now is not None else time.time()
        if issued + self.ttl < current or issued > current + 300:
            return None
        cookies = payload.get("cookies")
        return cookies if isinstance(cookies, list) else None

    # ── HTTP cookie 属性（写用户浏览器时用）──────────────────
    def cookie_kwargs(self, *, secure: bool | None = None) -> dict[str, Any]:
        """写浏览器 cookie 的属性。`secure` 默认 True；本地 http 调试时传 False。

        生产必须走 HTTPS（Secure=True），否则票据在明文链路上传输。
        """
        return {"httponly": True, "secure": True if secure is None else bool(secure),
                "samesite": "lax", "max_age": self.ttl, "path": "/"}


def apply_cookies(session, cookies: list[dict[str, Any]]) -> int:
    """把解包出来的 cookie 灌进 requests.Session（复用 CASClient 的会话）。"""
    loaded = 0
    for c in cookies or []:
        try:
            session.cookies.set(str(c["name"]), str(c["value"]),
                                domain=str(c.get("domain") or "").lstrip(".") or None,
                                path=str(c.get("path") or "/"))
            loaded += 1
        except Exception:  # noqa: BLE001
            continue
    return loaded


class CookiePayloadError(ValueError):
    """导入会话时的校验错误（错误码给前端/日志用，不带敏感值）。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def validate_payload(cookies: object) -> list[dict[str, Any]]:
    """校验浏览器导出的 CookieJar 形状，并返回必要子集（不触网）。

    规则：必须是列表；至少包含 SOURCEID_TGC；只保留 id./jw. 域的必需项。
    """
    if not isinstance(cookies, list) or not cookies:
        raise CookiePayloadError("INVALID_COOKIES", "cookies 不能为空")
    for c in cookies:
        if not isinstance(c, dict) or not c.get("name"):
            raise CookiePayloadError("INVALID_COOKIES", "cookies 条目缺少 name")
    essential = SessionVault.essential(cookies)
    if not any(str(c["name"]) == "SOURCEID_TGC" for c in essential):
        raise CookiePayloadError("MISSING_TGC", "缺少 SOURCEID_TGC（未真正登录）")
    return essential
