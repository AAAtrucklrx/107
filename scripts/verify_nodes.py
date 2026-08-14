# -*- coding: utf-8 -*-
"""nodes 层确定性规则回归（无 LLM 依赖）。

覆盖：_extract_profile 不填造假画像（Phase 0 修复 P1-3 的回归护栏）。
用法: python scripts/verify_nodes.py
退出码: 0=全部通过, 1=存在失败项
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILURES: list[str] = []
TOTAL: list[int] = []


def t(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    TOTAL.append(1)
    if not ok:
        FAILURES.append(name)


def main() -> None:
    from agents.qa.nodes import _build_tool_summary, _extract_profile

    # 无任何线索 → 全部留空（历史版本硬编码"计算机科学/大二/人工智能"）
    p1 = _extract_profile("推荐几门课", {})
    t("无线索画像留空", p1["major"] is None and p1["grade"] is None and p1["interests"] == [], str(p1))

    # 问题中的线索优先
    p2 = _extract_profile("推荐适合大二的AI方向选修课", {})
    t("年级/兴趣从问题提取", p2["grade"] == "大二" and p2["interests"] == ["AI"] and p2["major"] is None, str(p2))

    # 登录画像兜底
    p3 = _extract_profile("推荐几门课", {"major": "人工智能", "grade": "2025级"})
    t("画像兜底", p3["major"] == "人工智能" and p3["grade"] == "2025级", str(p3))

    # 偏好类型映射
    p4 = _extract_profile("有没有好拿分的课", {})
    t("easy_grade 映射", p4["preference_type"] == "easy_grade", str(p4))

    # 工具摘要推荐分支须输出画像说明（供 LLM 向用户说明自动画像依据）
    fake = [{"tool": "recommend_courses", "status": "done", "result": {
        "recommendations": [], "groups": {}, "total_candidates": 0,
        "profile_note": {"name": "冲分保绩", "desc": "给分好、难度低优先", "auto": True, "gpa": 2.2}}}]
    s = _build_tool_summary(fake)
    t("工具摘要含画像说明", "画像" in s and "GPA 2.2" in s, s[:120])

    # ── Phase 2a: 会话隔离（画像分桶 + CAS 客户端分桶）──
    from services.session_ctx import current_student, reset_student, set_student
    import tools.advisor_tools as _at
    from services.service_container import ServiceContainer

    _at.reset_profile()
    tok_a = set_student("PB0001")
    _at.update_profile(interest="AI")
    reset_student(tok_a)
    tok_b = set_student("PB0002")
    t("会话隔离-他生画像不可见", _at.get_profile() == {}, str(_at.get_profile()))
    reset_student(tok_b)
    tok_a2 = set_student("PB0001")
    t("会话隔离-本生画像仍在", _at.get_profile().get("interest") == "AI", str(_at.get_profile()))
    reset_student(tok_a2)

    _sc = ServiceContainer()
    tok_a3 = set_student("PB0001")
    _ca = _sc.cas_client
    _ca._logged_in = True
    _ca._student_id = "PB0001"
    reset_student(tok_a3)
    tok_b2 = set_student("PB0002")
    t("CAS 桶隔离-他生桶未登录", _sc.has_cas() is False, str(_sc.has_cas()))
    _cb = _sc.cas_client
    t("CAS 桶隔离-客户端不共享", _cb is not _ca, "PB0001/PB0002 为不同实例")
    reset_student(tok_b2)
    tok_a4 = set_student("PB0001")
    t("CAS 桶隔离-本生桶已登录", _sc.has_cas() is True, str(_sc.has_cas()))
    reset_student(tok_a4)
    _sc.reset()
    _at.reset_profile()
    t("会话清理-上下文复位为空", current_student() == "", repr(current_student()))

    print(f"\n结果: 通过 {len(TOTAL) - len(FAILURES)}/{len(TOTAL)}")


if __name__ == "__main__":
    main()
    sys.exit(1 if FAILURES else 0)
