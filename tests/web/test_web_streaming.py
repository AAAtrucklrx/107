"""联网合成真流式（web 路径 on_delta，2026-09-18）。

本地链路（agents/qa/nodes.py `compose`）早就逐段推 `answer.delta`，联网路径
（xiaowo_web/evidence/compose.py `compose_with_own_llm`）却是一次性 `invoke`：
实测首字 11.8s，期间只推了一段「正在整理」占位文案。本文件锁住联网流式的契约：

1. 传了 `on_delta` → 走 `chain.stream` 逐段回调，回调文本拼起来 == 返回值；
2. 没传 `on_delta` → 保持一次性 `invoke`（不为不需要流式的调用方增加开销）；
3. 流里一个字都没出（provider 不支持 stream）→ 回退 `invoke`，答案不丢；
4. 流中途断 → 保留已生成的部分，不抛错（用户至少看到半截而不是空白）。

另加两条"接线"断言：`compose_and_verify` 只给**首次合成**传 `on_delta`（修复稿再流一次
会让用户看到正文跳变）；runner 把 `request.emit_delta` 透传给 `pipeline.answer`。

已知取舍（用户 2026-09-18 明确接受）：流出去的是**未过闸门**的稿子，闸门若拒答、
或 runner 回退本地答案，最终 `answer.segment` / `answer.completed` 会**整段替换**它；
若整条 run 超时（GENERATION_TIMEOUT_PARTIAL），前端保留的仍是这份草稿，但会带上
"生成超时，以上为已生成的部分内容"的 limitations 与 `truncated=true`。
"""

from __future__ import annotations

import asyncio
import dataclasses
from types import SimpleNamespace

from langchain_core.runnables import Runnable

from tests.web.test_evidence_runner import _FixedRunner, _local_bundle, _request
from tests.web.test_pure_answer import _REFS, PureSearch, _pipeline
from xiaowo_web.chat.models import AnswerBundle
from xiaowo_web.evidence.runner import EvidenceAwareRunner


class _CountLLM(Runnable):
    """伪 LLM：记录 invoke/stream 调用次数，可模拟空流与流中断。"""

    def __init__(self, chunks: list[str], *, fail_at: int | None = None,
                 empty_stream: bool = False) -> None:
        self.chunks = chunks
        self.fail_at = fail_at
        self.empty_stream = empty_stream
        self.invoked = 0
        self.streamed = 0

    def invoke(self, _input, config=None, **_kwargs) -> SimpleNamespace:
        self.invoked += 1
        return SimpleNamespace(
            content="".join(self.chunks),
            response_metadata={"finish_reason": "stop"},
        )

    def stream(self, _input, config=None, **_kwargs):
        self.streamed += 1
        if self.empty_stream:
            return
        for index, chunk in enumerate(self.chunks):
            if self.fail_at is not None and index == self.fail_at:
                raise RuntimeError("stream broken (test)")
            yield SimpleNamespace(content=chunk, response_metadata={})


def _use_llm(monkeypatch, llm: Runnable) -> None:
    """把 compose 内部局部导入的 create_llm 换成伪 LLM，并屏蔽注入上下文。"""
    monkeypatch.setattr("utils.llm_client.create_llm", lambda **_kw: llm)
    monkeypatch.setattr("xiaowo_web.evidence.compose.injected_context", lambda: "")


# ---------- 1. compose_with_own_llm 的四条契约 ----------

def test_web_compose_streams_deltas(monkeypatch) -> None:
    """传 on_delta → 逐段回调，且回调文本拼起来就是最终答案。"""
    import xiaowo_web.evidence.compose as compose_module

    chunks = ["图书馆每周开放", "7*16 小时，", "电话 63607647。"]
    llm = _CountLLM(chunks)
    _use_llm(monkeypatch, llm)
    deltas: list[str] = []
    answer = compose_module.compose_with_own_llm("图书馆开放时间？", _REFS, on_delta=deltas.append)
    assert answer == "".join(chunks)
    assert "".join(deltas) == answer, "流出的内容必须是答案的前缀拼接"
    assert deltas, "至少要推一段，否则前端首字没提前"
    assert llm.streamed == 1 and llm.invoked == 0, "有 on_delta 时必须走 stream"


def test_web_compose_buffers_small_chunks(monkeypatch) -> None:
    """小 chunk 要攒到约 16 字才推一段（否则每个 token 一个 SSE 事件，前端抖）。"""
    import xiaowo_web.evidence.compose as compose_module

    llm = _CountLLM(["甲" * 10, "乙" * 10, "丙" * 10])
    _use_llm(monkeypatch, llm)
    deltas: list[str] = []
    compose_module.compose_with_own_llm("q", _REFS, on_delta=deltas.append)
    assert deltas == ["甲" * 10 + "乙" * 10, "丙" * 10]


def test_web_compose_without_callback_uses_invoke(monkeypatch) -> None:
    """没传 on_delta → 保持一次性 invoke（不引入流式开销）。"""
    import xiaowo_web.evidence.compose as compose_module

    llm = _CountLLM(["一次性答案。"])
    _use_llm(monkeypatch, llm)
    assert compose_module.compose_with_own_llm("q", _REFS) == "一次性答案。"
    assert llm.streamed == 0 and llm.invoked == 1


def test_web_compose_falls_back_when_stream_is_empty(monkeypatch) -> None:
    """provider 不支持 stream（一个字都没出）→ 回退 invoke，答案不能丢。"""
    import xiaowo_web.evidence.compose as compose_module

    llm = _CountLLM(["回退得到的答案。"], empty_stream=True)
    _use_llm(monkeypatch, llm)
    assert compose_module.compose_with_own_llm("q", _REFS, on_delta=lambda _d: None) == "回退得到的答案。"
    assert llm.streamed == 1 and llm.invoked == 1


def test_web_compose_keeps_partial_on_stream_break(monkeypatch) -> None:
    """流中途断 → 保留已生成内容直接返回（不抛错、不丢半截答案）。"""
    import xiaowo_web.evidence.compose as compose_module

    llm = _CountLLM(["甲" * 20, "乙" * 20, "丙" * 20], fail_at=1)
    _use_llm(monkeypatch, llm)
    deltas: list[str] = []
    answer = compose_module.compose_with_own_llm("q", _REFS, on_delta=deltas.append)
    assert answer == "甲" * 20
    assert "".join(deltas) == "甲" * 20
    assert llm.invoked == 0, "已有部分内容就不该再整篇重来"


# ---------- 2. compose_and_verify：只有首次合成流式 ----------

def test_compose_and_verify_streams_only_first_draft(monkeypatch) -> None:
    """首稿流出后若触发修复，修复稿**不流**（再流一次用户会看到正文跳变）。"""
    import xiaowo_web.evidence.compose as compose_module

    first = "图书馆每周开放 9*9 小时，电话 63607647。"
    llm = _CountLLM([first])
    _use_llm(monkeypatch, llm)
    monkeypatch.setattr(
        compose_module, "repair_answer",
        lambda q, r, prev, bad: "图书馆开放时间未在资料中给出，电话 63607647。",
    )
    deltas: list[str] = []
    answer, unsupported = compose_module.compose_and_verify(
        "图书馆开放时间？", _REFS, on_delta=deltas.append
    )
    assert "".join(deltas) == first, "流出的只能是首稿"
    assert "9*9" not in answer and "63607647" in answer
    assert unsupported == []
    assert llm.streamed == 1


# ---------- 3. 接线：pipeline 与 runner 要把回调传到底 ----------

def test_pipeline_forwards_on_delta_to_compose(tmp_path, monkeypatch) -> None:
    """纯搜索路径：answer(on_delta=…) 必须一路传到 compose_and_verify。"""
    import xiaowo_web.evidence.pipeline as pipeline_module

    seen: dict = {}

    def _fake_compose(question, refs, **kw):
        seen.update(kw)
        return "图书馆每周开放 7*16 小时。", []

    monkeypatch.setattr(pipeline_module, "judge_relevance", lambda question, refs, **kw: True)
    monkeypatch.setattr(pipeline_module, "compose_and_verify", _fake_compose)

    def cb(_delta: str) -> None:
        return None

    bundle = asyncio.run(
        _pipeline(tmp_path, PureSearch()).answer("中国科学技术大学图书馆开放时间？", on_delta=cb)
    )
    assert seen.get("on_delta") is cb
    assert bundle.terminal_reason == "AI_GENERATED"


def test_runner_forwards_emit_delta_to_pipeline() -> None:
    """联网兜底分支：runner 要把 request.emit_delta 交给 pipeline.answer。"""
    captured: dict = {}

    class _CapturePipeline:
        async def answer(self, _question, *, profile=None, on_stage=None, on_delta=None,
                         local_hint=None, rounds_limit=None) -> AnswerBundle:
            captured["on_delta"] = on_delta
            return AnswerBundle(markdown="联网回答。", terminal_reason="AI_GENERATED")

        async def close(self) -> None:
            return None

    runner = EvidenceAwareRunner(_FixedRunner(_local_bundle([])), _CapturePipeline())  # type: ignore[arg-type]
    def cb(_delta: str) -> None:
        return None

    # QaRunRequest 是 frozen dataclass：只能 replace，不能就地赋值
    request = dataclasses.replace(_request("中国科学技术大学图书馆开放时间？"), emit_delta=cb)
    asyncio.run(runner.run(request))
    assert captured.get("on_delta") is cb
