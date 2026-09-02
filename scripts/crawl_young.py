# -*- coding: utf-8 -*-
"""青春科大个人数据快照（测试版数据源，模式同 xiaowo_personal 备份）。

拉取当前 token 账号的：报名中/已结束活动、个人档案（五维成绩/工时/社团）、
兴趣标签、收藏/关注、待参与列表，落盘 scripts/data/young_personal/young_snapshot.json。

用法：py -3 scripts/crawl_young.py            # 全量
      py -3 scripts/crawl_young.py --public   # 仅公开列表（不需要个人接口成功）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import YOUNG_SNAPSHOT_PATH, YOUNG_TOKEN, YOUNG_PAGE_SIZE  # noqa: E402

OUT_DIR = YOUNG_SNAPSHOT_PATH.parent  # 与读者单一来源（activity_profile/activity_tools 同读 config.YOUNG_SNAPSHOT_PATH）
OUT_FILE = YOUNG_SNAPSHOT_PATH


def _act_min(a) -> dict:
    return {
        "id": a.id, "name": a.name, "start": a.start_time, "end": a.end_time,
        "apply_start": a.apply_start, "apply_end": a.apply_end,
        "organizer": a.organizer, "category": a.category, "module": a.module,
        "fav_count": a.fav_count, "people_num": a.people_num,
        "service_hour": a.service_hour, "description": a.description[:200],
        "place_info": a.place_info, "xq": a.xq,
        "contact": a.contact, "form": a.form,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public", action="store_true", help="仅公开列表")
    args = ap.parse_args()

    if not YOUNG_TOKEN or len(YOUNG_TOKEN) < 32:
        print("YOUNG_TOKEN 未配置或无效（.env），退出")
        return 1

    from services.young_client import YoungService
    svc = YoungService.from_token(YOUNG_TOKEN)

    snapshot: dict = {"fetched_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    enrol = svc.fetch_enrolment_activities(page_size=YOUNG_PAGE_SIZE)
    snapshot["enrolment"] = [_act_min(a) for a in enrol]
    print(f"报名中: {len(enrol)} 条")

    try:
        end = svc.fetch_end_activities(page_size=YOUNG_PAGE_SIZE)
        snapshot["end"] = [_act_min(a) for a in end]
        print(f"已结束: {len(end)} 条（首页口径）")
    except Exception as e:
        snapshot["end"] = []
        print(f"已结束拉取失败: {e}")

    if not args.public:
        for key, fn, label in (
            ("profile", svc.fetch_my_profile, "个人档案"),
            ("labels", svc.fetch_my_labels, "兴趣标签"),
            ("favorites", svc.fetch_my_favorites, "收藏"),
            ("followed_depts", svc.fetch_my_followed_depts, "关注组织"),
            ("tobe_involved", svc.fetch_tobe_involved, "待参与"),
        ):
            try:
                snapshot[key] = fn()
                n = len(snapshot[key]) if isinstance(snapshot[key], list) else 1
                print(f"{label}: {n} 条")
            except Exception as e:
                snapshot[key] = [] if key != "profile" else {}
                print(f"{label}拉取失败: {str(e)[:80]}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n快照已写入 {OUT_FILE}（{OUT_FILE.stat().st_size // 1024} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
