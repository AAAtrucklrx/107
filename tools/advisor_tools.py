"""
小蜗 — 选课顾问 Agent 工具
提供课程推荐、课程对比、教师分析、偏好收集
"""

from langchain_core.tools import tool
from services.service_container import ServiceContainer


def _db():
    """获取数据库实例"""
    return ServiceContainer().db


# 偏好数据存储在 session 级别（模块单例，供多轮对话复用）
_current_profile: dict = {}


def get_profile() -> dict:
    return _current_profile


def update_profile(**kwargs):
    _current_profile.update(kwargs)


def reset_profile():
    """重置偏好（用于测试或新用户）"""
    global _current_profile
    _current_profile = {}


@tool
def collect_preferences() -> dict:
    """
    启动偏好收集对话。返回当前已收集的偏好。
    实际的多轮对话由Agent通过自然语言完成，此Tool仅用于标记状态。

    Returns:
        {"status": "collecting", "collected_fields": [...], "remaining_fields": [...]}
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
def recommend_courses(profile: dict) -> dict:
    """
    根据用户偏好推荐课程。

    Args:
        profile: 用户偏好字典，格式：
            {"major": "计算机科学", "grade": "大二", "interests": ["人工智能"],
             "preference_type": "balanced", "target_gpa": 3.5, "max_results": 5}

    Returns:
        {"recommendations": [...], "total_candidates": N, "filtered_count": N}
    """
    try:
        db = _db()
    except RuntimeError:
        return {"recommendations": [], "total_candidates": 0, "filtered_count": 0, "error": "数据库未初始化"}

    all_courses = db.query("SELECT * FROM course_reviews")
    if not all_courses:
        return {"recommendations": [], "total_candidates": 0, "filtered_count": 0, "error": "暂无课程数据"}

    interests = profile.get("interests", [])
    pref_type = profile.get("preference_type", "balanced")

    # 权重配置
    weights = {
        "easy_grade": {"rating": 0.3, "give_score": 0.5, "interest": 0.2},
        "learn_hard": {"rating": 0.5, "give_score": 0.1, "interest": 0.4},
        "balanced": {"rating": 0.4, "give_score": 0.3, "interest": 0.3},
    }
    w = weights.get(pref_type, weights["balanced"])

    scored = []
    for c in all_courses:
        tags = [t.strip() for t in (c.get("tags") or "").split(",") if t.strip()]
        if tags and interests:
            match = len(set(tags) & set(interests)) / max(len(tags), 1) * 10
        else:
            match = 5.0

        give_label = c.get("give_score", "")
        give_score = 8.0 if "好" in give_label else (2.0 if "差" in give_label else 5.0)

        score = (
            w["rating"] * (c.get("rating") or 5)
            + w["give_score"] * give_score
            + w["interest"] * match
        )
        scored.append({
            "course_name": c["course_name"],
            "course_code": c.get("course_code", ""),
            "teacher": c.get("teacher", ""),
            "credits": c.get("credits", 0),
            "rating": c.get("rating", 0),
            "difficulty": c.get("difficulty", 0),
            "workload": c.get("workload", 0),
            "give_score": c.get("give_score", ""),
            "tags": c.get("tags", ""),
            "reason": _generate_reason(c, interests, pref_type),
            "review_summary": c.get("review_summary", ""),
            "review_count": c.get("review_count", 0),
            "_score": score,
        })

    scored.sort(key=lambda x: x["_score"], reverse=True)
    max_results = profile.get("max_results", 5)
    top = scored[:max_results]
    for item in top:
        del item["_score"]

    return {
        "recommendations": top,
        "total_candidates": len(all_courses),
        "filtered_count": len(top),
    }


def _generate_reason(course: dict, interests: list, pref_type: str) -> str:
    """生成推荐理由"""
    parts = []

    tags = [t.strip() for t in (course.get("tags") or "").split(",") if t.strip()]
    matched = set(tags) & set(interests)
    if matched:
        parts.append(f"与你的兴趣（{'、'.join(matched)}）高度匹配")

    rating = course.get("rating") or 0
    if rating >= 8:
        parts.append(f"评课社区评分 {rating}，口碑很好")
    elif rating >= 6:
        parts.append(f"评课社区评分 {rating}，中等偏上")

    give_score = course.get("give_score", "")
    if "好" in give_score and pref_type in ("balanced", "easy_grade"):
        parts.append("给分好")

    difficulty = course.get("difficulty") or 0
    if difficulty <= 4 and pref_type == "easy_grade":
        parts.append("难度低，容易拿高分")

    if not parts:
        parts.append("综合评分不错，值得考虑")

    return "；".join(parts)


@tool
def compare_courses(course_a: str, course_b: str) -> dict:
    """
    对比两门课程。

    Args:
        course_a: 第一门课程名
        course_b: 第二门课程名

    Returns:
        {"course_a": {...}, "course_b": {...}, "comparison": {...}}
    """
    try:
        db = _db()
    except RuntimeError:
        return {"error": "数据库未初始化"}

    a = db.query("SELECT * FROM course_reviews WHERE course_name LIKE ?", (f"%{course_a}%",))
    b = db.query("SELECT * FROM course_reviews WHERE course_name LIKE ?", (f"%{course_b}%",))

    if not a:
        return {"error": f"未找到课程：{course_a}"}
    if not b:
        return {"error": f"未找到课程：{course_b}"}

    a, b = a[0], b[0]

    def extract(c):
        return {
            "name": c["course_name"],
            "teacher": c.get("teacher", ""),
            "rating": c.get("rating", 0),
            "difficulty": c.get("difficulty", 0),
            "workload": c.get("workload", 0),
            "give_score": c.get("give_score", ""),
            "review_summary": c.get("review_summary", ""),
        }

    ca, cb = extract(a), extract(b)
    winner_rating = ca["name"] if ca["rating"] >= cb["rating"] else cb["name"]
    winner_easy = ca["name"] if ca["difficulty"] <= cb["difficulty"] else cb["name"]

    return {
        "course_a": ca,
        "course_b": cb,
        "comparison": {
            "winner_rating": winner_rating,
            "winner_easy": winner_easy,
            "suggestion": "如果你想学到真东西，选评分高的；如果想轻松拿分，选难度低的。",
        },
    }


@tool
def analyze_teacher(teacher_name: str) -> dict:
    """
    分析指定教师的评价。

    Args:
        teacher_name: 教师姓名

    Returns:
        {"teacher": "...", "courses": [...], "avg_rating": ..., "teaching_style": "...",
         "strengths": [...], "weaknesses": [...], "review_summary": "...", "review_count": N}
    """
    try:
        db = _db()
    except RuntimeError:
        return {"error": "数据库未初始化"}

    teacher = db.query("SELECT * FROM teacher_reviews WHERE name LIKE ?", (f"%{teacher_name}%",))

    if not teacher:
        # 尝试从 course_reviews 聚合
        courses = db.query(
            "SELECT * FROM course_reviews WHERE teacher LIKE ?",
            (f"%{teacher_name}%",),
        )
        if not courses:
            return {"error": f"未找到教师：{teacher_name}"}

        avg_rating = sum(c.get("rating") or 0 for c in courses) / len(courses)
        course_names = [c["course_name"] for c in courses]
        return {
            "teacher": teacher_name,
            "courses": course_names,
            "avg_rating": round(avg_rating, 1),
            "teaching_style": "暂无详细教学风格数据",
            "strengths": [],
            "weaknesses": [],
            "review_summary": f"该教师共教授 {len(courses)} 门课程，平均评分 {avg_rating:.1f}",
            "review_count": sum(c.get("review_count") or 0 for c in courses),
        }

    t = teacher[0]
    return {
        "teacher": t["name"],
        "courses": (t.get("courses") or "").split(","),
        "avg_rating": t.get("avg_rating", 0),
        "teaching_style": t.get("teaching_style", ""),
        "strengths": (t.get("strengths") or "").split(",") if t.get("strengths") else [],
        "weaknesses": (t.get("weaknesses") or "").split(",") if t.get("weaknesses") else [],
        "review_summary": t.get("review_summary", ""),
        "review_count": t.get("review_count", 0),
    }
