"""Deterministic query and feedback privacy scanning."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from xiaowo_web.chat.privacy import is_personal_query


_STUDENT_ID = re.compile(r"\b(?:PB|SA|BA|BE|MG|UG)\d{8}\b", re.IGNORECASE)
_CREDENTIAL = re.compile(
    r"(?:\bST-[A-Za-z0-9._~-]{6,}\b|\b(?:CASTGC|JSESSIONID)\s*=|"
    r"\b(?:authorization|cookie|token|ticket|session)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_PERSONAL_TABLE = re.compile(
    r"(?:成绩|绩点|GPA|课表|考试安排).{0,40}(?:\b\d{2,3}(?:\.\d+)?\b|周[一二三四五六日])",
    re.IGNORECASE | re.DOTALL,
)


class QuerySafetyError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class SanitizedQuery:
    text: str
    digest: str
    length_bucket: str


def contains_sensitive_text(value: str) -> bool:
    return bool(_STUDENT_ID.search(value) or _CREDENTIAL.search(value) or _PERSONAL_TABLE.search(value))


def sanitize_public_query(question: str, profile: dict[str, Any] | None = None) -> SanitizedQuery:
    if is_personal_query(question):
        raise QuerySafetyError("PERSONAL_QUERY", "个人数据问题禁止联网。")
    if _CREDENTIAL.search(question) or _PERSONAL_TABLE.search(question):
        raise QuerySafetyError("WEB_QUERY_UNSAFE", "问题包含不能发送到公网的敏感信息。")

    cleaned = question
    for value in (profile or {}).values():
        text = str(value or "").strip()
        if len(text) >= 2:
            cleaned = cleaned.replace(text, " ")
    cleaned = _STUDENT_ID.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned or len(cleaned) < 2 or contains_sensitive_text(cleaned):
        raise QuerySafetyError("WEB_QUERY_UNSAFE", "问题无法在保留含义的同时完成脱敏。")
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
    length = len(cleaned)
    bucket = "short" if length <= 40 else ("medium" if length <= 120 else "long")
    return SanitizedQuery(text=cleaned, digest=digest, length_bucket=bucket)
