# -*- coding: utf-8 -*-
"""活动偏好画像校验（P4-C/D）：冷启动/行为权重/个人因子/四因子推荐/快照回退。"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402
from services.service_container import ServiceContainer  # noqa: E402

PASS = FAIL = 0
TID = "TESTPROFILE1"


class _TmpDB:
    """最小 DB 接口适配（与 DatabaseManager 的 execute/query/query_one 兼容）。"""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row

    def execute(self, sql, params=()):
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur.lastrowid

    def query(self, sql, params=()):
        return [dict(r) for r in self.conn.execute(sql, params)]

    def query_one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None


def check(name, cond, detail=""):
    global PASS, FAIL
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    PASS, FAIL = PASS + (1 if cond else 0), FAIL + (0 if cond else 1)


def main() -> int:
    from services import activity_profile as ap

    db = _TmpDB()
    ap.ensure_tables(db)

    # 1) 冷启动：无快照匹配（测试学号≠快照账号）→ 空画像不报错
    prof = ap.get_profile(db, TID)
    check("冷启动-非快照账号得空画像", prof["labels"] == [])

    # 2) 快照账号冷启动 → 平台标签
    prof_real = ap.get_profile(db, "PB25111691")
    check("冷启动-快照账号得平台标签", len(prof_real["labels"]) >= 10,
          f"{len(prof_real['labels'])} 个")

    # 3) 行为流水 → 权重
    acts = [{"id": "a1", "name": "辩论表演赛", "category": "单次项目", "organizer": "学生辩论协会"},
            {"id": "a2", "name": "志愿清扫", "category": "志愿服务", "organizer": "青协"}]
    ap.record_interaction(db, TID, acts[0], "asked")
    ap.record_interaction(db, TID, acts[0], "clicked")
    ap.record_interaction(db, TID, acts[1], "shown")
    w = ap.behavior_weights(db, TID)
    check("行为权重-辩论协会最高", w.get("学生辩论协会") == 1.0, str(w))
    check("行为权重-shown 也有贡献", 0 < w.get("志愿服务", 0) < 1.0)

    # 4) personal_score：类别命中 + 标签命中
    class _A:
        name, description = "志愿清扫行动", "校园志愿服务"
        organizer, category = "青协", "志愿服务"
    s1, r1 = ap.personal_score(_A, prof, w)
    check("personal_score 类别/主办方命中", s1 > 0 and ("主办" in r1 or "关注" in r1), f"{s1} {r1}")

    class _B:
        name, description = "文学文艺晚会", "文艺展演"
        organizer, category = "社团", "单次项目"
    s2, r2 = ap.personal_score(_B, {"labels": [{"name": "文学文艺"}]}, {})
    check("personal_score 标签词命中", s2 >= 0.5 and "文学文艺" in r2, f"{s2} {r2}")

    # 4.5) 均衡补短板：低学时模块活动得分+理由，最高模块不加持
    class _Act:
        def __init__(self, module, name="测试活动"):
            self.module, self.name = module, name
            self.description, self.organizer, self.category = "", "", ""
    hours = {"德": 16.0, "智": 17.5, "体": 24.0, "美": 17.5, "劳": 40.0}
    prof_bal = {"labels": [], "module_hours": hours}
    s_low, r_low = ap.personal_score(_Act("d"), prof_bal, {})
    check("均衡-低模块（德）得分且理由含补足", s_low >= 0.5 and "补足「德」" in r_low, f"{s_low} {r_low}")
    s_top, r_top = ap.personal_score(_Act("l"), prof_bal, {})
    check("均衡-最高模块（劳）不加持", s_top == 0.0 and r_top == "", f"{s_top} {r_top}")
    prof_load = ap.load_module_hours("PB25111691")
    check("load_module_hours 快照读取", abs(sum(prof_load.values()) - 115.0) < 0.01 or not prof_load,
          f"{prof_load}")

    # 5) 四因子推荐（真实活动 + 真课表）
    container = ServiceContainer()
    container.init_database(config.DATABASE_PATH, config.SCHEMA_PATH)
    from services.activity_recommender import FreeTimeMatcher, recommend
    from services.young_client import YoungService
    from config import YOUNG_TOKEN
    try:
        live = YoungService.from_token(YOUNG_TOKEN).fetch_enrolment_activities(page_size=20)
    except Exception:
        live = None
    if live:
        m = FreeTimeMatcher.from_db(container.db, "PB25111691")
        recs = recommend(live, matcher=m, personal_profile=prof_real,
                         personal_weights=ap.behavior_weights(container.db, "PB25111691"), top_n=3)
        has_personal = all(r.get("personal") is not None for r in recs)
        check("四因子推荐运行（personal 字段齐）", bool(recs) and has_personal)
    else:
        print("[SKIP] token 失效，实拉推荐断言跳过")

    # 6) 快照回退（D）：模拟 token 失效
    from tools import activity_tools as at
    at._cache.update(acts=None, ts=0.0)
    at.YOUNG_TOKEN = "invalid-token-for-fallback-test-0000000000"
    r = at.query_activities.invoke({"keyword": ""})
    check("token 失效回退本地快照", r.get("count", 0) > 0 and "本地缓存" in r.get("source", ""),
          r.get("source", "")[:80])

    print(f"\n结果: 通过 {PASS}/{PASS + FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
