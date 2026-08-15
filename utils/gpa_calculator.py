"""
小蜗 — GPA 计算器
科大 4.3 制绩点计算
"""

# 科大百分制 → 4.3制绩点对照表
# 依据教务处《2025本科学习指南》成绩管理办法（教字〔2019〕14号）官方对应表：
#   100~95→4.3, 94~90→4.0, 89~85→3.7, 84~82→3.3, 81~78→3.0, 77~75→2.7,
#   74~72→2.3, 71~68→2.0, 67~65→1.7, 64→1.5, 63~61→1.3, 60→1.0, <60→0
_SCORE_TO_GP = [
    (95, 4.3),
    (90, 4.0),
    (85, 3.7),
    (82, 3.3),
    (78, 3.0),
    (75, 2.7),
    (72, 2.3),
    (68, 2.0),
    (65, 1.7),
    (64, 1.5),
    (61, 1.3),
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