"""
方案定位共享实现（Phase 2b 收敛）。

历史：advisor_tools 与 program_tools 各自复刻了一份 _resolve_program，且行为不一致
（advisor 带英才班/辅修优先级，program 无），同一专业可能在推荐与方案页定位到不同方案。
本模块为唯一实现，两处工具薄封装调用，保证口径一致。
"""
from __future__ import annotations

import re


def parse_grade_key(grade: str) -> int:
    """年级 → 可排序整数（"2024级"→2024，"大二"→无法解析返回 0）。"""
    m = re.match(r"\D*(\d{4})\D*", str(grade or ""))
    return int(m.group(1)) if m else 0


def prog_priority(r) -> int:
    """方案类型优先级：普通专业方案 0；英才班/带括号特殊方案 1；辅修 2。

    同年级多方案命中（普通班 vs 英才班/少年班等）时优先普通专业方案，
    避免给普通班学生推荐英才班专属课程（如量子物理、并行计算A 等）。"""
    name = r["name"] or ""
    if "辅修" in name:
        return 2
    if "英才班" in name or "（" in name or "(" in name:
        return 1
    return 0


# 学院/学部**常用简称 → 方案库里的学院名**（不含数字前缀）。
# 实测（2026-09-18）：用户问「转去生医部要补哪些课」，而库里的学院名是
# "910生命科学与医学部"——"生医部"既不是方案名也不是学院名子串，直接落空，
# 工具只能回"方案来源不可用"，用户以为没有生医部的方案（其实有 9 个）。
COLLEGE_ALIASES: dict[str, str] = {
    "生医部": "生命科学与医学部",
    "生命科学与医学部": "生命科学与医学部",
    "生院": "生命科学学院",
    "生命科学学院": "生命科学学院",
    "计院": "计算机科学与技术学院",
    "计算机学院": "计算机科学与技术学院",
    "计算机系": "计算机科学与技术学院",
    "物院": "物理学院",
    "化院": "化学与材料科学学院",
    "化材院": "化学与材料科学学院",
    "数院": "数学科学学院",
    "地空": "地球和空间科学学院",
    "地空学院": "地球和空间科学学院",
    "管院": "管理学院",
    "核院": "核科学技术学院",
    "工院": "工程科学学院",
    "少院": "少年班学院",
    "网安": "网络空间安全学院",
    "网安学院": "网络空间安全学院",
    "微电子": "集成电路学院（国家示范性微电子学院）",
    "集电": "集成电路学院（国家示范性微电子学院）",
    "集成电路学院": "集成电路学院（国家示范性微电子学院）",
    "信息学院": "信息科学技术学院",
    "信院": "信息科学技术学院",
    "人工智能学院": "人工智能与数据科学学院",
    "环境学院": "环境学院",
    "科技传播系": "科技传播系",
    "人文学院": "人文与社会科学学院",
}


def strip_college_noise(name: str | None) -> str:
    """学院名去掉数字前缀与单位后缀：'910生命科学与医学部' → '生命科学与医学'。

    后缀用「学院/系/部」而不是「学院/学部/系」：后者会把"…医学部"的"学部"整段吃掉，
    得到"生命科学与医"（2026-09-18 实测）。
    """
    return re.sub(r"^\d+", "", re.sub(r"(学院|系|部)$", "", str(name or ""))).strip()


def resolve_colleges(conn, query: str | None) -> list[str]:
    """查询词 → 方案库里的**学院名原值**（如 "910生命科学与医学部"）。

    顺序：① 学院名子串 ② 常用简称别名 ③ 去后缀词干**前缀**匹配（≥2 字，取最长）。
    """
    text = str(query or "").strip()
    if not text:
        return []
    colleges = [str(row[0]) for row in conn.execute("SELECT DISTINCT college FROM programs")]
    exact = [c for c in colleges if text in c]
    if exact:
        return exact
    alias = COLLEGE_ALIASES.get(text) or COLLEGE_ALIASES.get(text.replace("学院", ""))
    if alias:
        hit = [c for c in colleges if alias in c]
        if hit:
            return hit
    stem = strip_college_noise(text)
    if len(stem) >= 2:
        prefix_hits = [c for c in colleges if strip_college_noise(c).startswith(stem)]
        if prefix_hits:
            return sorted(prefix_hits, key=lambda c: (-len(strip_college_noise(c)), c))
    return []


def _college_candidates(conn, colleges: list[str], grade: str | None) -> list[dict]:
    """学院下的专业候选（按专业名去重，年级取最近、课程数取最大）。"""
    target = parse_grade_key(grade) if grade else 0
    best: dict[str, dict] = {}
    for college in colleges:
        rows = conn.execute(
            "SELECT p.id, p.name, p.college, p.grade, "
            "(SELECT COUNT(*) FROM program_courses pc WHERE pc.program_id = p.id) AS cc "
            "FROM programs p WHERE p.college = ?",
            (college,),
        ).fetchall()
        for row in rows:
            name = str(row["name"] or "")
            item = {
                "name": name,
                "grade": str(row["grade"] or ""),
                "college": strip_college_noise(row["college"]),
                "program_id": row["id"],
                "course_count": int(row["cc"] or 0),
            }
            current = best.get(name)
            if current is None:
                best[name] = item
                continue
            # 年级越接近越好；同年级取课程数更多（避开英才班/辅修壳）
            cur_diff = abs(parse_grade_key(current["grade"]) - target) if target else 0
            new_diff = abs(parse_grade_key(item["grade"]) - target) if target else 0
            if (new_diff, -item["course_count"]) < (cur_diff, -current["course_count"]):
                best[name] = item
    return sorted(best.values(), key=lambda c: (-c["course_count"], c["name"]))


def _nearby_programs(conn, text: str, limit: int = 5) -> list[dict]:
    """相近专业建议：按查询词的 2-gram 命中数排序（"生物医学工程" → 生物科学/生物技术/临床医学）。"""
    # 词元按**位置**加权：越靠前的二字词越有信息量（"生物医学工程"里的"生物" > "工程"），
    # 否则"高分子材料与工程""环境科学与工程"会凭"工程"两字挤进建议
    grams = [text[i:i + 2] for i in range(len(text) - 1)] or [text]
    weights = {gram: len(grams) - idx for idx, gram in enumerate(grams)}
    rows = conn.execute(
        "SELECT p.id, p.name, p.college, p.grade, "
        "(SELECT COUNT(*) FROM program_courses pc WHERE pc.program_id = p.id) AS cc "
        "FROM programs p"
    ).fetchall()
    scored = []
    for row in rows:
        name = str(row["name"] or "")
        score = sum(weight for gram, weight in weights.items() if gram in name)
        if score:
            # 其次普通专业方案优先（少年班/英才班/辅修方案名带括号或"英才班"），
            # 再短名优先（"临床医学专业培养方案" 优于 "少年班学院培养方案（生物科学）"）
            scored.append((score, prog_priority({"name": name}), len(name),
                           int(row["cc"] or 0), name, row))
    scored.sort(key=lambda item: (-item[0], item[1], item[2], -item[3], item[4]))
    out: list[dict] = []
    seen: set[str] = set()
    for _score, _prio, _len, _cc, name, row in scored:
        if name in seen:
            continue
        seen.add(name)
        out.append({
            "name": name,
            "college": strip_college_noise(row["college"]),
            "grade": str(row["grade"] or ""),
            "course_count": int(row["cc"] or 0),
        })
        if len(out) >= limit:
            break
    return out


def lookup_issue(conn, major: str | None, grade: str | None = None) -> dict | None:
    """方案定位的**前置问题**：学院级命中多专业 → 候选；完全找不到 → not_found + 相近建议。

    正常（能唯一定位到专业方案）返回 None，交回现有解析流程。
    """
    text = str(major or "").strip()
    if not text:
        return None
    if conn.execute("SELECT 1 FROM programs WHERE name LIKE ? LIMIT 1", (f"%{text}%",)).fetchone():
        return None  # 专业名能命中，交给 resolve_program
    colleges = resolve_colleges(conn, text)
    if colleges:
        candidates = _college_candidates(conn, colleges, grade)
        if len(candidates) <= 1:
            return None
        names = "、".join(item["name"] for item in candidates)
        return {
            "ambiguity": True,
            "query": text,
            "college": strip_college_noise(colleges[0]),
            "candidates": candidates,
            "message": (
                f"「{text}」是学院/学部，下有 {len(candidates)} 个专业方案：{names}。"
                "请先确认要转（要查）哪个专业，我再按那个专业的方案算补课清单。"
            ),
        }
    suggestions = _nearby_programs(conn, text)
    return {
        "not_found": True,
        "query": text,
        "suggestions": suggestions,
        "message": (
            f"培养方案库里没有叫「{text}」的专业/学院"
            + (f"；相近的专业有：{'、'.join(item['name'] for item in suggestions)}" if suggestions else "")
            + "。请给出准确的学院或专业名称（也可以说专业全称，如「临床医学」）。"
        ),
    }


def _lcp_len(a: str, b: str) -> int:
    """两串的最长公共前缀长度。"""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def resolve_program(conn, major: str | None, grade: str | None = None) -> dict | None:
    """全量库方案定位：同年级 → 最近低年级 → 最新；同年级内普通专业方案优先。

    匹配顺序：方案名 LIKE 精确优先（college LIKE 会误伤，如 major="人工智能" 命中
    人工智能与数据科学学院的数据科学方案），无命中才回退 college。

    并列时再按「方案名与查询词的公共前缀长度」排序：一个学院下常并列多个专业
    （203物理学院 有 物理学/应用物理学/天文学/光电信息科学与工程），只按年级+优先级
    会在同级同优先级里落到任意一个——实测 `物理学院` 命中了「天文学专业培养方案」。
    公共前缀把「物理学专业培养方案」顶上来（前缀「物理」）；对 `数学科学学院`
    同样能选到「数学与应用数学专业培养方案」（前缀「数学」），不依赖具体学院命名。

    最终排序：年级桶 → 普通方案优先 → 公共前缀长度 → 课程明细数 → 最新年级。
    课程明细数作最后裁决，既保证结果稳定（不再依赖数据库行序），也避免落到
    只有几门课的英才班/辅修壳（这些方案在同院内是「增设课程」而非完整方案）。

    Returns:
        {"id", "name", "college", "grade", ...} 或 None
    """
    if not major:
        return None
    # 附带课程明细数：并列时的数据驱动最终裁决，避免落到只有几门课的英オ班/辅修壳
    _SEL = ("SELECT p.*, (SELECT COUNT(*) FROM program_courses pc WHERE pc.program_id = p.id) "
            "AS course_count FROM programs p WHERE p.{col} LIKE ? ORDER BY p.grade DESC")
    rows = conn.execute(_SEL.format(col="name"), (f"%{major}%",)).fetchall()
    if not rows:
        # 学院名（含"生医部"这类简称）→ 该学院下的方案；仍无命中才认失败
        for college in resolve_colleges(conn, major):
            rows = conn.execute(_SEL.format(col="college"), (f"%{college}%",)).fetchall()
            if rows:
                break
    if not rows:
        return None

    target = parse_grade_key(grade)
    # 词干 = 查询词去掉「学院/学部/系」后缀与数字前缀。programs.college 形如
    # "203物理学院"，调用方可能原样传入（LLM、advisor_tools），解析器不能依赖调用方清洗：
    # 不去数字前缀则 stem="203物理"，公共前缀恒为 0，LCP 排序失效（会退回天文学）。
    stem = re.sub(r"^\d+", "", re.sub(r"(学院|学部|系)$", "", str(major))).strip()

    def _sort_key(r):
        g = parse_grade_key(r["grade"])
        if target:
            diff = g - target
            bucket = 0 if diff == 0 else (1 if diff < 0 else 2)
        else:
            bucket = 0  # 无年级信息: 不按年级分桶, 普通方案优先 + 最新在前
        # 顺序要点：prog_priority 必须在 prefix_rank 之前——否则「信息科学技术学院」
        # 会因「信息科技英才班」公共前缀更长而选中只有 5 门课的英オ班壳。
        # 末位字典序保证完全确定，不依赖数据库行序。
        return (bucket, prog_priority(r), -_lcp_len(str(r["name"] or ""), stem),
                -(r["course_count"] or 0), -g, str(r["name"] or ""))

    rows = sorted(rows, key=_sort_key)
    out = dict(rows[0])
    out.pop("course_count", None)  # 内部排序字段，不外泄
    return out
