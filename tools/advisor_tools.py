"""
小蜗 — 选课顾问 Agent 工具
数据源: data/course_data.db（icourse.club 真实评课, 8 表结构见 database/schema_course.sql）

推荐原则:
- 培养方案先约束候选：必修缺口优先，方案内选修优先于方向补充课
- 用户明确需求参与排序：兴趣、课程范围、工作量、给分、挑战性、教师和目标学期
- 自动画像只在用户没有明确需求时作为缺省信号，不能覆盖用户本轮表达
- 评分采用小样本收缩，避免少量满分评论压过稳定课程
- 无个人成绩、个人方案或实时排课时明确降级，不伪造匹配结论
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from langchain_core.tools import tool

from utils import course_name as _norm

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
    "作业少": "easy_grade", "低负担": "easy_grade", "摸鱼": "easy_grade",
    "省时": "easy_grade", "冲分": "easy_grade", "保绩": "easy_grade",
    "硬核": "learn_hard", "学东西": "learn_hard", "学到东西": "learn_hard",
    "挑战": "learn_hard", "收获": "learn_hard",
}

_VALID_PREFERENCES = frozenset(PROFILES)

# 兴趣词扩展只用于课程文本匹配，不改写用户原始需求，也不把扩展词展示成用户偏好。
_INTEREST_ALIASES = {
    "AI": ("人工智能", "机器学习", "深度学习", "模式识别", "计算机视觉", "自然语言处理", "智能"),
    "人工智能": ("人工智能", "机器学习", "深度学习", "模式识别", "计算机视觉", "自然语言处理", "智能"),
    "机器学习": ("机器学习", "深度学习", "模式识别", "数据挖掘", "神经网络", "人工智能"),
    "编程": ("编程", "程序设计", "程序设计实践", "软件"),
    "算法": ("算法", "组合数学", "离散数学", "运筹", "优化"),
    "数据": ("数据", "数据库", "统计", "信息"),
    "金融": ("金融", "经济", "投资", "计量"),
    "设计": ("设计", "艺术", "视觉", "交互"),
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


def _as_list(value) -> list[str]:
    """将工具的宽容列表参数归一为去空、保序、去重的字符串列表。"""
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _normalize_preference(value: str | None) -> str | None:
    """兼容英文枚举和自然语言偏好；无法识别时返回 None，避免静默套错画像。"""
    text = str(value or "").strip()
    if not text:
        return None
    if text in _VALID_PREFERENCES:
        return text
    for phrase, pref in _PREF_CN.items():
        if phrase in text:
            return pref
    return None


def _cdb() -> sqlite3.Connection:
    """每次新建连接（本地 sqlite 开销小, 避免缓存坏连接）。"""
    conn = sqlite3.connect(str(COURSE_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_course_name(name: str) -> str:
    """归一化课程名（共享实现 utils/course_name.norm_course_name）。

    去全角/半角括号、引号、空格与全角空格，ASCII 大写，使 '数学分析 (B1)'、
    '数学分析（B1）'、'数学分析 B1' 等变体收敛成同一 '数学分析B1'。"""
    return _norm.norm_course_name(name)


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


def _generate_reason(conn: sqlite3.Connection, course: dict, profile: dict,
                     rank: dict | None = None, program_hint: dict | None = None) -> list[str]:
    """只依据实际方案与评课信号生成解释；未知信息不包装成命中理由。"""
    reasons: list[str] = []
    rank = rank or {}
    hint = program_hint or {}
    status = rank.get("program_status")
    term = hint.get("term") or "未标注学期"
    if status == "required":
        if profile.get("_taken_known"):
            reasons.append(f"培养方案必修缺口（{term}），应优先安排")
        else:
            reasons.append(f"培养方案必修课（{term}）；尚未取得完整已修记录，请先确认是否已修")
    elif status == "elective":
        reasons.append(f"当前培养方案内选修课（{term}）")
    elif status == "outside":
        reasons.append("方向补充课，不在当前定位到的培养方案清单中")

    matched = rank.get("matched_interests") or []
    if matched:
        if profile.get("_auto_interests"):
            reasons.append(f"根据已修课程推测可能相关的方向「{'、'.join(matched[:3])}」")
        else:
            reasons.append(f"匹配本轮需求「{'、'.join(matched[:3])}」")
    matched_teachers = rank.get("matched_teachers") or []
    if matched_teachers:
        reasons.append(f"包含偏好的教师「{'、'.join(matched_teachers[:3])}」")

    pref = profile.get("preference_type", "balanced")
    dims = course["dims"]["avg"]
    if profile.get("workload_preference") == "low":
        if dims.get("作业", 0) >= 7.5:
            reasons.append("作业负担评价较低，符合省时需求")
        elif dims.get("作业", 0):
            reasons.append("作业负担评价不低，与省时需求并非完全匹配")
    elif pref == "easy_grade":
        if dims.get("给分", 0) >= 7.5:
            reasons.append("给分评价较好，适合冲分保绩")
        if dims.get("难度", 0) and dims["难度"] <= 4.5:
            reasons.append("难度评价偏高，冲分需谨慎")
    elif pref == "learn_hard":
        if dims.get("收获", 0) >= 7.5:
            reasons.append("收获评价较高，适合深入学习")
        if dims.get("难度", 0) and dims["难度"] <= 4.5:
            reasons.append("课程有挑战性")
    if profile.get("_auto_pref") and profile.get("gpa") is not None:
        reasons.append(f"未指定偏好时按 GPA {profile['gpa']} 使用「{PROFILES.get(pref, {}).get('name', pref)}」缺省排序")
    if course["rate_count"] < 10:
        reasons.append("评价样本较少，排序已做小样本收缩")
    return reasons


# ───────────────────────── 方案分组辅助 ─────────────────────────
#
# 必修组 + 选修组两段式推荐（任务 2）。方案定位统一收敛到 tools/_program_resolve.py
# （Phase 2b：与 tools/program_tools.py 共用同一实现，口径一致）。

from tools import _program_resolve as _pr


def _parse_grade_key(grade: str) -> int:
    """年级 → 可排序整数（"2024级"→2024，"大二"→无法解析返回 0）。"""
    return _pr.parse_grade_key(grade)


def _prog_priority(r) -> int:
    """方案类型优先级：普通专业方案 0；英才班/带括号特殊方案 1；辅修 2（见 _pr.prog_priority）。"""
    return _pr.prog_priority(r)


def _resolve_program(conn: sqlite3.Connection, major: str | None,
                     grade: str | None = None) -> tuple[int | None, str | None]:
    """全量库方案定位（共享实现）：同年级 → 最近低年级 → 最新；同年级内普通专业方案优先。

    Returns:
        (program_id, program_name)；无 major 或未命中时 (None, None)。
    """
    row = _pr.resolve_program(conn, major, grade)
    if row is None:
        return None, None
    return row["id"], row["name"]


def _parse_term_year(term: str) -> int | None:
    """从 term 首字符解析学年号（"2秋"→2）；无前缀/无法解析返 None。"""
    m = re.match(r"\s*(\d)", term or "")
    return int(m.group(1)) if m else None


def _term_urgency(term: str, current_yi: int | None,
                  target_term: str | None = None) -> int:
    """学期紧迫度档位（必修组内排序）：
    0=已过期应修未修（学年号 < current_year_index）置顶；
    1=当前学年且为下学期（1-8 月面向秋季、9-12 月面向春季）该修；
    2=当前学年但非下学期（可稍后修）；3=未来学年或无法解析（最后）。
    春/秋区分避免「2春」与「2秋」同档按评分乱排（如 8 月选课应 2秋 优先于 2春）。"""
    segments = [s.strip() for s in re.split(r"[,，、/]", term or "") if s.strip()]
    if not segments:
        return 3

    target = _canonical_target_term(target_term, current_yi)
    if target:
        if any(target == s for s in segments):
            return 0
        target_year = _parse_term_year(target)
        years = [y for y in (_parse_term_year(s) for s in segments) if y is not None]
        if target_year is not None and years:
            return 1 if min(years) < target_year else 3
        return 3

    def _one(segment: str) -> int:
        y = _parse_term_year(segment)
        if y is None or current_yi is None:
            return 3
        if y < current_yi:
            return 0
        if y > current_yi:
            return 3
        # 当前学年：区分春秋——「下学期」优先（8 月前面向秋季选课, 9 月起面向春季选课）
        next_is_autumn = date.today().month <= 8
        season = "秋" if "秋" in segment else "春"
        return 1 if (season == "秋") == next_is_autumn else 2

    return min(_one(segment) for segment in segments)


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


def _canonical_target_term(value: str | None, current_yi: int | None) -> str | None:
    """把用户目标学期归一为培养方案标签（如“大二上/下学期”→“2秋/2春”）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if text in {"next", "下学期", "下个学期"} and current_yi is not None:
        return f"{current_yi}{'秋' if date.today().month <= 8 else '春'}"
    m = re.search(r"([1-6])\s*(秋|春|夏)", text)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    zh = "一二三四五六"
    m = re.search(r"大([一二三四五六])\s*(?:年级)?\s*(上|下|秋|春)", text)
    if m:
        year = zh.index(m.group(1)) + 1
        season = "秋" if m.group(2) in {"上", "秋"} else "春"
        return f"{year}{season}"
    return None


def _urgency_label(urgency: int, target_term: str | None = None) -> str:
    if target_term:
        return {0: "target", 1: "earlier", 3: "future"}.get(urgency, "unknown")
    return {0: "overdue", 1: "next", 2: "same_year", 3: "future"}.get(urgency, "unknown")


def _row_dict(row) -> dict:
    return dict(row) if not isinstance(row, dict) else dict(row)


def _metric(row: dict, key: str, default: float = 6.0) -> float:
    try:
        value = float(row.get(key))
    except (TypeError, ValueError):
        return default
    return min(max(value, 0.0), 10.0) if value > 0 else default


def _adjusted_rating(row: dict) -> float:
    """对真实均分做轻量贝叶斯收缩，降低 1-2 条满分评论的排序优势。"""
    try:
        rating = float(row.get("rating_avg") or 0)
        count = max(int(row.get("rating_count") or 0), 0)
    except (TypeError, ValueError):
        return 7.0
    prior_count, prior_mean = 8, 7.0
    return (rating * count + prior_mean * prior_count) / (count + prior_count)


def _candidate_text(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in (
        "name", "dept", "course_type", "course_level", "program_category",
    ))


def _expanded_interest_terms(term: str) -> tuple[str, ...]:
    direct = _INTEREST_ALIASES.get(term)
    if direct:
        return direct
    upper = term.upper()
    return _INTEREST_ALIASES.get(upper, (term,))


def _match_need_terms(row: dict, terms: list[str]) -> tuple[list[str], float]:
    """返回强命中的原始需求词及加权覆盖率；院系弱命中不会冒充课程内容命中。"""
    if not terms:
        return [], 0.0
    primary = _norm_course_name(" ".join(str(row.get(k) or "") for k in (
        "name", "course_level", "program_category",
    )))
    secondary = _norm_course_name(" ".join(str(row.get(k) or "") for k in (
        "dept", "course_type",
    )))
    matched = []
    scores = []
    for term in terms:
        aliases = _expanded_interest_terms(term)
        direct = _norm_course_name(term)
        alias_norms = [_norm_course_name(alias) for alias in aliases if alias]
        if direct and direct in primary:
            score = 1.0
        elif any(alias in primary for alias in alias_norms):
            score = 0.85
        elif direct and direct in secondary:
            score = 0.45
        elif any(alias in secondary for alias in alias_norms):
            score = 0.25
        else:
            score = 0.0
        scores.append(score)
        if score >= 0.5:
            matched.append(term)
    return matched, sum(scores) / len(terms)


def _matches_scope(row: dict, scope: str | None) -> bool:
    if scope != "general":
        return True
    text = _candidate_text(row)
    return any(k in text for k in ("通识", "通修", "人文", "社科", "文化素质"))


def _preference_score(row: dict, profile: dict) -> float:
    """把评课四维映射为 0-1 需求适配分；缺维度时使用中性值而非猜测。"""
    rating = _adjusted_rating(row) / 10.0
    ease = _metric(row, "diff_avg") / 10.0       # 高分 = 评价更简单
    workload = _metric(row, "hw_avg") / 10.0     # 高分 = 作业更少
    grading = _metric(row, "score_avg") / 10.0
    gain = _metric(row, "gain_avg") / 10.0
    if profile.get("workload_preference") == "low":
        return 0.55 * workload + 0.15 * ease + 0.15 * grading + 0.15 * rating
    pref = profile.get("preference_type") or "balanced"
    if pref == "easy_grade":
        return 0.35 * grading + 0.25 * workload + 0.20 * ease + 0.20 * rating
    if pref == "learn_hard":
        challenge = 1.0 - ease
        return 0.45 * gain + 0.25 * challenge + 0.25 * rating + 0.05 * (1.0 - workload)
    return 0.55 * rating + 0.20 * gain + 0.15 * grading + 0.10 * workload


def _teacher_map(conn: sqlite3.Connection, rows: list[dict]) -> dict[int, set[str]]:
    ids = sorted({int(r["id"]) for r in rows if r.get("id") is not None})
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    out: dict[int, set[str]] = {}
    sql = (
        "SELECT ct.course_id AS course_id, t.name AS teacher FROM course_teachers ct "
        "JOIN teachers t ON t.id=ct.teacher_id WHERE ct.course_id IN (" + placeholders + ") "
        "UNION ALL SELECT course_id, teacher FROM reviews WHERE course_id IN (" + placeholders + ")"
    )
    for hit in conn.execute(sql, ids + ids):
        if hit["teacher"]:
            out.setdefault(int(hit["course_id"]), set()).update(_norm_teacher(hit["teacher"]))
    return out


def _rank_rows(conn: sqlite3.Connection, rows: list, profile: dict,
               current_yi: int | None, program_status: str) -> list[dict]:
    candidates = [_row_dict(row) for row in rows]
    preferred_teachers = _as_list(profile.get("preferred_teachers"))
    teachers_by_course = _teacher_map(conn, candidates) if preferred_teachers else {}
    need_terms = _as_list(profile.get("keywords")) + [
        x for x in _as_list(profile.get("interests"))
        if x not in _as_list(profile.get("keywords"))
    ]
    target_term = _canonical_target_term(profile.get("target_term"), current_yi)
    explicit_pref = bool(profile.get("_explicit_preference") or profile.get("workload_preference"))
    explicit_needs = bool(profile.get("_explicit_needs"))
    hard_preferences = set(_as_list(profile.get("_hard_preferences")))
    ranked_candidates = []

    for row in candidates:
        matched_interests, interest_score = _match_need_terms(row, need_terms)
        hard_interest_matches, _ = _match_need_terms(
            row, _as_list(profile.get("interests")),
        )
        course_teachers = teachers_by_course.get(int(row["id"]), set()) if row.get("id") else set()
        matched_teachers = [wanted for wanted in preferred_teachers
                            if any(wanted in actual or actual in wanted for actual in course_teachers)]
        teacher_score = len(matched_teachers) / len(preferred_teachers) if preferred_teachers else 0.0
        pref_score = _preference_score(row, profile)

        # “只要/必须”才把偏好升级为硬条件。无法从现有数据核验时不放宽条件。
        if "interests" in hard_preferences and not hard_interest_matches:
            continue
        if "workload_preference" in hard_preferences and _metric(row, "hw_avg") < 7.5:
            continue
        if "preferred_teachers" in hard_preferences and not matched_teachers:
            continue
        if "target_term" in hard_preferences:
            segments = [s.strip() for s in re.split(",|，|、|/", row.get("program_term") or "") if s.strip()]
            if not target_term or target_term not in segments:
                continue

        weighted: list[tuple[float, float]] = []
        if need_terms:
            weighted.append((interest_score, 0.55))
        if explicit_pref:
            weighted.append((pref_score, 0.30))
        if preferred_teachers:
            weighted.append((teacher_score, 0.15))
        if weighted:
            need_score = sum(v * w for v, w in weighted) / sum(w for _, w in weighted)
        else:
            # GPA 自动画像或均衡缺省只作为弱排序信号。
            need_score = pref_score

        urgency = _term_urgency(row.get("program_term") or "", current_yi, target_term)
        program_fit = {0: 1.0, 1: 0.9, 2: 0.65, 3: 0.25}.get(urgency, 0.25)
        quality = _adjusted_rating(row) / 10.0
        auto_needs = bool(profile.get("_auto_interests"))
        if program_status == "required":
            score = (0.45 * program_fit + 0.35 * need_score + 0.20 * quality
                     if explicit_needs else (
                         0.60 * program_fit + 0.10 * need_score + 0.30 * quality
                         if auto_needs else 0.65 * program_fit + 0.35 * quality
                     ))
        elif program_status == "elective":
            score = (0.30 * program_fit + 0.45 * need_score + 0.25 * quality
                     if explicit_needs else (
                         0.35 * program_fit + 0.20 * need_score + 0.45 * quality
                         if auto_needs else 0.45 * program_fit + 0.55 * quality
                     ))
        else:
            score = 0.65 * need_score + 0.35 * quality if explicit_needs else quality

        row["_rank"] = {
            "score": round(score, 4),
            "program_status": program_status,
            "urgency": _urgency_label(urgency, target_term),
            "urgency_rank": urgency,
            "matched_interests": matched_interests,
            "matched_teachers": matched_teachers,
            "interest_score": round(interest_score, 3),
            "preference_score": round(pref_score, 3),
            "adjusted_rating": round(_adjusted_rating(row), 2),
        }
        ranked_candidates.append(row)

    candidates = ranked_candidates

    if program_status == "required":
        candidates.sort(key=lambda r: (
            r["_rank"]["urgency_rank"], -r["_rank"]["interest_score"],
            -r["_rank"]["score"], -int(r.get("rating_count") or 0),
        ))
    elif program_status == "elective":
        candidates.sort(key=lambda r: (
            -r["_rank"]["score"], r["_rank"]["urgency_rank"],
            -int(r.get("rating_count") or 0),
        ))
    else:
        candidates.sort(key=lambda r: (-r["_rank"]["score"], -int(r.get("rating_count") or 0)))
    return candidates


def _pool_rows(conn: sqlite3.Connection, keywords: list[str], limit: int = 200) -> list:
    """全量评分候选池（真实均分降序），keywords 可选过滤课程名/院系/类型。

    不合并数据适配：同名多页（每师一页）按归一课名去重，保留均分最高页；
    同课多师的完整对比由 analyze_teacher 课程模式跨页聚合提供。"""
    where, params = "r.rating_count > 0", []
    if keywords:
        like = " OR ".join(
            f"({_SQL_NORM_NAME} LIKE ? OR c.dept LIKE ? OR c.course_type LIKE ? "
            f"OR c.course_level LIKE ?)"
            for _ in keywords
        )
        where += f" AND ({like})"
        for k in keywords:
            params += [f"%{_norm_course_name(k)}%", f"%{k}%", f"%{k}%", f"%{k}%"]
    rows = conn.execute(
        f"SELECT c.id, c.name, c.code, c.credit, c.dept, c.course_type, c.course_level, c.icourse_ids, "
        f"r.rating_avg, r.rating_count, r.diff_avg, r.hw_avg, r.score_avg, r.gain_avg "
        f"FROM courses c JOIN course_rates r ON r.course_id = c.id "
        f"WHERE {where} ORDER BY r.rating_avg DESC, r.rating_count DESC LIMIT ?",
        params + [limit],
    ).fetchall()
    seen_names: set[str] = set()
    out = []
    for r in rows:
        nk = _norm_course_name(r["name"])
        if nk in seen_names:
            continue
        seen_names.add(nk)
        out.append(r)
    return out


def _merge_candidate_rows(*pools: list) -> list[dict]:
    """合并聚焦池与宽池，同名课程只保留先出现的高优先候选。"""
    out: list[dict] = []
    seen: set[str] = set()
    for pool in pools:
        for raw in pool:
            row = _row_dict(raw)
            key = _norm_course_name(row.get("name") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def _candidate_pool(conn: sqlite3.Connection, profile: dict,
                    hard_keywords: list[str]) -> tuple[list[dict], bool]:
    """构造候选池：keywords 保持硬限定，其余兴趣只负责聚焦和排序。"""
    if hard_keywords:
        return [_row_dict(r) for r in _pool_rows(conn, hard_keywords, limit=400)], False
    interests = _as_list(profile.get("interests"))
    focused = _pool_rows(conn, interests, limit=250) if interests else []
    broad = _pool_rows(conn, None, limit=400)
    return _merge_candidate_rows(focused, broad), bool(interests and not focused)


def _build_item(conn: sqlite3.Connection, row, profile: dict) -> dict:
    """由课程行构建完整推荐条目（字段与旧实现一致）。
    row 需含 id/name/code/credit/dept/course_type/rating_avg/rating_count。"""
    row = _row_dict(row)
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
    if row.get("program_name"):
        program_hint = {
            "required": row.get("program_required") or "",
            "term": row.get("program_term") or "",
            "category": row.get("program_category") or "",
            "program": row.get("program_name") or "",
            "grade": row.get("program_grade") or "",
            "source": row.get("program_source") or "local",
        }
    else:
        program_hint = _program_hint(conn, profile.get("major"), cid, profile.get("grade"))
    rank = dict(row.get("_rank") or {})
    item = {
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
        "program_hint": program_hint,
        "recommendation_score": rank.get("score"),
        "match": {k: v for k, v in rank.items() if k != "urgency_rank"},
    }
    item["reasons"] = _generate_reason(conn, item, profile, rank, program_hint)
    return item


_RATED_SELECT = (
    "c.id, c.name, c.code, c.credit, c.dept, c.course_type, c.course_level, c.icourse_ids, "
    "r.rating_avg, r.rating_count, r.diff_avg, r.hw_avg, r.score_avg, r.gain_avg"
)


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    """同名多个评课页只保留样本量较大的一个；必修身份优先于选修身份。"""
    best: dict[str, dict] = {}
    for raw in rows:
        row = _row_dict(raw)
        key = _norm_course_name(row.get("name") or "")
        old = best.get(key)
        if old is None:
            best[key] = row
            continue
        old_required = old.get("program_required") == "必修"
        new_required = row.get("program_required") == "必修"
        if new_required and not old_required:
            best[key] = row
        elif new_required == old_required and int(row.get("rating_count") or 0) > int(old.get("rating_count") or 0):
            best[key] = row
    return list(best.values())


def _load_local_program_rows(conn: sqlite3.Connection, prog_id: int,
                             prog_name: str) -> tuple[list[dict], int, list[str]]:
    meta = conn.execute("SELECT grade FROM programs WHERE id=?", (prog_id,)).fetchone()
    grade = meta["grade"] if meta else ""
    rows = conn.execute(
        f"SELECT {_RATED_SELECT}, pc.required AS program_required, pc.term AS program_term, "
        "pc.category AS program_category FROM program_courses pc "
        "JOIN courses c ON c.id=pc.course_id JOIN course_rates r ON r.course_id=c.id "
        "WHERE pc.program_id=? AND pc.course_id IS NOT NULL",
        (prog_id,),
    ).fetchall()
    out = []
    for raw in rows:
        row = _row_dict(raw)
        row.update({"program_name": prog_name, "program_grade": grade, "program_source": "generic"})
        out.append(row)
    all_rows = conn.execute(
        "SELECT name FROM program_courses WHERE program_id=?", (prog_id,),
    ).fetchall()
    rated = {_norm_course_name(r.get("name") or "") for r in out}
    unmatched = [r["name"] for r in all_rows if _norm_course_name(r["name"]) not in rated]
    return _dedupe_rows(out), len(all_rows), unmatched


def _find_rated_course(conn: sqlite3.Connection, course: dict) -> dict | None:
    row = None
    code = str(course.get("code") or "").strip()
    if code:
        row = conn.execute(
            f"SELECT {_RATED_SELECT} FROM courses c JOIN course_rates r ON r.course_id=c.id "
            "WHERE UPPER(c.code)=UPPER(?) ORDER BY r.rating_count DESC LIMIT 1",
            (code,),
        ).fetchone()
    if row is None and course.get("name"):
        row = conn.execute(
            f"SELECT {_RATED_SELECT} FROM courses c JOIN course_rates r ON r.course_id=c.id "
            f"WHERE {_SQL_NORM_NAME}=? ORDER BY r.rating_count DESC LIMIT 1",
            (_norm_course_name(course["name"]),),
        ).fetchone()
    return _row_dict(row) if row is not None else None


def _load_personal_program_rows(conn: sqlite3.Connection, personal_tree) \
        -> tuple[list[dict], int, list[str]]:
    from tools.program_tools import _parse_tree

    courses = _parse_tree(personal_tree) if personal_tree else []
    out = []
    unmatched = []
    for course in courses:
        row = _find_rated_course(conn, course)
        if row is None:
            unmatched.append(course.get("name") or course.get("code") or "未命名课程")
            continue
        row.update({
            "program_required": course.get("required") or "",
            "program_term": course.get("term") or "",
            "program_category": course.get("category") or "",
            "program_name": "个人培养方案",
            "program_grade": "",
            "program_source": "personal",
        })
        out.append(row)
    return _dedupe_rows(out), len(courses), unmatched


def _split_targets(max_results: int, profile: dict) -> tuple[int, int]:
    """根据用户明确课程范围分配必修/非必修名额；明确需求时给选修更多空间。"""
    scope = profile.get("course_scope") or "all"
    if scope in {"elective", "general"}:
        return 0, max_results
    if scope == "required":
        return max_results, 0
    if max_results <= 1:
        return max_results, 0
    ratio = 0.4 if profile.get("_explicit_needs") else 0.6
    req = max(1, int(round(max_results * ratio)))
    return req, max_results - req


def _recommend_flat(conn: sqlite3.Connection, profile: dict, keywords: list[str],
                    max_results: int, taken: set[str]) -> dict:
    """无方案命中：按用户需求 + 可靠度排序，并明确标记为无方案降级。"""
    scope = profile.get("course_scope") or "all"
    if scope in {"required", "elective"}:
        return {
            "recommendations": [], "groups": None, "progress": None,
            "total_candidates": 0, "filtered_count": 0, "keyword_fallback": False,
            "need_fallback": False, "scope_unavailable": scope,
            "program_context": {
                "matched": False, "source": "unavailable", "name": None,
                "taken_courses_known": bool(profile.get("_taken_known")),
            },
        }
    rows, need_fallback = _candidate_pool(conn, profile, keywords)
    filtered = [_row_dict(r) for r in rows
                if _norm_course_name(r["name"]) not in taken and _matches_scope(_row_dict(r), scope)]
    ranked = _rank_rows(conn, filtered, profile, profile.get("current_year_index"), "unscoped")
    if profile.get("_explicit_interests") and not any(
        row.get("_rank", {}).get("matched_interests") for row in ranked
    ):
        need_fallback = True
    items = [_build_item(conn, row, profile) for row in ranked[:max_results]]
    return {
        "recommendations": items,
        "groups": None,
        "progress": None,
        "total_candidates": len(ranked),
        "filtered_count": len(items),
        "keyword_fallback": False,
        "need_fallback": need_fallback,
        "program_context": {
            "matched": False,
            "source": "unavailable",
            "name": None,
            "taken_courses_known": bool(profile.get("_taken_known")),
        },
    }


def _recommend_grouped(conn: sqlite3.Connection, profile: dict, keywords: list[str],
                       max_results: int, taken: set[str], current_yi: int | None,
                       program_rows: list[dict], program_name: str,
                       program_source: str, program_total: int,
                       unmatched: list[str]) -> dict:
    """培养方案内必修/选修先分层，再用本轮明确需求和可靠评分排序。"""
    scope = profile.get("course_scope") or "all"

    def _hard_match(row: dict) -> bool:
        return not keywords or bool(_match_need_terms(row, keywords)[0])

    available = [row for row in program_rows
                 if _norm_course_name(row.get("name") or "") not in taken and _hard_match(row)]
    req_rows = [row for row in available if row.get("program_required") == "必修"] \
        if scope in {"all", "required"} else []
    elec_rows = [row for row in available if row.get("program_required") != "必修"
                 and _matches_scope(row, scope)] if scope in {"all", "elective", "general"} else []
    ranked_required = _rank_rows(conn, req_rows, profile, current_yi, "required")
    ranked_elective = _rank_rows(conn, elec_rows, profile, current_yi, "elective")

    program_names = {_norm_course_name(row.get("name") or "") for row in program_rows}
    allow_exploratory = bool(profile.get("_explicit_interests")) and scope in {"all", "general"}
    pool, need_fallback = _candidate_pool(conn, profile, keywords) if allow_exploratory else ([], False)
    outside_rows = []
    for raw in pool:
        row = _row_dict(raw)
        name = _norm_course_name(row.get("name") or "")
        if name in taken or name in program_names or not _matches_scope(row, scope):
            continue
        outside_rows.append(row)
    ranked_outside = _rank_rows(conn, outside_rows, profile, current_yi, "outside")
    # 方向补充必须真的命中本轮明确方向；不匹配的课不能仅凭高分进入该组。
    ranked_outside = [row for row in ranked_outside
                      if row.get("_rank", {}).get("matched_interests")]
    if profile.get("_explicit_interests"):
        need_fallback = not any(
            row.get("_rank", {}).get("matched_interests")
            for row in (ranked_required + ranked_elective + ranked_outside)
        )

    req_target, nonreq_target = _split_targets(max_results, profile)
    req_sel_rows = ranked_required[:req_target]
    nonreq_target += max(req_target - len(req_sel_rows), 0)

    need_terms = keywords or _as_list(profile.get("interests"))
    outside_target = 0
    if ranked_outside and nonreq_target:
        if need_terms:
            outside_target = min(max(1, round(nonreq_target * 0.35)), nonreq_target)
        elif len(ranked_elective) < nonreq_target:
            outside_target = min(nonreq_target - len(ranked_elective), len(ranked_outside))
    elec_target = max(nonreq_target - outside_target, 0)
    elec_sel_rows = ranked_elective[:elec_target]
    outside_target += max(elec_target - len(elec_sel_rows), 0)
    outside_sel_rows = ranked_outside[:outside_target]
    if len(outside_sel_rows) < outside_target:
        extra = outside_target - len(outside_sel_rows)
        elec_sel_rows.extend(ranked_elective[len(elec_sel_rows):len(elec_sel_rows) + extra])

    # 若非必修候选不足，通用/必修请求可继续补充必修；明确只要选修时不越过用户范围。
    selected_count = len(req_sel_rows) + len(elec_sel_rows) + len(outside_sel_rows)
    if selected_count < max_results and scope not in {"elective", "general"}:
        need = max_results - selected_count
        req_sel_rows.extend(ranked_required[len(req_sel_rows):len(req_sel_rows) + need])

    required = [_build_item(conn, row, profile) for row in req_sel_rows]
    elective = [_build_item(conn, row, profile) for row in elec_sel_rows]
    exploratory = [_build_item(conn, row, profile) for row in outside_sel_rows]

    progress = None
    if profile.get("_taken_known"):
        required_all = [row for row in program_rows if row.get("program_required") == "必修"]
        taken_required = [row for row in required_all
                          if _norm_course_name(row.get("name") or "") in taken]
        progress = {
            "required_taken": len(taken_required),
            "required_total": len(required_all),
            "required_remaining": len(required_all) - len(taken_required),
            "credits_taken": round(sum(float(row.get("credit") or 0) for row in taken_required), 1),
        }

    recommendations = required + elective + exploratory
    return {
        "recommendations": recommendations,
        "groups": {"required": required, "elective": elective, "exploratory": exploratory},
        "progress": progress,
        "total_candidates": len(ranked_required) + len(ranked_elective) + len(ranked_outside),
        "filtered_count": len(recommendations),
        "keyword_fallback": False,
        "need_fallback": need_fallback,
        "program_context": {
            "matched": True,
            "source": program_source,
            "name": program_name,
            "course_count": program_total,
            "rated_course_count": len(program_rows),
            "unrated_course_count": len(unmatched),
            "taken_courses_known": bool(profile.get("_taken_known")),
            "target_term": _canonical_target_term(profile.get("target_term"), current_yi),
        },
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
                      gpa: float | None = None,
                      workload_preference: str | None = None,
                      course_scope: str | None = None,
                      preferred_teachers: list[str] | str | None = None,
                      target_term: str | None = None,
                      personal_tree: dict | list | None = None) -> dict:
    """
    根据培养方案和用户本轮需求推荐课程。先排除已修课并按方案分为必修、方案内选修，
    再综合兴趣、负担、给分、挑战、教师、目标学期与小样本收缩评分排序；方案外课程
    只作为透明标注的方向补充。无方案或个人数据时明确降级。

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
        max_results: 返回条数（默认 10；明确用户需求时约 4 必修 + 6 非必修）
        taken_courses: 已修课程名列表（可选，登录后由上层从成绩单读取传入；
            None 表示未知，不会声称课程是“未修缺口”）
        current_year_index: 当前学年（可选，1=大一，2=大二…）；None 时按 profile.grade
            推算（"大二"→2；"2024级"→当前日期所在学年）
        current_term: 当前学期标识（可选，如 "2026秋"）；None 时由当前日期推断
        gpa: 4.3 制 GPA（可选，登录用户由上层从成绩单计算注入）；未显式指定
            preference_type 时按 GPA 自动推断画像（≥3.7 硬核 / ≤2.7 冲分 / 其余均衡）
        workload_preference: 工作量偏好（low=低负担）
        course_scope: all|required|elective|general（全部/必修/选修/通识）
        preferred_teachers: 偏好的教师姓名列表
        target_term: 目标方案学期，如 2秋、大二上、下学期
        personal_tree: 登录用户的个人培养方案树；有数据时优先于本地通用方案

    Returns:
        {"recommendations": [...], "groups": {"required": [...], "elective": [...],
          "exploratory": [...]},
         "progress": {"required_taken", "required_total", "credits_taken"},
         "total_candidates": N, "filtered_count": N, "profile_note": {...}, "keyword_fallback": bool}
        每门课: {name, code, credit, dept, rating_avg, rate_count, dims, teachers,
                 terms, top_reviews, program_hint, reasons}
        recommendations = 必修 + 方案内选修 + 方向补充拼接；无方案时 groups 为 None。
    """
    # 宽容参数：顶层显式参数覆盖 profile 中的缺省值，避免旧画像盖过用户本轮表达。
    p = dict(profile or {})
    if major is not None:
        p["major"] = major
    if grade is not None:
        p["grade"] = grade
    if interests is not None:
        p["interests"] = _as_list(interests)
    if keywords is not None:
        p["keywords"] = _as_list(keywords)
    if taken_courses is not None:
        p["taken_courses"] = _as_list(taken_courses)
    if current_year_index is not None:
        p["current_year_index"] = current_year_index
    if current_term is not None:
        p["current_term"] = current_term
    if gpa is not None:
        p["gpa"] = gpa
    if workload_preference is not None:
        p["workload_preference"] = workload_preference
    if course_scope is not None:
        p["course_scope"] = course_scope
    if preferred_teachers is not None:
        p["preferred_teachers"] = _as_list(preferred_teachers)
    if target_term is not None:
        p["target_term"] = target_term

    raw_pref = preference_type if preference_type is not None else (
        preference if preference is not None else p.get("preference_type") or p.get("preference")
    )
    normalized_pref = _normalize_preference(raw_pref)
    if normalized_pref:
        p["preference_type"] = normalized_pref
        p["_explicit_preference"] = True
    else:
        p.pop("preference_type", None)

    if raw_pref and any(term in str(raw_pref) for term in ("任务少", "作业少", "低负担", "省时", "轻松", "水课")):
        p.setdefault("workload_preference", "low")

    workload = str(p.get("workload_preference") or "").lower()
    if workload and any(k in workload for k in ("low", "少", "低", "轻", "省时")):
        p["workload_preference"] = "low"
        if not p.get("_explicit_preference"):
            p["preference_type"] = "easy_grade"
        p["_explicit_preference"] = True
    elif workload:
        p.pop("workload_preference", None)

    scope_text = str(p.get("course_scope") or "all").lower()
    scope = next((value for value, aliases in {
        "required": ("required", "必修", "补修", "缺口"),
        "elective": ("elective", "选修", "任选"),
        "general": ("general", "通识", "通修"),
    }.items() if any(alias in scope_text for alias in aliases)), "all")
    p["course_scope"] = scope
    p["interests"] = _as_list(p.get("interests"))
    p["keywords"] = _as_list(p.get("keywords"))
    p["preferred_teachers"] = _as_list(p.get("preferred_teachers"))
    if "_explicit_interests" not in p:
        p["_explicit_interests"] = bool(p["interests"] and not p.get("_auto_interests"))
    hard_preferences = set(_as_list(p.get("_hard_preferences")))
    p["_hard_preferences"] = [key for key in (
        "interests", "workload_preference", "preferred_teachers", "target_term",
    ) if key in hard_preferences]
    p["_taken_known"] = "taken_courses" in p and p.get("taken_courses") is not None

    explicit_fields = (
        p["interests"], p["keywords"], p.get("_explicit_preference"),
        p["preferred_teachers"], scope != "all", p.get("target_term"),
        p["_hard_preferences"],
    )
    p["_explicit_needs"] = bool(p.get("_explicit_needs") or any(explicit_fields))
    p.setdefault("max_results", max_results)
    try:
        p["max_results"] = min(max(int(p.get("max_results") or 10), 1), 20)
    except (TypeError, ValueError):
        p["max_results"] = 10
    profile = p

    # 用户明确需求永远优先；只有缺少明确偏好时才按 GPA 选择缺省画像。
    if not profile.get("preference_type"):
        profile["preference_type"] = _infer_preference(profile)
        if profile.get("gpa") is not None:
            profile["_auto_pref"] = True

    try:
        conn = _cdb()
    except sqlite3.Error:
        return {"recommendations": [], "groups": None, "progress": None,
                "total_candidates": 0, "filtered_count": 0, "error": "课程数据库不可用"}

    hard_keywords = profile.get("keywords") or []
    max_results = profile["max_results"]

    # 当前学年：显式传入优先，否则按年级推算
    current_yi = profile.get("current_year_index")
    if current_yi is None:
        current_yi = _infer_current_year_index(profile.get("grade"))
    profile["current_year_index"] = current_yi
    # 已修课程（归一化集合）; 未提供视为全部未修。
    # (L) 实验/语言班型后缀兼容：成绩里 "计算机程序设计(L)" 视同 "计算机程序设计" 已修。
    taken: set[str] = set()
    for tc in (profile.get("taken_courses") or []):
        n = _norm_course_name(tc)
        taken.add(n)
        if n.endswith("L") and len(n) > 1:
            taken.add(n[:-1])
    # 无明确兴趣时才由已修课程推测方向；该信号权重低于用户本轮表达。
    if not profile.get("interests") and taken:
        auto_ints = [kw for kw in _TAKEN_INTEREST_KW
                     if any(kw in n for n in (profile.get("taken_courses") or []))]
        if auto_ints:
            profile["interests"] = auto_ints[:5]
            profile["_auto_interests"] = True

    program_rows: list[dict] = []
    program_total = 0
    unmatched: list[str] = []
    program_name = ""
    program_source = ""
    personal_requested = bool(personal_tree is not None or profile.get("_personal_program_expected"))
    if personal_tree is not None:
        try:
            program_rows, program_total, unmatched = _load_personal_program_rows(conn, personal_tree)
        except Exception:
            program_rows, program_total, unmatched = [], 0, []
        if program_total:
            program_name, program_source = "个人培养方案", "personal"

    # 个人方案为空/不可用时，按专业和年级定位本地通用方案。
    try:
        prog_id, prog_name = _resolve_program(conn, profile.get("major"), profile.get("grade"))
    except sqlite3.Error:
        prog_id, prog_name = None, None
    if not program_total and prog_id is not None:
        program_rows, program_total, unmatched = _load_local_program_rows(conn, prog_id, prog_name)
        program_name, program_source = prog_name, "generic"

    if not program_total:
        result = _recommend_flat(conn, profile, hard_keywords, max_results, taken)
    else:
        result = _recommend_grouped(
            conn, profile, hard_keywords, max_results, taken, current_yi,
            program_rows, program_name, program_source, program_total, unmatched,
        )

    conn.close()
    pref = profile.get("preference_type", "balanced")
    note = dict(PROFILES.get(pref, PROFILES["balanced"]))
    if profile.get("_auto_pref") and profile.get("gpa") is not None:
        note["auto"] = True
        note["gpa"] = profile["gpa"]
        note["source"] = "gpa_default"
    elif profile.get("_explicit_preference"):
        note["auto"] = False
        note["source"] = "explicit"
    else:
        note["auto"] = False
        note["source"] = "default"
    note["needs"] = {
        "interests": profile.get("interests") or [],
        "course_scope": profile.get("course_scope") or "all",
        "workload_preference": profile.get("workload_preference"),
        "preferred_teachers": profile.get("preferred_teachers") or [],
        "target_term": _canonical_target_term(profile.get("target_term"), current_yi),
    }
    result.setdefault("profile_note", note)
    limitations = []
    if not result.get("program_context", {}).get("matched"):
        limitations.append(
            "未提供专业或未定位到对应培养方案，本次仅按用户需求与评课数据排序。"
        )
        if result.get("scope_unavailable") == "required":
            limitations.append("没有培养方案时无法判断哪些课程属于你的必修要求，因此未生成必修推荐。")
        elif result.get("scope_unavailable") == "elective":
            limitations.append("没有培养方案时无法确认哪些课程属于你的方案内选修，因此未放宽课程范围。")
    elif personal_requested and program_source != "personal":
        limitations.append("个人培养方案数据不可用，已降级为本地同专业同年级方案。")
    if not profile.get("_taken_known"):
        limitations.append("未取得完整已修课程记录，无法确认所有必修缺口或排除全部已修课。")
    if unmatched:
        limitations.append(f"培养方案中有 {len(unmatched)} 门课程缺少评课映射，未参与评分排序。")
    if profile.get("_conflict_not_checked"):
        limitations.append(
            "本次只完成课程推荐，候选课尚未与个人课表做冲突检查；确定候选后请使用独立冲突检查。"
        )
    if hard_keywords and not result.get("recommendations"):
        limitations.append("指定课程范围没有命中可核验候选，本次未放宽该硬条件。")
    if profile.get("_hard_preferences") and not result.get("recommendations"):
        limitations.append("“只要/必须”条件没有命中可核验候选，本次未放宽这些硬条件。")
    if (result.get("keyword_fallback") or result.get("need_fallback")) \
            and not profile.get("_hard_preferences"):
        limitations.append("指定方向未命中足够候选，已回退到更宽的评课候选池。")
    result["limitations"] = limitations
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
        # 完全同名课程(如"图论"精确命中"图论"): 直接聚合该课程全部班级/老师, 不因
        # 近似名课程(代数图论/数理逻辑与图论)触发 ambiguity —— "只说课程时返回所有老师的课"
        exact = [
            m for m in uniq
            if _norm_course_name(m["name"]) == _norm_course_name(course or "")
        ]
        if exact:
            c = exact[0]
        elif len(uniq) > 1:
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
        else:
            c = exact[0] if exact else uniq[0]
        # 班级聚合: courses 表每行=一个班级(不合并同课多师), 合教老师组合整体展示
        # (如"许胤龙, 吕敏, 李永坤"为一组, 不拆成单个老师)
        class_rows = conn.execute(
            "SELECT c.id, c.code, c.credit, c.dept, c.rating_avg, c.rate_count, "
            "GROUP_CONCAT(t.name, ', ') AS teacher_names "
            "FROM courses c LEFT JOIN course_teachers ct ON ct.course_id = c.id "
            "LEFT JOIN teachers t ON t.id = ct.teacher_id "
            "WHERE c.name = ? GROUP BY c.id ORDER BY c.rate_count DESC",
            (c["name"],),
        ).fetchall()
        teachers: list[dict] = []
        for row in class_rows:
            teachers.append({
                "name": row["teacher_names"] or "",
                "code": row["code"] or "",
                "dept": row["dept"] or "",
                "credit": row["credit"],
                "rating_avg": round(row["rating_avg"], 1) if row["rating_avg"] is not None else None,
                "rate_count": row["rate_count"] or 0,
            })
        teachers.sort(key=lambda t: (-(t["rating_avg"] or 0), -(t["rate_count"] or 0)))
        reviews: list[dict] = []
        for row in class_rows:
            reviews.extend(_top_reviews(conn, row["id"], limit=2))
            if len(reviews) >= 6:
                break
        agg = conn.execute(
            "SELECT COALESCE(SUM(rate_count),0), COALESCE(SUM(rating_avg*rate_count),0) "
            "FROM courses WHERE name = ?",
            (c["name"],),
        ).fetchone()
        rate_count = agg[0] or 0
        rating_avg = round(agg[1] / rate_count, 1) if rate_count else 0.0
        conn.close()
        return {
            "course": c["name"],
            "code": c["code"],
            "credit": c["credit"],
            "dept": c["dept"],
            "teachers": teachers,
            "rating_avg": rating_avg,
            "rate_count": rate_count,
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
    # 双参数模式：同时提供 course 时，仅保留该课程下的记录（"某老师在XX课怎么样"）
    # 简称映射 + 归一化后互相包含匹配（"数分B2"→"数学分析(B2)"，不依赖 LLM 规范化）
    if course:
        _alias = {
            "数分": "数学分析", "线代": "线性代数", "概统": "概率论与数理统计",
            "高数": "高等数学", "大物": "大学物理", "大英": "大学英语",
        }
        c_key = _norm_course_name(course)
        for k, v in _alias.items():
            c_key = c_key.replace(k, v)
        matched = []
        for r in rows:
            n_key = _norm_course_name(r["name"] or "")
            if c_key and (c_key in n_key or n_key in c_key):
                matched.append(r)
        rows = matched
        if not rows:
            conn.close()
            return {"error": f"未找到教师 {teacher_name} 在课程「{course}」的评价记录"}
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
