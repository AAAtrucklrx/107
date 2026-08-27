"""Storage-level ownership, retention, and encryption checks."""

from __future__ import annotations

import sqlite3

from tests.web.helpers import make_settings
from xiaowo_web.storage import WebStore


def test_run_events_are_owned_and_expiry_becomes_tombstone(tmp_path) -> None:
    settings = make_settings(tmp_path)
    store = WebStore(settings)
    store.initialize()
    _, owner = store.create_session(
        principal_id="anon:a",
        auth_mode="anonymous",
        profile={},
        is_admin=False,
        now=10,
    )
    _, other = store.create_session(
        principal_id="anon:b",
        auth_mode="anonymous",
        profile={},
        is_admin=False,
        now=10,
    )
    run = store.create_run(owner.session_key, "auto", now=10)
    first = store.append_event(run.run_id, "run.created", {"stage": "queued"})
    second = store.finish_run(
        run.run_id,
        "completed",
        "answer.completed",
        {"claims": [], "sources": [], "limitations": [], "terminal_reason": "completed"},
    )
    assert first["id"] == 1
    assert second and second["id"] == 2
    assert store.get_run(run.run_id, other.session_key) is None

    store.prune_expired(now=10 + settings.run_event_retention_seconds + 1)
    assert store.get_run(run.run_id, owner.session_key) is None
    assert store.is_expired_run(run.run_id, owner.session_key) is True
    assert store.is_expired_run(run.run_id, other.session_key) is False


def test_cas_conversation_content_is_not_plaintext(tmp_path) -> None:
    settings = make_settings(
        tmp_path,
        mode="cas",
        data_key="separate-data-key",
        public_origin="https://testserver",
        extra={
            "CAS_SERVICE_URL": "https://testserver/api/v1/auth/cas/callback",
            "XIAOWO_SESSION_SECRET": "s" * 32,
        },
    )
    store = WebStore(settings)
    store.initialize()
    conversation = store.create_conversation("cas:PB25111691", "测试会话")
    store.append_exchange(
        conversation_id=conversation["conversation_id"],
        owner_key="cas:PB25111691",
        run_id="run-1",
        question="我的敏感问题",
        answer="敏感回答",
        metadata={},
    )
    with sqlite3.connect(settings.app_db_path) as conn:
        stored = [row[0] for row in conn.execute("SELECT content_value FROM web_messages")]
    assert all(value.startswith("gcm:") for value in stored)
    assert all("敏感" not in value for value in stored)
    loaded = store.get_conversation(conversation["conversation_id"], "cas:PB25111691")
    assert loaded is not None
    assert [message["content"] for message in loaded["messages"]] == ["我的敏感问题", "敏感回答"]
