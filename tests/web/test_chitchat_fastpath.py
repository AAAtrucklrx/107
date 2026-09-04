"""闲聊快路径：embedding_parse 跳过检索；compose 模板回应（不调 LLM）。"""

from __future__ import annotations

import agents.qa.nodes as nodes
from agents.qa.nodes import compose, embedding_parse


def _state(query: str = "你好") -> dict:
    return {
        "query": query,
        "module_signal": "自动判断",
        "candidates": [],
        "candidates_found": False,
        "retrieval_log": [],
        "tool_results": [],
        "rounds": 0,
        "intent": "",
        "intent_top3": [],
    }


def test_embedding_parse_chitchat_skips_retrieval(monkeypatch) -> None:
    called = {"count": 0}

    class FakeStore:
        def search(self, *_args, **_kwargs):
            called["count"] += 1
            raise AssertionError("闲聊不应触发知识库检索")

    monkeypatch.setattr(nodes, "_get_faq_store", lambda: FakeStore())
    out = embedding_parse(_state("你好"))
    assert called["count"] == 0
    assert out["chitchat"] is True
    assert out["intent"] == "闲聊"
    assert out["candidates"] == []


def test_compose_chitchat_uses_template_without_llm(monkeypatch) -> None:
    called = {"count": 0}

    def boom(*_args, **_kwargs):
        called["count"] += 1
        raise AssertionError("闲聊 compose 不应调用 LLM")

    monkeypatch.setattr("utils.llm_client.create_llm", boom)
    state = _state("你好")
    state["chitchat"] = True
    state["intent"] = "闲聊"
    # 12 条 0 分候选（旧 bug：导致闲聊仍走 LLM 合成的场景）
    state["candidates"] = [{"id": f"c{i}", "content": "x", "score": 0.0} for i in range(12)]
    state["candidates_found"] = True
    out = compose(state)
    assert called["count"] == 0
    assert "小蜗" in out["answer"]


def test_compose_non_chitchat_does_not_use_greeting_template(monkeypatch) -> None:
    """非闲聊问题即使候选命中也不走闲聊模板；LLM 不可用时降级格式化候选。"""

    def boom(*_args, **_kwargs):
        raise RuntimeError("LLM down (test)")

    monkeypatch.setattr("utils.llm_client.create_llm", boom)
    state = _state("图书馆几点开门")
    state["intent"] = "知识问答"
    state["candidates"] = [{"id": "c1", "content": "图书馆 7:00-22:30", "score": 0.8}]
    state["candidates_found"] = True
    out = compose(state)
    assert "我是小蜗，科大校园智能助手" not in out["answer"]
    assert "7:00" in out["answer"] and "22:30" in out["answer"]
