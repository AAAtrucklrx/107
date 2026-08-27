"""Small authenticated field cipher for server-side personal text."""

from __future__ import annotations

import base64
import hashlib

from Crypto.Cipher import AES


class FieldCipher:
    """Encrypt configured data with AES-GCM; demo-only installs may store plain text."""

    def __init__(self, secret: str) -> None:
        self._key = hashlib.sha256(secret.encode("utf-8")).digest() if secret else None

    @property
    def enabled(self) -> bool:
        return self._key is not None

    def seal(self, value: str) -> str:
        if self._key is None:
            encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
            return f"plain:{encoded}"
        cipher = AES.new(self._key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(value.encode("utf-8"))
        payload = cipher.nonce + tag + ciphertext
        return "gcm:" + base64.urlsafe_b64encode(payload).decode("ascii")

    def open(self, value: str) -> str:
        prefix, separator, encoded = value.partition(":")
        if not separator or prefix not in {"plain", "gcm"}:
            raise ValueError("unsupported encrypted field format")
        payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
        if prefix == "plain":
            return payload.decode("utf-8")
        if self._key is None or len(payload) < 32:
            raise ValueError("encrypted field key is unavailable")
        nonce, tag, ciphertext = payload[:16], payload[16:32], payload[32:]
        cipher = AES.new(self._key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
