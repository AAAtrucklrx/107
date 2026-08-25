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
    from agents.qa.nodes import _build_tool_summary, _extract_profile, _is_recommendation_request

    # 无任何线索 → 全部留空（历史版本硬编码"计算机科学/大二/人工智能"）
    p1 = _extract_profile("推荐几门课", {})
    t("无线索画像留空", p1["major"] is None and p1["grade"] is None and p1["interests"] == [], str(p1))

    # 问题中的线索优先
    p2 = _extract_profile("推荐适合大二的AI方向选修课", {})
    t("年级/兴趣从问题提取", p2["grade"] == "大二" and p2["interests"] == ["AI"]
      and p2["major"] is None and p2["_explicit_interests"] is True, str(p2))

    # 登录画像兜底
    p3 = _extract_profile("推荐几门课", {"major": "人工智能", "grade": "2025级"})
    t("画像兜底", p3["major"] == "人工智能" and p3["grade"] == "2025级", str(p3))

    # 偏好类型映射
    p4 = _extract_profile("有没有好拿分的课", {})
    t("easy_grade 映射", p4["preference_type"] == "easy_grade", str(p4))
    p5 = _extract_profile("只要人工智能方向、必须张三老师的大二上课程", {})
    t("只要/必须升级明确偏好为硬条件",
      set(p5["_hard_preferences"]) == {"interests", "preferred_teachers", "target_term"}
      and p5["preferred_teachers"] == ["张三"], str(p5))
    p6 = _extract_profile("推荐几门课，会不会和我的课表冲突？", {})
    t("复合推荐仅记录未查冲突说明",
      p6["_conflict_not_checked"] is True and "schedule_constraints" not in p6, str(p6))
    t("选修名词不等于推荐动作",
      not _is_recommendation_request("我的选修课冲突吗"), "")

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

    # ── Phase 2c: 脏前缀/规则续写剥离加固 ──
    from agents.qa.nodes import _strip_rule_prefix
    _strip_cases = [
        ("smart_toy | smart_toy\n\n小蜗来啦！", "小蜗来啦！"),   # 多段拼接
        ("smart_toy\n\n小蜗来啦！", "小蜗来啦！"),              # 换行分隔
        ("smart_toy | 小蜗来啦！", "小蜗来啦！"),               # 竖线分隔
        ("\u200bsmart_toy | 小蜗来啦！", "小蜗来啦！"),         # 零宽空格前置
        ("\n\nsmart_toy | 小蜗来啦！", "小蜗来啦！"),           # 空行前置
        ("必须注明来源\n\n小蜗来啦！", "小蜗来啦！"),            # 规则续写单行
        ("小蜗来啦！", "小蜗来啦！"),                           # 正常正文不动
        ("办理流程如下：\n1. 带证件", "办理流程如下：\n1. 带证件"),  # 冒号结尾不动
    ]
    for _i, (_inp, _want) in enumerate(_strip_cases, 1):
        _got = _strip_rule_prefix(_inp)
        t(f"前缀剥离-{_i}", _got == _want, f"got={_got!r}")

    # ── P1-2: 测试版离线模式与方案树注入（显式开关替代 monkey-patch）──
    import tools.course_tools as _ct2
    from services.cas_client import CASClient as _CAS

    _ct2.set_offline_mode(True)
    t("离线模式-课表拉取返回 None", _ct2._fetch_real_schedule(object(), 1) is None, "")
    t("离线模式-成绩拉取返回 None", _ct2._fetch_real_grades(object()) is None, "")
    _ct2.set_offline_mode(False)
    _fake_client = _CAS()
    _fake_client.inject_program_tree({"fake": "tree"})
    t("方案树注入-返回注入数据", _fake_client.get_my_program_tree() == {"fake": "tree"}, "")
    _fake_client.inject_program_tree(None)
    t("方案树注入-清除后不再注入", _fake_client.get_my_program_tree() != {"fake": "tree"}, "")

    # ── P2: young_client token 校验与数字容错 ──
    from services.young_client import EncryptedHttpProvider, _to_int
    _short_ok = False
    try:
        EncryptedHttpProvider("short")
    except ValueError:
        _short_ok = True
    t("young-短token拒绝", _short_ok, "")
    t("young-数字容错", _to_int("1.2k") == 0 and _to_int("7") == 7 and _to_int(None) == 0, "")
    _long_ok = True
    try:
        EncryptedHttpProvider("a" * 32)
    except ValueError:
        _long_ok = False
    t("young-合法token通过", _long_ok, "")

    # ── P2-12: 等级制成绩保留（用 jw 自带 gp，不编造映射）──
    from tools.course_tools import _fetch_real_grades

    class _StubCas:
        def get_grade_semesters(self):
            return [{"id": 1, "nameZh": "2025-2026-1"}]

        def get_grades(self, sem_ids):
            return {"semesters": [{"id": 1, "scores": [
                {"courseNameCh": "数值课", "credits": 3, "score": 88},
                {"courseNameCh": "等级课", "credits": 2, "score": "优秀", "gp": 4.0},
                {"courseNameCh": "缺绩点课", "credits": 2, "score": "通过"},
            ]}]}

    _grades = _fetch_real_grades(_StubCas())
    t("等级制-保留且缺绩点跳过", len(_grades) == 2, str([g["course_name"] for g in _grades]))
    _lv = next((g for g in _grades if g["course_name"] == "等级课"), None)
    t("等级制-哨兵与原文", _lv is not None and _lv["score"] == -1 and _lv["score_text"] == "优秀"
      and _lv["score_display"] == "优秀" and _lv["grade_point"] == 4.0, str(_lv))
    _nv = next((g for g in _grades if g["course_name"] == "数值课"), None)
    t("数值-正常路径", _nv is not None and _nv["score"] == 88 and _nv["score_display"] == 88
      and abs(_nv["grade_point"] - 3.7) < 0.01, str(_nv))

    # ── 选课 H 项: 冲突/退补选路由与工具摘要分支 ──
    from agents.qa.nodes import _plan_course, _plan_advisor
    _pc1 = _plan_course("我选的课时间冲突吗", "PB1")
    t("路由-冲突→check_course_conflict", _pc1[0]["tool"] == "check_course_conflict", str(_pc1))
    _pc2 = _plan_course("我学分是不是超了，要退哪门", "PB1")
    t("路由-退补选→evaluate_selection_pressure",
      _pc2[0]["tool"] == "evaluate_selection_pressure", str(_pc2))
    _pa1 = _plan_advisor("推荐课会不会和我课表撞课", {})
    t("路由-复合推荐→recommend_courses",
      _pa1[0]["tool"] == "recommend_courses", str(_pa1))
    _pa_standalone = _plan_advisor("我的选修课冲突吗", {})
    t("路由-独立选修冲突→check_course_conflict",
      _pa_standalone[0]["tool"] == "check_course_conflict", str(_pa_standalone))
    _pa2 = _plan_advisor("退补选建议", {})
    t("路由-选课顾问退补选→evaluate_selection_pressure",
      _pa2[0]["tool"] == "evaluate_selection_pressure", str(_pa2))

    # ── AGENT 决策加固: 确定性路由 / 工具校验 ──
    from agents.qa.nodes import _check_tool_choice, _direct_tool_route, _valid_tools
    _r1 = _direct_tool_route({"intent": "选课冲突", "query": "我选的课时间冲突吗"})
    t("确定性路由-选课冲突→check_course_conflict",
      _r1 is not None and _r1["tool_calls"][0]["tool"] == "check_course_conflict", str(_r1))
    _r2 = _direct_tool_route({"intent": "退补选评估", "query": "我学分是不是超了，帮我看看要不要退课"})
    t("确定性路由-退补选→evaluate_selection_pressure",
      _r2 is not None and _r2["tool_calls"][0]["tool"] == "evaluate_selection_pressure", str(_r2))
    _r3 = _direct_tool_route({"intent": "选课冲突", "query": "我的选课情况怎么样"})
    t("确定性路由-意图命中但无关键词→交LLM", _r3 is None, str(_r3))
    _r4 = _direct_tool_route({"intent": "知识问答", "query": "我选的课时间冲突吗"})
    t("确定性路由-非路由意图→交LLM", _r4 is None, str(_r4))
    _r5 = _direct_tool_route({"intent": "选课冲突", "query": "我选的课时间冲突吗",
                              "tool_results": [{"tool": "check_course_conflict", "status": "done"}]})
    t("确定性路由-已有结果转合成（轮1死循环回归）",
      _r5 is not None and _r5["decision"] == "compose", str(_r5))
    _r6 = _direct_tool_route({"intent": "选课冲突", "query": "推荐几门课会不会和我课表冲突？"})
    t("确定性路由-复合推荐不受 embedding 冲突意图影响",
      _r6 is not None and _r6["tool_calls"][0]["tool"] == "recommend_courses", str(_r6))
    _r7 = _direct_tool_route({"intent": "选课冲突", "query": "我的选修课冲突吗"})
    t("确定性路由-名词型选修冲突保持独立检查",
      _r7 is not None and _r7["tool_calls"][0]["tool"] == "check_course_conflict", str(_r7))
    # 轮2 回归：think 直接走确定性路由 compose 分支不得崩溃（455 行日志越界）
    from agents.qa.nodes import think as _think
    _t = _think({"query": "我选的课时间冲突吗", "intent": "选课冲突", "rounds": 1,
                 "tool_results": [{"tool": "check_course_conflict", "status": "done"}],
                 "thought_log": [], "student_id": "PB1", "user_profile": {}})
    t("think-路由已有结果转合成不崩溃（轮2回归）",
      _t.get("decision") == "compose" and _t.get("tool_calls") == [], str(_t))
    _vt = _valid_tools()
    # P4-1 起：内置 28 工具 + 生态工具（eco: 前缀，动态加载；echo 为协议自检样例）
    _eco = [v for v in _vt if v.startswith("eco:")]
    t("工具校验-注册表含内置28+生态工具", len(_vt) - len(_eco) >= 28
      and "check_course_conflict" in _vt and "evaluate_selection_pressure" in _vt
      and "eco:echo" in _vt, f"{len(_vt)} 工具（生态 {len(_eco)}）")
    t("工具校验-合法工具ok", _check_tool_choice("query_grade", set()) == "ok", "")
    t("工具校验-未知工具unknown", _check_tool_choice("not_a_tool", set()) == "unknown", "")
    t("工具校验-重复调用done", _check_tool_choice("query_grade", {"query_grade"}) == "done", "")
    t("工具校验-空工具empty", _check_tool_choice("", set()) == "empty", "")
    from tools.advisor_tools import recommend_courses as _recommend_tool
    _recommend_schema = _recommend_tool.args_schema.model_json_schema().get("properties", {})
    t("推荐工具已永久移除时间约束参数", "schedule_constraints" not in _recommend_schema,
      str(sorted(_recommend_schema)))

    _fake_cc = [{"tool": "check_course_conflict", "status": "done", "result": {
        "total": 2, "courses": [{"course_name": "A"}, {"course_name": "B"}],
        "conflicts": [{"course_a": "力学B", "course_b": "热学B", "day": "周二",
                       "reason": "节次重叠", "weeks_unknown": True}],
        "conflict_count": 1, "time_incomplete": ["X课"], "missing": ["量子力学"]}}]
    _s_cc = _build_tool_summary(_fake_cc)
    t("工具摘要-冲突分支", "check_course_conflict" in _s_cc and "力学B × 热学B" in _s_cc
      and "保守判定" in _s_cc and "量子力学" in _s_cc, _s_cc[:150])

    _fake_p = [{"tool": "evaluate_selection_pressure", "status": "done", "result": {
        "credit_cap": 30.0, "source": "fallback",
        "current": {"course_count": 14, "total_credits": 31.0, "credit_cap": 30.0,
                    "conflict_count": 0, "time_incomplete": [],
                    "daily": {"周二": {"course_count": 4, "slot_count": 4}},
                    "busiest_day": "周二"},
        "after_add_drop": {"course_count": 13, "total_credits": 28.5, "conflict_count": 0},
        "drops_applied": ["力学B"], "adds_pending": ["量子力学"],
        "suggestions": ["当前总学分 31.0 已超过 30.0 学分参考上限"]}}]
    _s_p = _build_tool_summary(_fake_p)
    t("工具摘要-压力分支", "evaluate_selection_pressure" in _s_p and "31.0" in _s_p
      and "模拟后" in _s_p and "建议" in _s_p, _s_p[:150])

    print(f"\n结果: 通过 {len(TOTAL) - len(FAILURES)}/{len(TOTAL)}")


if __name__ == "__main__":
    main()
    sys.exit(1 if FAILURES else 0)
