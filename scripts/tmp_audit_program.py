"""培养方案数据排查：数据库 → API → 前端契约，找英语课与类似不一致。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(r"f:\小蜗")

for db_name in ("data/course_data.db", "database/xiaowo.db"):
    db = ROOT / db_name
    if not db.exists():
        print(f"--- {db_name}: 不存在")
        continue
    print(f"--- {db_name} ---")
    con = sqlite3.connect(db)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        count = con.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        print(f"  {t}: {count} rows")
    con.close()

# 培养方案相关表里搜"英语"
for db_name in ("data/course_data.db", "database/xiaowo.db"):
    db = ROOT / db_name
    if not db.exists():
        continue
    con = sqlite3.connect(db)
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for t in tables:
        cols = [c[1] for c in con.execute(f"PRAGMA table_info([{t}])")]
        text_cols = [c for c in cols]
        if not text_cols:
            continue
        try:
            like = " OR ".join(f"[{c}] LIKE '%英语%'" for c in text_cols)
            rows = con.execute(f"SELECT * FROM [{t}] WHERE {like} LIMIT 5").fetchall()
            if rows:
                print(f"\n[{db_name}] {t} 含'英语' {len(rows)}+ 行, 列: {cols}")
                for row in rows[:3]:
                    print("   ", str(row)[:200])
        except sqlite3.OperationalError as exc:
            print(f"  ({t} 跳过: {exc})")
    con.close()
