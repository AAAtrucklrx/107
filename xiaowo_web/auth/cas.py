"""CAS provider boundary; real network access is enabled only in CAS mode."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class CasIdentity:
    student_id: str
    profile: dict[str, Any]


class CasProvider(Protocol):
    def login_url(self, service_url: str) -> str: ...

    def authenticate(self, ticket: str, service_url: str) -> CasIdentity | None: ...


class ExistingCasProvider:
    """Adapt the existing isolated CAS clients without exposing their cookies."""

    def login_url(self, service_url: str) -> str:
        from services.cas_client import CASClient

        return CASClient().get_login_url(service_url)

    def authenticate(self, ticket: str, service_url: str) -> CasIdentity | None:
        from services.service_container import ServiceContainer

        client = ServiceContainer().authenticate_ticket(ticket, service_url)
        if client is None or not client.student_id:
            return None
        profile = client.get_student_info() or {"id": client.student_id}
        returned_id = str(profile.get("id") or "").strip().upper()
        student_id = client.student_id.strip().upper()
        if returned_id and returned_id != student_id:
            return None
        profile["id"] = student_id
        profile["logged_in"] = True
        profile.setdefault("profile_source", "cas_authenticated")
        return CasIdentity(student_id=student_id, profile=profile)
