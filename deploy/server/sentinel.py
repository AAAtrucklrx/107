# -*- coding: utf-8 -*-
"""业务哨兵（2026-09-03 增补）：每 5 分钟探测"搜索可用性 + 发布索引"，状态写
 deploy/server/run/sentinel_state.json（readiness 的 approved_index/search_quality 读取）。

设计：不阻断主服务；连续 2 次失败才把 ok 置 false（防抖）；侧车可达但结果空（引擎风控）
时 search_ok=false——正是我们 09-03 遇到的"搜索全挂"场景。

用法：python deploy/server/sentinel.py            # 常驻循环（start_all 管理）
      python deploy/server/sentinel.py --once     # 单次探测（测试）
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import requests

ROOT = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get("XIAOWO_SENTINEL_STATE_PATH") or ROOT / "deploy" / "server" / "run" / "sentinel_state.json")
INTERVAL = 300  # 5 分钟
FAIL_STREAK_LIMIT = 2
SEARXNG = os.environ.get("XIAOWO_SEARXNG_URL", "http://127.0.0.1:8080")


def _search_once() -> tuple[bool, int]:
    """一次搜索：hits>0 即算通（引擎风控/限流会得到 0 结果 → 失败）。

    跟随 XIAOWO_SEARCH_PROVIDER：baidu（千帆搜索 API，国内直连）/ bocha（Web Search
    API）或 searxng（sidecar）。
    """
    from xiaowo_web.settings import WebSettings

    try:
        settings = WebSettings.from_env()
    except Exception:
        return False, -1
    if not settings.web_search_enabled:
        return False, -1
    if settings.search_provider in ("bocha", "baidu"):
        import asyncio

        if settings.search_provider == "bocha":
            from xiaowo_web.evidence.clients import BochaWebSearchClient

            client = BochaWebSearchClient(
                settings.bocha_api_key,
                base_url=settings.bocha_base_url,
                timeout=8.0,
            )
        else:
            from xiaowo_web.evidence.clients import BaiduSearchClient

            client = BaiduSearchClient(
                settings.baidu_api_key,
                base_url=settings.baidu_base_url,
                timeout=8.0,
            )

        async def _probe() -> tuple[bool, int]:
            try:
                batch = await client.search("中国科学技术大学", limit=3)
                return bool(batch.hits), len(batch.hits)
            except Exception:
                return False, -1
            finally:
                await client.close()

        try:
            return asyncio.run(_probe())
        except Exception:
            return False, -1
    try:
        r = requests.get(
            SEARXNG + "/search",
            params={"q": "中国科学技术大学", "format": "json", "language": "zh-CN"},
            timeout=8,
        )
        if r.status_code != 200:
            return False, -1
        payload = r.json()
        hits = len(payload.get("results") or [])
        return hits > 0, hits
    except Exception:
        return False, -1


def _index_once() -> bool:
    """active 指针 + manifest/bm25 文件在盘。"""
    import sqlite3

    from xiaowo_web.settings import WebSettings

    try:
        settings = WebSettings.from_env()
        db = sqlite3.connect(settings.review_db_path)
        rows = db.execute("SELECT namespace, generation_id FROM active_index_state").fetchall()
        db.close()
        for ns, gen in rows:
            manifest = (
                Path(settings.web_evidence_dir) / "approved" / "manifests" / ns / f"{gen}.json"
            )
            bm25 = Path(settings.published_bm25_dir) / ns / f"{gen}.json"
            if manifest.is_file() and bm25.is_file():
                return True
        return False
    except Exception:
        return False


def main() -> int:
    once = "--once" in sys.argv
    fail_streak = 0
    while True:
        search_ok, hits = _search_once()
        index_ok = _index_once()
        fail_streak = fail_streak + 1 if not search_ok else 0
        state = {
            "checked_at": time.time(),
            "checked_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "search_ok": bool(search_ok),
            "search_hits": hits,
            "index_ok": bool(index_ok),
            "fail_streak": fail_streak,
        }
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        mark = "[ok]" if (search_ok and index_ok) else ("[WARN]" if fail_streak < FAIL_STREAK_LIMIT else "[FAIL]")
        print(f"{mark} 哨兵: search_ok={search_ok}(hits={hits}) index_ok={index_ok} 连续失败={fail_streak}", flush=True)
        if once:
            return 0
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
