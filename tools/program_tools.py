"""
小蜗 — 培养方案工具
数据源: data/course_data.db（programs / program_courses 表，见 database/schema_course.sql）

供培养方案三合一页面（我的方案 / 学期规划 / 进度概览）与 QA 流程调用：
- get_my_program       → 按专业定位方案，返回模块分组的课程清单
- get_program_progress → 已修/必修学分明细 + 必修缺口清单
- plan_semester        → 按方案 term（"2秋"）排出第 N 学年该修课程

登录态个人方案适配：传入 personal_tree（jw root-module-json 模块树）时优先用
个人方案，解析为与全量库相同的 courses 结构后走同一套逻辑；未传入用全量库。
解析函数 _parse_tree 放本文件内。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from langchain_core.tools import tool

from tools import _program_resolve as _pr
from utils import course_name as _norm

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COURSE_DB = PROJECT_ROOT / "data" / "course_data.db"


def _cdb() -> sqlite3.Connection:
    """每次新建连接（本地 sqlite 开销小, 避免缓存坏连接）。"""
    conn = sqlite3.connect(str(COURSE_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _norm_course_name(name: str) -> str:
    """归一化课程名（共享实现 utils/course_name）：去括号/引号/空白并 ASCII 大写，
    用于已修课程与方案课程名的模糊比对。"""
    return _norm.norm_course_name(name)


def _norm_taken_set(names: list[str]) -> set[str]:
    """已修课程名归一化集合；兼容 (L) 班型后缀（成绩 '计算机程序设计(L)' 视同已修同名课）。"""
    s: set[str] = set()
    for n in (names or []):
        k = _norm_course_name(n)
        s.add(k)
        if k.endswith("L") and len(k) > 1:
            s.add(k[:-1])
    return s


def _parse_grade_key(grade: str) -> int:
    """年级 → 可排序整数（"2024级"→2024，"大二"→无法解析返回 0）。"""
    return _pr.parse_grade_key(grade)


def _resolve_program(conn: sqlite3.Connection, major: str, grade: str = None) -> dict | None:
    """全量库定位方案（共享实现 tools/_program_resolve，与选课推荐口径一致）。

    Returns:
        {"id", "name", "college", "grade"} 或 None
    """
    return _pr.resolve_program(conn, major, grade)


# ── 全量库 courses 结构（与 program_courses 行一致） ─────────────────

def _load_full_program_courses(conn: sqlite3.Connection, program_id: int) -> list[dict]:
    """读取全量库方案课程行 → 统一 courses 结构。"""
    out = []
    for r in conn.execute(
        "SELECT code, name, required, credit, category, term FROM program_courses "
        "WHERE program_id=? ORDER BY id",
        (program_id,),
    ):
        out.append(_make_course(r["code"], r["name"], r["required"], r["credit"],
                                r["category"], r["term"], course_id=None))
    return out


def _make_course(code, name, required, credit, category, term, course_id=None) -> dict:
    """构造统一 courses 条目（全量库与个人树共用同一结构）。"""
    return {
        "code": code or "",
        "name": name or "",
        "required": required or "",
        "credit": _to_float(credit),
        "category": category or "",
        "term": term or "",
        "_course_id": course_id,
    }


def _to_float(v) -> float | None:
    """credit 宽容转 float（库内为字符串/数字），失败返 None。"""
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _public_plan_course(c: dict) -> dict:
    """统一 courses 条目 → 对外展示字段（剔除内部 _course_id）。"""
    return {"code": c["code"], "name": c["name"], "required": c["required"],
            "credit": c["credit"], "term": c["term"], "category": c["category"]}


# ── 个人方案模块树解析（jw root-module-json） ───────────────────────
#
# 结构与全量库 root-module-json 一致：节点含 type.nameZh / planCourses[].course /
# readableTerms / compulsory；subModules 递归嵌套。这里把树扁平化为与全量库
# 相同的 courses 结构（code/name/required/credit/category/term）。

def _parse_tree(tree: dict | list) -> list[dict]:
    """解析个人方案模块树 → 统一 courses 结构 list[dict]。

    兼容 dict（含 subModules 字段）与 list（子模块列表）两种形态及浅层容错。
    """
    courses: list[dict] = []
    _walk_tree(tree, courses)
    return courses


def _walk_tree(node, courses: list[dict], parent_category: str = "") -> None:
    """递归遍历模块树节点，planCourses[].course 收集为课程行。

    jw 个人方案树实测结构（2026-08-13）：节点用 children 递归、模块名在
    type.nameZh、学分在 course.credits、学期在 planCourses.readableTerms(list)。
    这里兼容 children/subModules 两种子节点字段名。
    """
    if isinstance(node, dict):
        t = node.get("type") or {}
        name = (t.get("nameZh") if isinstance(t, dict) else None) \
            or node.get("nameZh") or node.get("name") or ""
        category = _join_category(parent_category, name)
        for pc in node.get("planCourses") or []:
            if not isinstance(pc, dict):
                continue
            c = pc.get("course") or pc
            if not isinstance(c, dict):
                continue
            terms = pc.get("readableTerms") or []
            if isinstance(terms, list):
                terms = ",".join(str(x).strip() for x in terms if str(x).strip())
            courses.append(_make_course(
                c.get("code", ""),
                c.get("nameZh") or c.get("name", ""),
                "必修" if pc.get("compulsory") or c.get("required") == "必修" else "选修",
                c.get("credits") or c.get("credit"),
                category,
                terms or c.get("term", ""),
                course_id=None,
            ))
        kids = node.get("children") or node.get("subModules") or []
        for sub in kids:
            _walk_tree(sub, courses, category)
    elif isinstance(node, list):
        for item in node:
            _walk_tree(item, courses, parent_category)


def _join_category(parent: str, name: str) -> str:
    """模块层级拼为 category（"通修课程/数学通修" 风格）。"""
    name = (name or "").strip()
    if not name:
        return parent
    return f"{parent}/{name}" if parent else name


# ── 统一入口：个人树优先，否则全量库 ──────────────────────

def _resolve_courses(major: str, grade: str, personal_tree: dict | list | None):
    """返回 (program_meta, courses)。个人树优先；未传入/解析为空则用全量库。"""
    conn = None
    try:
        conn = _cdb()
    except sqlite3.Error:
        conn = None

    if personal_tree is not None:
        courses = _parse_tree(personal_tree)
        if courses:
            return {"id": None, "name": "个人培养方案", "college": "", "grade": grade or "",
                    "personal": True}, courses

    if conn is None:
        return {"id": None, "name": "", "college": "", "grade": grade or "",
                "personal": False}, []
    prog = _resolve_program(conn, major, grade)
    if not prog:
        return {"id": None, "name": "", "college": "", "grade": grade or "",
                "personal": False}, []
    prog = dict(prog)
    prog["personal"] = False
    return prog, _load_full_program_courses(conn, prog["id"])


# ── 工具 ─────────────────────────────────────────────

@tool
def get_my_program(major: str, grade: Optional[str] = None,
                   personal_tree: Optional[dict | list] = None) -> dict:
    """
    获取专业培养方案，按模块（category）分组的课程清单。

    Args:
        major: 专业名（如 "计算机科学与技术"）
        grade: 年级（可选，如 "2024级"、"大二"；缺省选最近方案）
        personal_tree: 登录后个人方案模块树（可选，jw root-module-json）；
            传入时优先用个人方案

    Returns:
        {"program_id", "name", "college", "grade", "totalCredits",
         "modules": [{"category", "required_credits", "course_count"}],
         "courses": [{"code","name","required","credit","term","category"}]}
        courses 已按 category 分组（category 升序）。
    """
    prog, courses = _resolve_courses(major, grade, personal_tree)

    # modules: 按一级模块名聚合（category 首段），若无 category 用空段位
    module_agg: dict[str, dict] = {}
    for c in courses:
        first = (c["category"].split("/")[0] if c["category"] else "").strip()
        key = first or "未分类"
        m = module_agg.setdefault(key, {"category": first, "credits": 0.0, "count": 0})
        m["count"] += 1
        if c["credit"] is not None:
            m["credits"] += c["credit"]

    modules = [{
        "category": agg["category"],
        "required_credits": round(agg["credits"], 1),
        "course_count": agg["count"],
    } for agg in module_agg.values()]
    modules.sort(key=lambda x: x["category"])

    # totalCredits: 全量库 programs 无该字段 → 用课程学分汇总
    total = round(sum(c["credit"] or 0 for c in courses), 1)

    courses_public = sorted((_public_plan_course(c) for c in courses),
                            key=lambda c: (c["category"], c["term"]))
    return {
        "program_id": prog["id"],
        "name": prog["name"],
        "college": prog["college"],
        "grade": prog["grade"],
        "personal": prog.get("personal", False),
        "totalCredits": total,
        "modules": modules,
        "courses": [dict(c) for c in courses_public],
        "source": "local",  # 本地方案库/个人方案树数据，非教务实时
    }


@tool
def get_program_progress(major: str, grade: Optional[str] = None,
                         taken_courses: Optional[list[str]] = None,
                         taken_credits: Optional[list[float]] = None,
                         personal_tree: Optional[dict | list] = None) -> dict:
    """
    培养进度：已修必修 / 必修总数、学分明细、必修缺口清单。

    Args:
        major: 专业名
        grade: 年级（可选）
        taken_courses: 已修课程名列表（登录后由上层从成绩单读取，None 视为未修）
        taken_credits: 已修课程学分列表（可与 taken_courses 一一对应；缺省按方案行 credit 估算）
        personal_tree: 登录后个人方案模块树（可选）

    Returns:
        {"program_id", "name", "required_total", "required_taken",
         "required_remaining": [{"code","name","credit","term","category"}],
         "credits_taken", "credits_required", "percent",
         "modules_progress": [{"category", "taken", "total"}]}
    """
    prog, courses = _resolve_courses(major, grade, personal_tree)

    required = [c for c in courses if c["required"] == "必修"]

    # 已修判定：taken_courses 归一化匹配方案课程名（兼容 (L) 班型后缀）
    taken_names = _norm_taken_set(taken_courses)
    credits_by_index: dict = {}
    if taken_courses and taken_credits:
        for idx, tc in enumerate(taken_courses):
            if idx < len(taken_credits):
                credits_by_index[_norm_course_name(tc)] = _to_float(taken_credits[idx])

    taken = []
    remaining = []
    for c in required:
        norm = _norm_course_name(c["name"])
        if norm in taken_names:
            taken.append(c)
        else:
            remaining.append(c)

    # 缺省按方案行 credit 估算已修学分；显式 given 时优先
    def credit_of(c: dict) -> float:
        v = credits_by_index.get(_norm_course_name(c["name"]))
        return v if v is not None else (c["credit"] or 0.0)

    credits_taken = round(sum(credit_of(c) for c in taken), 1)
    credits_required = round(sum(c["credit"] or 0 for c in required), 1)
    percent = round(credits_taken / credits_required * 100, 1) if credits_required else 0.0

    # modules_progress: 按一级模块名聚合
    module_agg: dict[str, dict] = {}
    for c in required:
        key = (c["category"].split("/")[0] if c["category"] else "").strip() or "未分类"
        m = module_agg.setdefault(key, {"category": key, "taken": 0, "total": 0})
        m["total"] += 1
        if _norm_course_name(c["name"]) in taken_names:
            m["taken"] += 1
    modules_progress = sorted(
        [{"category": m["category"], "taken": m["taken"], "total": m["total"]}
         for m in module_agg.values()],
        key=lambda x: x["category"],
    )

    return {
        "program_id": prog["id"],
        "name": prog["name"],
        "required_total": len(required),
        "required_taken": len(taken),
        "required_remaining": [{
            "code": c["code"], "name": c["name"], "credit": c["credit"],
            "term": c["term"], "category": c["category"],
        } for c in remaining],
        "credits_taken": credits_taken,
        "credits_required": credits_required,
        "percent": percent,
        "modules_progress": modules_progress,
        "source": "local",  # 本地方案库/个人方案树数据，非教务实时
    }


def _parse_term_year(term: str) -> int | None:
    """从 term 首字符解析学年号（"2秋"→2，"3秋"→3）；无前缀/无法解析返 None。"""
    m = re.match(r"\s*(\d)", term or "")
    return int(m.group(1)) if m else None


@tool
def plan_semester(major: str, grade: Optional[str] = None, year_index: int = 1,
                  personal_tree: Optional[dict | list] = None) -> dict:
    """
    学期规划：按方案 term（"2秋"）排出第 N 学年该修课程。

    Args:
        major: 专业名
        grade: 年级（可选）
        year_index: 学年序号（1=大一，2=大二…）
        personal_tree: 登录后个人方案模块树（可选）

    Returns:
        {"year_index", "terms": [{"term": "2秋", "courses": [...]}], "total_credits"}
        term 无学年前缀的（如 "" 或 "空"）归入「未标注」分组。
    """
    prog, courses = _resolve_courses(major, grade, personal_tree)

    terms: dict[str, list[dict]] = {}
    total = 0.0
    for c in courses:
        # 该课程若跨多学期（"1秋,1春"）任一段落在目标学年则纳入
        segments = [s.strip() for s in (c["term"] or "").split(",")
                    if s.strip() and s.strip() not in ("空",)]
        if not segments:
            # term 无学年前缀（空/无法解析）→ 归入「未标注」分组
            bucket = "未标注"
            terms.setdefault(bucket, []).append(_public_plan_course(c))
            if c["credit"] is not None:
                total += c["credit"]
            continue
        for seg in segments:
            if _parse_term_year(seg) == int(year_index):
                terms.setdefault(seg, []).append(_public_plan_course(c))
                if c["credit"] is not None:
                    total += c["credit"]
                break

    # 分组内排序稳定即可；terms 列表按学期标签排序（"未标注"置末）
    ordered = sorted(
        [{"term": t, "courses": terms[t]} for t in terms],
        key=lambda x: (x["term"] == "未标注", x["term"]),
    )
    return {
        "year_index": year_index,
        "terms": ordered,
        "total_credits": round(total, 1),
        "source": "local",  # 本地方案库/个人方案树数据，非教务实时
    }