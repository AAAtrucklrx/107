# -*- coding: utf-8 -*-
"""工具层单测：推荐排序 / 关键词过滤 / 低 workload 偏好 / 教师对比 / 空结果边界 / 课程名归一化匹配与多班型歧义

用法: python scripts/verify_tools.py
退出码: 0=全部断言通过, 1=存在失败项
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.advisor_tools import analyze_teacher, compare_courses, recommend_courses

PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))
        raise SystemExit(1)


# 1. 推荐排序: rating_avg 降序
r = recommend_courses.invoke({
    "profile": {"major": "计算机科学", "grade": "大二",
                "interests": ["人工智能"], "preference_type": "easy_grade", "max_results": 5}})
recs = r["recommendations"]
ok("推荐非空", len(recs) > 0, f"{len(recs)} 门, 候选 {r['total_candidates']}")
avgs = [c["rating_avg"] for c in recs]
# 分组排序语义（2026-08 重构）: 必修组前置（学期紧迫度非降, 同档评分降序）,
# 选修组评分降序; 不再要求全局均分降序（低分必修可能排在高分选修前）
import re as _re
groups = r.get("groups") or {}
req_recs, ele_recs = groups.get("required") or [], groups.get("elective") or []

def _term_year(c):
    hint = c.get("program_hint") or {}
    m = _re.match(r"\s*(\d)", str(hint.get("term", "")))
    return int(m.group(1)) if m else 2

def _urgency(c):
    y = _term_year(c)
    return 0 if y < 2 else (1 if y == 2 else 2)

req_urg = [_urgency(c) for c in req_recs]
ok("必修组紧迫度非降(过期置顶)", req_urg == sorted(req_urg), f"{req_urg}")
seg_ok = True
i = 0
while i < len(req_recs):
    j = i
    while j + 1 < len(req_recs) and req_urg[j + 1] == req_urg[i]:
        j += 1
    seg = [c["rating_avg"] for c in req_recs[i:j + 1]]
    if seg != sorted(seg, reverse=True):
        seg_ok = False
    i = j + 1
ok("必修组同档评分降序", seg_ok,
   [f"{c['name']}:{c['rating_avg']}({_term_year(c)})" for c in req_recs][:6])
ele_urg = [_urgency(c) for c in ele_recs]
ok("选修组紧迫度非降(当前学年优先)", ele_urg == sorted(ele_urg), f"{ele_urg}")
seg_ok_e = True
i = 0
while i < len(ele_recs):
    j = i
    while j + 1 < len(ele_recs) and ele_urg[j + 1] == ele_urg[i]:
        j += 1
    seg = [c["rating_avg"] for c in ele_recs[i:j + 1]]
    if seg != sorted(seg, reverse=True):
        seg_ok_e = False
    i = j + 1
ok("选修组同档评分降序", seg_ok_e,
   [f"{c['name']}:{c['rating_avg']}({_term_year(c)})" for c in ele_recs][:6])
ok("必修组前置", recs[:len(req_recs)] == req_recs,
   f"必修 {len(req_recs)} 门 + 选修 {len(ele_recs)} 门")
ok("推荐含关键字段", all(c.get("name") and c.get("teachers") is not None
                        and c.get("rating_avg") and c.get("dims") for c in recs),
   f"首课: {recs[0]['name']} | {recs[0]['rating_avg']}分·{recs[0]['rate_count']}条")

# 2. 关键词过滤: 结果应全部命中关键词
r_kw = recommend_courses.invoke({"profile": {"max_results": 10}, "keywords": ["数学分析"]})
kw_recs = r_kw["recommendations"]
ok("关键词过滤非空", len(kw_recs) > 0, f"{len(kw_recs)} 门")
ok("关键词全部命中", all("数学分析" in c["name"] for c in kw_recs),
   [c["name"] for c in kw_recs][:5])

# 3. 低 workload 偏好: "任务少" → easy_grade（冲分保绩）
r_lw = recommend_courses.invoke({
    "profile": {"major": "计算机科学", "grade": "大二", "preference": "任务少", "max_results": 3}})
ok("任务少→easy_grade 映射", r_lw.get("profile_note", {}).get("name") == "冲分保绩",
   str(r_lw.get("profile_note")))
lw_hw = [c["dims"]["avg"].get("作业") for c in r_lw["recommendations"]]
ok("低 workload 推荐含作业维度", all(h is not None for h in lw_hw), f"作业均分={lw_hw}")

# 4. 教师对比: 课程模式返回多师 + 各师评分
ta = analyze_teacher.invoke({"course": "数学分析(B1)"})
teachers = ta.get("teachers") or []
ok("课程模式返回教师", len(teachers) > 0, f"{len(teachers)} 位")
ok("教师含均分/样本量", all(t.get("rating_avg") is not None and t.get("rate_count") is not None
                          for t in teachers),
   [(t["name"], t["rating_avg"], t["rate_count"]) for t in teachers])
ok("教师均分降序", [t["rating_avg"] for t in teachers] ==
   sorted([t["rating_avg"] for t in teachers], reverse=True))
ok("课程模式含评论样本", len(ta.get("reviews_sample") or []) > 0,
   f"{len(ta.get('reviews_sample') or [])} 条")
ok("样本评论带教师标注", all(rv.get("teacher") for rv in (ta.get("reviews_sample") or [])[:3]))

# 4b. 教师模式 + 课程对比
tp = analyze_teacher.invoke({"teacher_name": "邵帅"})
ok("教师模式返回课程", len(tp.get("courses") or []) > 0, f"{len(tp.get('courses') or [])} 门课")
cmp_r = compare_courses.invoke({"course_a": "数学分析(B1)", "course_b": "线性代数(B1)"})
ok("课程对比返回 winner", bool(cmp_r.get("comparison", {}).get("rating_winner")),
   f"{cmp_r['comparison']['rating_winner']} (diff {cmp_r['comparison']['rating_diff']})")

# 5. 空结果边界: 不存在的课程/教师 → error; 过窄课程范围保持硬约束
cmp_err = compare_courses.invoke({"course_a": "不存在的课程XYZ123", "course_b": "另一个不存在的课ABC"})
ok("对比不存在的课程返回 error", "error" in cmp_err, cmp_err.get("error", ""))
ok("教师不存在返回 error", "error" in analyze_teacher.invoke({"teacher_name": "不存在老师XYZ123"}))
r_emp = recommend_courses.invoke({"profile": {"max_results": 3}, "keywords": ["绝对不存在的课程词XYZ"]})
ok("过窄课程范围不放宽", r_emp.get("keyword_fallback") is False and not r_emp["recommendations"],
   f"fallback={r_emp.get('keyword_fallback')}, {len(r_emp['recommendations'])} 门")
ok("过窄课程范围说明硬条件", any("课程范围" in text and "未放宽" in text
                                  for text in r_emp.get("limitations") or []),
   r_emp.get("limitations"))

# 6. 课程名归一化匹配: 无括号 / 带空格输入都能命中库中 "数学分析(B1)"
r_np = analyze_teacher.invoke({"course": "数学分析B1"})
ok("无括号命中非空 teachers", len(r_np.get("teachers") or []) > 0,
   f"{len(r_np.get('teachers') or [])} 位, 课程={r_np.get('course')}")
ok("无括号命中无 error", "error" not in r_np, r_np.get("error", ""))

r_sp = analyze_teacher.invoke({"course": "数学分析 (B1)"})
ok("带空格命中非空 teachers", len(r_sp.get("teachers") or []) > 0,
   f"{len(r_sp.get('teachers') or [])} 位, 课程={r_sp.get('course')}")
ok("带空格命中无 error", "error" not in r_sp, r_sp.get("error", ""))

# 7. 多班型歧义: "数学分析" 同时命中 B1/B2 → ambiguity + 候选确认
r_amb = analyze_teacher.invoke({"course": "数学分析"})
cand_names = [c["name"] for c in (r_amb.get("candidates") or [])]
ok("多班型返回 ambiguity", r_amb.get("ambiguity") is True, r_amb.get("message", ""))
ok("歧义候选含 B1/B2", "数学分析(B1)" in cand_names and "数学分析(B2)" in cand_names,
   f"{cand_names}")

# 8. 关键词归一化过滤: "数学分析B1" 应命中所有含「数学分析」的课程
r_kw = recommend_courses.invoke({"profile": {"max_results": 5}, "keywords": ["数学分析B1"]})
kw_recs = r_kw.get("recommendations") or []
ok("关键词归一化结果非空", len(kw_recs) > 0, f"{len(kw_recs)} 门")
ok("关键词归一化全部含「数学分析」", all("数学分析" in c["name"] for c in kw_recs),
   [c["name"] for c in kw_recs][:5])

# 9. 课程搜索跨库合并: 本地种子课程表仅少量样例,
#    search_courses 应合并评课库完整课程表, 使「数学分析B1」可被检索
from database.db_manager import DatabaseManager
from services.service_container import ServiceContainer
ServiceContainer()._db = DatabaseManager("database/xiaowo.db")
# 注入假 Catalog（始终失败）, 让断言确定性地覆盖本地 fallback 路径
class _OfflineCatalog:
    def search_courses(self, keyword):
        raise RuntimeError("offline")
ServiceContainer()._catalog_api = _OfflineCatalog()
from tools.course_tools import search_courses, _norm_course_name

r_sc = search_courses.invoke({"keyword": "数学分析B1"})
sc_names = [c["course_name"] for c in (r_sc.get("courses") or [])]
ok("课程搜索跨库命中 B1", r_sc.get("count", 0) >= 1 and "数学分析(B1)" in sc_names,
   f"count={r_sc.get('count')}, {sc_names}")

r_sc2 = search_courses.invoke({"keyword": "数学分析"})
sc2_names = [c["course_name"] for c in (r_sc2.get("courses") or [])]
ok("课程搜索多班型覆盖", "数学分析(B1)" in sc2_names and "数学分析(B2)" in sc2_names,
   f"{len(sc2_names)} 门: {sc2_names}")
ok("课程搜索无重复课程名",
   len({_norm_course_name(n) for n in sc2_names}) == len(sc2_names),
   sc2_names)

# 10. 选课 H 项: 冲突检测与退补选压力评估（伪登录测试账号 PB25111691）
from services.session_ctx import set_student, reset_student
from tools.selection_tools import check_course_conflict, evaluate_selection_pressure

_tok = set_student("PB25111691")
_ca = ServiceContainer().cas_client
_ca._logged_in = True
_ca._student_id = "PB25111691"
try:
    r_cc = check_course_conflict.invoke({"student_id": "PB25111691"})
    ok("冲突检测读取已选课程", r_cc.get("total", 0) >= 10, f"{r_cc.get('total')} 门")
    ok("冲突检测结果结构",
       all(c.get("course_a") and c.get("course_b") and c.get("day") and c.get("reason")
           for c in r_cc.get("conflicts") or []),
       f"{r_cc.get('conflict_count')} 处")
    pair_key = {tuple(sorted([c["course_a"], c["course_b"]])) for c in r_cc.get("conflicts", [])}
    ok("周次不重叠不算冲突(力学B×热学B)", ("力学B", "热学B") not in pair_key,
       f"冲突对: {pair_key or '无'}")

    r_p = evaluate_selection_pressure.invoke({"student_id": "PB25111691"})
    cur = r_p.get("current") or {}
    ok("压力评估含学分统计", cur.get("total_credits", 0) > 0 and cur.get("credit_cap") == 30.0,
       f"学分 {cur.get('total_credits')}/{cur.get('credit_cap')} 超={cur.get('over_cap')}")
    ok("压力评估含每日分布", bool(cur.get("daily")) and bool(cur.get("busiest_day")),
       f"最忙 {cur.get('busiest_day')}")

    r_sim = evaluate_selection_pressure.invoke(
        {"student_id": "PB25111691", "drop_courses": ["力学B"], "add_courses": ["量子力学"]})
    after = r_sim.get("after_add_drop") or {}
    ok("模拟退课学分下降",
       cur.get("total_credits") - after.get("total_credits", cur.get("total_credits")) > 0,
       f"{cur.get('total_credits')} → {after.get('total_credits')}")
    ok("加课无排课数据如实标注", "量子力学" in (r_sim.get("adds_pending") or []),
       str(r_sim.get("adds_pending")))
finally:
    reset_student(_tok)

print(f"\n结果: {PASS}/{PASS} 断言通过")
