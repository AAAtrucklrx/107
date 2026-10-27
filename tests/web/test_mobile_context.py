"""Credentials must survive both thread boundaries without shared sessions."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from services.cas_client import CASClient
from services.jw_session import current_client, reset_client, set_client
from services.session_ctx import reset_student, set_student
from services.service_container import ServiceContainer
from tests.web.test_legacy_runner import _request
from xiaowo_web.chat.runner import LegacyQaRunner


def synthetic_client(student_id):
    client = CASClient()
    client._student_id = student_id
    client._logged_in = True
    client.session.cookies.set("SESSION", student_id, domain="jw.ustc.edu.cn", path="/")
    return client


def test_service_container_uses_request_client_without_global_bucket():
    client = synthetic_client("PB26000001")
    token = set_client(client)
    student = set_student("PB26000001")
    try:
        container = ServiceContainer()
        assert container.has_cas()
        assert container.cas_client is client
        assert "PB26000001" not in container._cas_clients
    finally:
        reset_client(token)
        reset_student(student)


def test_school_probe_marks_network_failure_as_retryable(monkeypatch):
    import requests
    client = CASClient()
    def unavailable(*args, **kwargs):
        raise requests.Timeout("synthetic timeout")
    monkeypatch.setattr(client.session, "get", unavailable)
    assert client.probe_jw_session() is False
    assert client._probe_unavailable is True


def test_both_thread_boundaries_keep_two_students_isolated(monkeypatch):
    import agents.qa.nodes as nodes
    import threading
    barriers = {sid: threading.Barrier(2) for sid in ("PB26000001", "PB26000002")}
    data_ids = {"PB26000001": 260001, "PB26000002": 260002}
    # Exercise actual LegacyQaRunner + act pools; substitute only the business tool.
    class IdentityTool:
        def invoke(self, _args):
            client = current_client()
            if client is not None:
                barriers[client.student_id].wait(timeout=5)
            return {"student_id": client.student_id if client else None,
                    "student_data_id": client.student_data_id if client else None,
                    "session_id": id(client.session) if client else None}
    monkeypatch.setattr(nodes, "_build_tool_registry", lambda: {"identity_a": IdentityTool(), "identity_b": IdentityTool()})
    observed = {}
    def qa(_question, **kwargs):
        result = nodes.act({"decision": "call_tool", "tool_calls": [{"tool": "identity_a", "args": {}}, {"tool": "identity_b", "args": {}}],
                            "tool_results": [], "rounds": 0, "student_id": kwargs["student_id"]})
        observed[kwargs["student_id"]] = [r["result"] for r in result["tool_results"]]
        return {"answer": "已完成", "tool_results": [], "candidates": []}
    runner = LegacyQaRunner(qa, max_workers=2)
    async def run_one(sid):
        from dataclasses import replace
        from xiaowo_web.auth.models import Principal
        client = synthetic_client(sid)
        client._student_data_id = data_ids[sid]
        token = set_client(client)
        try:
            await runner.run(replace(_request(), principal=Principal(sid, "cas", {"id": sid}, False, sid)))
        finally:
            reset_client(token)
    async def both():
        await asyncio.gather(run_one("PB26000001"), run_one("PB26000002"))
    try:
        asyncio.run(both())
    finally:
        runner.close()
    for sid, results in observed.items():
        assert [r["student_id"] for r in results] == [sid, sid]
        assert [r["student_data_id"] for r in results] == [data_ids[sid], data_ids[sid]]
        assert results[0]["session_id"] != results[1]["session_id"]
    assert current_client() is None


@pytest.mark.parametrize("boundary", ["tool", "school_http"])
def test_cancelled_worker_keeps_live_guard_without_revoking_another_user(monkeypatch, boundary):
    """A cancelled asyncio task cannot kill its thread; the live guard must stop it."""
    import threading
    import requests
    from dataclasses import replace
    from services.jw_session import in_thread_context, set_access_guard, reset_access_guard
    from xiaowo_web.auth.models import Principal
    from xiaowo_web.errors import ApiError

    started, release, finished, revoked = (threading.Event() for _ in range(4))
    actions, blocked = [], []

    def synthetic_send(_session, request, **_kwargs):
        actions.append(request.headers["X-Synthetic-Student"])
        response = requests.Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(requests.Session, "send", synthetic_send)

    def qa(_question, **kwargs):
        sid = kwargs["student_id"]
        if sid == "SYNTHETIC_A":
            started.set()
            assert release.wait(5)
        try:
            if boundary == "tool":
                in_thread_context(lambda: actions.append(current_client().student_id))()
            else:
                prepared = requests.Request("GET", "https://jw.ustc.edu.cn/synthetic",
                                            headers={"X-Synthetic-Student": sid}).prepare()
                current_client().session.send(prepared)
        except ApiError as exc:
            blocked.append((sid, exc.status_code))
        finally:
            if sid == "SYNTHETIC_A":
                finished.set()
        return {"answer": "已完成", "tool_results": [], "candidates": []}

    runner = LegacyQaRunner(qa, max_workers=2)

    async def run_one(sid):
        def guard():
            if sid == "SYNTHETIC_A" and revoked.is_set():
                raise ApiError(401, "AUTH_REQUIRED", "合成会话已撤销")
        guard_token = set_access_guard(guard)
        token = set_client(synthetic_client(sid))
        try:
            request = replace(_request(), question="这是一条足够长的纯离线会话撤销边界测试问题",
                              principal=Principal(sid, "cas", {"id": sid}, False, sid))
            return await runner.run(request)
        finally:
            reset_client(token)
            reset_access_guard(guard_token)

    async def exercise():
        old = asyncio.create_task(run_one("SYNTHETIC_A"))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            revoked.set()
            old.cancel()
            with pytest.raises(asyncio.CancelledError):
                await old
            await run_one("SYNTHETIC_B")
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        runner.close()
    assert actions == ["SYNTHETIC_B"]
    assert blocked == [("SYNTHETIC_A", 401)]
    assert current_client() is None
