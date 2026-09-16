# -*- coding: utf-8 -*-
"""用 catalog 公开 API 展开培养方案里的「共享模块」引用（体育 / 通识等）。

背景（2026-09-16 发现）：
`scripts/data/programs_jw/` 来自 jw **学校级**方案树，它对「要求通过子模块」型分组
（`requiredSubModuleNum > 0`）只给空壳节点——节点在、课程 0。于是入库后
`program_courses` 缺了一大块：体育通修/选修（119 门）、核心通识选修 等。

而 `catalog.ustc.edu.cn` 的**公开 API**（免登录）把这块补上了：
- 方案树节点自带 `public` 字段 = 共享模块 id；
- `GET /api/teach/course-module/info/{public_id}` 返回该共享模块的完整内容。

本脚本：读现库的 programs → 经 catalog 方案树映射到 catalog 方案 id → 取方案详情
→ 找到所有 `public` 引用 → 展开共享模块 → 把课程补进 `program_courses`。

**分级模块（英语）默认不展开**：它必须按学生个人级别选一层，直接全塞进库会让
`get_program_progress` 的「必修总数」虚增、缺口全错。英语走单独的推断逻辑（P-B）。

用法：
    py scripts/enrich_shared_modules.py                  # 预览：只报告，不写库
    py scripts/enrich_shared_modules.py --yes             # 写入 --db 指定的库
    py scripts/enrich_shared_modules.py --yes --db /tmp/x.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CATALOG = "https://catalog.ustc.edu.cn"
SLEEP = 0.15  # 抓取礼仪：限速
# **只展开个人方案树证明会 materialize 的模块**（2026-09-16 实测）。
# 证据：demo 个人树 222 门 = 基础 85 + 体育通修/选修 119 + 英语通修L3 18，
# 其中**没有**任何核心通识选修池课程。所以"见到 public 就展开"是错的：
# 核心通识选修池(49090/49773)、四史一课(45354) 在每个方案上会多算 200~270 行，
# 633 个方案合计虚增约 12 万行，且与个人树口径不一致。
EXPAND_KW = ("体育通修",)   # P-A：与分级无关，可安全批量展开
LEVELED_KW = ("英语",)      # P-B：必须按学生级别选一层，单独处理（见下）


def api(path: str, timeout: int = 60):
    req = urllib.request.Request(CATALOG + path, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def catalog_index() -> dict:
    """(nameZh, grade) -> catalog program id。"""
    tree = api("/api/teach/program/tree")
    out = {}
    for col in tree.values():
        for m in (col.get("majors") or {}).values():
            for p in (m.get("programs") or []):
                out[(str(p.get("nameZh") or ""), str(p.get("grade") or ""))] = p.get("id")
    return out


def collect_public_refs(node, path, out) -> None:
    """递归收集方案树里带 public 的节点 → (完整路径, public_id)。"""
    s = node.get("self") or {}
    name = str(s.get("type") or "")
    cur = path + [name] if name else path
    if s.get("public"):
        out.append(("/".join(cur), s["public"]))
    for ch in (node.get("children") or []):
        collect_public_refs(ch, cur, out)


def module_courses(mod: dict, prefix: str) -> list:
    """把共享模块展开为课程行；prefix 是该引用在方案里的完整路径。

    公共模块根节点的名字与方案侧的叶子同名（如都叫「选修」），所以根节点的课程
    直接用 prefix，子节点再往后拼——与个人方案树的 category 约定一致。
    """
    rows = []

    def walk(node, path):
        s = node.get("self") or {}
        for c in (s.get("courses") or []):
            co = c.get("course") or {}
            terms = c.get("terms") or []
            rows.append({
                "code": str(co.get("code") or ""),
                "name": str(co.get("nameZh") or ""),
                "required": "必修" if c.get("compulsory") else "选修",
                "exam": str(c.get("examMode") or ""),
                "credit": co.get("credits") if co.get("credits") is not None else "",
                "category": "/".join(path),
                "term": ",".join(str(t) for t in terms),
            })
        for ch in (node.get("children") or []):
            nm = str((ch.get("self") or {}).get("type") or "")
            walk(ch, path + [nm] if nm else path)

    walk(mod, [prefix])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="展开培养方案的共享模块引用（catalog 公开 API）")
    ap.add_argument("--db", default=str(PROJECT_ROOT / "data" / "course_data.db"))
    ap.add_argument("--yes", action="store_true", help="真正写库；缺省只预览")
    args = ap.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"库不存在: {db_path}")
        return 1

    print(f"库: {db_path}   模式: {'写入' if args.yes else '预览（不写）'}")
    idx = catalog_index()
    print(f"catalog 方案索引: {len(idx)} 条")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    courses_by_code = {}
    for cid, code in conn.execute("SELECT id, code FROM courses").fetchall():
        if code:
            courses_by_code.setdefault(str(code), cid)
            courses_by_code.setdefault(str(code).lstrip("0"), cid)
    programs = conn.execute("SELECT id, name, grade FROM programs ORDER BY id").fetchall()

    ref_cache: dict = {}
    matched = skipped_leveled = skipped_present = missing = skipped_policy = 0
    skipped_paths: Counter = Counter()
    planned = []  # (program_id, ref_path, module_id, rows)
    for p in programs:
        cid = idx.get((str(p["name"]), str(p["grade"]).rstrip("级")))
        if cid is None:
            missing += 1
            continue
        matched += 1
        try:
            detail = api(f"/api/teach/program/info/{cid}")
        except Exception as e:  # noqa: BLE001
            print(f"   ! 方案 {cid} 读取失败: {e}")
            continue
        refs: list = []
        for n in (detail.get("moduleTree") or []):
            collect_public_refs(n, [], refs)
        for path, pub in refs:
            if any(k in path for k in LEVELED_KW):
                skipped_leveled += 1
                continue
            # 白名单之外一律不展开：个人树没展开的（通识池等）我们也不展开
            if not any(k in path for k in EXPAND_KW):
                skipped_policy += 1
                skipped_paths[path] += 1
                continue
            # 已有该路径下的课程 → 说明这个方案本来就带着（如 demo 的 fixture 数据）
            have = conn.execute(
                "SELECT COUNT(*) FROM program_courses WHERE program_id=? AND category LIKE ?",
                (p["id"], path + "%"),
            ).fetchone()[0]
            if have:
                skipped_present += 1
                continue
            if pub not in ref_cache:
                try:
                    ref_cache[pub] = api(f"/api/teach/course-module/info/{pub}")
                except Exception as e:  # noqa: BLE001
                    print(f"   ! 共享模块 {pub} 读取失败: {e}")
                    ref_cache[pub] = None
                time.sleep(SLEEP)
            mod = ref_cache[pub]
            if not mod:
                continue
            rows = module_courses(mod, path)
            if rows:
                planned.append((p["id"], p["name"], path, pub, rows))

    total_rows = sum(len(r[4]) for r in planned)
    print()
    print(f"方案匹配 catalog: {matched}   未匹配: {missing}")
    print(f"跳过（分级模块，留给 P-B）: {skipped_leveled}")
    print(f"跳过（不在白名单：个人树本就未展开的）: {skipped_policy}")
    print(f"跳过（该路径下已有课程）: {skipped_present}")
    print(f"涉及不同的共享模块: {len([k for k, v in ref_cache.items() if v])}")
    print(f"将补入: {len(planned)} 个(方案,模块) 组合，共 {total_rows} 行")
    if skipped_paths:
        print("   白名单外被跳过的路径（前 8）:")
        for path, n in skipped_paths.most_common(8):
            print(f"      {path[:56]:<58}{n} 次")
    print()
    by_path = Counter()
    for _pid, _name, path, pub, rows in planned:
        by_path[(path, pub)] += len(rows)
    for (path, pub), n in by_path.most_common(20):
        print(f"   public={pub:<8} {path[:52]:<54} {n} 行")

    if not args.yes:
        print("\n（预览结束，未写库。加 --yes 写入）")
        conn.close()
        return 0

    inserted = 0
    for pid, name, path, pub, rows in planned:
        for r in rows:
            cid2 = courses_by_code.get(r["code"]) or courses_by_code.get(r["code"].lstrip("0"))
            conn.execute(
                "INSERT INTO program_courses(program_id, course_id, code, name, required, exam, credit, category, term) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (pid, cid2, r["code"], r["name"], r["required"], r["exam"], r["credit"], r["category"], r["term"]),
            )
            inserted += 1
    conn.commit()
    tot = conn.execute("SELECT COUNT(*) FROM program_courses").fetchone()[0]
    conn.close()
    print(f"\n已写入 {inserted} 行；program_courses 现有 {tot} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
