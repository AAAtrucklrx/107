"""课程推荐：伪关键词清零修复（2026-09-22）。

用户实测（09-19 线上）：「下学期给我推荐一些课程」得到"0 门"空答，两条局限为
「培养方案中有 47 门课程缺少评课映射，未参与评分排序。」+
「指定课程范围没有命中可核验候选，本次未放宽该硬条件。」

根因：`_extract_course_keywords` 的正则把「推荐 / 课 / 课程」**前面的任意 2~12 字**
当成课程名——「下学期给我推荐一些课程」切出 `["下学期给我", "一些"]`，
「帮我推荐几门课」切出 `["帮我", "几门"]`；而 keywords 在推荐里是课程范围
**硬过滤且永不放宽**（`_recommend_grouped._hard_match`）→ 候选被直接清零。

本文件锁住三层修复：
① 抽取器查库校验（`_looks_like_course_name`）：伪关键词不再产生；
② 工具侧守卫（`recommend_courses`）：哪儿都匹配不到的检索词不参与硬过滤；
③ 空池兜底（`_program_term_courses`）：真·硬条件清空候选时，另开字段给目标学期的
   方案课程清单（含无评课映射的课），**推荐列表仍为空、硬条件未被放宽**。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agents.qa.nodes import _extract_profile
import tools.advisor_tools as advisor_tools
from tools.advisor_tools import course_name_exists, recommend_courses

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema_course.sql"

# 迷你个人方案树（教务 jw 实测形态：children 递归 + planCourses[].course）
# 2春: 必修「操作系统原理与设计」(有评课)、必修「没有评课映射的必修课」(无评课)、选修「图论」；
# 1秋: 选修「早就修过的课」(既非目标学期，也已在已修里)。
TREE = {
    "children": [
        {
            "type": {"nameZh": "专业核心课程"},
            "planCourses": [
                {"compulsory": True, "readableTerms": ["2春"],
                 "course": {"code": "01117401", "nameZh": "操作系统原理与设计", "credits": 4.0}},
                {"compulsory": True, "readableTerms": ["2春"],
                 "course": {"code": "PHYS0001", "nameZh": "没有评课映射的必修课", "credits": 2.0}},
                {"compulsory": False, "readableTerms": ["2春"],
                 "course": {"code": "CS3001", "nameZh": "图论", "credits": 3.0}},
                {"compulsory": False, "readableTerms": ["1秋"],
                 "course": {"code": "OLD001", "nameZh": "早就修过的课", "credits": 3.0}},
            ],
        }
    ]
}

TAKEN = ["早就修过的课", "高等数学"]


def _mini_db(tmp_path: Path):
    """最小评课库：4 门课（含一门只在库里、不在方案里的方向课）。"""
    db = tmp_path / "course_data.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.executemany(
        "INSERT INTO courses(id, name, dept, code, credit, icourse_ids, rating_avg, rate_count)"
        " VALUES(?,?,?,?,?,?,?,?)",
        [
            (1, "操作系统原理与设计", "计算机科学与技术学院", "01117401", 4.0, "[11]", 8.0, 40),
            (2, "图论", "数学科学学院", "CS3001", 3.0, "[12]", 9.0, 3),
            (3, "量子信息导论", "物理学院", "PHYS9001", 3.0, "[13]", 8.5, 6),
            (4, "数学分析(B1)", "数学科学学院", "MATH1001", 6.0, "[14]", 7.5, 40),
        ],
    )
    conn.executemany(
        "INSERT INTO course_rates(course_id, rating_sum, rating_count, rating_avg, dims_dist)"
        " VALUES(?,?,?,?,?)",
        [
            (1, 320.0, 40, 8.0, json.dumps({"难度": {"中等": 30, "困难": 10}}, ensure_ascii=False)),
            (2, 27.0, 3, 9.0, "{}"),
            (3, 51.0, 6, 8.5, "{}"),
            (4, 300.0, 40, 7.5, "{}"),
        ],
    )
    conn.commit()
    conn.close()

    def _fake_cdb():
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        return c

    return _fake_cdb


def _call(**kwargs) -> dict:
    base = dict(
        major="计算机科学与技术", grade="2025级", gpa=3.27,
        taken_courses=list(TAKEN), personal_tree=TREE,
        target_term="下学期", current_year_index=2, max_results=10,
    )
    base.update(kwargs)
    return recommend_courses.invoke(base)


# ── ① 课程名校验 ──────────────────────────────────────────────

def test_course_name_exists_separates_real_courses_from_noise(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    for term in ("图论", "操作系统原理与设计", "量子信息", "数学分析", "量子信息导论"):
        assert course_name_exists(term) is True, f"{term} 应被认成真实课程名/方向词"
    # 09-19 线上被误当课程名的那些词
    for term in ("下学期给我", "一些", "帮我", "几门", "推荐下学期", "请根据我的培养方案和已修"):
        assert course_name_exists(term) is False, f"{term} 是解析噪声，不该算课程名"


def test_course_name_exists_is_lenient_when_db_unavailable(monkeypatch) -> None:
    """库不可用时不做判定（返回 True），避免无法核验时改变既有语义。"""
    def _boom():
        raise sqlite3.Error("课程数据库不可用")

    monkeypatch.setattr(advisor_tools, "_cdb", _boom)
    assert course_name_exists("下学期给我") is True


# ── ② 工具侧守卫：解析噪声不再清零候选 ────────────────────────

def test_parsing_noise_keywords_no_longer_empty_the_pool(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    out = _call(keywords=["下学期给我", "一些"])
    names = [item["name"] for item in out["recommendations"]]
    assert names, "伪关键词被丢弃后应正常出推荐，不再是 0 门"
    assert "操作系统原理与设计" in names and "图论" in names
    joined = "；".join(out["limitations"])
    assert "已忽略不是课程名的检索词" in joined
    assert "下学期给我" in joined and "一些" in joined
    assert "指定课程范围没有命中可核验候选" not in joined, "不是候选清零，而是噪声被忽略"


def test_noise_detection_does_not_flag_real_course_names() -> None:
    """词首/词尾锚定，别误伤真课程名（"思想道德与法治" 不以"想"开头）。"""
    for term in ("思想道德与法治", "计算机组成原理", "大学英语", "概率论与数理统计",
                 "图论", "散打I", "数学分析", "量子信息", "人工智能"):
        assert advisor_tools.is_keyword_noise(term) is False, f"{term} 不该被判成噪声"
    for term in ("下学期给我", "一些", "帮我", "几门", "推荐下学期",
                 "请根据我的培养方案和已修", "有什么", "我想"):
        assert advisor_tools.is_keyword_noise(term) is True, f"{term} 是问句功能词，应判为噪声"


def test_user_named_course_missing_from_db_stays_hard(tmp_path, monkeypatch) -> None:
    """边界回归（scripts/verify_tools.py「过窄课程范围不放宽」）：
    用户真报了一个库里没有的课名 → 仍按硬条件处理、如实回 0 门，绝不偷偷换成别的推荐。"""
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    out = _call(keywords=["绝对不存在的课程词XYZ"])
    assert out["recommendations"] == []
    assert out.get("keyword_fallback") is False
    assert "绝对不存在的课程词XYZ" not in "；".join(out["limitations"]), "不得被当成噪声丢弃"


def test_wrong_keyword_extracted_from_real_query_is_harmless(tmp_path, monkeypatch) -> None:
    """端到端：09-19 原句走 `_extract_profile` 后不再产出伪关键词。"""
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    keywords = _extract_profile("下学期给我推荐一些课程", {})["keywords"]
    assert keywords == []
    out = _call(keywords=keywords)
    assert out["recommendations"], "原句应能正常推荐"


def test_extractor_keeps_real_course_names(tmp_path, monkeypatch) -> None:
    """既有行为不回归：真课程名照旧提取（scripts/verify_nodes.py 的 k1~k5 语义）。"""
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    assert _extract_profile("图论课推荐", {})["keywords"] == ["图论"]
    assert _extract_profile("量子信息怎么样", {})["keywords"] == ["量子信息"]
    assert _extract_profile("推荐AI方向的选修课", {})["keywords"] == []
    assert _extract_profile("有什么推荐", {})["keywords"] == []


# ── ③ 空池兜底：真·硬条件清空时给方案课程清单 ────────────────

def test_real_direction_absent_from_program_falls_back_to_term_list(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    out = _call(keywords=["量子信息"])  # 库里真有（前缀命中），但本人方案里没有
    assert out["recommendations"] == [], "硬条件不放宽：推荐列表仍为空"
    fb = out.get("program_term_courses")
    assert fb and fb["target_term"] == "2春"
    assert fb["count"] == 3, "2春 且未修读的方案课程：2 必修 + 1 选修"
    assert [i["name"] for i in fb["required"]] == ["操作系统原理与设计", "没有评课映射的必修课"]
    assert [i["name"] for i in fb["elective"]] == ["图论"]
    assert fb["credits"] == 9.0
    # 无评课映射的课也要列出来，并如实标 rated=False
    rated_flags = {i["name"]: i["rated"] for i in fb["required"] + fb["elective"]}
    assert rated_flags["没有评课映射的必修课"] is False
    assert rated_flags["操作系统原理与设计"] is True
    # 已修 / 非目标学期的课不进清单
    assert "早就修过的课" not in rated_flags
    assert "指定方向未命中可核验候选" in "；".join(out["limitations"])


def test_no_fallback_when_recommendations_exist(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    out = _call()
    assert out["recommendations"]
    assert "program_term_courses" not in out, "有推荐就不需要兜底清单"


# ── ④ 边界：课程范围仍是硬条件 ────────────────────────────────

def test_course_scope_required_is_still_hard(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    out = _call(course_scope="required")
    names = [item["name"] for item in out["recommendations"]]
    assert "操作系统原理与设计" in names
    assert "图论" not in names, "明确只要必修时，方案内选修不得回填"


def test_real_course_name_still_takes_exact_lookup_path(tmp_path, monkeypatch) -> None:
    """真课程名仍走"指定课程直查"，不受本次修复影响。"""
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    out = _call(keywords=["数学分析"])
    assert out.get("source") == "exact_course"
    assert [item["name"] for item in out["recommendations"]] == ["数学分析(B1)"]


# ── 2026-09-22 增补：剥掉尾部角色词（「线性代数（B1）老师推荐」） ──────────
# 用户实测（09-21 23:06）：问「线性代数（B1）老师推荐」，正则抓到整词「线性代数（B1）老师」，
# 查库校验失败 → 关键词被整条丢掉 → 退化成"没有可核验候选"的空答（还转去联网）。
# 真正该丢的是尾巴上的「老师」，课程名要留住。

def test_extractor_strips_teacher_role_suffix(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    assert _extract_profile("操作系统原理与设计老师推荐", {})["keywords"] == ["操作系统原理与设计"]
    assert _extract_profile("数学分析(B1)老师推荐", {})["keywords"] == ["数学分析(B1)"]
    assert _extract_profile("图论老师怎么样", {})["keywords"] == ["图论"]


def test_keyword_candidates_prefers_longest_valid_form() -> None:
    """原词优先，剥尾后能命中才用剥尾的（避免把真课程名的尾巴削掉）。"""
    from agents.qa.nodes import _keyword_candidates

    assert _keyword_candidates("线性代数（B1）老师") == ["线性代数（B1）老师", "线性代数（B1）"]
    assert _keyword_candidates("图论") == ["图论"]
    assert _keyword_candidates("思想道德与法治") == ["思想道德与法治"]


def test_teacher_question_reaches_course_listing(tmp_path, monkeypatch) -> None:
    """剥尾后不再是空关键词：同一句能落到该课程的各班（而不是"0 门"）。"""
    monkeypatch.setattr(advisor_tools, "_cdb", _mini_db(tmp_path))
    keywords = _extract_profile("操作系统原理与设计老师推荐", {})["keywords"]
    assert keywords == ["操作系统原理与设计"]
    out = _call(keywords=keywords)
    assert out.get("source") == "exact_course"
    assert [item["name"] for item in out["recommendations"]] == ["操作系统原理与设计"]
