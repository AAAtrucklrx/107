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
    from agents.qa.nodes import _extract_profile

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

    print(f"\n结果: 通过 {len(TOTAL) - len(FAILURES)}/{len(TOTAL)}")


if __name__ == "__main__":
    main()
    sys.exit(1 if FAILURES else 0)
