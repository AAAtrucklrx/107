"""演示身份的个人培养方案接线 + 学校级方案不得混入个人数据。

背景（2026-09-16 核实）：demo 学号没有 CAS 登录态（has_cas() 恒为 False），
原先拿不到个人方案树，只能落回全量库方案。而全量库 program 3011（2025级 计算机）
曾被灌入该生个人方案树 —— 222 门 = 学校级 85 + 体育选项池 119 + 个人英语 L3 18，
于是"学校级方案"里长期混着个人数据，且答出来还标着 source=generic。

本文件锁住两件事：
1. demo 的个人方案走 fixtures/demo/<学号>_tree.json 这条离线通道（source=personal）；
2. 学校级方案不得再出现个人英语分级（英语通修L1..L4）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TREE_FIX = ROOT / "fixtures" / "demo" / "PB25111691_tree.json"
DEMO_FIX = ROOT / "fixtures" / "demo" / "PB25111691.json"
COURSE_DB = ROOT / "data" / "course_data.db"
JW_PROG_3011 = ROOT / "scripts" / "data" / "programs_jw" / "3011.json"
DEMO_ID = "PB25111691"


@pytest.fixture(autouse=True)
def _clear_cache():
    from agents.qa import nodes
    nodes.clear_personal_tree_cache(DEMO_ID)
    yield
    nodes.clear_personal_tree_cache(DEMO_ID)


# ── 1. 离线方案树 fixture 本身 ──────────────────────────

def test_demo_tree_fixture_is_self_describing():
    """方案树文件必须自带 student_id 自证归属（防串他人数据）。"""
    payload = json.loads(TREE_FIX.read_text(encoding="utf-8"))
    assert payload["student_id"] == DEMO_ID
    assert isinstance(payload["tree"], dict)


def test_demo_tree_parses_to_the_personal_plan():
    """离线树解析出的就是个人方案：222 门，含本人英语 L3。"""
    from tools.program_tools import _parse_tree
    payload = json.loads(TREE_FIX.read_text(encoding="utf-8"))
    courses = _parse_tree(payload["tree"])
    assert len(courses) == payload["course_count"] == 222
    eng = [c for c in courses if "英语通修L" in (c.get("category") or "")]
    assert len(eng) == 18
    assert {re.search(r"英语通修(L\d)", c["category"]).group(1) for c in eng} == {"L3"}


def test_demo_tree_rejects_another_student():
    from agents.qa.nodes import _load_demo_personal_tree
    assert _load_demo_personal_tree(DEMO_ID) is not None
    assert _load_demo_personal_tree("PB00000000") is None


# ── 2. _load_personal_tree 的演示回退 ────────────────────

def test_load_personal_tree_falls_back_to_demo_fixture():
    """无 CAS 登录态时，demo 学号仍应拿到个人方案树。"""
    from agents.qa.nodes import _load_personal_tree
    assert _load_personal_tree(DEMO_ID) is not None


def test_load_personal_tree_unknown_student_is_none():
    """回退不能变成后门：只有自带 fixture 的学号才走这条路。"""
    from agents.qa.nodes import _load_personal_tree
    assert _load_personal_tree("PB00000000") is None


def test_demo_ask_for_own_major_yields_personal_source():
    """端到端：带上演示树时，培养方案必须标成个人来源。"""
    from tools.program_tools import get_my_program
    payload = json.loads(TREE_FIX.read_text(encoding="utf-8"))
    out = get_my_program.invoke({
        "major": "计算机科学与技术", "grade": "2025级", "personal_tree": payload["tree"],
    })
    assert out["personal"] is True
    assert out["source"] == "personal"
    assert len(out["courses"]) == 222


# ── 3. demo fixture 的标称必须与内容一致 ─────────────────

def test_demo_fixture_program_is_labelled_personal():
    """内容本是个人方案，标称就不能是 generic —— 否则页面自相矛盾。"""
    prog = json.loads(DEMO_FIX.read_text(encoding="utf-8"))["program"]
    assert prog["personal"] is True
    assert prog["source"] == "personal"
    assert len(prog["courses"]) == 222


# ── 4. 学校级方案不得混入个人数据 ────────────────────────

@pytest.mark.skipif(not COURSE_DB.exists(), reason="需要 data/course_data.db（部署数据，不入 git）")
def test_school_level_db_has_no_personal_english_level():
    """英语分级是学生个人的分级结果；学校级方案库里一个都不该有。"""
    con = sqlite3.connect(f"file:{COURSE_DB}?mode=ro", uri=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM program_courses WHERE category LIKE '%英语通修L%'"
        ).fetchone()[0]
    finally:
        con.close()
    assert n == 0, f"学校级方案库混入了 {n} 行个人英语分级"


@pytest.mark.skipif(
    not (COURSE_DB.exists() and JW_PROG_3011.exists()),
    reason="需要 data/course_data.db 与 scripts/data/programs_jw/3011.json",
)
def test_program_3011_matches_school_level_source():
    """3011（2025级 计算机）必须等于 jw 学校级原始数据，而不是被个人方案顶掉。"""
    jw = json.loads(JW_PROG_3011.read_text(encoding="utf-8"))
    con = sqlite3.connect(f"file:{COURSE_DB}?mode=ro", uri=True)
    try:
        n = con.execute(
            "SELECT COUNT(*) FROM program_courses WHERE program_id=3011"
        ).fetchone()[0]
    finally:
        con.close()
    assert n == len(jw["courses"]) == 85
