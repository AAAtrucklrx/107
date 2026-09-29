# -*- coding: utf-8 -*-
"""本地评课库版「全校开课查询」（2026-09-29）。

为什么要单独一个模块：`tools/course_tools.py` 里的模块级名字会在注册/包装流程中
被替换成 StructuredTool，函数内部用全局名调用会报「'StructuredTool' object is not callable」。
放在独立模块里、函数内导入，拿到的永远是真正的函数。

口径：课程编号/名称/教师/开课院系/学分/课程类型/开课学期（评课社区数据）。
只有教务才有的「上课时间与地点」不在此口径内，返回为空并在 note 里说明。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from utils.teacher_name import clean_names

_REVIEW_DB = Path(__file__).resolve().parents[1] / "data" / "course_data.db"


def search_lessons_local(student_id: str = None, keyword: str = None, limit: int = 30) -> dict:
    kw = (keyword or "").strip()
    like = f"%{kw}%"
    try:
        conn = sqlite3.connect(f"file:{_REVIEW_DB}?mode=ro", uri=True)
    except Exception as exc:  # noqa: BLE001
        return {"student_id": student_id, "lessons": [], "count": 0, "source": "local_cache",
                "message": f"本地评课库不可用：{type(exc).__name__}"}
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT c.code, c.name, c.dept, c.credit, c.course_type, c.rating_avg,
                   (SELECT group_concat(t.name, '、') FROM course_teachers ct
                      JOIN teachers t ON t.id = ct.teacher_id
                     WHERE ct.course_id = c.id) AS teachers,
                   (SELECT group_concat(term) FROM course_terms
                     WHERE course_id = c.id) AS terms
              FROM courses c
             WHERE ? = ''
                OR c.name LIKE ? OR c.code LIKE ? OR c.dept LIKE ?
                OR EXISTS (SELECT 1 FROM course_teachers ct JOIN teachers t ON t.id = ct.teacher_id
                            WHERE ct.course_id = c.id AND t.name LIKE ?)
             ORDER BY c.rating_avg DESC, c.id
             LIMIT ?
            """,
            (kw, like, like, like, like, limit),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        return {"student_id": student_id, "lessons": [], "count": 0, "source": "local_cache",
                "message": f"本地开课数据查询失败：{type(exc).__name__}: {str(exc)[:80]}"}
    finally:
        conn.close()
    lessons = [{
        "code": r["code"] or "",
        "course_name": r["name"] or "",
        "teachers": clean_names(r["teachers"]),
        "schedule": "",
        "open_department": r["dept"] or "",
        "credits": r["credit"],
        "course_type": r["course_type"] or "",
        "rating_avg": r["rating_avg"],
        # SQLite 的 group_concat 不支持 DISTINCT+分隔符，去重在这里做
        "semesters": sorted({x for x in (r["terms"] or "").split(",") if x}),
    } for r in rows]
    return {
        "student_id": student_id,
        "lessons": lessons,
        "count": len(lessons),
        "source": "local_cache",
        "note": "本地评课库口径：课程/教师/开课院系/学期；具体上课时间与地点需统一认证登录后查教务。",
    }
