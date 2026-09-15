# -*- coding: utf-8 -*-
"""学号隐私模式的单一来源回归（`xiaowo_web/privacy_patterns.py`）。

背景（2026-09-15 定位两个真缺陷）：

1. **边界 `\\b` 在中文旁边不成立。** Python 3 的 `\\w` 含汉字，
   「我的学号是PB25111691」里汉字与 `P` 之间没有词边界，整条正则失效，
   脱敏与「个人问题识别」双双放行——而不带空格恰恰是中文最自然的写法。
2. **名单三处各抄一份且不一致。** `chat/privacy.py` 只认 `PB`；
   `evidence/privacy.py` 与 `evidence/url_security.py` 认
   `(?:PB|SA|BA|BE|MG|UG)`。于是研究生学号在「个人问题识别」里整类漏判，
   `SB`/`SG` 谁都不认，`MG`/`UG` 是无出处的猜测（`d5431db` 初始提交）。

本文件锁住：单一来源、16 个前缀全认、中文紧邻可识别、课程编号不误伤。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xiaowo_web.chat.privacy import is_personal_query
from xiaowo_web.evidence.privacy import (
    QuerySafetyError,
    contains_sensitive_text,
    sanitize_public_query,
)
from xiaowo_web.evidence.url_security import _SENSITIVE_VALUE
from xiaowo_web.privacy_patterns import STUDENT_ID_PREFIXES, STUDENT_ID_RE

_DB = Path(__file__).resolve().parents[2] / "data" / "course_data.db"

_ALL_PREFIXES = (
    "PB",                                    # 本科
    "BA", "BE", "BZ", "SA", "SB", "SM", "SF", "SG",  # 学历教育研究生（双证）
    "BJ",                                    # 非学历教育研究生（单证）
    "BC", "SC",                              # 代培研究生
    "BL", "SL", "CB", "CS",                  # 留学生与港澳台研究生
)


def _id(prefix: str) -> str:
    return f"{prefix}25111691"


# ── 单一来源 ──

def test_prefix_set_matches_the_authoritative_list():
    assert set(STUDENT_ID_PREFIXES) == set(_ALL_PREFIXES)
    assert len(STUDENT_ID_PREFIXES) == len(set(STUDENT_ID_PREFIXES)), "前缀不得重复"


def test_unverified_prefixes_removed():
    """MG/UG 出自 d5431db 的猜测、无任何出处，已移除。"""
    assert "MG" not in STUDENT_ID_PREFIXES
    assert "UG" not in STUDENT_ID_PREFIXES


def test_three_consumers_share_one_source():
    from xiaowo_web.evidence import privacy as evidence_privacy

    assert evidence_privacy._STUDENT_ID is STUDENT_ID_RE, "脱敏器必须复用同一编译实例"
    assert STUDENT_ID_RE.pattern in _SENSITIVE_VALUE.pattern, "URL 守卫必须复用同一 pattern"


# ── 边界：中文紧邻、无空格（原缺陷的回归）──

@pytest.mark.parametrize("text", [
    "我的学号是PB25111691",
    "我的学号是 PB25111691",
    "PB25111691",
    "学号PB25111691的成绩",
    "帮我查成绩PB25111691",
])
def test_matches_with_and_without_space(text):
    assert STUDENT_ID_RE.search(text), f"未匹配: {text}"


def test_redacts_when_adjacent_to_chinese():
    """回归：原先这条整条漏掉。"""
    assert "PB25111691" not in STUDENT_ID_RE.sub(" ", "我的学号是PB25111691")


def test_no_space_cjk_case_counts_as_personal_query():
    assert is_personal_query("我的学号是PB25111691") is True
    assert is_personal_query("我的学号是SA25111691") is True
    assert contains_sensitive_text("学号SA25111691") is True


def test_id_in_question_blocks_web_search_entirely():
    """问题里带学号时不是「脱敏放行」而是直接禁止联网（沿用既有设计，更强）。"""
    with pytest.raises(QuerySafetyError) as exc:
        sanitize_public_query("我的学号是PB25111691，帮我看下选课规则")
    assert exc.value.code == "PERSONAL_QUERY"


def test_profile_identity_still_redacted_out_of_question():
    """画像里的姓名/学号照旧被抹掉（这条是既有行为，不得回归）。"""
    sanitized = sanitize_public_query(
        "测试想查中国科大图书馆今天开放吗",
        {"name": "测试", "id": "PB25111691"},
    )
    assert "测试" not in sanitized.text
    assert "中国科大图书馆今天开放吗" in sanitized.text


# ── 16 个前缀全覆盖 ──

@pytest.mark.parametrize("prefix", _ALL_PREFIXES)
def test_every_prefix_is_recognised(prefix):
    sid = _id(prefix)
    assert STUDENT_ID_RE.search(sid), f"脱敏器漏了 {prefix}"
    assert contains_sensitive_text(sid), f"敏感文本判定漏了 {prefix}"
    assert is_personal_query(f"我的学号是{sid}"), f"个人问题识别漏了 {prefix}"


def test_previously_unrecognised_prefixes_now_detected():
    """SB（单独考试硕士/退役士兵）与 SG（MBA/MPA）原先两个正则都不认。"""
    for prefix in ("SB", "SG"):
        assert is_personal_query(f"我的学号是{_id(prefix)}"), prefix
        assert contains_sensitive_text(_id(prefix)), prefix


# ── 不误伤 ──

@pytest.mark.parametrize("code", [
    # 形态取自 program_courses 实测分布：AA#####/AA####/###A##/######/AA####A/AAAA####
    "CS1002A", "PHYS1004C", "FL1009", "EDUS1001",
    "MATH1001", "BA1002A", "SC2201", "01101", "001548", "011103",
])
def test_course_codes_are_not_matched(code):
    assert not STUDENT_ID_RE.search(code), f"课程编号被误伤: {code}"
    assert not contains_sensitive_text(code), f"课程编号被误判为敏感: {code}"


def test_real_course_codes_never_take_the_id_shape():
    """实测护栏：真实课程编号无一是「2 字母 + 8 数字」，故与学号模式不冲突。

    `CS10020001` 这种串**会**被学号模式命中——那是对的（`CS` 是港澳台硕士生前缀），
    它只是不构成任何真实课程编号形态。
    """
    assert STUDENT_ID_RE.search("CS10020001"), "CS 是真实前缀，8 位数字应当命中"


@pytest.mark.skipif(not _DB.exists(), reason="需要 data/course_data.db（部署数据，不入 git）")
def test_no_real_course_code_collides_with_id_pattern():
    import sqlite3

    conn = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    try:
        codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM program_courses") if r[0]]
    finally:
        conn.close()
    assert codes, "课程编号表为空，护栏失效"
    collided = [code for code in codes if STUDENT_ID_RE.search(code)]
    assert not collided, f"真实课程编号与学号模式冲突: {collided[:5]}"


def test_longer_digit_run_is_not_truncated():
    """9 位数字串不是学号，不得被截成 8 位。"""
    assert not STUDENT_ID_RE.search("PB251116912")


# ── URL 参数守卫同样收紧 ──

def test_url_guard_pattern_covers_id_adjacent_to_chinese():
    assert _SENSITIVE_VALUE.search("https://example.com/?q=我的学号是SA25111691")
    assert _SENSITIVE_VALUE.search("https://example.com/?q=SB25111691")


def test_url_guard_still_catches_credentials():
    assert _SENSITIVE_VALUE.search("https://example.com/?t=ST-super-secret")
    assert _SENSITIVE_VALUE.search("https://example.com/?a=Bearer abcdef0123456")
