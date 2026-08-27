"""认证领域模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Principal:
    principal_id: str
    auth_mode: str
    profile: dict[str, Any]
    is_admin: bool
    session_key: str
    csrf_token: str = ""

    @property
    def is_authenticated(self) -> bool:
        return self.auth_mode in {"demo", "cas"}

    @property
    def history_owner_key(self) -> str | None:
        if self.auth_mode == "cas":
            return f"cas:{self.principal_id}"
        if self.auth_mode == "demo":
            return f"demo:{self.session_key}"
        return None

    @property
    def review_namespace(self) -> str | None:
        if not self.is_admin:
            return None
        return "production" if self.auth_mode == "cas" else "demo"
