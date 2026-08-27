"""anonymous、demo 与 CAS 的统一认证服务。"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from xiaowo_web.auth.models import Principal
from xiaowo_web.settings import AuthMode, DEMO_STUDENT_ID, PROJECT_ROOT, WebSettings
from xiaowo_web.storage import WebStore


DEMO_PROFILE_FALLBACK = {
    "id": DEMO_STUDENT_ID,
    "name": "测试",
    "major": "人工智能",
    "grade": "2025级",
    "profile_source": "demo_fixture",
    "logged_in": True,
}


class AuthService:
    def __init__(self, settings: WebSettings, store: WebStore) -> None:
        self.settings = settings
        self.store = store

    def resolve(self, raw_token: str) -> Principal | None:
        principal = self.store.resolve_session(raw_token)
        if principal is None:
            return None
        if principal.auth_mode != AuthMode.ANONYMOUS.value and principal.auth_mode != self.settings.auth_mode.value:
            self.store.cancel_owner_runs(principal.session_key)
            self.store.delete_session(principal.session_key)
            return None
        return principal

    def create_anonymous(self) -> tuple[str, Principal]:
        anonymous_id = f"anon:{secrets.token_urlsafe(12)}"
        return self.store.create_session(
            principal_id=anonymous_id,
            auth_mode=AuthMode.ANONYMOUS.value,
            profile={"logged_in": False},
            is_admin=False,
        )

    def login_demo(self, old_principal: Principal | None = None) -> tuple[str, Principal]:
        if old_principal is not None:
            self.store.cancel_owner_runs(old_principal.session_key)
            self.store.delete_session(old_principal.session_key)
        profile = self._load_demo_profile()
        return self.store.create_session(
            principal_id=DEMO_STUDENT_ID,
            auth_mode=AuthMode.DEMO.value,
            profile=profile,
            is_admin=DEMO_STUDENT_ID in self.settings.admin_ids,
        )

    def login_cas(
        self,
        student_id: str,
        profile: dict[str, str],
        old_principal: Principal | None = None,
    ) -> tuple[str, Principal]:
        normalized_id = student_id.strip().upper()
        if old_principal is not None:
            self.store.cancel_owner_runs(old_principal.session_key)
            self.store.delete_session(old_principal.session_key)
        bound_profile = dict(profile)
        bound_profile["id"] = normalized_id
        bound_profile["logged_in"] = True
        return self.store.create_session(
            principal_id=normalized_id,
            auth_mode=AuthMode.CAS.value,
            profile=bound_profile,
            is_admin=normalized_id in self.settings.admin_ids,
        )

    def logout(self, principal: Principal) -> None:
        self.store.cancel_owner_runs(principal.session_key)
        self.store.delete_session(principal.session_key)

    def rotate_csrf(self, principal: Principal) -> Principal:
        csrf_token = self.store.rotate_csrf(principal.session_key)
        return Principal(
            principal_id=principal.principal_id,
            auth_mode=principal.auth_mode,
            profile=principal.profile,
            is_admin=principal.is_admin,
            session_key=principal.session_key,
            csrf_token=csrf_token,
        )

    def sign_cas_state(self) -> str:
        nonce = secrets.token_urlsafe(24)
        signature = hmac.new(
            self.settings.session_secret.encode("utf-8"),
            nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"{nonce}.{signature}"

    def validate_cas_state(self, state: str, cookie_state: str) -> bool:
        if not state or not cookie_state or not hmac.compare_digest(state, cookie_state):
            return False
        try:
            nonce, signature = state.rsplit(".", 1)
        except ValueError:
            return False
        expected = hmac.new(
            self.settings.session_secret.encode("utf-8"),
            nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    @staticmethod
    def _load_demo_profile() -> dict[str, str]:
        # 与 AcademicService 同源:演示学业数据统一来自 fixtures/demo/PB25111691.json
        fixture = PROJECT_ROOT / "fixtures" / "demo" / f"{DEMO_STUDENT_ID}.json"
        if not fixture.exists():
            return dict(DEMO_PROFILE_FALLBACK)
        try:
            payload = json.loads(fixture.read_text(encoding="utf-8"))
            user = payload.get("user") if isinstance(payload, dict) else None
            if not isinstance(user, dict) or str(user.get("id", "")).upper() != DEMO_STUDENT_ID:
                return dict(DEMO_PROFILE_FALLBACK)
            return {
                "id": DEMO_STUDENT_ID,
                "name": str(user.get("name") or DEMO_PROFILE_FALLBACK["name"]),
                "major": str(user.get("major") or DEMO_PROFILE_FALLBACK["major"]),
                "grade": str(user.get("grade") or DEMO_PROFILE_FALLBACK["grade"]),
                "logged_in": True,
                "profile_source": "demo_fixture",
            }
        except (OSError, ValueError, TypeError):
            return dict(DEMO_PROFILE_FALLBACK)
