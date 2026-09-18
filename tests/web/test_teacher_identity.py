"""教师身份与判定器窗口（2026-09-18 用户实测两个缺陷）。

症状：问「龚伟老师评价，还有龚伟老师的实验室是什么」——
1. 回答把**龚伟峰**的英语课算到**龚伟**头上（英语口语实践高级 10.0、英语交流II 9.8…），
   引用的评论标签里其实写着 `[龚伟峰]`；
2. 「实验室」那半句完全没查（没联网），回答直接说"没检索到"。

根因：
1. `analyze_teacher` 用 `teachers.name LIKE '%龚伟%'` 找人，而评课库把合教组合**逗号连写**
   存一行（"马建辉, 龚伟"、"张曼君, 龚伟峰"）→ 两个人的课被合并统计；返回体还把查询串
   原样当结果名（`"teacher": teacher_name`），把混淆藏住。
2. `_llm_judge_answered` 把答案截成 `[:800]` 再判定，而"实验室没查到"那句在 800 字之后
   → 判定器没看到缺项 → 判「能」→ 不进联网。
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from langchain_core.runnables import Runnable

import tools.advisor_tools as advisor_tools
from tests.web.test_evidence_runner import _request
from xiaowo_web.chat.models import AnswerBundle
from xiaowo_web.evidence.runner import EvidenceAwareRunner, _local_merge_hint
from tools.advisor_tools import _split_teacher_names, analyze_teacher
from xiaowo_web.evidence import runner as runner_mod
from xiaowo_web.evidence.runner import _judge_window


def _catalog(tmp_path):
    """最小评课库：龚伟（计算机类）+ 龚伟峰（英语类）+ 一门合教课。"""
    db = tmp_path / "course_data.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE teachers(id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE courses(id INTEGER PRIMARY KEY, name TEXT, dept TEXT, code TEXT,
                             credit REAL, rating_avg REAL, rate_count INTEGER);
        CREATE TABLE course_teachers(id INTEGER PRIMARY KEY, course_id INTEGER, teacher_id INTEGER,
                                     rating_sum REAL, rating_count INTEGER, rating_avg REAL,
                                     dims_dist TEXT);
        CREATE TABLE reviews(id INTEGER PRIMARY KEY, course_id INTEGER, icourse_id TEXT,
                             teacher TEXT, author TEXT, stars REAL, term TEXT, difficulty TEXT,
                             homework TEXT, give_score TEXT, harvest TEXT, content TEXT);
        """
    )
    con.executemany("INSERT INTO teachers(id, name) VALUES(?,?)", [
        (1, "龚伟"), (2, "龚伟峰"), (3, "马建辉, 龚伟"), (4, "张曼君, 龚伟峰"),
        (5, "中级）（龚伟峰, Richard Cormack"),
        (6, "欧阳伟峰"),
    ])
    con.executemany(
        "INSERT INTO courses(id, name, dept, code, credit, rating_avg, rate_count) VALUES(?,?,?,?,?,?,?)",
        [
            (10, "Java软件开发基础", "计算机学院", "CS1", 3.0, 7.0, 4),
            (11, "数据结构", "计算机学院", "CS2", 3.0, 9.5, 10),
            (12, "英语交流II", "外语系", "EN1", 2.0, 9.8, 6),
            (13, "大学英语听说II", "外语系", "EN2", 2.0, 8.7, 18),
        ],
    )
    con.executemany(
        "INSERT INTO course_teachers(id, course_id, teacher_id, rating_sum, rating_count, rating_avg, dims_dist) "
        "VALUES(?,?,?,?,?,?,?)",
        [
            (100, 10, 1, 28.0, 4, 7.0, "{}"),
            (101, 11, 3, 95.0, 10, 9.5, "{}"),
            (102, 12, 2, 58.8, 6, 9.8, "{}"),
            (103, 13, 4, 156.6, 18, 8.7, "{}"),
            (104, 12, 6, 30.0, 3, 10.0, "{}"),
        ],
    )
    con.executemany(
        "INSERT INTO reviews(id, course_id, icourse_id, teacher, author, stars, term, difficulty, "
        "homework, give_score, harvest, content) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (200, 11, "ic1", "马建辉, 龚伟", "甲", 5.0, "2023秋", "中等", "适中", "好", "多",
             "这门课讲得很清楚，作业量适中，老师认真负责。"),
            (201, 11, "ic2", "龚伟峰", "乙", 5.0, "2023秋", "中等", "适中", "好", "多",
             "这是英语老师的评论，不该出现在龚伟的评论样本里。"),
            (202, 12, "ic3", "张曼君, 龚伟峰", "丙", 5.0, "2023秋", "中等", "适中", "好", "多",
             "英语课氛围轻松，给分大方，老师很温柔。"),
        ],
    )
    con.commit()
    con.close()

    def _fake_cdb():
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        return conn

    return _fake_cdb


def test_split_teacher_names_handles_combos() -> None:
    assert _split_teacher_names("马建辉, 龚伟") == ["马建辉", "龚伟"]
    assert _split_teacher_names("张曼君，龚伟峰") == ["张曼君", "龚伟峰"]
    assert _split_teacher_names("") == []
    assert _split_teacher_names(None) == []


def test_exact_name_does_not_merge_similar_teacher(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _catalog(tmp_path))
    out = analyze_teacher.invoke({"teacher_name": "龚伟"})
    names = [c["name"] for c in out["courses"]]
    assert out["teacher"] == "龚伟"
    assert out["matched_by"] == "exact"
    assert out["matched_teachers"] == ["龚伟"]
    assert set(names) == {"Java软件开发基础", "数据结构"}, "不能把龚伟峰的英语课并进来"
    assert "英语交流II" not in names and "大学英语听说II" not in names
    assert out["review_count"] == 14  # 4 + 10，只算龚伟自己的课


def test_co_taught_combo_row_counts_as_this_teacher(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _catalog(tmp_path))
    out = analyze_teacher.invoke({"teacher_name": "龚伟"})
    assert "数据结构" in [c["name"] for c in out["courses"]], "合教行'马建辉, 龚伟'也算龚伟的课"


def test_similar_teacher_keeps_its_own_courses(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _catalog(tmp_path))
    out = analyze_teacher.invoke({"teacher_name": "龚伟峰"})
    assert out["matched_teachers"] == ["龚伟峰"]
    assert "英语交流II" in [c["name"] for c in out["courses"]]
    assert "Java软件开发基础" not in [c["name"] for c in out["courses"]]


def test_prefix_query_returns_candidates_instead_of_merging(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _catalog(tmp_path))
    out = analyze_teacher.invoke({"teacher_name": "龚"})
    assert out.get("ambiguity") is True
    candidates = {c["name"] for c in out["candidates"]}
    assert {"龚伟", "龚伟峰"} <= candidates
    assert not any("（" in name for name in candidates), "爬虫脏名不该出现在候选里"
    assert "请确认" in out["message"]


def test_reviews_are_filtered_by_name_component(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _catalog(tmp_path))
    out = analyze_teacher.invoke({"teacher_name": "龚伟"})
    datas = [c for c in out["courses"] if c["name"] == "数据结构"]
    assert datas, "应有数据结构的记录"
    contents = " ".join(r["content"] for r in (datas[0]["top_reviews"] or []))
    assert "作业量适中" in contents
    assert "英语老师的评论" not in contents, "同课程下别的人的评论不能混进来"


def test_fuzzy_single_match_is_disclosed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _catalog(tmp_path))
    out = analyze_teacher.invoke({"teacher_name": "阳伟"})
    assert out["matched_by"] == "fuzzy"
    assert out["teacher"] == "欧阳伟峰"
    assert "不是评课库里的精确教师名" in out.get("note", "")


# ---------- 判定器窗口 ----------

def test_judge_window_keeps_head_and_tail() -> None:
    long_answer = "甲" * 3000 + "实验室这次没有检索到"
    window = _judge_window(long_answer)
    assert "实验室这次没有检索到" in window, "缺项常在末尾，必须看得到"
    assert window.startswith("甲甲"), "开头也不能丢"
    assert len(window) < len(long_answer)
    short = "短答案"
    assert _judge_window(short) == short


class _CapturingLLM(Runnable):
    def __init__(self, text: str = "能") -> None:
        self.text = text
        self.inputs: list = []

    def invoke(self, _input, config=None, **_kwargs) -> SimpleNamespace:
        self.inputs.append(_input)
        return SimpleNamespace(content=self.text, response_metadata={"finish_reason": "stop"})


def test_judge_sees_answer_tail(monkeypatch) -> None:
    llm = _CapturingLLM("不能")
    monkeypatch.setattr("utils.llm_client.create_llm", lambda **_kw: llm)
    verdict = runner_mod._llm_judge_answered("龚伟老师评价，还有他的实验室是什么", "甲" * 2000 + "实验室没有查到")
    assert verdict is False
    assert "实验室没有查到" in str(llm.inputs[0]), "判定器提示词里必须带上答案尾部"


# ---------- 摘要要点名老师 ----------

def test_tool_summary_names_matched_teacher() -> None:
    from agents.qa.nodes import _build_tool_summary

    summary = _build_tool_summary([{
        "tool": "analyze_teacher",
        "status": "done",
        "result": {
            "teacher": "龚伟", "matched_by": "exact", "matched_teachers": ["龚伟"],
            "avg_rating": 8.3, "review_count": 18,
            "courses": [{"name": "Java软件开发基础", "rating_avg": 7.2, "rate_count": 4, "dept": "计算机学院"}],
            "reviews_sample": [{"teacher": "龚伟, 曹晨红", "content": "从零基础教起。"}],
        },
    }])
    assert "龚伟" in summary and "匹配方式 exact" in summary
    assert "Java软件开发基础" in summary
    assert "[龚伟, 曹晨红]" in summary, "每条评论要带上它对应的老师"


def test_tool_summary_surfaces_ambiguity() -> None:
    from agents.qa.nodes import _build_tool_summary

    summary = _build_tool_summary([{
        "tool": "analyze_teacher",
        "status": "done",
        "result": {
            "ambiguity": True,
            "message": "评课库里没有叫「龚」的老师，但有姓名相近的龚伟、龚伟峰",
            "candidates": [{"name": "龚伟", "course_count": 5}, {"name": "龚伟峰", "course_count": 16}],
        },
    }])
    assert "龚伟峰" in summary
    assert "确认" in summary, "必须让模型先让用户确认是哪一位"

# ---------- 缺陷 3：多问句不吃"工具结果"快速豁免 + 公开工具结果可合并 ----------

def test_looks_multi_part() -> None:
    assert runner_mod._looks_multi_part("龚伟老师评价，还有龚伟老师的实验室是什么")
    assert runner_mod._looks_multi_part("教学秘书和院长分别是谁")
    assert not runner_mod._looks_multi_part("龚伟老师评价怎么样")


def _tool_bundle() -> AnswerBundle:
    return AnswerBundle(
        markdown="龚伟老师共 5 门课，综合均分 8.3。实验室这次没有查到。",
        claims=[{"claim_id": "c1", "status": "confirmed"}],
        sources=[{"level": "tool_result", "tool": "analyze_teacher", "title": "教师评价"}],
        terminal_reason="local_answer",
    )


def test_multi_part_question_does_not_take_tool_exemption(monkeypatch) -> None:
    """工具只答了一半时，必须让判定器（进而联网）介入。"""
    calls = {"n": 0}

    def _judge(*_a, **_k):
        calls["n"] += 1
        return False

    monkeypatch.setattr(runner_mod, "_llm_judge_answered", _judge)
    verdict = asyncio.run(runner_mod._local_answered(
        _tool_bundle(), "龚伟老师评价，还有龚伟老师的实验室是什么"
    ))
    assert verdict is False
    assert calls["n"] == 1, "多问句时判定器必须被调用（否则永不联网）"


def test_single_part_question_keeps_tool_exemption(monkeypatch) -> None:
    calls = {"n": 0}

    def _judge(*_a, **_k):
        calls["n"] += 1
        return False

    monkeypatch.setattr(runner_mod, "_llm_judge_answered", _judge)
    verdict = asyncio.run(runner_mod._local_answered(_tool_bundle(), "龚伟老师评价怎么样"))
    assert verdict is True
    assert calls["n"] == 0, "单问句仍然走快速豁免，不浪费一次判定调用"


def test_merge_hint_allows_public_tool_blocks_personal() -> None:
    public = AnswerBundle(markdown="龚伟老师 5 门课，均分 8.3。", terminal_reason="local_answer")
    public.sources = [{"level": "tool_result", "tool": "analyze_teacher", "title": "教师评价"}]
    hint = _local_merge_hint(public)
    assert hint and hint["titles"] == ["教师评价"], "公开评课数据必须能带进联网合并"

    personal = AnswerBundle(markdown="你的课表今天有电磁学C。", terminal_reason="local_answer")
    personal.sources = [{"level": "tool_result", "tool": "query_schedule", "title": "个人课表"}]
    assert _local_merge_hint(personal) is None

    unknown = AnswerBundle(markdown="某工具结果。", terminal_reason="local_answer")
    unknown.sources = [{"level": "tool_result", "title": "某工具"}]
    assert _local_merge_hint(unknown) is None, "拿不到工具名时保守不放行"


def test_composite_tool_answer_reaches_web_with_hint(monkeypatch) -> None:
    from tests.web.test_cache_final_answer import _LocalStub, _WebStub, _request

    monkeypatch.setattr(runner_mod, "_llm_judge_answered", lambda *_a, **_k: False)
    web = AnswerBundle(
        markdown="评价 + 实验室（联网）。",
        claims=[{"claim_id": "c1", "status": "generated"}],
        sources=[{"level": "official_primary", "title": "计算机学院官网"}],
        terminal_reason="AI_GENERATED",
    )
    pipeline = _WebStub(web)
    runner = EvidenceAwareRunner(_LocalStub(_tool_bundle()), pipeline)  # type: ignore[arg-type]
    result = asyncio.run(runner.run(_request("龚伟老师评价，还有龚伟老师的实验室是什么")))
    assert pipeline.hints and pipeline.hints[0] is not None
    assert pipeline.hints[0]["titles"] == ["教师评价"]
    assert result is web, "联网答案生效，同时评价内容作为合并基础带过去"
