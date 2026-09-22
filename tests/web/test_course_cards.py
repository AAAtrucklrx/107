"""课程卡片的「可点追问」与同名多页披露（2026-09-22）。

背景（用户实测）：同学看到「量子物理 1.5 分」后**不知道怎么继续查这门课**——
那个 1.5 分其实是评课社区里一个**未标注老师**的页面，同一门课还有 29 个页面（区间 1.5~10.0）。
用户定案：**评分口径不动**（那页的分数是真实数据），改为
① 摘要层如实披露"同名 N 页 / 区间 / 本分数的来处"，并给出下钻路径；
② 课程卡片可点：点某行/快捷按钮 → 前端把「量子物理有哪些老师？」这类追问填进输入框（只填充不发送）。

本文件锁住后端这两块的行为。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import agents.qa.nodes as nodes
import tools.advisor_tools as advisor_tools
from tools.advisor_tools import _same_name_pages, recommend_courses

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema_course.sql"


def _db(tmp_path: Path):
    """同名 3 页：9.0/50 条、5.0/30 条、1.0/1 条（末页样本 < 3，不参与区间）。"""
    path = tmp_path / "course_data.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    rows = [
        (1, "量子物理", "PHYS101002", 9.0, 50, "涂涛"),
        (2, "量子物理", "PHYS101001", 5.0, 30, "周祥发"),
        (3, "量子物理", "PHYS101008", 1.0, 1, None),
    ]
    for cid, name, code, avg, count, teacher in rows:
        conn.execute(
            "INSERT INTO courses(id, name, dept, code, credit, icourse_ids, rating_avg, rate_count)"
            " VALUES(?,?,?,?,?, '[]', ?, ?)",
            (cid, name, "物理学院", code, 3.0, avg, count),
        )
        conn.execute(
            "INSERT INTO course_rates(course_id, rating_sum, rating_count, rating_avg, dims_dist)"
            " VALUES(?,?,?,?, '{}')",
            (cid, avg * count, count, avg),
        )
        if teacher:
            conn.execute("INSERT INTO teachers(id, name) VALUES(?,?)", (cid, teacher))
            conn.execute("INSERT INTO course_teachers(course_id, teacher_id) VALUES(?,?)", (cid, cid))
    conn.commit()
    conn.close()

    def _fake_cdb():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return _fake_cdb


def _tree() -> dict:
    return {"children": [{"type": {"nameZh": "通修"}, "planCourses": [
        {"compulsory": True, "readableTerms": ["2春"],
         "course": {"code": "PHYS101002", "nameZh": "量子物理", "credits": 3.0}},
    ]}]}


def test_same_name_pages_reports_count_and_scored_range(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _db(tmp_path))
    conn = advisor_tools._cdb()
    try:
        info = _same_name_pages(conn, "量子物理")
    finally:
        conn.close()
    assert info["same_name_pages"] == 3
    assert info["same_name_scored_pages"] == 2, "样本 <3 的页不参与区间"
    assert info["score_range"] == "5.0~9.0"


def test_recommend_item_discloses_page_basis(tmp_path, monkeypatch) -> None:
    """课程级条目必须说清"这个分数只是其中一页"，并给出区间。"""
    monkeypatch.setattr(advisor_tools, "_cdb", _db(tmp_path))
    out = recommend_courses.invoke({
        "profile": {"max_results": 5, "gpa": 3.0}, "personal_tree": _tree(),
        "taken_courses": [], "target_term": "下学期", "current_year_index": 2,
    })
    item = out["recommendations"][0]
    assert item["same_name_pages"] == 3
    assert item["score_range"] == "5.0~9.0"
    note = item["page_note"]
    assert "同名共 3 页" in note and "5.0~9.0" in note
    assert "一个页面" in note, "不得声称是整门课的口径（口径由用户定案：不改）"


def test_single_page_course_has_no_disclosure_noise(tmp_path, monkeypatch) -> None:
    path = tmp_path / "course_data.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.execute("INSERT INTO courses(id, name, dept, code, credit, icourse_ids, rating_avg, rate_count)"
                 " VALUES(1,'图论','数学科学学院','CS3001',3.0,'[]',8.9,56)")
    conn.execute("INSERT INTO course_rates(course_id, rating_sum, rating_count, rating_avg, dims_dist)"
                 " VALUES(1,498.0,56,8.9,'{}')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(advisor_tools, "_cdb", lambda: sqlite3.connect(path))
    conn = advisor_tools._cdb()
    try:
        assert _same_name_pages(conn, "图论") == {"same_name_pages": 1}
    finally:
        conn.close()


# ── 卡片：逐行追问问句 + 卡片级动作 ──────────────────────────

def test_recommend_card_carries_row_questions() -> None:
    tables = nodes._tool_to_structured([{
        "tool": "recommend_courses", "status": "done",
        "result": {"recommendations": [
            {"name": "量子物理", "credit": 3.0, "rating_avg": 1.5, "rate_count": 83,
             "teachers": [], "reasons": []},
            {"name": "图论", "credit": 3.0, "rating_avg": 8.9, "rate_count": 56,
             "teachers": [{"name": "徐宏力"}], "reasons": []},
        ]},
    }])
    card = tables[0]
    # 2026-09-22 调整：不再统一问「有哪些老师」，改为按该行老师问这个班（无则问这门课）
    assert card["row_questions"] == ["量子物理怎么样？", "图论 徐宏力 老师怎么样？"]
    assert len(card["row_questions"]) == len(card["rows"]), "追问问句必须与行一一对应"
    assert card["rows"][0][1] == "（该页未标注教师）", "无教师时不得写『未知』"


def test_analyze_teacher_course_card_has_row_questions_and_actions() -> None:
    tables = nodes._tool_to_structured([{
        "tool": "analyze_teacher", "status": "done",
        "result": {"course": "量子物理", "teachers": [
            {"name": "涂涛", "dept": "物理学院", "rating_avg": 9.8, "rate_count": 62, "dims_mode": {}},
        ]},
    }])
    card = tables[0]
    assert card["row_questions"] == ["量子物理 涂涛 老师怎么样？"]
    assert card["actions"] == [{"label": "量子物理 的评论原文", "question": "量子物理的评论"}]


def test_non_course_card_stays_plain() -> None:
    """非课程卡片不得带可点追问（避免到处都能点）。"""
    tables = nodes._tool_to_structured([{
        "tool": "find_empty_room", "status": "done",
        "result": {"rooms": [{"name": "3C301", "free_slots": "14:00-16:00"}]},
    }])
    if tables:  # 该工具的卡结构可能随实现变化；有卡就必须是纯展示
        assert "row_questions" not in tables[0]
        assert "actions" not in tables[0]


def test_summary_adds_drilldown_hint_and_page_disclosure() -> None:
    text = nodes._build_tool_summary([{
        "tool": "recommend_courses", "status": "done",
        "result": {
            "recommendations": [{
                "name": "量子物理", "credit": 3.0, "rating_avg": 1.5, "rate_count": 83,
                "teachers": [], "reasons": [], "terms": ["2022春"],
                "community_url": "https://icourse.club/course/1234/",
                "same_name_pages": 30, "score_range": "1.5~10.0",
                "page_note": "本分数只代表「量子物理」在评课社区的一个页面",
            }],
            "groups": {"required": [], "elective": [], "exploratory": []},
        },
    }])
    assert "下钻指引" in text, "必须把『怎么继续查』交给模型转述"
    assert "该课在评课社区共 30 页" in text
    assert "1.5~10.0" in text
    assert "（该页未标注教师）" in text
    assert "评课页: https://icourse.club/course/1234/" in text


def test_teacher_row_question_covers_every_row_kind() -> None:
    """每一行都要能问，且问的是「这个班」（用户要求：合教/异常名也可问）。"""
    from agents.qa.nodes import _teacher_row_question

    assert _teacher_row_question("量子物理", "涂涛") == "量子物理 涂涛 老师怎么样？"
    assert _teacher_row_question("量子物理", "陈东明, 李群祥") == "量子物理 陈东明、李群祥 合教的班怎么样？"
    assert _teacher_row_question("量子物理", "（未标注老师）") == "量子物理 未标注老师的那个班怎么样？"
    assert _teacher_row_question("量子物理", "英）（严以京, 胡水明") == "量子物理 英）（严以京, 胡水明 这个班怎么样？"
    # 任何一行都必须拿到可用的追问句（不能有不可点的行）
    for name in ("涂涛", "陈东明, 李群祥", "（未标注老师）", "未知", "英）（严以京, 胡水明", ""):
        assert _teacher_row_question("量子物理", name), f"{name!r} 也该能问"


def test_analyze_card_questions_align_with_every_row() -> None:
    tables = nodes._tool_to_structured([{
        "tool": "analyze_teacher", "status": "done",
        "result": {"course": "量子物理", "teachers": [
            {"name": "涂涛", "rating_avg": 9.8, "rate_count": 62, "dims_mode": {}},
            {"name": "（未标注老师）", "rating_avg": 1.5, "rate_count": 83, "dims_mode": {}},
            {"name": "陈东明, 李群祥", "rating_avg": 9.6, "rate_count": 11, "dims_mode": {}},
        ]},
    }])
    card = tables[0]
    assert len(card["row_questions"]) == len(card["rows"]) == 3
    assert card["row_questions"][0] == "量子物理 涂涛 老师怎么样？"
    assert card["row_questions"][1] == "量子物理 未标注老师的那个班怎么样？", "未标注老师的班也要能问"
    assert card["row_questions"][2] == "量子物理 陈东明、李群祥 合教的班怎么样？"
    assert all(card["row_questions"]), "不允许有不可点的行"


def test_analyze_teacher_course_payload_discloses_pages_and_link(tmp_path, monkeypatch) -> None:
    """analyze_teacher 课程模式也要给评课页链接与同名页披露（同学据此下钻）。"""
    path = tmp_path / "course_data.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    for cid, avg, count in ((1, 9.8, 60), (2, 5.0, 30), (3, 1.5, 80)):
        conn.execute("INSERT INTO courses(id, name, dept, code, credit, icourse_ids, rating_avg, rate_count)"
                     " VALUES(?, '量子物理','物理学院',?,3.0,?,?,?)",
                     (cid, f"PHYS{1000 + cid}", f"[{cid}]", avg, count))
        conn.execute("INSERT INTO course_rates(course_id, rating_sum, rating_count, rating_avg, dims_dist)"
                     " VALUES(?,?,?,?, '{}')", (cid, avg * count, count, avg))
    conn.commit()
    conn.close()

    def _fake_cdb():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(advisor_tools, "_cdb", _fake_cdb)
    out = advisor_tools.analyze_teacher(course="量子物理") if not hasattr(
        advisor_tools.analyze_teacher, "invoke") else advisor_tools.analyze_teacher.invoke({"course": "量子物理"})
    assert out["community_url"].startswith("https://icourse.club/course/")
    assert out["same_name_pages"] == 3
    assert out["score_range"] == "1.5~9.8"
    assert "加权" in out["page_note"]


def test_recommend_card_questions_are_class_scoped_not_generic() -> None:
    """用户实测（2026-09-22）：「电磁学C 点击后还是"有哪些老师"」——推荐卡不允许再出这句。"""
    tables = nodes._tool_to_structured([{
        "tool": "recommend_courses", "status": "done",
        "result": {"recommendations": [
            {"name": "电磁学C", "credit": 3.0, "rating_avg": 8.0, "rate_count": 40,
             "teachers": [{"name": "张三"}], "reasons": []},
            {"name": "量子物理", "credit": 3.0, "rating_avg": 7.2, "rate_count": 457,
             "teachers": [{"name": "陈东明"}, {"name": "李群祥"}], "reasons": []},
            {"name": "计算方法", "credit": 3.0, "rating_avg": 4.6, "rate_count": 290,
             "teachers": [], "reasons": []},
        ]},
    }])
    questions = tables[0]["row_questions"]
    assert questions == [
        "电磁学C 张三 老师怎么样？",
        "量子物理 陈东明、李群祥 合教的班怎么样？",
        "计算方法怎么样？",
    ]
    assert not any("有哪些老师" in (q or "") for q in questions), "不允许再出现泛化问法"


def test_search_card_question_stays_course_level() -> None:
    tables = nodes._tool_to_structured([{
        "tool": "search_courses", "status": "done",
        "result": {"courses": [{"course_name": "电磁学C", "course_code": "PHYS2001", "dept": "物理学院"}]},
    }])
    assert tables[0]["row_questions"] == ["电磁学C怎么样？"]
