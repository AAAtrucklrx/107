# -*- coding: utf-8 -*-
"""选课推荐全链路回归测试（LangGraph 流水线测试入口, TEST_SCRIPTS 第一顺位）。

覆盖:
1. 推荐排序 = 真实均分降序（不归一化）
2. 同课多师: 各老师独立均分/样本量
3. 评论引用: 点赞序 + 作者去重（匿名不去重）+ 条数上限
4. 画像软过滤: easy_grade 给分/难度提示
5. compare_courses / analyze_teacher 基本契约
6. 培养方案弱标注（program_hint）
7. 数据完整性: 表结构/对账/孤儿/学期重复（临时库）

临时库隔离: 全部测试使用 tempfile 构造的迷你 course_data.db, 不依赖全量爬取。
用法: python scripts/test_fixes.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

FAILURES: list[str] = []
TOTAL: list[int] = []


def t(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    TOTAL.append(1)
    if not ok:
        FAILURES.append(name)


def build_mini_db() -> str:
    """构造迷你 course_data.db（模拟 8 表 + 评分/评论/老师/方案数据）。

    临时目录默认 tempfile.mkdtemp；受限环境（沙箱/CI）可用环境变量
    XIAOWO_TEST_TMP 指定一个可写的既有目录（该目录下同名库文件构建前先清除）。"""
    schema = Path(__file__).resolve().parents[1] / "database" / "schema_course.sql"
    base = os.environ.get("XIAOWO_TEST_TMP")
    if base:
        tmp_dir = Path(base)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = tmp_dir / "course_data.db"
        if tmp.exists():
            tmp.unlink()
    else:
        tmp = Path(tempfile.mkdtemp(prefix="xiaowo_test_")) / "course_data.db"
    conn = sqlite3.connect(str(tmp))
    conn.executescript(schema.read_text(encoding="utf-8"))
    cur = conn.cursor()

    # 课程: 2 门单师课 + 1 门同课多师课（A 高分 B 低分）
    cur.execute("INSERT INTO courses(id, name, dept, code, credit, icourse_ids) VALUES(1, '组合数学', '计算机科学与技术系', 'COMP6002P04', 3.0, '[101]')")
    cur.execute("INSERT INTO courses(id, name, dept, code, credit, icourse_ids) VALUES(2, '社会心理学', '心理学系', 'HS153403', 2.0, '[102]')")
    cur.execute("INSERT INTO courses(id, name, dept, code, credit, icourse_ids) VALUES(3, '数学分析(B1)', '数学科学学院', 'MATH100604', 6.0, '[103]')")
    cur.execute("INSERT INTO courses(id, name, dept, code, credit, icourse_ids) VALUES(4, '算法设计', '计算机科学与技术系', 'CS1001', 3.0, '[104]')")

    # 评论（id 顺序模拟点赞序: 先插入 = 更靠前）
    def rev(cid, iid, rid, teacher, author, stars, term, diff, hw, gs, gain, content):
        cur.execute(
            "INSERT INTO reviews(course_id, icourse_id, review_iid, teacher, author, stars, term, "
            "difficulty, homework, give_score, harvest, content) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, iid, rid, teacher, author, stars, term, diff, hw, gs, gain, content))

    # 课程1: 邵帅 4 条（3 条有作者, 2 条同作者 → 去重应留 1）
    rev(1, 101, 1, "邵帅", "学生甲", 5, "2023秋", "困难", "很多", "超好", "很多", "邵帅的组合数学讲得极好，证明推导非常清晰。")
    rev(1, 101, 2, "邵帅", "学生乙", 4, "2022秋", "困难", "多", "好", "多", "作业很难但收获很大，值得一学。")
    rev(1, 101, 3, "邵帅", "匿名用户", 5, "2024秋", "困难", "很多", "超好", "很多", "匿名第一条评论内容。")
    rev(1, 101, 4, "邵帅", "匿名用户", 3, "2021秋", "困难", "多", "一般", "多", "匿名第二条评论内容。")
    # 课程2: 杨映秋 2 条
    rev(2, 102, 1, "杨映秋", "学生丙", 5, "2025秋", "简单", "很少", "好", "一般", "社会心理学很有意思，课堂互动多。")
    rev(2, 102, 2, "杨映秋", "学生丁", 4, "2024秋", "简单", "少", "好", "多", "考试不难，认真听讲就能拿高分。")
    # 课程3: 同课多师: 汪琥庭 9.5 / 李四 5.0
    rev(3, 103, 1, "汪琥庭", "学生戊", 5, "2024秋", "中等", "中等", "超好", "很多", "汪爷爷上课好，数分讲得清楚。")
    rev(3, 103, 2, "汪琥庭", "学生己", 5, "2023秋", "中等", "中等", "超好", "很多", "跟着汪老师学数分很踏实。")
    rev(3, 103, 3, "汪琥庭", "学生庚", 4, "2022秋", "中等", "中等", "好", "多", "板书工整，答疑耐心。")
    rev(3, 103, 4, "李四", "学生辛", 3, "2024秋", "困难", "很多", "差", "少", "李四的数分讲得快，跟不上。")
    rev(3, 103, 5, "李四", "学生壬", 2, "2023秋", "困难", "很多", "很差", "少", "不推荐李四，作业多且讲得含糊。")
    # 课程4: 单师 1 条（样本少提示）
    rev(4, 104, 1, "张三", "学生癸", 5, "2025春", "中等", "中等", "好", "多", "算法课内容扎实。")

    # course_rates 聚合（维度 CASE 映射与 build_course_db.py 一致）
    cur.execute(
        "INSERT INTO course_rates(course_id, rating_sum, rating_count, rating_avg, diff_sum, diff_count, diff_avg, "
        "hw_sum, hw_count, hw_avg, score_sum, score_count, score_avg, gain_sum, gain_count, gain_avg, dims_dist) "
        "SELECT course_id, SUM(stars*2.0), SUM(CASE WHEN stars>0 THEN 1 ELSE 0 END), AVG(CASE WHEN stars>0 THEN stars*2.0 END), "
        "SUM(CASE difficulty WHEN '简单' THEN 10 WHEN '中等' THEN 6.5 WHEN '困难' THEN 3 END), "
        "SUM(CASE WHEN difficulty != '' THEN 1 ELSE 0 END), "
        "AVG(CASE difficulty WHEN '简单' THEN 10 WHEN '中等' THEN 6.5 WHEN '困难' THEN 3 END), "
        "SUM(CASE homework WHEN '很少' THEN 10 WHEN '少' THEN 8 WHEN '中等' THEN 6.5 WHEN '多' THEN 4 WHEN '很多' THEN 2 END), "
        "SUM(CASE WHEN homework != '' THEN 1 ELSE 0 END), "
        "AVG(CASE homework WHEN '很少' THEN 10 WHEN '少' THEN 8 WHEN '中等' THEN 6.5 WHEN '多' THEN 4 WHEN '很多' THEN 2 END), "
        "SUM(CASE give_score WHEN '很差' THEN 2 WHEN '差' THEN 4 WHEN '一般' THEN 6 WHEN '好' THEN 8 WHEN '超好' THEN 10 END), "
        "SUM(CASE WHEN give_score != '' THEN 1 ELSE 0 END), "
        "AVG(CASE give_score WHEN '很差' THEN 2 WHEN '差' THEN 4 WHEN '一般' THEN 6 WHEN '好' THEN 8 WHEN '超好' THEN 10 END), "
        "SUM(CASE harvest WHEN '没有' THEN 2 WHEN '少' THEN 4 WHEN '一般' THEN 6 WHEN '多' THEN 8 WHEN '很多' THEN 10 END), "
        "SUM(CASE WHEN harvest != '' THEN 1 ELSE 0 END), "
        "AVG(CASE harvest WHEN '没有' THEN 2 WHEN '少' THEN 4 WHEN '一般' THEN 6 WHEN '多' THEN 8 WHEN '很多' THEN 10 END), "
        "'{}' FROM reviews GROUP BY course_id")
    # 学期
    cur.execute("INSERT INTO course_terms(course_id, term) VALUES(1, 20231),(1, 20232),(2, 20251),(3, 20241),(4, 20252)")
    # 教师
    cur.execute("INSERT INTO teachers(id, name) VALUES(1, '邵帅'),(2, '杨映秋'),(3, '汪琥庭'),(4, '李四'),(5, '张三')")
    # 同课多师聚合
    cur.execute(
        "INSERT INTO course_teachers(course_id, teacher_id, rating_sum, rating_count, rating_avg, dims_dist) "
        "SELECT course_id, 3, 28, 3, 9.33, '{}' FROM reviews WHERE course_id=3 AND teacher='汪琥庭' LIMIT 1")
    cur.execute(
        "INSERT INTO course_teachers(course_id, teacher_id, rating_sum, rating_count, rating_avg, dims_dist) "
        "SELECT course_id, 4, 10, 2, 5.0, '{}' FROM reviews WHERE course_id=3 AND teacher='李四' LIMIT 1")
    # 方案
    cur.execute("INSERT INTO programs(id, name, college, grade) VALUES(1, '数学与应用数学专业培养方案', '数学科学学院', '2025级')")
    cur.execute(
        "INSERT INTO program_courses(program_id, course_id, code, name, required, exam, credit, category, term) "
        "VALUES(1, 3, 'MATH100604', '数学分析(B1)', '必修', '笔试（闭卷）', '6', '专业基础课程', '1秋')")
    # courses 汇总回填
    cur.execute(
        "UPDATE courses SET rating_avg = (SELECT rating_avg FROM course_rates r WHERE r.course_id = courses.id),"
        " rate_count = (SELECT rating_count FROM course_rates r WHERE r.course_id = courses.id)")
    conn.commit()
    conn.close()
    return str(tmp)


def run() -> None:
    db_path = build_mini_db()
    # 指向临时库
    import tools.advisor_tools as at
    at.COURSE_DB = Path(db_path)

    # ── 1. 推荐排序 = 必修组前置 + 组内真实均分降序 ──
    r = at.recommend_courses.invoke({"profile": {"major": "数学与应用数学", "max_results": 10}})
    recs = r["recommendations"]
    names = [c["name"] for c in recs]
    t("推荐返回非空", len(recs) >= 4, f"共 {len(recs)} 门")
    # 分组语义: 必修组前置（同档按评分降序）, 选修组评分降序; 不再要求全局均分降序
    groups = r.get("groups") or {}
    req_recs = groups.get("required") or []
    ele_recs = groups.get("elective") or []
    req_ok = all(req_recs[i]["rating_avg"] >= req_recs[i + 1]["rating_avg"]
                 for i in range(len(req_recs) - 1))
    ele_ok = all(ele_recs[i]["rating_avg"] >= ele_recs[i + 1]["rating_avg"]
                 for i in range(len(ele_recs) - 1))
    t("必修组前置且组内降序", req_ok and ele_ok and recs[:len(req_recs)] == req_recs,
      f"必修 {len(req_recs)} 门 / 选修 {len(ele_recs)} 门")
    # 数学分析(B1): 5 条评论（汪 5,5,4 + 李 3,2）→ (19×2)/5 = 7.6, 与 course_rates 一致
    math = next((c for c in recs if c["name"] == "数学分析(B1)"), None)
    if math:
        t("评分=星级均值×2", abs(math["rating_avg"] - 7.6) < 0.2, f"got {math['rating_avg']}")
    else:
        t("评分=星级均值×2", False, "未找到数学分析(B1)")

    # ── 2. 同课多师 ──
    if math:
        t("同课多师已区分", math["multi_teacher"] and len(math["teachers"]) == 2,
          f"teachers={[(x['name'], x['rating_avg']) for x in math['teachers']]}")
        wa = next((x for x in math["teachers"] if x["name"] == "汪琥庭"), None)
        li = next((x for x in math["teachers"] if x["name"] == "李四"), None)
        t("老师均分各自独立", wa and li and wa["rating_avg"] > li["rating_avg"],
          f"汪琥庭 {wa['rating_avg'] if wa else '-'} vs 李四 {li['rating_avg'] if li else '-'}")

    # ── 3. 评论引用: 点赞序(插入序) + 作者去重 + 条数 ──
    cmb = next((c for c in recs if c["name"] == "组合数学"), None)
    if cmb:
        revs = cmb["top_reviews"]
        t("单师课评论 5-6 条", 4 <= len(revs) <= 6, f"got {len(revs)}")
        authors = [x["author"] for x in revs]
        named = [a for a in authors if a != "匿名用户"]
        dup_named = len(named) != len(set(named))
        t("有作者去重（匿名保留）", not dup_named and authors.count("匿名用户") == 2, f"作者序列: {authors}")
        # 点赞序: 学生甲(第1条)应在前面
        t("评论按点赞序", revs[0]["author"] == "学生甲", f"首条作者: {revs[0]['author']}")
        t("评论内容真实原文", revs[0]["content"].startswith("邵帅的组合数学"), "")
    else:
        t("评论引用检查", False, "未找到组合数学")

    # 同课多师评论: 每师最多 3 条, 总量封顶 6
    if math:
        t("多师课评论封顶 6", len(math["top_reviews"]) <= 6, f"got {len(math['top_reviews'])}")

    # ── 4. 画像软过滤理由 ──
    r_easy = at.recommend_courses.invoke({"profile": {"preference_type": "easy_grade", "max_results": 5}})
    hard = next((c for c in r_easy["recommendations"] if c["name"] == "组合数学"), None)
    if hard:
        joined = "；".join(hard["reasons"])
        t("画像理由（难度提示）", "难度" in joined and ("谨慎" in joined or "冲分" in joined), joined)
    else:
        t("画像理由", False, "未找到组合数学")

    # ── 5. compare_courses ──
    cmp = at.compare_courses.invoke({"course_a": "组合数学", "course_b": "社会心理学"})
    t("对比返回双方", "course_a" in cmp and "course_b" in cmp and "comparison" in cmp, "")
    if "comparison" in cmp:
        t("对比评分胜者", cmp["comparison"]["rating_winner"] == "社会心理学",
          f"winner={cmp['comparison']['rating_winner']}")

    # ── 6. analyze_teacher ──
    ta = at.analyze_teacher.invoke({"teacher_name": "汪琥庭"})
    t("教师分析返回", "teacher" in ta and ta["teacher"] == "汪琥庭" and len(ta["courses"]) == 1,
      f"courses={[(c['name'], c['rating_avg']) for c in ta.get('courses', [])]}")
    t("教师均分", abs(ta["avg_rating"] - 9.3) < 0.2, f"got {ta['avg_rating']}")

    # ── 6b. analyze_teacher 课程模式（"XX课哪个老师好"回归） ──
    tc = at.analyze_teacher.invoke({"course": "数学分析"})
    t("课程老师对比返回", "course" in tc and tc["course"] == "数学分析(B1)" and len(tc["teachers"]) == 2,
      f"course={tc.get('course')} teachers={[(x['name'], x['rating_avg']) for x in tc.get('teachers', [])]}")
    if "teachers" in tc and len(tc["teachers"]) == 2:
        t("课程老师对比排序", tc["teachers"][0]["rating_avg"] > tc["teachers"][1]["rating_avg"],
          f"{tc['teachers'][0]['name']} {tc['teachers'][0]['rating_avg']} vs {tc['teachers'][1]['name']} {tc['teachers'][1]['rating_avg']}")

    # ── 7. 培养方案弱标注 ──
    if math:
        ph = math["program_hint"]
        t("培养方案弱标注", ph and ph["required"] == "必修" and ph["term"] == "1秋", f"{ph}")

    # ── 8. 数据完整性（临时库核心断言） ──
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    n_c, n_r = cur.execute("SELECT COUNT(*) FROM courses").fetchone()[0], cur.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    t("临时库行数", n_c == 4 and n_r == 12, f"courses={n_c} reviews={n_r}")
    mism = cur.execute(
        "SELECT COUNT(*) FROM course_rates r WHERE r.rating_count != "
        "(SELECT COUNT(*) FROM reviews v WHERE v.course_id = r.course_id AND v.stars > 0)").fetchone()[0]
    t("预聚合对账", mism == 0, f"不一致 {mism}")
    dup = cur.execute(
        "SELECT COUNT(*) FROM (SELECT course_id, term FROM course_terms GROUP BY course_id, term HAVING COUNT(*)>1)").fetchone()[0]
    t("学期无重复", dup == 0)
    orphan = cur.execute(
        "SELECT COUNT(*) FROM course_teachers t WHERE NOT EXISTS (SELECT 1 FROM courses c WHERE c.id = t.course_id)").fetchone()[0]
    t("无孤儿引用", orphan == 0)
    conn.close()

    # ── 9. 构建流程回归: 同课多师评论合并不丢（两 icourse 页评论 id 相同） ──
    import build_course_db as bcd
    tmp_data = Path(db_path).parent / "data"
    (tmp_data / "raw").mkdir(parents=True, exist_ok=True)
    (tmp_data / "programs").mkdir(parents=True, exist_ok=True)
    bcd.DATA_DIR = tmp_data
    bcd.RAW_DIR = tmp_data / "raw"
    bcd.PROG_DIR = tmp_data / "programs"

    def mk_rev(rid, stars, author, content):
        return {"id": rid, "author": author, "stars": stars, "term": "2024秋",
                "dims": {}, "content": content}

    (bcd.RAW_DIR / "103.json").write_text(json.dumps({
        "id": 103, "course_name": "数学分析(B1)", "teacher": "汪琥庭", "rating": 9.3, "rate_count": 3,
        "terms": ["2024秋"], "code": "MATH100604", "credit": "6", "dept": "数学科学学院",
        "course_type": "", "course_level": "", "dims_agg": {},
        "reviews": [mk_rev(1, 5, "学生甲", "汪爷爷上课好，数分讲得清楚。"),
                     mk_rev(2, 5, "学生乙", "跟着汪老师学数分很踏实。"),
                     mk_rev(3, 4, "学生丙", "板书工整，答疑耐心。")]}, ensure_ascii=False))
    (bcd.RAW_DIR / "105.json").write_text(json.dumps({
        "id": 105, "course_name": "数学分析(B1)", "teacher": "李四", "rating": 5.0, "rate_count": 2,
        "terms": ["2024秋"], "code": "MATH100604", "credit": "6", "dept": "数学科学学院",
        "course_type": "", "course_level": "", "dims_agg": {},
        "reviews": [mk_rev(1, 3, "学生丁", "李四的数分讲得快，跟不上。"),
                     mk_rev(2, 2, "学生戊", "不推荐李四，作业多且讲得含糊。")]}, ensure_ascii=False))
    db2 = Path(db_path).parent / "course_data_build.db"
    bcd.build(db2)
    c2 = sqlite3.connect(str(db2))
    n_bc = c2.execute("SELECT COUNT(*) FROM courses").fetchone()[0]
    n_br = c2.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    n_bt = c2.execute("SELECT COUNT(*) FROM course_teachers").fetchone()[0]
    t("构建合并同课多师不丢评论", n_bc == 1 and n_br == 5 and n_bt == 2,
      f"courses={n_bc} reviews={n_br} course_teachers={n_bt}")
    c2.close()

    # ── 10. 个性化 v1: GPA 自动画像 / 显式偏好优先 / 已修课程兴趣推断 ──
    r_hi = at.recommend_courses.invoke({"profile": {"gpa": 3.8, "max_results": 3}})
    t("GPA≥3.7 自动硬核画像", r_hi.get("profile_note", {}).get("auto") is True
      and r_hi["profile_note"].get("name") == "硬核学习", str(r_hi.get("profile_note")))

    r_lo = at.recommend_courses.invoke({"profile": {"gpa": 2.2, "max_results": 3}})
    t("GPA≤2.7 自动冲分画像", r_lo.get("profile_note", {}).get("auto") is True
      and r_lo["profile_note"].get("name") == "冲分保绩", str(r_lo.get("profile_note")))

    r_mid = at.recommend_courses.invoke({"profile": {"gpa": 3.2, "max_results": 3}})
    t("GPA 中段保持均衡画像", r_mid.get("profile_note", {}).get("name") == "均衡兼顾",
      str(r_mid.get("profile_note")))

    r_exp = at.recommend_courses.invoke({"profile": {"gpa": 3.9, "preference_type": "easy_grade", "max_results": 3}})
    t("显式偏好优先于 GPA", r_exp.get("profile_note", {}).get("name") == "冲分保绩"
      and not r_exp["profile_note"].get("auto"), str(r_exp.get("profile_note")))

    r_int = at.recommend_courses.invoke({"profile": {"taken_courses": ["计算机程序设计"], "max_results": 5}})
    hit = any("兴趣「计算机」" in "；".join(c.get("reasons") or []) for c in r_int["recommendations"])
    t("已修课程推断兴趣理由", hit,
      f"样本 reasons: {[c['reasons'][:2] for c in r_int['recommendations'][:3]]}")

    # ── 11. Phase 2b: 方案定位双实现收敛（advisor 与 program 口径一致）──
    conn2 = sqlite3.connect(db_path)
    conn2.row_factory = sqlite3.Row
    try:
        import tools.program_tools as _pt
        row = _pt._resolve_program(conn2, "数学与应用数学", "2025级")
        pid2, pname2 = at._resolve_program(conn2, "数学与应用数学", "2025级")
        t("方案定位双实现收敛", row is not None and row["id"] == pid2 and row["name"] == pname2,
          f"program={row} advisor=({pid2},{pname2})")
        t("方案定位无专业不命中", _pt._resolve_program(conn2, "", None) is None
          and at._resolve_program(conn2, None, None) == (None, None), "")
    finally:
        conn2.close()

    print(f"\n结果: 通过 {len(TOTAL) - len(FAILURES)}/{len(TOTAL)}")


if __name__ == "__main__":
    run()
    sys.exit(1 if FAILURES else 0)
