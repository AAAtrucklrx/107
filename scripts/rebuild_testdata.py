# -*- coding: utf-8 -*-
"""从 xiaowo.db 缓存重建测试版个人数据备份 data.json（data.json 丢失后的恢复路径）。

用途: 测试版 app_test.py 的 %TEMP%\\xiaowo_personal\\data.json 丢失时运行本脚本重建。
生成 data.json 供伪登录使用（user 画像 + grades/courses 种子 + program_tree 注入），
数据源为数据库内真实缓存（PB25111691: 26 成绩 / 14 选课）。
输出: scripts/data/xiaowo_personal/data.json（gitignored；启动 app_test 时
设置 TEMP=F:\\小蜗\\scripts\\data 指向该目录即可被读取）。
用法: python scripts/rebuild_testdata.py
"""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SID = "PB25111691"
OUT = Path(__file__).resolve().parents[1] / "scripts" / "data" / "xiaowo_personal" / "data.json"

conn = sqlite3.connect(str(Path(__file__).resolve().parents[1] / "database" / "xiaowo.db"))
conn.row_factory = sqlite3.Row

grades = [dict(r) for r in conn.execute(
    "SELECT semester, course_name, credits, score, score_text, grade_point FROM student_grades WHERE student_id = ? ORDER BY semester", (SID,))]
courses = [dict(r) for r in conn.execute(
    "SELECT course_code, course_name, teacher, credits, time, location, semester FROM student_courses WHERE student_id = ?", (SID,))]
conn.close()

data = {
    "user": {
        "id": SID,
        "name": "测试",
        "major": "人工智能",
        "grade": "2025级",
    },
    "grades": grades,
    "courses": courses,
    "semester": courses[0].get("semester", "2025-2026-2") if courses else "2025-2026-2",
    "program_tree": {},  # 无真实方案树时注入空结构，方案类工具退化为库方案
}

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"OK: {OUT} ({len(grades)} grades / {len(courses)} courses, {OUT.stat().st_size} bytes)")
