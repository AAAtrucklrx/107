"""复合问题：本地答一半 + 联网补一半时，**合并**而不是赢家通吃（2026-09-18）。

实测症状：问「中科大计算机学院教学秘书和院长分别是谁」——
本地答出了教秘（王海龙 / 0551-63600913 / wanghl@ustc.edu.cn，教务处 official_primary），
院长没收录；判定器把整题判成"没答全"后走联网，而联网合成只看得到检索结果，
于是**本地那半段被整段覆盖**，联网又只抓到那个页面的标题、没抓到正文，
最后反过来对教秘说"没法给你"。

两处修法各有一组测试：

- A 判定器认"缺项"：多并列问句只要有一项没正面回答就判「不能」→ 必进联网；
- B 合并：`_local_merge_hint()` 把本地已确认内容交给联网合成当"必须保留的基础"，
  并计入可核验证据；**个人数据（tool_result/tool_cache）绝不注入**。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.runnables import Runnable

from tests.web.test_evidence_runner import _FixedRunner, _local_bundle, _request
from tests.web.test_pure_answer import _REFS
from xiaowo_web.chat.models import AnswerBundle
from xiaowo_web.evidence import runner as runner_mod
from xiaowo_web.evidence.compose import _local_hint_block
from xiaowo_web.evidence.runner import EvidenceAwareRunner, _local_merge_hint

LOCAL_ANSWER = "计算机学院教学秘书王海龙，电话 0551-63600913，邮箱 wanghl@ustc.edu.cn。"


# ---------- A. 判定器认缺项 ----------

def test_judge_prompt_marks_partially_answered_multi_part_questions() -> None:
    prompt = runner_mod._ANSWER_JUDGE_PROMPT
    assert "多个并列的询问点" in prompt
    assert "任何一项" in prompt, "必须点明'任一缺项即不能'，否则答一半也算答出来"


# ---------- B1. 本地内容 → 合成提示块 ----------

def test_local_hint_block_carries_facts_and_titles() -> None:
    block = _local_hint_block({"markdown": LOCAL_ANSWER, "titles": ["教学秘书联系方式及办公地点"]})
    assert LOCAL_ANSWER in block
    assert "教学秘书联系方式及办公地点" in block
    assert "必须完整保留" in block
    # 本地那句"未收录"不能当成事实保留，否则联网查到的院长白查
    assert "未收录" in block and "不要保留那种说法" in block


def test_local_hint_block_empty_for_no_hint() -> None:
    assert _local_hint_block(None) == ""
    assert _local_hint_block({"markdown": "   "}) == ""


class _CapturingLLM(Runnable):
    """记录送给 LLM 的提示词，用来验证本地内容确实注入了合成。"""

    def __init__(self, text: str = "答案。") -> None:
        self.text = text
        self.inputs: list = []

    def invoke(self, _input, config=None, **_kwargs) -> SimpleNamespace:
        self.inputs.append(_input)
        return SimpleNamespace(content=self.text, response_metadata={"finish_reason": "stop"})


def test_compose_prompt_includes_local_confirmed_content(monkeypatch) -> None:
    import xiaowo_web.evidence.compose as compose_module

    llm = _CapturingLLM("合成答案。")
    monkeypatch.setattr("utils.llm_client.create_llm", lambda **_kw: llm)
    compose_module.compose_with_own_llm(
        "教学秘书是谁？", _REFS,
        local_hint={"markdown": LOCAL_ANSWER, "titles": ["教学秘书联系方式及办公地点"]},
    )
    prompt_text = str(llm.inputs[0])
    assert LOCAL_ANSWER in prompt_text
    assert "教学秘书联系方式及办公地点" in prompt_text


# ---------- B2. 本地事实要能过可核验闸门 ----------

def test_local_hint_makes_local_facts_verifiable(monkeypatch) -> None:
    """教秘电话在本地、联网只抓到标题：不把本地内容算进证据，它会被当"无据数字"对冲掉。"""
    import xiaowo_web.evidence.compose as compose_module

    monkeypatch.setattr(
        compose_module, "compose_with_own_llm",
        lambda q, r, on_delta=None, local_hint=None: LOCAL_ANSWER,
    )
    monkeypatch.setattr(compose_module, "injected_context", lambda: "")
    monkeypatch.setattr(compose_module, "repair_answer", lambda *a, **k: LOCAL_ANSWER)

    _answer, without_hint = compose_module.compose_and_verify("计算机学院教学秘书是谁？", _REFS)
    assert "0551-63600913" in without_hint, "不带本地内容时，电话确实会被判成无据数字"

    answer, with_hint = compose_module.compose_and_verify(
        "计算机学院教学秘书是谁？", _REFS,
        local_hint={"markdown": LOCAL_ANSWER, "titles": ["教学秘书联系方式及办公地点"]},
    )
    assert with_hint == [], "带上本地已确认内容后，同一个电话就是有据事实"
    assert "0551-63600913" in answer


# ---------- B3. 什么时候带、什么时候绝不带 ----------

def test_merge_hint_requires_public_local_answer() -> None:
    public = AnswerBundle(markdown=LOCAL_ANSWER, terminal_reason="local_answer")
    public.sources = [{"level": "official_primary", "title": "教学秘书联系方式及办公地点"}]
    hint = _local_merge_hint(public)
    assert hint and hint["markdown"] == LOCAL_ANSWER
    assert hint["titles"] == ["教学秘书联系方式及办公地点"]

    personal = AnswerBundle(markdown="你的课表：电磁学C。", terminal_reason="local_answer")
    personal.sources = [{"level": "tool_result", "title": "个人课表"}]
    assert _local_merge_hint(personal) is None, "个人数据绝不能进联网检索与合成"

    empty = AnswerBundle(markdown="   ", terminal_reason="local_answer")
    assert _local_merge_hint(empty) is None

    cached = AnswerBundle(markdown=LOCAL_ANSWER, terminal_reason="cache_hit")
    cached.sources = [
        {"source_id": "semantic-cache", "title": "语义缓存回答", "citation": "缓存"},
        {"level": "official_primary", "title": "教学秘书联系方式及办公地点"},
    ]
    cached_hint = _local_merge_hint(cached)
    assert cached_hint and cached_hint["titles"] == ["教学秘书联系方式及办公地点"], \
        "语义缓存命中的本地答案同样要当基础带过去，且占位来源不算真来源"

    webish = AnswerBundle(markdown=LOCAL_ANSWER, terminal_reason="EVIDENCE_INSUFFICIENT")
    webish.sources = [{"level": "official_primary", "title": "x"}]
    assert _local_merge_hint(webish) is None, "只有本地回答才值得当基础带过去"


def test_merge_hint_trims_and_limits_titles() -> None:
    bundle = AnswerBundle(markdown="啊" * 5000, terminal_reason="local_answer")
    bundle.sources = [{"level": "local_curated", "title": f"来源{i}"} for i in range(9)]
    hint = _local_merge_hint(bundle)
    assert hint is not None
    assert len(hint["markdown"]) == runner_mod._LOCAL_HINT_MAX_CHARS
    assert len(hint["titles"]) == 5


# ---------- B4. runner 端到端：透传 + 披露 ----------

class _CapturePipeline:
    def __init__(self) -> None:
        self.hints: list = []
        self.bundle = AnswerBundle(markdown="联网回答。", terminal_reason="AI_GENERATED")

    async def answer(self, _question, *, profile=None, on_stage=None, on_delta=None,
                     local_hint=None, rounds_limit=None) -> AnswerBundle:
        self.hints.append(local_hint)
        return self.bundle

    async def close(self) -> None:
        return None


def test_runner_passes_public_local_content_and_discloses() -> None:
    local = _local_bundle([])  # claims 为空 → 必然走联网
    local.sources = [{"level": "official_primary", "title": "教学秘书联系方式及办公地点"}]
    pipeline = _CapturePipeline()
    runner = EvidenceAwareRunner(_FixedRunner(local), pipeline)  # type: ignore[arg-type]
    result = asyncio.run(runner.run(_request("中科大计算机学院教学秘书是谁")))

    hint = pipeline.hints[0]
    assert hint and hint["titles"] == ["教学秘书联系方式及办公地点"]
    assert any("同时包含本地知识库已确认内容" in item for item in result.limitations), \
        "合并后必须让用户看得见'哪部分来自本地'"


def test_runner_does_not_send_personal_answers_to_web() -> None:
    local = _local_bundle([])
    local.markdown = "你的课表今天有电磁学C。"
    local.sources = [{"level": "tool_result", "title": "个人课表"}]
    pipeline = _CapturePipeline()
    runner = EvidenceAwareRunner(_FixedRunner(local), pipeline)  # type: ignore[arg-type]
    asyncio.run(runner.run(_request("我今天有什么课")))

    assert pipeline.hints[0] is None
