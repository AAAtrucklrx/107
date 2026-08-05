"""
小蜗 — GPA 计算器
科大 4.3 制绩点计算
"""

# 科大百分制 → 4.3制绩点对照表
_SCORE_TO_GP = [
    (100, 4.3),
    (95, 4.0),
    (90, 3.7),
    (85, 3.3),
    (82, 3.0),
    (78, 2.7),
    (75, 2.3),
    (72, 2.0),
    (68, 1.7),
    (64, 1.5),
    (60, 1.0),
    (0, 0.0),
]


def score_to_grade_point(score: int) -> float:
    """百分制分数 → 4.3制绩点"""
    for threshold, gp in _SCORE_TO_GP:
        if score >= threshold:
            return gp
    return 0.0


def calculate_gpa(grades: list[dict]) -> dict:
    """
    计算 GPA（科大 4.3 制）。

    Args:
        grades: [{"credits": 4, "score": 88, "grade_point": 3.7}, ...]

    Returns:
        {"gpa": 3.53, "total_credits": 13, "weighted_sum": 45.9}
    """
    if not grades:
        return {"gpa": 0.0, "total_credits": 0, "weighted_sum": 0.0}

    total_credits = sum(g["credits"] for g in grades)
    weighted_sum = sum(g["grade_point"] * g["credits"] for g in grades)
    gpa = round(weighted_sum / total_credits, 2) if total_credits > 0 else 0.0

    return {
        "gpa": gpa,
        "total_credits": total_credits,
        "weighted_sum": round(weighted_sum, 2),
    }