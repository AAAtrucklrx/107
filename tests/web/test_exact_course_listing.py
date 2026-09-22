"""指定课程直查（列全部班级）的评分呈现纪律（2026-09-22）。

用户实测（09-21 23:06「线性代数课程推荐」）列了 69 个班级，其中：
- 末行是 `线性代数(B1) | 未知 | 4.0 | 0.0分·0条` —— 那是**评课社区里一条评课都没有**的行，
  被渲染成"0 分"，还因为没挂老师显示"未知"；
- 开头两行是 `线性代数 | 郭文彬 | 5.0分·1条`、`线性代数 | 陈效群 | 2.0分·1条` ——
  两条 1 条样本的裸「线性代数」排在 92 条样本的班级前面；
- 表里还有一批 `10.0分·1条` / `1.0分·1条`，没有任何样本量提示。

修法：
① 0 条样本的班级不进评分表，改为计数 + limitations 说明（"没有任何评课数据"≠"0 分"）；
② 样本量 < 3 条的班级排到最后，并带 `sample_note`；
③ 排序后真实大样本班级在表首。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import tools.advisor_tools as advisor_tools
from tools.advisor_tools import recommend_courses

SCHEMA = Path(__file__).resolve().parents[2] / "database" / "schema_course.sql"


def _db(tmp_path: Path):
    """迷你评课库：3 个大样本班 + 2 个 1 条样本班 + 1 个 0 条样本班（无老师）。

    (id, name, code, rating_avg, rate_count, teacher)
    """
    rows = [
        (1, "线性代数(B1)", "MATH100909", 8.5, 92, "史毅茜"),
        (2, "线性代数(B1)", "MATH100910", 8.2, 78, "乐珏"),
        (3, "线性代数(A1)", "MATH100402", 7.2, 66, "王新茂"),
        (4, "线性代数", "00151406", 2.0, 1, "陈效群"),        # 1 条样本、极低分
        (5, "线性代数(B1)", "MATH100907", 10.0, 1, "许金兴"),  # 1 条样本、满分
        (6, "线性代数(B1)", "MATH100917", 0.0, 0, None),       # 0 条样本、无老师
        (7, "线性代数(B1)", "MATH100918", 10.0, 1, None),      # 1 条样本、评课页没标老师
    ]
    path = tmp_path / "course_data.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    for cid, name, code, avg, count, _teacher in rows:
        conn.execute(
            "INSERT INTO courses(id, name, dept, code, credit, icourse_ids, rating_avg, rate_count)"
            " VALUES(?,?,?,?,?, '[]', ?, ?)",
            (cid, name, "数学科学学院", code, 4.0, avg, count),
        )
        conn.execute(
            "INSERT INTO course_rates(course_id, rating_sum, rating_count, rating_avg, dims_dist)"
            " VALUES(?,?,?,?, '{}')",
            (cid, avg * count, count, avg),
        )
    for idx, (_cid, _name, _code, _avg, _count, teacher) in enumerate(rows, start=1):
        if not teacher:
            continue
        conn.execute("INSERT INTO teachers(id, name) VALUES(?,?)", (idx, teacher))
        conn.execute("INSERT INTO course_teachers(course_id, teacher_id) VALUES(?,?)", (idx, idx))
    conn.commit()
    conn.close()

    def _fake_cdb():
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        return c

    return _fake_cdb


def _listing(tmp_path, monkeypatch, keywords=("线性代数",)) -> dict:
    monkeypatch.setattr(advisor_tools, "_cdb", _db(tmp_path))
    return recommend_courses.invoke({"profile": {"max_results": 10}, "keywords": list(keywords)})


def test_zero_sample_class_is_not_rendered_as_zero_score(tmp_path, monkeypatch) -> None:
    """0 条样本的班级不得进表（此前渲染成「未知 | 0.0分·0条」）。"""
    out = _listing(tmp_path, monkeypatch)
    assert out.get("source") == "exact_course"
    recs = out["recommendations"]
    assert all((item["rate_count"] or 0) > 0 for item in recs), "0 条样本的行不该出现在评分表里"
    assert out.get("unrated_class_count") == 1
    joined = "；".join(out["limitations"])
    assert "没有任何评课数据" in joined


def test_big_sample_classes_lead_the_table(tmp_path, monkeypatch) -> None:
    """排序：大样本班级在前，1 条样本的（含极低/满分）垫底并带 sample_note。"""
    out = _listing(tmp_path, monkeypatch)
    recs = out["recommendations"]
    counts = [item["rate_count"] for item in recs]
    assert counts == sorted(counts, reverse=True), f"应按样本量降序：{counts}"
    assert recs[0]["name"] == "线性代数(B1)" and recs[0]["rate_count"] == 92
    tail = recs[-2:]
    assert {item["rate_count"] for item in tail} == {1}
    assert all("样本量少" in (item.get("sample_note") or "") for item in tail)
    assert "样本量少于" in "；".join(out["limitations"])


def test_small_sample_rows_are_still_listed(tmp_path, monkeypatch) -> None:
    """小样本不是去掉，而是垫底 + 标注（班级确实存在，用户仍能看到）。"""
    out = _listing(tmp_path, monkeypatch)
    names = [item["name"] for item in out["recommendations"]]
    assert names.count("线性代数") == 1, "1 条样本的裸「线性代数」仍在表内，但不在首位"
    assert names[0] != "线性代数"


def test_listing_still_lists_all_classes_within_max_results(tmp_path, monkeypatch) -> None:
    """直查仍不受 max_results 截断（原有语义不变）。"""
    out = _listing(tmp_path, monkeypatch)
    assert len(out["recommendations"]) == 5, "7 行 - 1 行 0 样本 - 1 行无老师小样本 = 5"


def test_class_without_teacher_and_tiny_sample_is_not_listed(tmp_path, monkeypatch) -> None:
    """「未知 | 10.0分·1条」这类页不列表：既不认识老师、样本又不足 3 条。"""
    out = _listing(tmp_path, monkeypatch)
    assert all(item["teachers"] for item in out["recommendations"]), "表里不应再有无教师名的行"
    assert out.get("unlabeled_class_count") == 1
    assert "没有标注教师" in "；".join(out["limitations"])


def test_series_merge_unaffected_by_ordering_change(tmp_path, monkeypatch) -> None:
    """系列合并语义不变：问「线性代数」仍能得到 B1/A1 各系列班。"""
    out = _listing(tmp_path, monkeypatch)
    names = sorted({item["name"] for item in out["recommendations"]})
    assert names == ["线性代数", "线性代数(A1)", "线性代数(B1)"]
