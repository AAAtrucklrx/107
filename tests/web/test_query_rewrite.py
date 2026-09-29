"""查询改写 / 指代消解（2026-09-29）：触发规则、输出清洗、超时回退、图内接线与遥测。"""
from __future__ import annotations

import time

from agents.qa import nodes
from agents.qa.rewrite import RewriteWorker, should_rewrite

_HISTORY = [
    {"role": "user", "content": "量子物理有哪些老师"},
    {"role": "assistant", "content": "量子物理有 3 位老师…"},
]


def test_should_rewrite_only_for_elliptical_with_history():
    assert should_rewrite("那门课呢", _HISTORY)          # 含指代
    assert should_rewrite("明天呢", _HISTORY)            # 极短追问
    assert not should_rewrite("帮我推荐几门通识课", _HISTORY)  # 完整问句
    assert not should_rewrite("那门课呢", [])             # 没有历史
    assert not should_rewrite("", _HISTORY)


def test_rewrite_worker_cleans_model_output():
    worker = RewriteWorker(invoke=lambda _p: '改写后：量子物理 张三 老师怎么样？"')
    value, ms, fallback = worker.rewrite("那门课呢", _HISTORY)
    assert value == "量子物理 张三 老师怎么样？"
    assert fallback is False and ms >= 0


def test_rewrite_worker_falls_back_on_bad_output():
    assert RewriteWorker(invoke=lambda _p: "").rewrite("那门课呢", _HISTORY)[0] == "那门课呢"
    assert RewriteWorker(invoke=lambda _p: None).rewrite("那门课呢", _HISTORY)[2] is True


def test_rewrite_worker_falls_back_on_exception_and_deadline():
    def boom(_p):
        raise RuntimeError("llm down")

    value, _ms, fallback = RewriteWorker(invoke=boom).rewrite("那门课呢", _HISTORY)
    assert value == "那门课呢" and fallback is True
    # 预算已用完：一次模型调用都不该发生
    calls = []
    worker = RewriteWorker(invoke=lambda p: calls.append(p) or "改写")
    assert worker.rewrite("那门课呢", _HISTORY, deadline=time.time() - 1)[2] is True
    assert calls == []


def test_embedding_parse_reports_rewrite_telemetry():
    """图内接线：改写结果进遥测；改写后分更准就采纳其意图。"""
    rewritten = "量子物理 张三 老师怎么样"
    worker = RewriteWorker(invoke=lambda _p: rewritten)

    class _Fake(RewriteWorker):
        def rewrite(self, query, history=None, *, deadline=None):
            return rewritten, 12.3, False

    nodes.set_rewrite_worker(_Fake(invoke=lambda _p: rewritten))
    try:
        out = nodes.embedding_parse({
            "query": "那门课呢",
            "chat_history": _HISTORY,
            "module_signal": "自动判断",
        })
    finally:
        nodes.set_rewrite_worker(None)

    assert out["rewritten_query"] == rewritten
    assert out["rewrite_ms"] == 12.3
    assert out["rewrite_fallback"] is False
    from knowledge.intent_classifier import classify
    assert out["intent"] == classify(rewritten)["intent"]
