"""
小蜗 — 选课顾问 Agent 工具
数据源: data/course_data.db（icourse.club 真实评课, 8 表结构见 database/schema_course.sql）

推荐原则（与用户确认）:
- 排序: 真实星级均分降序（不归一化）, 平手时样本量大优先
- 老师维度: 同课多师并列, 各自均分/样本量/代表评论
- 画像: 仅软过滤 + 理由生成, 不参与排序权重
- 展示: 文字流（标题行 + 评分行 + 5-6 条真实评论引用, 点赞序, 作者去重）
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from langchain_core.tools import tool

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COURSE_DB = PROJECT_ROOT / "data" / "course_data.db"

DIM_KEYS = {"难度": "diff", "作业": "hw", "给分": "score", "收获": "gain"}

# 画像定义（软过滤规则 + 理由模板）
PROFILES = {
    "easy_grade": {"name": "冲分保绩", "desc": "给分好、难度低优先"},
    "learn_hard": {"name": "硬核学习", "desc": "收获大、有挑战优先"},
    "balanced": {"name": "均衡兼顾", "desc": "评分与难度均衡考虑"},
}

# 中文偏好描述 → preference_type（宽容参数映射）
_PREF_CN = {
    "给分好": "easy_grade", "好拿分": "easy_grade", "轻松": "easy_grade",
    "水课": "easy_grade", "不点名": "easy_grade", "任务少": "easy_grade",
    "摸鱼": "easy_grade", "省时": "easy_grade", "硬核": "learn_hard",
    "学东西": "learn_hard", "挑战": "learn_hard",
}

# 已修课程名 → 兴趣关键词（个性化 v1：仅作理由信号，不参与候选池过滤）
_TAKEN_INTEREST_KW = [
    "人工智能", "机器学习", "深度学习", "数据挖掘", "大数据", "数据科学",
    "计算机", "程序设计", "算法", "数学分析", "线性代数", "概率论",
    "物理", "化学", "生物", "金融", "经济", "管理", "文学", "历史", "哲学", "艺术",
]


def _infer_preference(profile: dict) -> str:
    """个性化 v1：未显式指定偏好时按 GPA 自动推断画像。

    4.3 制 GPA ≥3.7 → 硬核学习；≤2.7 → 冲分保绩；其余/无 GPA → 均衡。
    显式 preference_type（用户明说或 LLM 判断）永远优先。"""
    pref = profile.get("preference_type")
    if pref:
        return pref
    try:
        g = float(profile.get("gpa"))
    except (TypeError, ValueError):
        return "balanced"
    if g >= 3.7:
        return "learn_hard"
    if g <= 2.7:
        return "easy_grade"
    return "balanced"


def _cdb() -> sqlite3.Connection:
    """每次新建连接（本地 sqlite 开销小, 避免缓存坏连接）。"""
    conn = sqlite3.connect(str(COURSE_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_course_name(name: str) -> str:
    """归一化课程名: 去全角/半角括号、引号、空格与全角空格, ASCII 大写。

    使 '数学分析 (B1)'、'数学分析（B1）'、'数学分析 B1' 等变体都收敛成
    同一 '数学分析B1', 与数据库里 '数学分析(B1)' 的写法互相匹配;
    中文/英文引号不影响课程名匹配, 一并去掉（如 '"科学与社会"研讨课'）。
    """
    s = (name or "").translate(str.maketrans("（）", "()")).replace("　", " ")
    s = s.replace("(", "").replace(")", "").replace(" ", "")
    for ch in "\"'“”‘’":
        s = s.replace(ch, "")
    return s.upper()


# SQL 端课程名归一化表达式, 与 _norm_course_name 保持一致（去括号/空格/大写）,
# 使搜索词与库里名称能以同一形式模糊匹配。
_SQL_NORM_NAME = (
    "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(UPPER(c.name),'(',''),')',''),"
    "'（',''),'）',''),' ',''),'　','')"
)


def _match_courses(conn: sqlite3.Connection, name: str) -> list[dict]:
    """按课程名模糊查（含评分信息）。名称端与 SQL 端都归一化括号/空格/大小写,
    使 '数学分析B1' 能匹配到 '数学分析(B1)'。按样本量降序返回 list[dict]。"""
    like = f"%{_norm_course_name(name)}%"
    rows = conn.execute(
        "SELECT c.id, c.name, c.code, c.credit, c.dept, r.rating_avg, r.rating_count "
        "FROM courses c JOIN course_rates r ON r.course_id = c.id "
        f"WHERE {_SQL_NORM_NAME} LIKE ? "
        "ORDER BY r.rating_count DESC",
        (like,),
    ).fetchall()
    return [dict(r) for r in rows]


# 偏好状态（Phase 2a：按"当前学生"分桶，多用户会话隔离；脚本/测试未设置时用默认桶）
_profiles: dict[str, dict] = {}


def _profile_key() -> str:
    from services.session_ctx import current_student
    return current_student() or "_anon"


def get_profile() -> dict:
    return _profiles.setdefault(_profile_key(), {})


def update_profile(**kwargs):
    _profiles.setdefault(_profile_key(), {}).update(kwargs)


def reset_profile():
    _profiles[_profile_key()] = {}


# ───────────────────────── 展示辅助 ─────────────────────────

def _term_text(y: int) -> str:
    """20231 → 2023秋"""
    return f"{y // 10}{['', '秋', '春', '夏'][y % 10]}"


def _recent_terms(conn: sqlite3.Connection, course_id: int, n: int = 3) -> list[str]:
    rows = conn.execute(
        "SELECT term FROM course_terms WHERE course_id=? ORDER BY term DESC LIMIT ?",
        (course_id, n),
    ).fetchall()
    return [_term_text(r["term"]) for r in rows]


def _dims_info(conn: sqlite3.Connection, course_id: int) -> dict:
    """课程维度: 映射均分 + 文本分布众数。"""
    r = conn.execute(
        "SELECT diff_avg, hw_avg, score_avg, gain_avg, dims_dist FROM course_rates WHERE course_id=?",
        (course_id,),
    ).fetchone()
    if not r:
        return {"avg": {}, "mode": {}}
    dist = json.loads(r["dims_dist"] or "{}")
    mode = {}
    for k, v in dist.items():
        if v:
            mode[k] = max(v, key=v.get)
    return {
        "avg": {"难度": round(r["diff_avg"] or 0, 1), "作业": round(r["hw_avg"] or 0, 1),
                "给分": round(r["score_avg"] or 0, 1), "收获": round(r["gain_avg"] or 0, 1)},
        "mode": mode,
    }


def _norm_teacher(name: str) -> list[str]:
    """合教名拆分: '计永胜, 石攀, 周永刚' → ['计永胜', '石攀', '周永刚']"""
    parts = re.split(r"[,，、/]", name or "")
    parts = [p.strip() for p in parts if p.strip()]
    return parts or [name]


def _teacher_cells(conn: sqlite3.Connection, course_id: int) -> list[dict]:
    """同课多师: 各老师均分/样本量/维度分布（合教名拆分为单人后按名聚合）。"""
    agg: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT t.name, ct.rating_avg, ct.rating_count, ct.dims_dist "
        "FROM course_teachers ct JOIN teachers t ON t.id = ct.teacher_id "
        "WHERE ct.course_id=?",
        (course_id,),
    ).fetchall():
        dist = json.loads(r["dims_dist"] or "{}")
        for tname in _norm_teacher(r["name"]):
            a = agg.setdefault(tname, {"sum": 0.0, "n": 0, "dist": {}})
            a["sum"] += (r["rating_avg"] or 0) * (r["rating_count"] or 0)
            a["n"] += r["rating_count"] or 0
            for k, v in dist.items():
                d = a["dist"].setdefault(k, {})
                for kval, cnt in v.items():
                    d[kval] = d.get(kval, 0) + cnt
    out = []
    for name, a in agg.items():
        mode = {k: max(v, key=v.get) for k, v in a["dist"].items() if v}
        out.append({
            "name": name,
            "rating_avg": round(a["sum"] / max(a["n"], 1), 1),
            "rate_count": a["n"],
            "dims_mode": mode,
        })
    out.sort(key=lambda x: (-x["rating_avg"], -x["rate_count"]))
    return out


def _top_reviews(conn: sqlite3.Connection, course_id: int, teacher: str | None = None,
                 limit: int = 6) -> list[dict]:
    """代表性评论: icourse 服务端按点赞最多排序（DOM 顺序即点赞序）, 作者去重（匿名不去重）。
    teacher 为单人姓名时模糊匹配合教名（如 '计永胜' 也匹配 '计永胜, 石攀, 周永刚'）。"""
    if teacher:
        rows = conn.execute(
            "SELECT author, teacher, stars, term, difficulty, homework, give_score, harvest, content "
            "FROM reviews WHERE course_id=? AND teacher LIKE ? ORDER BY id LIMIT 200",
            (course_id, f"%{teacher}%"),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT author, teacher, stars, term, difficulty, homework, give_score, harvest, content "
            "FROM reviews WHERE course_id=? ORDER BY id LIMIT 200",
            (course_id,),
        ).fetchall()
    seen: set[str] = set()
    out = []
    for r in rows:
        author = r["author"] or ""
        if author and author != "匿名用户" and author in seen:
            continue
        if author:
            seen.add(author)
        content = (r["content"] or "").strip()
        if len(content) < 10:
            continue
        dims = []
        for k, v in (("难度", r["difficulty"]), ("作业", r["homework"]),
                     ("给分", r["give_score"]), ("收获", r["harvest"])):
            if v:
                dims.append(f"{k}:{v}")
        out.append({
            "author": author or "匿名",
            "teacher": r["teacher"] or "",
            "stars": r["stars"],
            "term": r["term"],
            "dims": dims,
            "content": content[:400],
        })
        if len(out) >= limit:
            break
    return out


def _program_hint(conn: sqlite3.Connection, major: str | None, course_id: int,
                  grade: str | None = None) -> dict | None:
    """培养方案弱标注: 该课程是否出现在用户专业相关的方案中（必修/选修 + 学期标注）。

    优先展示「同年级 + 普通专业方案」，与 _resolve_program 的实际定位一致，
    避免给 2025 级用户标注 2026 级方案造成误导；其次才是普通方案 / 最新方案。"""
    if not major:
        return None
    g = _parse_grade_key(grade)
    # name 匹配优先：与 _resolve_program 一致，避免 college 模糊误伤（如 "人工智能" 命中数据科学方案）
    rows = conn.execute(
        "SELECT id FROM programs WHERE name LIKE ? ORDER BY grade DESC",
        (f"%{major}%",),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT id FROM programs WHERE college LIKE ? ORDER BY grade DESC",
            (f"%{major}%",),
        ).fetchall()
    if not rows:
        return None
    prog_ids = [r["id"] for r in rows[:50]]
    placeholders = ",".join("?" * len(prog_ids))
    params = [course_id] + prog_ids
    grade_clause = ""
    if g:
        grade_clause = "CASE WHEN p.grade LIKE ? THEN 0 ELSE 1 END, "
        params.append(f"%{g}级%")
    sql = (
        "SELECT pc.required, pc.term, pc.category, p.name, p.grade "
        "FROM program_courses pc JOIN programs p ON p.id = pc.program_id "
        f"WHERE pc.course_id=? AND p.id IN ({placeholders}) "
        f"ORDER BY {grade_clause}"
        "CASE WHEN p.name LIKE '%专业培养方案%' AND p.name NOT LIKE '%英才班%' "
        " AND p.name NOT LIKE '%辅修%' THEN 0 ELSE 1 END, p.grade DESC LIMIT 3"
    )
    hits = conn.execute(sql, params).fetchall()
    if not hits:
        return None
    h = hits[0]
    return {"required": h["required"], "term": h["term"], "program": h["name"], "grade": h["grade"]}


def _generate_reason(conn: sqlite3.Connection, course: dict, profile: dict) -> list[str]:
    """软过滤理由: 兴趣匹配 + 画像提示（不参与排序）。"""
    reasons: list[str] = []
    interests = profile.get("interests") or []
    if interests:
        matched = [k for k in interests if k and (k in course["name"] or k in course["dept"]
                                                  or k in course["course_type"])]
        if matched:
            reasons.append(f"与兴趣「{'、'.join(matched[:3])}」相关")
    pref = profile.get("preference_type", "balanced")
    dims = course["dims"]["avg"]
    if pref == "easy_grade":
        if dims.get("给分", 0) >= 8:
            reasons.append("给分评价好, 适合冲分")
        if dims.get("难度", 0) and dims["难度"] <= 4.5:
            reasons.append("注意: 难度评价较高, 冲分需谨慎")
    elif pref == "learn_hard":
        if dims.get("收获", 0) >= 8:
            reasons.append("收获评价高, 值得深入学习")
        if dims.get("难度", 0) and dims["难度"] <= 4.5:
            reasons.append("课程有挑战性")
    if profile.get("_auto_pref") and profile.get("gpa") is not None:
        reasons.append(f"按你的 GPA {profile['gpa']} 自动采用「{PROFILES.get(pref, {}).get('name', pref)}」画像")
    if course["rate_count"] < 10:
        reasons.append("样本较少, 评分仅供参考")
    return reasons


# ───────────────────────── 方案分组辅助 ─────────────────────────
#
# 必修组 + 选修组两段式推荐（任务 2）。方案定位规则与 tools/program_tools.py
# 的 _resolve_program 一致（同年级 → 最近低年级 → 最新；programs 表只含 2022
# 级及以后）。此处独立复刻，避免跨模块导入引入循环依赖。

def _parse_grade_key(grade: str) -> int:
    """年级 → 可排序整数（"2024级"→2024，"大二"→无法解析返回 0）。"""
    m = re.match(r"\D*(\d{4})\D*", str(grade or ""))
    return int(m.group(1)) if m else 0


def _prog_priority(r) -> int:
    """方案类型优先级：普通专业方案 0；英才班/带括号特殊方案 1；辅修 2。

    同年级多方案命中（普通班 vs 英才班/少年班等）时优先普通专业方案，
    避免给普通班学生推荐英才班专属课程（如量子物理、并行计算A 等）。"""
    name = r["name"] or ""
    if "辅修" in name:
        return 2
    if "英才班" in name or "（" in name or "(" in name:
        return 1
    return 0


def _resolve_program(conn: sqlite3.Connection, major: str | None,
                     grade: str | None = None) -> tuple[int | None, str | None]:
    """全量库方案定位：同年级 → 最近低年级 → 最新；同年级内普通专业方案优先。

    Returns:
        (program_id, program_name)；无 major 或未命中时 (None, None)。
    """
    if not major:
        return None, None
    # name 精确优先：college LIKE 会误伤（如 major="人工智能" 命中 人工智能与数据科学学院 的
    # 数据科学与大数据技术方案，把计算机学生推向别专业课程），仅当 name 无命中时才回退 college
    rows = conn.execute(
        "SELECT * FROM programs WHERE name LIKE ? ORDER BY grade DESC",
        (f"%{major}%",),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT * FROM programs WHERE college LIKE ? ORDER BY grade DESC",
            (f"%{major}%",),
        ).fetchall()
    if not rows:
        return None, None
    target = _parse_grade_key(grade)

    def _sort_key(r):
        g = _parse_grade_key(r["grade"])
        if target:
            diff = g - target
            bucket = 0 if diff == 0 else (1 if diff < 0 else 2)
        else:
            bucket = 0  # 无年级信息: 不按年级分桶, 普通方案优先 + 最新在前
        return (bucket, _prog_priority(r), -g)

    rows = sorted(rows, key=_sort_key)
    row = rows[0]
    return row["id"], row["name"]


def _parse_term_year(term: str) -> int | None:
    """从 term 首字符解析学年号（"2秋"→2）；无前缀/无法解析返 None。"""
    m = re.match(r"\s*(\d)", term or "")
    return int(m.group(1)) if m else None


def _term_urgency(term: str, current_yi: int | None) -> int:
    """学期紧迫度档位（必修组内排序）：
    0=已过期应修未修（学年号 < current_year_index）置顶；
    1=当前学年且为下学期（1-8 月面向秋季、9-12 月面向春季）该修；
    2=当前学年但非下学期（可稍后修）；3=未来学年或无法解析（最后）。
    春/秋区分避免「2春」与「2秋」同档按评分乱排（如 8 月选课应 2秋 优先于 2春）。"""
    y = _parse_term_year(term)
    if y is None or current_yi is None:
        return 3
    if y < current_yi:
        return 0
    if y > current_yi:
        return 3
    # 当前学年：区分春秋——「下学期」优先（8 月前面向秋季选课, 9 月起面向春季选课）
    month = date.today().month
    next_is_autumn = month <= 8
    season = "秋" if "秋" in (term or "") else "春"
    return 1 if (season == "秋") == next_is_autumn else 2


def _infer_current_year_index(grade: str | None, selection: bool = True) -> int | None:
    """由年级推算当前学年号："大二"→2；"2024级"→面向今年 9 月开学学年 - 入学年 + 1。

    selection=True 为选课场景：以 9 月开学为新学年基准（暑假选课即面向下学期开学后的新学年），
    ay=today.year，2025 级在 2026 年 8 月/9 月 → 2（大二）；
    selection=False 为学籍学年（身份展示"我大几"）：1-8 月仍按上一学年（ay=today.year-1），
    避免每年 1-8 月身份年级高一档。
    无法推算时返 None（必修组全体按当前学年档处理）。"""
    gs = str(grade or "")
    for idx, zh in enumerate(["一", "二", "三", "四"], start=1):
        if f"大{zh}" in gs:
            return idx
    g = _parse_grade_key(grade)
    if not g:
        return None
    today = date.today()
    if selection:
        ay = today.year  # 选课永远面向今年 9 月开学的新学年（1-8 月为下学期选课, 9-12 月已在新学年）
    else:
        ay = today.year - 1 if today.month < 9 else today.year  # 学籍学年：1-8 月仍属上一学年
    return max(ay - g + 1, 1)


def _infer_current_term() -> str:
    """由当前日期推断当前学期："2026秋"（9 月起）/"2025春"（2-8 月）。"""
    today = date.today()
    return f"{today.year}秋" if today.month >= 9 else f"{today.year - 1}春"


def _infer_next_selection_term() -> str:
    """下一选课学期：1-8 月面向当年秋、9-12 月面向次年春（与 _term_urgency 口径一致）。"""
    today = date.today()
    return f"{today.year}秋" if today.month <= 8 else f"{today.year + 1}春"


def _pool_rows(conn: sqlite3.Connection, keywords: list[str], limit: int = 200) -> list:
    """全量评分候选池（真实均分降序），keywords 可选过滤课程名/院系/类型。"""
    where, params = "r.rating_count > 0", []
    if keywords:
        like = " OR ".join(
            f"({_SQL_NORM_NAME} LIKE ? OR c.dept LIKE ? OR c.course_type LIKE ?)"
            for _ in keywords
        )
        where += f" AND ({like})"
        for k in keywords:
            params += [f"%{_norm_course_name(k)}%", f"%{k}%", f"%{k}%"]
    return conn.execute(
        f"SELECT c.id, c.name, c.code, c.credit, c.dept, c.course_type, c.icourse_ids, "
        f"r.rating_avg, r.rating_count FROM courses c JOIN course_rates r ON r.course_id = c.id "
        f"WHERE {where} ORDER BY r.rating_avg DESC, r.rating_count DESC LIMIT ?",
        params + [limit],
    ).fetchall()


def _build_item(conn: sqlite3.Connection, row, profile: dict) -> dict:
    """由课程行构建完整推荐条目（字段与旧实现一致）。
    row 需含 id/name/code/credit/dept/course_type/rating_avg/rating_count。"""
    cid = row["id"]
    dims = _dims_info(conn, cid)
    teachers = _teacher_cells(conn, cid)
    multi = len(teachers) > 1
    if multi:  # 同课多师: 每师最多 3 条, 总量封顶 6
        reviews = []
        for t in teachers:
            reviews.extend(_top_reviews(conn, cid, t["name"], limit=3))
        reviews = reviews[:6]
    else:
        reviews = _top_reviews(conn, cid, limit=6)
    return {
        "id": cid,
        "name": row["name"],
        "code": row["code"],
        "credit": row["credit"],
        "dept": row["dept"],
        "course_type": row["course_type"],
        "rating_avg": round(row["rating_avg"], 1),
        "rate_count": row["rating_count"],
        "dims": dims,
        "teachers": teachers,
        "multi_teacher": multi,
        "terms": _recent_terms(conn, cid),
        "top_reviews": reviews,
        "program_hint": _program_hint(conn, profile.get("major"), cid, profile.get("grade")),
        "reasons": _generate_reason(conn, {"name": row["name"], "dept": row["dept"],
                                           "course_type": row["course_type"],
                                           "rate_count": row["rating_count"], "dims": dims},
                                    profile),
    }


def _split_targets(max_results: int) -> tuple[int, int]:
    """条数拆分：默认 10（6 必修 + 4 选修，60%/40%）；<=1 时全给第一组。"""
    if max_results <= 1:
        return max_results, 0
    req = int(round(max_results * 0.6))
    return req, max_results - req


def _recommend_flat(conn: sqlite3.Connection, profile: dict, keywords: list[str],
                    max_results: int, taken: set[str]) -> dict:
    """无方案命中：纯评分推荐（保留关键字过滤与过窄回退全量）。"""
    rows = _pool_rows(conn, keywords)
    keyword_fallback = False
    if not rows and keywords:
        keyword_fallback = True
        rows = _pool_rows(conn, None)
    total = len(rows)
    items = []
    for r in rows:
        if _norm_course_name(r["name"]) in taken:
            continue
        items.append(_build_item(conn, r, profile))
    return {
        "recommendations": items[:max_results],
        "groups": None,
        "progress": None,
        "total_candidates": total,
        "filtered_count": len(items[:max_results]),
        "keyword_fallback": keyword_fallback,
    }


def _recommend_grouped(conn: sqlite3.Connection, profile: dict, keywords: list[str],
                       max_results: int, taken: set[str], current_yi: int | None,
                       prog_id: int, prog_name: str) -> dict:
    """分成「必修组 + 选修组」：必修组前置、按学期紧迫度 + 评分；选修组按评分降序。"""
    # ── 必修组候选: 方案必修且未修, 按紧迫度（已过期置顶→当前学年→未来）再按评分降序 ──
    req_rows = conn.execute(
        "SELECT c.id, c.name, c.code, c.credit, c.dept, c.course_type, c.icourse_ids, "
        "r.rating_avg, r.rating_count, pc.term FROM program_courses pc "
        "JOIN courses c ON c.id = pc.course_id JOIN course_rates r ON r.course_id = c.id "
        "WHERE pc.program_id=? AND pc.required='必修' AND pc.course_id IS NOT NULL",
        (prog_id,),
    ).fetchall()

    def _req_sort_key(rr):
        urgency = _term_urgency(rr["term"], current_yi)
        if "毕业" in (rr["name"] or ""):
            urgency = 3  # 毕业论文/设计仅毕业年级修, 非毕业班一律排最后
        return (urgency, -(rr["rating_avg"] or 0))

    req_rows.sort(key=_req_sort_key)
    required = [_build_item(conn, rr, profile) for rr in req_rows
                if _norm_course_name(rr["name"]) not in taken]
    required_ids = {it["id"] for it in required}  # 必修组整体排除出选修组，保证两组不重叠

    # ── 选修组候选: 方案内选修优先（按评分降序），方案外高分池仅作条数补足 ──
    # （避免方案外课程混排靠前造成“乱推”观感）
    elective: list[dict] = []
    seen_ids: set[int] = set()
    for pr in conn.execute(
        "SELECT c.id, c.name, c.code, c.credit, c.dept, c.course_type, c.icourse_ids, "
        "r.rating_avg, r.rating_count FROM program_courses pc "
        "JOIN courses c ON c.id = pc.course_id JOIN course_rates r ON r.course_id = c.id "
        "WHERE pc.program_id=? AND pc.required='选修' AND pc.course_id IS NOT NULL",
        (prog_id,),
    ).fetchall():
        if _norm_course_name(pr["name"]) in taken or pr["id"] in required_ids or pr["id"] in seen_ids:
            continue
        seen_ids.add(pr["id"])
        elective.append(_build_item(conn, pr, profile))
    elective.sort(key=lambda it: (-it["rating_avg"], -it["rate_count"]))
    for r in _pool_rows(conn, keywords):
        if _norm_course_name(r["name"]) in taken or r["id"] in required_ids or r["id"] in seen_ids:
            continue
        seen_ids.add(r["id"])
        elective.append(_build_item(conn, r, profile))

    # ── 条数拆分: 60%/40%（不足互补）──
    req_target, elec_target = _split_targets(max_results)
    req_sel = required[:req_target]
    if max_results <= 1:
        elec_sel: list[dict] = []
    else:
        short = max(req_target - len(req_sel), 0)  # 必修不足其目标时选修补足
        elec_sel = elective[:elec_target + short]

    # ── 进度摘要: 已修必修/方案必修总数/已修学分（用方案行 credit 汇总）──
    progress = None
    if taken:
        total_req = 0
        taken_req = 0
        credits_taken = 0.0
        for pc in conn.execute(
            "SELECT name, credit FROM program_courses WHERE program_id=? AND required='必修'",
            (prog_id,),
        ):
            total_req += 1
            if _norm_course_name(pc["name"]) in taken:
                taken_req += 1
                try:
                    credits_taken += float(pc["credit"]) if pc["credit"] else 0.0
                except (TypeError, ValueError):
                    pass
        progress = {"required_taken": taken_req, "required_total": total_req,
                    "credits_taken": round(credits_taken, 1)}

    recommendations = req_sel + elec_sel
    return {
        "recommendations": recommendations,
        "groups": {"required": req_sel, "elective": elec_sel},
        "progress": progress,
        "total_candidates": len(req_rows) + len(_pool_rows(conn, keywords)),
        "filtered_count": len(recommendations),
        "keyword_fallback": False,
    }


# ───────────────────────── 工具 ─────────────────────────

@tool
def collect_preferences() -> dict:
    """
    启动偏好收集对话。返回当前已收集的偏好。
    实际的多轮对话由Agent通过自然语言完成，此Tool仅用于标记状态。

    Returns:
        {"status": "collecting", "collected_fields": [...], "remaining_fields": [...], "current_profile": {...}}
    """
    profile = get_profile()
    all_fields = ["major", "grade", "interests", "preference_type", "target_gpa"]
    collected = list(profile.keys())
    remaining = [f for f in all_fields if f not in profile]
    return {
        "status": "collecting" if remaining else "ready",
        "collected_fields": collected,
        "remaining_fields": remaining,
        "current_profile": profile,
    }


@tool
def recommend_courses(profile: dict | None = None, major: str | None = None,
                      grade: str | None = None, interests: list[str] | str | None = None,
                      preference_type: str | None = None, preference: str | None = None,
                      keywords: list[str] | str | None = None, max_results: int = 10,
                      taken_courses: list[str] | None = None,
                      current_year_index: int | None = None,
                      current_term: str | None = None,
                      gpa: float | None = None) -> dict:
    """
    根据用户画像推荐课程。有专业方案时按「必修组 + 选修组」两段式返回（必修组前置、
    按学期紧迫度 + 评分排序）；无专业/未命中方案时保持纯评分推荐。

    Args:
        profile: 用户画像, 格式: {"major": "计算机科学", "grade": "大二",
            "interests": ["人工智能", "数学"], "preference_type": "easy_grade|learn_hard|balanced",
            "max_results": 10, "taken_courses": [...], "current_year_index": 3}；
            也可省略 profile 直接用下面的顶层参数
        major: 专业名（可选，如 "计算机科学"）
        grade: 年级（可选，如 "大二"、"2024级"）
        interests: 兴趣方向（可选，列表或单个字符串，如 ["人工智能", "数学"]）
        preference_type: 偏好类型（可选，easy_grade=冲分保绩/learn_hard=硬核学习/balanced=均衡）
        preference: 中文偏好描述（可选，如 "给分好""不点名""任务少"→冲分保绩，"硬核""学东西"→硬核学习）
        keywords: 限定候选范围的关键词（可选，如 ["数学分析"]，匹配课程名/院系/类型；
            不要用“给分好”“不点名”这类偏好词）
        max_results: 返回条数（默认 10，约 6 必修 + 4 选修，不足互补）
        taken_courses: 已修课程名列表（可选，登录后由上层从成绩单读取传入；
            None 视为全部未修）
        current_year_index: 当前学年（可选，1=大一，2=大二…）；None 时按 profile.grade
            推算（"大二"→2；"2024级"→当前日期所在学年）
        current_term: 当前学期标识（可选，如 "2026秋"）；None 时由当前日期推断
        gpa: 4.3 制 GPA（可选，登录用户由上层从成绩单计算注入）；未显式指定
            preference_type 时按 GPA 自动推断画像（≥3.7 硬核 / ≤2.7 冲分 / 其余均衡）

    Returns:
        {"recommendations": [...], "groups": {"required": [...], "elective": [...]},
         "progress": {"required_taken", "required_total", "credits_taken"},
         "total_candidates": N, "filtered_count": N, "profile_note": {...}, "keyword_fallback": bool}
        每门课: {name, code, credit, dept, rating_avg, rate_count, dims, teachers,
                 terms, top_reviews, program_hint, reasons}
        recommendations = 必修组 + 选修组拼接（向后兼容）; 无分组时 groups 为 None。
    """
    # 宽容参数: 顶层参数自动归一化进 profile（兼容 LLM 不定式传参）
    p = dict(profile or {})
    if major:
        p.setdefault("major", major)
    if grade:
        p.setdefault("grade", grade)
    if interests:
        p.setdefault("interests", interests if isinstance(interests, list) else [interests])
    if preference_type:
        p.setdefault("preference_type", preference_type)
    if preference:
        p.setdefault("preference_type", _PREF_CN.get(preference, preference))
    if p.get("preference") and not p.get("preference_type"):
        p["preference_type"] = _PREF_CN.get(p["preference"], p["preference"])
    if keywords:
        p.setdefault("keywords", keywords if isinstance(keywords, list) else [keywords])
    if taken_courses:
        p.setdefault("taken_courses", taken_courses)
    if current_year_index:
        p.setdefault("current_year_index", current_year_index)
    if current_term:
        p.setdefault("current_term", current_term)
    if gpa is not None:
        p.setdefault("gpa", gpa)
    p.setdefault("max_results", max_results)
    profile = p

    # 个性化 v1：未显式指定偏好时按 GPA 自动推断（显式偏好永远优先）
    if not profile.get("preference_type"):
        profile["preference_type"] = _infer_preference(profile)
        if profile.get("gpa") is not None:
            profile["_auto_pref"] = True

    try:
        conn = _cdb()
    except sqlite3.Error:
        return {"recommendations": [], "groups": None, "progress": None,
                "total_candidates": 0, "filtered_count": 0, "error": "课程数据库不可用"}

    keywords = profile.get("keywords") or profile.get("interests") or []
    max_results = profile.get("max_results", 10)

    # 当前学年：显式传入优先，否则按年级推算
    current_yi = profile.get("current_year_index")
    if current_yi is None:
        current_yi = _infer_current_year_index(profile.get("grade"))
    # 已修课程（归一化集合）; 未提供视为全部未修。
    # (L) 实验/语言班型后缀兼容：成绩里 "计算机程序设计(L)" 视同 "计算机程序设计" 已修。
    taken: set[str] = set()
    for tc in (profile.get("taken_courses") or []):
        n = _norm_course_name(tc)
        taken.add(n)
        if n.endswith("L") and len(n) > 1:
            taken.add(n[:-1])
    # 个性化 v1：无兴趣线索时由已修课程名推断兴趣（仅作理由信号，不参与池过滤）
    if not profile.get("interests") and taken:
        auto_ints = [kw for kw in _TAKEN_INTEREST_KW
                     if any(kw in n for n in (profile.get("taken_courses") or []))]
        if auto_ints:
            profile["interests"] = auto_ints[:5]

    # 方案定位：无 major / 未命中 / 库缺方案表 → 纯评分推荐
    try:
        prog_id, prog_name = _resolve_program(conn, profile.get("major"), profile.get("grade"))
    except sqlite3.Error:
        prog_id, prog_name = None, None

    if prog_id is None:
        result = _recommend_flat(conn, profile, keywords, max_results, taken)
    else:
        result = _recommend_grouped(conn, profile, keywords, max_results, taken,
                                    current_yi, prog_id, prog_name)

    conn.close()
    pref = profile.get("preference_type", "balanced")
    note = dict(PROFILES.get(pref, PROFILES["balanced"]))
    if profile.get("_auto_pref") and profile.get("gpa") is not None:
        note["auto"] = True
        note["gpa"] = profile["gpa"]
    result.setdefault("profile_note", note)
    return result


@tool
def compare_courses(course_a: str, course_b: str) -> dict:
    """
    对比两门课程（评分/难度/给分/收获 + 同课多师 + 代表评论）。

    Args:
        course_a: 课程 A 名称（支持模糊）
        course_b: 课程 B 名称（支持模糊）

    Returns:
        {"course_a": {...}, "course_b": {...}, "comparison": {...}}
    """
    try:
        conn = _cdb()
    except sqlite3.Error:
        return {"error": "课程数据库不可用"}

    def find(name: str) -> dict | None:
        rows = _match_courses(conn, name)
        if not rows:
            return None
        r = rows[0]  # 已按 rating_count 降序, 取样本量最大者
        cid = r["id"]
        return {
            "id": cid, "name": r["name"], "code": r["code"], "credit": r["credit"],
            "dept": r["dept"], "rating_avg": round(r["rating_avg"], 1),
            "rate_count": r["rating_count"],
            "dims": _dims_info(conn, cid),
            "teachers": _teacher_cells(conn, cid),
            "terms": _recent_terms(conn, cid),
            "top_reviews": _top_reviews(conn, cid, limit=4),
        }

    a = find(course_a)
    if not a:
        conn.close()
        return {"error": f"未找到课程：{course_a}"}
    b = find(course_b)
    if not b:
        conn.close()
        return {"error": f"未找到课程：{course_b}"}
    conn.close()

    def dim(key):
        return a["dims"]["avg"].get(key, 0), b["dims"]["avg"].get(key, 0)

    ra, rb = a["rating_avg"], b["rating_avg"]
    da, db_ = dim("难度")
    sa, sb = dim("给分")
    ga, gb = dim("收获")
    return {
        "course_a": a,
        "course_b": b,
        "comparison": {
            "rating_winner": a["name"] if ra >= rb else b["name"],
            "rating_diff": round(abs(ra - rb), 1),
            "easier": a["name"] if da >= db_ else b["name"],
            "score_winner": a["name"] if sa >= sb else b["name"],
            "gain_winner": a["name"] if ga >= gb else b["name"],
            "suggestion": (
                f"评分：{a['name']} {ra} vs {b['name']} {rb}；"
                f"若以分数为重选评分高者, 若在意难度与体验请结合评论判断。"
            ),
        },
    }


@tool
def analyze_teacher(teacher_name: str | None = None, course: str | None = None) -> dict:
    """
    分析指定教师的评价，或对比某课程下的所有老师。

    Args:
        teacher_name: 教师姓名（支持模糊），与 course 二选一或同时提供
        course: 课程名称（支持模糊）；提供时返回该课程下各老师的评分对比

    Returns:
        教师模式: {"teacher", "courses", "avg_rating", "review_count", "reviews_sample"}
        课程模式: {"course", "teachers", "rating_avg", "rate_count", "reviews_sample"}
    """
    try:
        conn = _cdb()
    except sqlite3.Error:
        return {"error": "课程数据库不可用"}

    if not teacher_name and not course:
        conn.close()
        return {"error": "请提供教师姓名（teacher_name）或课程名称（course）"}

    # 课程模式: 按课程查老师对比（支持 "XX课哪个老师好"）
    if not teacher_name:
        matches = _match_courses(conn, course)
        if not matches:
            conn.close()
            return {"error": f"未找到课程：{course}"}
        # 同名课程可能有多条记录（不同 course_id）: 按 name 去重取样本量最大者
        best_by_name: dict[str, dict] = {}
        for m in matches:
            if m["name"] not in best_by_name or m["rating_count"] > best_by_name[m["name"]]["rating_count"]:
                best_by_name[m["name"]] = m
        uniq = list(best_by_name.values())
        if len(uniq) > 1:
            # 近似名课程多门（如 B1/B2 分班）, 交给用户确认, 避免误选
            conn.close()
            return {
                "ambiguity": True,
                "candidates": [
                    {"name": m["name"], "code": m["code"], "dept": m["dept"],
                     "rating_avg": m["rating_avg"], "rating_count": m["rating_count"]}
                    for m in uniq
                ],
                "message": f"找到多门与「{course}」相关的课程, 请指定确切课程名（如区分 B1/B2 分班）",
            }
        c = uniq[0]
        cid = c["id"]
        teachers = _teacher_cells(conn, cid)
        # 评论样本: 按老师分组取样（每师最多 2 条, 总量 ≤6）, 带 teacher 标注供引用
        reviews: list[dict] = []
        for t in teachers:
            reviews.extend(_top_reviews(conn, cid, t["name"], limit=2))
            if len(reviews) >= 6:
                break
        conn.close()
        return {
            "course": c["name"],
            "code": c["code"],
            "credit": c["credit"],
            "dept": c["dept"],
            "teachers": teachers,
            "rating_avg": round(c["rating_avg"], 1),
            "rate_count": c["rating_count"],
            "reviews_sample": reviews[:6],
        }

    # 老师模式: 教师名模糊匹配 course_teachers（含合教组合, 如"魏海明, 计永胜"）, 同课多组合取样本量大者
    rows = conn.execute(
        "SELECT c.id, c.name, ct.rating_avg, ct.rating_count "
        "FROM course_teachers ct JOIN courses c ON c.id = ct.course_id "
        "WHERE ct.teacher_id IN (SELECT id FROM teachers WHERE name LIKE ?) "
        "ORDER BY ct.rating_count DESC",
        (f"%{teacher_name}%",),
    ).fetchall()
    if not rows:
        conn.close()
        return {"error": f"未找到教师：{teacher_name}"}
    seen: dict[int, dict] = {}
    for r in rows:
        if r["id"] not in seen or r["rating_count"] > seen[r["id"]]["rate_count"]:
            seen[r["id"]] = {
                "name": r["name"],
                "rating_avg": round(r["rating_avg"], 1),
                "rate_count": r["rating_count"],
                "top_reviews": _top_reviews(conn, r["id"], teacher_name, limit=2),
            }
    courses = list(seen.values())
    courses.sort(key=lambda x: (-x["rating_avg"], -x["rate_count"]))

    n_reviews = sum(c["rate_count"] for c in courses)
    avg = round(sum(c["rating_avg"] * c["rate_count"] for c in courses) / max(n_reviews, 1), 1)
    sample = []
    for c in courses[:3]:
        sample.extend(c["top_reviews"])
    conn.close()
    return {
        "teacher": teacher_name,
        "courses": courses,
        "avg_rating": avg,
        "review_count": n_reviews,
        "reviews_sample": sample[:6],
    }
