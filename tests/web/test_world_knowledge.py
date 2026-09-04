"""世界知识快速通道 + 证据制软化（insufficient 内容展示）。"""

from __future__ import annotations

import hashlib

import agents.qa.nodes as nodes
from agents.qa.graph import _route_after_parse
from agents.qa.nodes import embedding_parse, world_knowledge
from xiaowo_web.evidence.pipeline import EvidencePipeline


def _state(query: str = "合肥有什么景点", found: bool = False) -> dict:
    return {
        "query": query,
        "module_signal": "自动判断",
        "candidates": [{"id": "x", "content": "c", "score": 1.0}] if found else [],
        "candidates_found": found,
        "retrieval_log": [],
        "tool_results": [],
        "rounds": 0,
        "intent": "",
        "intent_top3": [],
    }


def test_embedding_parse_flags_world_knowledge_for_general_questions(monkeypatch) -> None:
    called = {"search": 0}

    class FakeStore:
        def search(self, *_a, **_k):
            called["search"] += 1
            return {"results": [], "found": False}

    monkeypatch.setattr(nodes, "_get_faq_store", lambda: FakeStore())
    out = embedding_parse(_state("合肥有什么值得一去的景点？"))
    assert out["world_knowledge"] is True
    assert called["search"] == 1


def test_embedding_parse_no_world_knowledge_for_campus_questions(monkeypatch) -> None:
    monkeypatch.setattr(nodes, "_get_faq_store", lambda: FakeStore())
    # 科大触发词 → 不进入世界知识通道（留给校内链路/微信通道）
    out = embedding_parse(_state("中国科学技术大学有什么景点吗？"))
    assert out["world_knowledge"] is False


def test_embedding_parse_no_world_knowledge_when_kb_hit(monkeypatch) -> None:
    class FakeStore:
        def search(self, *_a, **_k):
            return {"results": [{"id": "c1", "content": "x", "score": 0.9}], "found": True}

    monkeypatch.setattr(nodes, "_get_faq_store", lambda: FakeStore())
    out = embedding_parse(_state("图书馆开放时间是几点？", found=True))
    assert out["world_knowledge"] is False


class FakeStore:
    def search(self, *_a, **_k):
        return {"results": [], "found": False}


def test_route_after_parse() -> None:
    assert _route_after_parse({"world_knowledge": True}) == "world_knowledge"
    assert _route_after_parse({"world_knowledge": False}) == "think"
    assert _route_after_parse({}) == "think"


def test_world_knowledge_node_answer_with_disclaimer(monkeypatch) -> None:
    from langchain_core.prompts import ChatPromptTemplate

    class FakeLLM:
        def __init__(self, *_a, **_k):
            pass

    class FakeChain:
        def invoke(self, _vars):
            return type("R", (), {"content": "合肥有包公园、三河古镇等景点。（以上为通用信息，非联网核实，仅供参考）"})()

    def fake_create(*_a, **_k):
        from langchain_core.prompts import ChatPromptTemplate as C
        return ChatPromptTemplate.from_messages([("system", "x")]) | FakeChain()

    monkeypatch.setattr("utils.llm_client.create_llm", fake_create)
    out = world_knowledge(_state())
    assert "包公园" in out["answer"]
    assert "非联网核实" in out["answer"]


def test_world_knowledge_node_degrades_when_llm_down() -> None:
    out = world_knowledge({"query": "合肥有什么景点", "llm_down": True})
    assert "无法核实" in out["answer"]


# ── 证据制软化：insufficient 时展示提取内容 ──

def _bundle(claims):
    return EvidencePipeline._insufficient(
        [],
        ["已找到公开来源，但尚无声明达到确定性证据门槛。"],
        claims=claims,
    )


def test_insufficient_shows_extracted_content_when_present() -> None:
    claims = [{
        "claim_id": "c1", "text": "比尔·盖茨罕见发文，强调AI治理和公平使用。",
        "kind": "factual", "status": "insufficient", "evidence": [],
    }]
    answer = _bundle(claims)
    assert "仅供参考" in answer.markdown
    assert "比尔·盖茨" in answer.markdown
    assert answer.claims[0]["text"] == "比尔·盖茨罕见发文，强调AI治理和公平使用。"


def test_insufficient_keeps_placeholder_when_no_content() -> None:
    answer = _bundle(None)
    assert answer.markdown == "暂未找到足够可靠的联网证据。"
    assert answer.claims[0]["status"] == "insufficient"
