"""培养方案定位：学院简称 / 学院级候选 / 失败态 / 真实进度（2026-09-18）。

用户实测：问「转去生医部要补哪些课」→ 小蜗答"拿不到可用的培养方案数据，0 门 / 0.0%"。
而库里**有**生医部（`910生命科学与医学部`）9 个方案（临床医学 72~76 门 ×5 年级、
中国科大-协和医学英才班 32~34 门 ×4 年级）。三个根因：

1. `生医部` 既不是方案名也不是学院名子串（库里是"910生命科学与医学部"）→ 定位落空；
2. 失败文案把"没有这个专业名"和"教务个人方案拿不到"混成一句"方案来源不可用"；
3. "生物医学工程"在方案库里确实不存在（0 条），但工具不告诉用户生医部里到底有哪些专业。

另外 `get_program_progress` 要能算差集，必须把已修课程喂进去（act 层 `_enrich_program_args`）。
"""

from __future__ import annotations

import sqlite3

import agents.qa.nodes as nodes
import tools.program_tools as program_tools
from tools._program_resolve import COLLEGE_ALIASES, resolve_colleges
from tools.program_tools import get_my_program, get_program_progress, plan_semester


def _catalog(tmp_path):
    """最小方案库：一个学部（两个专业），一个相近名专业。"""
    db = tmp_path / "course_data.db"
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE programs(id INTEGER PRIMARY KEY, name TEXT, college TEXT, grade TEXT);
        CREATE TABLE program_courses(id INTEGER PRIMARY KEY, program_id INTEGER, code TEXT,
                                     name TEXT, required TEXT, credit REAL, category TEXT, term TEXT);
        """
    )
    con.executemany("INSERT INTO programs(id, name, college, grade) VALUES(?,?,?,?)", [
        (1, "临床医学专业培养方案", "910生命科学与医学部", "2025级"),
        (2, "中国科大-协和医学英才班培养方案", "910生命科学与医学部", "2025级"),
        (3, "生物科学专业培养方案", "207生命科学学院", "2025级"),
        (4, "计算机科学与技术专业培养方案", "215计算机科学与技术学院", "2025级"),
    ])
    rows = []
    # 临床医学：3 门必修
    for idx, name in enumerate(["数学分析(B1)", "力学B", "人体解剖学"], start=1):
        rows.append((idx, 1, f"C{idx}", name, "必修", 3.0, "通修", "1秋"))
    # 协和英才班：2 门必修
    for idx, name in enumerate(["生物化学", "医学统计学"], start=100):
        rows.append((idx, 2, f"E{idx}", name, "必修", 2.0, "专业", "2春"))
    # 生物科学：1 门必修
    rows.append((200, 3, "B1", "普通生物学", "必修", 3.0, "专业", "1秋"))
    # 计算机：1 门必修
    rows.append((300, 4, "J1", "数据结构", "必修", 3.0, "专业", "2秋"))
    con.executemany(
        "INSERT INTO program_courses(id, program_id, code, name, required, credit, category, term) "
        "VALUES(?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()

    def _fake_cdb():
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        return conn

    return _fake_cdb


def test_college_alias_resolves_abbreviation(tmp_path) -> None:
    conn = _catalog(tmp_path)()
    try:
        assert resolve_colleges(conn, "生医部") == ["910生命科学与医学部"]
        assert resolve_colleges(conn, "生命科学与医学部") == ["910生命科学与医学部"]
        assert resolve_colleges(conn, "生物医学工程") == []
    finally:
        conn.close()
    assert COLLEGE_ALIASES["生医部"] == "生命科学与医学部"


def test_college_level_query_returns_candidates(tmp_path, monkeypatch) -> None:
    """学院级命中多个专业 → 候选确认，不替用户挑一个。"""
    monkeypatch.setattr(program_tools, "_cdb", _catalog(tmp_path))
    out = get_my_program.invoke({"major": "生医部", "grade": "2025级"})
    assert out["ambiguity"] is True
    assert out["college"] == "生命科学与医学"
    names = [c["name"] for c in out["candidates"]]
    assert names == ["临床医学专业培养方案", "中国科大-协和医学英才班培养方案"]
    assert "请先确认" in out["message"]
    # 进度与学期规划走同一前置分支，避免又给出 0 门占位
    assert get_program_progress.invoke({"major": "生医部", "grade": "2025级"})["ambiguity"] is True
    assert plan_semester.invoke({"major": "生医部", "grade": "2025级"})["ambiguity"] is True


def test_unknown_major_reports_not_found_with_suggestions(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(program_tools, "_cdb", _catalog(tmp_path))
    out = get_my_program.invoke({"major": "生物医学工程", "grade": "2025级"})
    assert out.get("not_found") is True
    assert "没有叫「生物医学工程」的专业" in out["message"]
    suggested = [c["name"] for c in out["suggestions"]]
    assert "生物科学专业培养方案" in suggested, "相近专业要给出来（生物/医学方向的真实专业）"
    assert not any("临床医学" == name for name in suggested[:1]), "建议按相关度排序，不是随便列"


def test_exact_major_still_resolves(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(program_tools, "_cdb", _catalog(tmp_path))
    out = get_my_program.invoke({"major": "临床医学", "grade": "2025级"})
    assert out["name"] == "临床医学专业培养方案"
    assert len(out["courses"]) == 3


def test_progress_needs_taken_courses_injected(tmp_path, monkeypatch) -> None:
    """差集能算出来：注入已修课程后 required_taken > 0（act 层负责注入）。"""
    monkeypatch.setattr(program_tools, "_cdb", _catalog(tmp_path))
    bare = get_program_progress.invoke({"major": "临床医学", "grade": "2025级"})
    assert bare["required_taken"] == 0 and bare["taken_courses_known"] is False

    done = get_program_progress.invoke({
        "major": "临床医学", "grade": "2025级",
        "taken_courses": ["数学分析(B1)", "力学B", "大学英语"],
    })
    assert done["taken_courses_known"] is True
    assert done["required_taken"] == 2
    assert done["required_total"] == 3
    assert [c["name"] for c in done["required_remaining"]] == ["人体解剖学"]


def test_enrich_program_args_injects_taken_courses(monkeypatch) -> None:
    """act 层必须把已修课程补进 get_program_progress 的参数（否则永远 0/N 占位）。"""
    state = {"user_profile": {"major": "计算机科学与技术", "grade": "2025级"}}
    monkeypatch.setattr(nodes, "_load_taken_courses", lambda sid: ["数学分析(B1)", "力学B"])
    monkeypatch.setattr(nodes, "_load_personal_tree", lambda sid: None)
    args = {"major": "临床医学", "grade": "2025级"}
    nodes._enrich_program_args(args, state, "PB25111691", include_taken=True)
    assert args["taken_courses"] == ["数学分析(B1)", "力学B"]


# ---------- 路由：认学院简称 + 补课问句 -------------------------------------

def _route_state(query: str, *, student_id: str = "PB25111691") -> dict:
    return {
        "query": query,
        "intent": "选课推荐",
        "student_id": student_id,
        "user_profile": {"major": "计算机科学与技术", "grade": "2025级"},
        "tool_results": [],
        "thought_log": [],
        "rounds": 0,
    }


def test_target_college_detects_abbreviation() -> None:
    assert nodes._detect_target_college("转去生医部要补哪些课", "计算机科学与技术") == "生命科学与医学部"
    assert nodes._detect_target_college("转去物理学院要补哪些课", "计算机科学与技术") == "物理学院"


def test_route_transfer_credit_question_uses_target_program() -> None:
    out = nodes._direct_tool_route(_route_state("转去生医部要补哪些课"))
    assert out is not None and out["decision"] == "call_tool"
    call = out["tool_calls"][0]
    assert call["tool"] == "get_program_progress"
    assert call["args"]["major"] == "生命科学与医学部", "目标是**别人**专业，不是本人方案"


# ---------- 摘要：歧义/未找到必须说清楚 -------------------------------------

def test_summary_surfaces_ambiguity_and_not_found() -> None:
    summary = nodes._build_tool_summary([{
        "tool": "get_program_progress", "status": "done",
        "result": {
            "ambiguity": True, "query": "生医部", "college": "生命科学与医学",
            "message": "「生医部」是学院/学部，下有 2 个专业方案：临床医学专业培养方案、中国科大-协和医学英才班培养方案。",
            "candidates": [
                {"name": "临床医学专业培养方案", "grade": "2025级", "course_count": 76, "college": "生命科学与医学"},
                {"name": "中国科大-协和医学英才班培养方案", "grade": "2025级", "course_count": 34, "college": "生命科学与医学"},
            ],
        },
    }])
    assert "临床医学专业培养方案" in summary and "协和医学英才班" in summary
    assert "不得替用户挑一个" in summary

    miss = nodes._build_tool_summary([{
        "tool": "get_program_progress", "status": "done",
        "result": {
            "not_found": True, "query": "生物医学工程",
            "message": "培养方案库里没有叫「生物医学工程」的专业/学院",
            "suggestions": [{"name": "生物科学专业培养方案", "grade": "2025级",
                             "course_count": 80, "college": "生命科学"}],
        },
    }])
    assert "没有叫「生物医学工程」的专业" in miss
    assert "生物科学专业培养方案" in miss
    assert "数据源不可用" in miss, "要提醒模型别把'没这个名字'说成'数据源不可用'"

# ---------- 目标专业 vs 目标学院优先级 ----------

def test_route_prefers_named_major_over_college() -> None:
    """用户已经说了"临床医学"，就不要再回学院候选确认。"""
    out = nodes._direct_tool_route(_route_state("转去生医部临床医学要补哪些课"))
    assert out["tool_calls"][0]["args"]["major"] == "临床医学"


def test_route_keeps_college_when_major_name_is_inside_it() -> None:
    """'物理学院' 里恰好含专业名 '物理学'，但不能把目标改成专业。"""
    nodes._KNOWN_COLLEGE_CACHE = ["计算机科学与技术学院", "物理学院"]
    try:
        out = nodes._direct_tool_route(_route_state("我想转去物理学院，对比一下物理学院的培养方案"))
        assert out["tool_calls"][0]["args"]["major"] == "物理学院"
    finally:
        nodes._KNOWN_COLLEGE_CACHE = []


def test_guess_major_only_for_short_named_phrases() -> None:
    assert nodes._guess_major_from_query("生物医学工程的培养方案") == "生物医学工程"
    assert nodes._guess_major_from_query("生物医学工程专业怎么样") == "生物医学工程"
    assert nodes._guess_major_from_query("帮我对比一下培养方案，我该选哪些课？") is None
    assert nodes._guess_major_from_query("我的培养方案是什么") is None
    assert nodes._guess_major_from_query("培养方案里有哪些必修课") is None
    assert nodes._guess_major_from_query("转去生医部要补哪些课") is None


def test_route_unknown_major_still_goes_to_program_tool() -> None:
    """库里没有的专业名（生物医学工程）也要走方案工具，才能拿到 not_found + 相近建议。"""
    out = nodes._direct_tool_route(_route_state("生物医学工程的培养方案"))
    assert out is not None and out["decision"] == "call_tool"
    assert out["tool_calls"][0]["args"]["major"] == "生物医学工程"
    assert out["tool_calls"][0]["tool"] in {"get_my_program", "get_program_progress"}

# ---------- 专业名猜测的判据（回归：不要再猜出"请根据我"） ----------

def test_looks_like_major_rejects_function_phrases() -> None:
    assert nodes._looks_like_major("请根据我") is False
    assert nodes._looks_like_major("帮我对比一下") is False
    assert nodes._looks_like_major("下学期") is False
    assert nodes._looks_like_major("培养") is False
    assert nodes._looks_like_major("") is False


def test_looks_like_major_accepts_real_majors() -> None:
    for name in ("生物医学工程", "临床医学", "计算机科学与技术", "信息与计算科学",
                 "人工智能", "核工程与核技术", "数学与应用数学", "英语",
                 # 这几个字面里含"常用功能字"但其实是合法专业名（逐个踩过）
                 "应用物理学", "考古学", "档案学", "营养学"):
        assert nodes._looks_like_major(name) is True, name


def test_guess_major_from_natural_prompt_is_none() -> None:
    """实测回归：「请根据我的培养方案和已修课程推荐下学期课程。」曾被猜成"请根据我"。"""
    assert nodes._guess_major_from_query("请根据我的培养方案和已修课程推荐下学期课程。") is None
    assert nodes._guess_major_from_query("生物医学工程的培养方案") == "生物医学工程"


def test_route_my_program_next_term_adds_plan_semester() -> None:
    """本人方案的"推荐下学期课程"：进度 + 按学期排课（major 必须是本人专业）。"""
    out = nodes._direct_tool_route(_route_state("请根据我的培养方案和已修课程推荐下学期课程。"))
    assert out is not None and out["decision"] == "call_tool"
    calls = [(c["tool"], c["args"]["major"]) for c in out["tool_calls"]]
    assert calls == [("get_program_progress", "计算机科学与技术"),
                     ("plan_semester", "计算机科学与技术")], calls
    plan = out["tool_calls"][1]["args"]
    assert plan["year_index"] == 2, "2025 级在 2026 秋（大二）提问 → 下个学期属第 2 学年"

# ---------- 下一个学期要指明是哪一组学期 ----------

def test_plan_semester_marks_next_term(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(program_tools, "_cdb", _catalog(tmp_path))
    out = plan_semester.invoke({"major": "中国科大-协和医学英才班", "grade": "2025级", "year_index": 2})
    from config import SEMESTER

    digit = str(SEMESTER["name"]).rsplit("-", 1)[-1]
    assert out["target_term"] == ("2春" if digit == "1" else "2秋"), \
        "秋季提问时，'下学期'是同属该学年的春季"
    assert [t["term"] for t in out["terms"]] == ["2春"]


def test_plan_semester_summary_marks_next_term() -> None:
    summary = nodes._build_tool_summary([{
        "tool": "plan_semester", "status": "done",
        "result": {
            "year_index": 2, "total_credits": 7.0, "target_term": "2春", "source": "generic",
            "terms": [
                {"term": "2秋", "courses": [{"name": "图论", "credit": 3.0, "required": "必修", "category": "专业基础"}]},
                {"term": "2春", "courses": [{"name": "操作系统原理与设计", "credit": 4.0, "required": "必修", "category": "专业基础"}]},
            ],
        },
    }])
    assert "2春" in summary
    assert "不得把同一学年的另一个学期" in summary, "必须提醒模型别把 2秋 当'下学期'"
