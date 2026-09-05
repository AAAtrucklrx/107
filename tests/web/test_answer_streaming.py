"""compose 增量流式（answer.delta）与个人数据高置信直连路由（2026-09-05）。

覆盖三块：
1. `_direct_tool_route` 个人数据路由：登录+疑似个人指代→直连工具；未登录/导入/长句不触发；
   已有 done 结果转 compose。
2. `compose` 增量流式：有 action_sink 时走 chain.stream 逐段推 answer_delta；
   无 sink 保持原 invoke；finish_reason=length 标记截断；流中断保留部分内容。
3. ChatManager：emit_delta 以 answer.delta 事件透出且先于终态；生成超时但已有
   流式正文时部分收尾（GENERATION_TIMEOUT_PARTIAL），无流式正文仍整体失败。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.runnables import Runnable

from agents.qa.nodes import _direct_tool_route, compose
from tests.web.helpers import SlowRunner, bootstrap, make_settings, mutation_headers, parse_sse
from xiaowo_web.chat.models import AnswerBundle, QaRunRequest
from xiaowo_web.main import create_app


def _route_state(query: str, *, student_id: str = "PB21000001") -> dict:
    return {
        "query": query,
        "intent": "知识问答",
        "student_id": student_id,
        "user_profile": {},
        "tool_results": [],
        "thought_log": [],
        "rounds": 0,
    }


# ---------- 1. 个人数据高置信直连路由 ----------

def test_personal_route_short_query_calls_grade_tool() -> None:
    out = _direct_tool_route(_route_state("我的成绩怎么样"))
    assert out is not None and out["decision"] == "call_tool"
    assert [c["tool"] for c in out["tool_calls"]] == ["query_grade"]


def test_personal_route_gpa_and_exam_and_schedule() -> None:
    assert [c["tool"] for c in _direct_tool_route(_route_state("算一下我的绩点"))["tool_calls"]] == ["calc_gpa"]
    assert [c["tool"] for c in _direct_tool_route(_route_state("期末考试什么时候"))["tool_calls"]] == ["query_exam"]
    assert [c["tool"] for c in _direct_tool_route(_route_state("我这周课表"))["tool_calls"]] == ["query_schedule"]


def test_personal_route_requires_login() -> None:
    assert _direct_tool_route(_route_state("我的成绩怎么样", student_id="")) is None


def test_personal_route_skips_schedule_import() -> None:
    assert _direct_tool_route(_route_state("帮我导入课表")) is None


def test_personal_route_skips_long_impersonal_query() -> None:
    query = "请问学校规定的成绩复查具体流程是什么样的呢"
    assert len(query) > 12 and "我" not in query and "自己" not in query
    assert _direct_tool_route(_route_state(query)) is None


def test_personal_route_done_result_goes_compose() -> None:
    state = _route_state("我的成绩怎么样")
    state["tool_results"] = [{"tool": "query_grade", "status": "done"}]
    out = _direct_tool_route(state)
    assert out is not None and out["decision"] == "compose" and out["tool_calls"] == []


# ---------- 2. compose 增量流式 ----------


class _FakeStreamLLM(Runnable):
    """伪 LLM：stream 逐 chunk 产出；fail_at 指定下标处中断流。"""

    def __init__(self, chunks: list[str], *, finish_reason: str = "stop", fail_at: int | None = None) -> None:
        self.chunks = chunks
        self.finish_reason = finish_reason
        self.fail_at = fail_at

    def invoke(self, _input, config=None, **_kwargs) -> SimpleNamespace:
        return SimpleNamespace(
            content="".join(self.chunks),
            response_metadata={"finish_reason": self.finish_reason},
        )

    def stream(self, _input, config=None, **_kwargs):
        for index, chunk in enumerate(self.chunks):
            if self.fail_at is not None and index == self.fail_at:
                raise RuntimeError("stream broken (test)")
            meta = {"finish_reason": self.finish_reason} if index == len(self.chunks) - 1 else {}
            yield SimpleNamespace(content=chunk, response_metadata=meta)


def _compose_state() -> dict:
    return {
        "query": "图书馆几点开门",
        "intent": "知识问答",
        "candidates": [],
        "candidates_found": False,
        "retrieval_log": [],
        "tool_results": [],
        "rounds": 1,
        "chat_history": [],
        "student_id": "",
        "user_profile": {},
    }


def test_compose_streams_answer_delta_when_sink_present(monkeypatch) -> None:
    chunks = ["小蜗回答第一段，", "第二段内容较长用于触发缓冲推送，", "结尾。"]
    monkeypatch.setattr("agents.qa.nodes.create_llm", lambda **_kw: _FakeStreamLLM(chunks))
    events: list[tuple[str, dict | None]] = []
    state = _compose_state()
    state["action_sink"] = lambda message, payload=None: events.append((message, payload))
    out = compose(state)

    assert out["answer"] == "".join(chunks)
    assert out["truncated"] is False
    deltas = [payload["delta"] for message, payload in events if message == "answer_delta"]
    # 16 字缓冲：前两 chunk 累计 23 字触发一次推送，末尾 3 字收尾再推一次
    assert len(deltas) == 2
    assert "".join(deltas) == "".join(chunks)


def test_compose_marks_truncated_on_length_finish_reason(monkeypatch) -> None:
    chunks = ["触顶截断的回答内容，超过预算。"]
    monkeypatch.setattr(
        "agents.qa.nodes.create_llm",
        lambda **_kw: _FakeStreamLLM(chunks, finish_reason="length"),
    )
    state = _compose_state()
    state["action_sink"] = lambda *_args, **_kwargs: None
    out = compose(state)
    assert out["answer"] == "".join(chunks)
    assert out["truncated"] is True


def test_compose_keeps_partial_content_when_stream_breaks(monkeypatch) -> None:
    chunks = ["完整流前半段内容，", "后半段不应出现。"]
    monkeypatch.setattr(
        "agents.qa.nodes.create_llm",
        lambda **_kw: _FakeStreamLLM(chunks, fail_at=1),
    )
    events: list[tuple[str, dict | None]] = []
    state = _compose_state()
    state["action_sink"] = lambda message, payload=None: events.append((message, payload))
    out = compose(state)

    assert out["answer"] == chunks[0]
    assert out["truncated"] is True


def test_compose_without_sink_uses_invoke(monkeypatch) -> None:
    chunks = ["无 sink 时走 invoke 一次性返回。"]
    fake = _FakeStreamLLM(chunks)
    monkeypatch.setattr("agents.qa.nodes.create_llm", lambda **_kw: fake)
    out = compose(_compose_state())  # 不设 action_sink
    assert out["answer"] == "".join(chunks)
    assert out["truncated"] is False


# ---------- 3. ChatManager：answer.delta 透出与超时部分收尾 ----------


class DeltaRunner:
    async def run(self, request: QaRunRequest) -> AnswerBundle:
        if request.emit_delta is not None:
            request.emit_delta("流式第一段，")
            request.emit_delta("流式第二段。")
        return AnswerBundle(markdown="完整回答正文", terminal_reason="completed")

    def close(self) -> None:
        return None


class StreamingTimeoutRunner:
    async def run(self, request: QaRunRequest) -> AnswerBundle:
        if request.emit_delta is not None:
            request.emit_delta("已生成的部分内容")
        await asyncio.sleep(5)
        raise AssertionError("不应在超时前完成")

    def close(self) -> None:
        return None


def _run_events(app, question: str) -> list[dict]:
    with TestClient(app) as client:
        csrf, _ = bootstrap(client)
        created = client.post(
            "/api/v1/chat/runs",
            json={"question": question, "mode": "local"},
            headers=mutation_headers(csrf),
        ).json()
        return parse_sse(client.get("/api/v1" + created["events_url"]).text)


def test_answer_delta_events_stream_before_completion(tmp_path) -> None:
    app = create_app(make_settings(tmp_path), runner=DeltaRunner())
    events = _run_events(app, "增量流式透出测试")

    deltas = [e for e in events if e["type"] == "answer.delta"]
    assert [d["data"]["delta"] for d in deltas] == ["流式第一段，", "流式第二段。"]
    completed_at = next(i for i, e in enumerate(events) if e["type"] == "answer.completed")
    assert all(i < completed_at for i, e in enumerate(events) if e["type"] == "answer.delta")
    final_segment = [e for e in events if e["type"] == "answer.segment" and not e["data"].get("placeholder")]
    assert final_segment and final_segment[-1]["data"]["markdown"] == "完整回答正文"


def test_generation_timeout_with_streamed_text_finishes_partial(tmp_path) -> None:
    settings = make_settings(tmp_path, extra={
        "XIAOWO_GENERATION_TIMEOUT_SECONDS": "0.5",
        "XIAOWO_RUN_TIMEOUT_SECONDS": "3",
    })
    app = create_app(settings, runner=StreamingTimeoutRunner())
    events = _run_events(app, "流式超时部分收尾测试")

    assert events[-1]["type"] == "answer.completed"
    data = events[-1]["data"]
    assert data["truncated"] is True
    assert data["terminal_reason"] == "GENERATION_TIMEOUT_PARTIAL"
    assert any("超时" in note for note in data["limitations"])
    assert not [e for e in events if e["type"] == "run.failed"]
    assert [e["data"]["delta"] for e in events if e["type"] == "answer.delta"] == ["已生成的部分内容"]


def test_generation_timeout_without_streamed_text_fails_whole_run(tmp_path) -> None:
    settings = make_settings(tmp_path, extra={
        "XIAOWO_GENERATION_TIMEOUT_SECONDS": "0.5",
        "XIAOWO_RUN_TIMEOUT_SECONDS": "3",
    })
    app = create_app(settings, runner=SlowRunner())
    events = _run_events(app, "无流式正文超时整体失败测试")

    assert events[-1]["type"] == "run.failed"
    assert events[-1]["data"]["code"] == "UPSTREAM_TIMEOUT"
