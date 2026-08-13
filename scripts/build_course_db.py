# -*- coding: utf-8 -*-
"""构建选课推荐数据库 data/course_data.db。

输入: scripts/data/icourse_list.json + scripts/data/raw/*.json + scripts/data/programs_jw/*.json
输出: data/course_data.db（8 表, schema 见 database/schema_course.sql）

用法: python scripts/build_course_db.py [--db data/course_data.db]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_FILE = PROJECT_ROOT / "database" / "schema_course.sql"
DATA_DIR = PROJECT_ROOT / "scripts" / "data"
RAW_DIR = DATA_DIR / "raw"
PROG_DIR = DATA_DIR / "programs_jw"

# 维度文字 → 映射分（1-10, 分数越高越好, 仅作参考展示）
DIFF_MAP = {"简单": 10, "中等": 6.5, "困难": 3.0}
HW_MAP = {"很少": 10, "少": 8.0, "中等": 6.5, "多": 4.0, "很多": 2.0}
SCORE_MAP = {"很差": 2.0, "差": 4.0, "一般": 6.0, "好": 8.0, "超好": 10.0}
GAIN_MAP = {"没有": 2.0, "少": 4.0, "一般": 6.0, "多": 8.0, "很多": 10.0}

TERM_RE = re.compile(r"20(\d{2})([春秋夏])")
TERM_CN = {"秋": 1, "春": 2, "夏": 3}


def term_to_yyyyn(term_text: str) -> int | None:
    m = TERM_RE.search(term_text or "")
    if not m:
        return None
    # YYYYN: 2026秋 → 20261; 注意 (2000+年号) 需整体乘 10 再加学期
    return (2000 + int(m.group(1))) * 10 + TERM_CN[m.group(2)]


def load_raw() -> list[dict]:
    """加载全部课程详情 JSON。"""
    out = []
    for f in sorted(RAW_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as e:
            print(f"跳过损坏文件 {f.name}: {e}")
    return out


def load_programs() -> list[dict]:
    """加载 jw 培养方案 JSON（scripts/data/programs_jw/*.json）。

    字段对齐: jw 原始字段 department -> college，其余 name/grade/courses 与
    下游逻辑一致（grade 已是 "2015级" 格式，courses 中 term 为 "2秋" 等）。
    """
    out = []
    for f in sorted(PROG_DIR.glob("*.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"跳过损坏文件 {f.name}: {e}")
            continue
        # jw 权威字段映射: college <- department
        p.setdefault("college", p.get("department", ""))
        out.append(p)
    return out


def build(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    cur = conn.cursor()

    raw = load_raw()
    print(f"加载课程详情: {len(raw)} 门")

    # ── 1. courses: 按 (name, dept) 合并 ──
    groups: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "icourse_ids": [], "code": "", "credit": None, "type": "", "level": "",
        "raws": [],
    })
    for d in raw:
        key = (d.get("course_name", "").strip(), d.get("dept", "").strip())
        if not key[0]:
            continue
        g = groups[key]
        g["icourse_ids"].append(d["id"])
        g["raws"].append(d)
        if not g["code"] and d.get("code"):
            g["code"] = d["code"]
        if g["credit"] is None and d.get("credit"):
            try:
                g["credit"] = float(d["credit"])
            except ValueError:
                pass
        if not g["type"] and d.get("course_type"):
            g["type"] = d["course_type"]
        if not g["level"] and d.get("course_level"):
            g["level"] = d["course_level"]

    course_id_map: dict[int, int] = {}   # icourse_id → course_id
    for (name, dept), g in sorted(groups.items()):
        cur.execute(
            "INSERT INTO courses(name, dept, code, credit, course_type, course_level, icourse_ids) "
            "VALUES(?,?,?,?,?,?,?)",
            (name, dept, g["code"], g["credit"], g["type"], g["level"],
             json.dumps(g["icourse_ids"], ensure_ascii=False)),
        )
        cid = cur.lastrowid
        for iid in g["icourse_ids"]:
            course_id_map[iid] = cid
    print(f"courses: {len(groups)}")

    # ── 2. reviews ──
    review_rows = 0
    seen_rid: set[tuple[int, int]] = set()  # (icourse_id, review_iid): 同课多师合并后不同页评论 id 可能重复
    for d in raw:
        cid = course_id_map.get(d["id"])
        if cid is None:
            continue
        teacher = d.get("teacher", "").strip()
        for r in d.get("reviews", []):
            rid = r.get("id", 0)
            if (d["id"], rid) in seen_rid:
                continue
            seen_rid.add((d["id"], rid))
            dims = r.get("dims", {})
            cur.execute(
                "INSERT OR IGNORE INTO reviews(course_id, icourse_id, review_iid, teacher, author, "
                "stars, term, difficulty, homework, give_score, harvest, content) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, d["id"], rid, teacher, r.get("author", ""), r.get("stars", 0),
                 r.get("term", ""), dims.get("课程难度", ""), dims.get("作业多少", ""),
                 dims.get("给分好坏", ""), dims.get("收获大小", ""), r.get("content", "")),
            )
            review_rows += 1
    print(f"reviews: {review_rows}")

    # ── 3. course_rates: 从 reviews 单条 GROUP BY 全量重建 ──
    # COALESCE: 某维度全空时 SUM/AVG 返回 NULL, 违反 NOT NULL 约束
    # 星级仅统计已评分评论 (stars > 0), 未评分评论不拉低真实均分
    dims_agg = (
        "SELECT course_id,"
        "  COALESCE(SUM(stars*2.0),0), SUM(CASE WHEN stars>0 THEN 1 ELSE 0 END),"
        "  COALESCE(AVG(CASE WHEN stars>0 THEN stars*2.0 END),0),"
        "  COALESCE(SUM(CASE difficulty WHEN '简单' THEN 10 WHEN '中等' THEN 6.5 WHEN '困难' THEN 3 END),0),"
        "  SUM(CASE WHEN difficulty != '' THEN 1 ELSE 0 END),"
        "  COALESCE(AVG(CASE difficulty WHEN '简单' THEN 10 WHEN '中等' THEN 6.5 WHEN '困难' THEN 3 END),0),"
        "  COALESCE(SUM(CASE homework WHEN '很少' THEN 10 WHEN '少' THEN 8 WHEN '中等' THEN 6.5 WHEN '多' THEN 4 WHEN '很多' THEN 2 END),0),"
        "  SUM(CASE WHEN homework != '' THEN 1 ELSE 0 END),"
        "  COALESCE(AVG(CASE homework WHEN '很少' THEN 10 WHEN '少' THEN 8 WHEN '中等' THEN 6.5 WHEN '多' THEN 4 WHEN '很多' THEN 2 END),0),"
        "  COALESCE(SUM(CASE give_score WHEN '很差' THEN 2 WHEN '差' THEN 4 WHEN '一般' THEN 6 WHEN '好' THEN 8 WHEN '超好' THEN 10 END),0),"
        "  SUM(CASE WHEN give_score != '' THEN 1 ELSE 0 END),"
        "  COALESCE(AVG(CASE give_score WHEN '很差' THEN 2 WHEN '差' THEN 4 WHEN '一般' THEN 6 WHEN '好' THEN 8 WHEN '超好' THEN 10 END),0),"
        "  COALESCE(SUM(CASE harvest WHEN '没有' THEN 2 WHEN '少' THEN 4 WHEN '一般' THEN 6 WHEN '多' THEN 8 WHEN '很多' THEN 10 END),0),"
        "  SUM(CASE WHEN harvest != '' THEN 1 ELSE 0 END),"
        "  COALESCE(AVG(CASE harvest WHEN '没有' THEN 2 WHEN '少' THEN 4 WHEN '一般' THEN 6 WHEN '多' THEN 8 WHEN '很多' THEN 10 END),0)"
        " FROM reviews GROUP BY course_id"
    )
    rates = cur.execute(dims_agg).fetchall()
    cur.executemany(
        "INSERT INTO course_rates(course_id, rating_sum, rating_count, rating_avg,"
        " diff_sum, diff_count, diff_avg, hw_sum, hw_count, hw_avg,"
        " score_sum, score_count, score_avg, gain_sum, gain_count, gain_avg) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rates,
    )
    # 维度文本分布（用于展示）
    dist_rows: dict[int, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    for row in cur.execute("SELECT course_id, difficulty, homework, give_score, harvest FROM reviews").fetchall():
        cid, diff, hw, gs, gain = row
        if diff:
            dist_rows[cid]["难度"][diff] += 1
        if hw:
            dist_rows[cid]["作业"][hw] += 1
        if gs:
            dist_rows[cid]["给分"][gs] += 1
        if gain:
            dist_rows[cid]["收获"][gain] += 1
    for cid, dist in dist_rows.items():
        cur.execute("UPDATE course_rates SET dims_dist=? WHERE course_id=?",
                    (json.dumps({k: dict(v) for k, v in dist.items()}, ensure_ascii=False), cid))
    print(f"course_rates: {len(rates)}")

    # ── 4. course_terms: YYYYN 编码 ──
    term_rows: set[tuple[int, int]] = set()
    for row in cur.execute("SELECT DISTINCT course_id, term FROM reviews WHERE term != ''").fetchall():
        y = term_to_yyyyn(row[1])
        if y:
            term_rows.add((row[0], y))
    cur.executemany("INSERT OR IGNORE INTO course_terms(course_id, term) VALUES(?,?)", sorted(term_rows))
    print(f"course_terms: {len(term_rows)}")

    # ── 5. teachers + course_teachers ──
    teacher_map: dict[str, int] = {}
    teacher_agg: dict[tuple[int, str], dict] = defaultdict(lambda: {"sum": 0.0, "n": 0, "dist": defaultdict(lambda: defaultdict(int))})
    for row in cur.execute("SELECT course_id, teacher, stars, difficulty, homework, give_score, harvest FROM reviews").fetchall():
        cid, tname, stars, diff, hw, gs, gain = row
        if not tname or not stars:
            continue
        if tname not in teacher_map:
            cur.execute("INSERT INTO teachers(name) VALUES(?)", (tname,))
            teacher_map[tname] = cur.lastrowid
        agg = teacher_agg[(cid, tname)]
        agg["sum"] += stars * 2.0
        agg["n"] += 1
        if diff:
            agg["dist"]["难度"][diff] += 1
        if hw:
            agg["dist"]["作业"][hw] += 1
        if gs:
            agg["dist"]["给分"][gs] += 1
        if gain:
            agg["dist"]["收获"][gain] += 1
    for (cid, tname), agg in sorted(teacher_agg.items()):
        cur.execute(
            "INSERT OR IGNORE INTO course_teachers(course_id, teacher_id, rating_sum, rating_count, rating_avg, dims_dist) "
            "VALUES(?,?,?,?,?,?)",
            (cid, teacher_map[tname], agg["sum"], agg["n"], agg["sum"] / agg["n"],
             json.dumps({k: dict(v) for k, v in agg["dist"].items()}, ensure_ascii=False)),
        )
    print(f"teachers: {len(teacher_map)}, course_teachers: {len(teacher_agg)}")

    # ── 6. programs + program_courses（course_id 尽力匹配） ──
    programs = load_programs()
    # courses 索引: code 精确（去前导0）/ name 相等 / name 包含
    courses_by_code: dict[str, int] = {}
    courses_by_name: dict[str, int] = {}
    for row in cur.execute("SELECT id, code, name FROM courses").fetchall():
        cid, code, name = row
        if code:
            courses_by_code.setdefault(code, cid)
            courses_by_code.setdefault(code.lstrip("0"), cid)
        if name:
            courses_by_name.setdefault(name, cid)
    matched = 0
    total_rows = 0
    for p in programs:
        cur.execute("INSERT OR IGNORE INTO programs(id, name, college, grade) VALUES(?,?,?,?)",
                    (p["pid"], p.get("name", ""), p.get("college", ""), p.get("grade", "")))
        for c in p.get("courses", []):
            total_rows += 1
            cid = None
            code = c.get("code", "").strip()
            cname = c.get("name", "").strip()
            if code:
                cid = courses_by_code.get(code) or courses_by_code.get(code.lstrip("0"))
            if cid is None and cname:
                cid = courses_by_name.get(cname)
            if cid is None and cname:
                # 名称包含匹配: 方案名是课程名的子串（如 "数学分析(A1)" vs "数学分析(A1)（某老师）"）
                for row in cur.execute(
                    "SELECT id FROM courses WHERE name LIKE ? LIMIT 5", (cname + "%",)
                ).fetchall():
                    cid = row[0]
                    break
            if cid:
                matched += 1
            cur.execute(
                "INSERT INTO program_courses(program_id, course_id, code, name, required, exam, credit, category, term) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (p["pid"], cid, code, cname, c.get("required", ""), c.get("exam", ""),
                 c.get("credit", ""), c.get("category", ""), c.get("term", "")),
            )
    print(f"programs: {len(programs)}, program_courses: {total_rows}, 匹配到课程: {matched} ({matched / max(total_rows, 1) * 100:.1f}%)")

    # ── 7. courses 汇总评分回填 ──
    # COALESCE: 详情页无评论的课程（reviews 为空数组）在 course_rates 无行, 保持 0
    cur.execute(
        "UPDATE courses SET rating_avg = COALESCE((SELECT rating_avg FROM course_rates r WHERE r.course_id = courses.id), 0),"
        " rate_count = COALESCE((SELECT rating_count FROM course_rates r WHERE r.course_id = courses.id), 0)"
    )

    conn.commit()
    conn.close()
    print(f"完成: {db_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="构建选课推荐数据库")
    ap.add_argument("--db", default=str(PROJECT_ROOT / "data" / "course_data.db"))
    args = ap.parse_args()
    build(Path(args.db))


if __name__ == "__main__":
    main()
