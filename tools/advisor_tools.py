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


def _cdb() -> sqlite3.Connection:
    """每次新建连接（本地 sqlite 开销小, 避免缓存坏连接）。"""
    conn = sqlite3.connect(str(COURSE_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


# 偏好状态（session 级模块单例）
_current_profile: dict = {}


def get_profile() -> dict:
    return _current_profile


def update_profile(**kwargs):
    _current_profile.update(kwargs)


def reset_profile():
    global _current_profile
    _current_profile = {}


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


def _program_hint(conn: sqlite3.Connection, major: str | None, course_id: int) -> dict | None:
    """培养方案弱标注: 该课程是否出现在用户专业相关的方案中（必修/选修 + 学期标注）。"""
    if not major:
        return None
    hits = conn.execute(
        "SELECT pc.required, pc.term, pc.category, p.name, p.grade "
        "FROM program_courses pc JOIN programs p ON p.id = pc.program_id "
        "WHERE pc.course_id=? AND (p.name LIKE ? OR p.college LIKE ?) LIMIT 3",
        (course_id, f"%{major}%", f"%{major}%"),
    ).fetchall()
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
    if course["rate_count"] < 10:
        reasons.append("样本较少, 评分仅供参考")
    return reasons


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
                      keywords: list[str] | str | None = None, max_results: int = 5) -> dict:
    """
    根据用户画像推荐课程（真实均分降序, 同课多师并列, 附真实评论引用）。

    Args:
        profile: 用户画像, 格式: {"major": "计算机科学", "grade": "大二",
            "interests": ["人工智能", "数学"], "preference_type": "easy_grade|learn_hard|balanced",
            "max_results": 5}；也可省略 profile 直接用下面的顶层参数
        major: 专业名（可选，如 "计算机科学"）
        grade: 年级（可选，如 "大二"）
        interests: 兴趣方向（可选，列表或单个字符串，如 ["人工智能", "数学"]）
        preference_type: 偏好类型（可选，easy_grade=冲分保绩/learn_hard=硬核学习/balanced=均衡）
        preference: 中文偏好描述（可选，如 "给分好""不点名""任务少"→冲分保绩，"硬核""学东西"→硬核学习）
        keywords: 限定候选范围的关键词（可选，如 ["数学分析"]，匹配课程名/院系/类型；
            不要用“给分好”“不点名”这类偏好词）
        max_results: 返回条数（默认 5）

    Returns:
        {"recommendations": [...], "total_candidates": N, "filtered_count": N}
        每门课: {name, code, credit, dept, rating_avg, rate_count, dims, teachers,
                 terms, top_reviews, program_hint, reasons}
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
    p.setdefault("max_results", max_results)
    profile = p

    try:
        conn = _cdb()
    except sqlite3.Error:
        return {"recommendations": [], "total_candidates": 0, "filtered_count": 0, "error": "课程数据库不可用"}

    keywords = profile.get("keywords") or profile.get("interests") or []
    where, params = "r.rating_count > 0", []
    if keywords:
        like = " OR ".join("(c.name LIKE ? OR c.dept LIKE ? OR c.course_type LIKE ?)" for _ in keywords)
        where += f" AND ({like})"
        for k in keywords:
            params += [f"%{k}%", f"%{k}%", f"%{k}%"]
    rows = conn.execute(
        f"SELECT c.id, c.name, c.code, c.credit, c.dept, c.course_type, c.icourse_ids, "
        f"r.rating_avg, r.rating_count FROM courses c JOIN course_rates r ON r.course_id = c.id "
        f"WHERE {where} ORDER BY r.rating_avg DESC, r.rating_count DESC LIMIT 200",
        params,
    ).fetchall()
    keyword_fallback = False
    if not rows and keywords:
        # 关键词过窄（如偏好词被当课程名）无结果: 回退全量推荐
        rows = conn.execute(
            "SELECT c.id, c.name, c.code, c.credit, c.dept, c.course_type, c.icourse_ids, "
            "r.rating_avg, r.rating_count FROM courses c JOIN course_rates r ON r.course_id = c.id "
            "WHERE r.rating_count > 0 ORDER BY r.rating_avg DESC, r.rating_count DESC LIMIT 200"
        ).fetchall()
        keyword_fallback = True
    total = len(rows)

    recommendations = []
    for r in rows:
        cid = r["id"]
        teachers = _teacher_cells(conn, cid)
        multi = len(teachers) > 1
        # 评论引用: 单师 6 条; 同课多师每师最多 3 条, 总量封顶 6
        if multi:
            reviews = []
            for t in teachers:
                reviews.extend(_top_reviews(conn, cid, t["name"], limit=3))
            reviews = reviews[:6]
        else:
            reviews = _top_reviews(conn, cid, limit=6)
        item = {
            "id": cid,
            "name": r["name"],
            "code": r["code"],
            "credit": r["credit"],
            "dept": r["dept"],
            "course_type": r["course_type"],
            "rating_avg": round(r["rating_avg"], 1),
            "rate_count": r["rating_count"],
            "dims": _dims_info(conn, cid),
            "teachers": teachers,
            "multi_teacher": multi,
            "terms": _recent_terms(conn, cid),
            "top_reviews": reviews,
            "program_hint": _program_hint(conn, profile.get("major"), cid),
            "reasons": _generate_reason(conn, {"name": r["name"], "dept": r["dept"],
                                               "course_type": r["course_type"],
                                               "rate_count": r["rating_count"],
                                               "dims": _dims_info(conn, cid)}, profile),
        }
        recommendations.append(item)

    conn.close()
    max_results = profile.get("max_results", 5)
    return {
        "recommendations": recommendations[:max_results],
        "total_candidates": total,
        "filtered_count": len(recommendations[:max_results]),
        "profile_note": PROFILES.get(profile.get("preference_type", "balanced"), PROFILES["balanced"]),
        "keyword_fallback": keyword_fallback,
    }


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
        r = conn.execute(
            "SELECT c.id, c.name, c.code, c.credit, c.dept, r.rating_avg, r.rating_count "
            "FROM courses c JOIN course_rates r ON r.course_id = c.id "
            "WHERE c.name LIKE ? ORDER BY r.rating_count DESC LIMIT 1",
            (f"%{name}%",),
        ).fetchone()
        if not r:
            return None
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
        c = conn.execute(
            "SELECT c.id, c.name, c.code, c.credit, c.dept, r.rating_avg, r.rating_count "
            "FROM courses c JOIN course_rates r ON r.course_id = c.id "
            "WHERE c.name LIKE ? ORDER BY r.rating_count DESC LIMIT 1",
            (f"%{course}%",),
        ).fetchone()
        if not c:
            conn.close()
            return {"error": f"未找到课程：{course}"}
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
