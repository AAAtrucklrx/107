# -*- coding: utf-8 -*-
"""校园官方站点直采 → 审核库（方案 A，2026-09-03）。

无需搜索引擎：直接抓官方站 RSS/列表 → 正文（Crawl4AI）→ enqueue_candidate 进
review.db（demo 命名空间）→ 走既有"审核 → 发布"管线，问答自动获得官方一手来源
（发布时按 candidate.level 提权，官方站点 = official_primary）。

用法：
  python scripts/collect_official_pages.py --once                    # 单轮（默认最近 90 天，每源 20 篇）
  python scripts/collect_official_pages.py --dry-run                 # 只列候选不入库
  python scripts/collect_official_pages.py --since-days 30 --limit 5

SOURCES 为可扩展配置：新增源只需加一项（feed 或列表页解析后续扩展）。
"""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from xiaowo_web.settings import WebSettings  # noqa: E402

# ── 官方源配置（可扩展）─────────────────────────────
SOURCES = [
    {
        "name": "教务通知",
        "feed": "https://www.teach.ustc.edu.cn/category/notice/feed",
        "institution": "中国科学技术大学教务处",
        "level": "official_primary",
    },
]

CRAWL4AI = "http://127.0.0.1:11235"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
      "Accept-Language": "zh-CN,zh;q=0.9"}


def _fetch_feed(url: str) -> list[dict]:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for it in root.findall(".//item"):
        link = (it.findtext("link") or "").strip()
        title = " ".join((it.findtext("title") or "").split())
        pub = (it.findtext("pubDate") or "").strip()
        if not link or not title:
            continue
        items.append({"title": title, "url": link, "published_at": pub})
    return items


def _fetch_body(url: str) -> str | None:
    """正文经 Crawl4AI（robots 尊重、hash 校验）；失败返回 None。"""
    try:
        r = requests.post(
            CRAWL4AI + "/crawl", timeout=60,
            json={"url": url, "respect_robots": True, "max_redirects": 5,
                  "max_html_bytes": 2 * 1024 * 1024, "max_pdf_bytes": 20 * 1024 * 1024,
                  "max_pdf_pages": 200, "credentials": None},
        )
        payload = r.json()
        if payload.get("status_code") not in (200, 201):
            return None
        md = str(payload.get("markdown") or "")
        return md if len(md) >= 100 else None
    except Exception:
        return None


def _existing_urls(settings: WebSettings) -> set[str]:
    """已入库（任何状态）的 URL 集合，用于增量跳过。"""
    db = sqlite3.connect(settings.review_db_path)
    try:
        rows = db.execute("SELECT normalized_url FROM web_snapshots").fetchall()
    finally:
        db.close()
    return {str(r[0]) for r in rows if r[0]}


def _in_window(pub: str, since_days: int) -> bool:
    """pubDate（RFC 822 如 'Wed, 02 Sep 2026 12:00:00 +0800'）在窗口内。"""
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(pub)
    except Exception:
        return True  # 解析失败不放行？保守：视为旧文，跳过
    cutoff = time.time() - since_days * 86400
    return dt.timestamp() >= cutoff


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="单轮执行（默认即单轮）")
    ap.add_argument("--dry-run", action="store_true", help="只列候选不入库")
    ap.add_argument("--since-days", type=int, default=90)
    ap.add_argument("--limit", type=int, default=20, help="每源最多取 N 篇")
    ap.add_argument("--namespace", default="demo")
    args = ap.parse_args()

    settings = WebSettings.from_env()
    known = _existing_urls(settings)
    store = None
    if not args.dry_run:
        from xiaowo_web.review import ReviewStore
        store = ReviewStore(settings)

    total_enqueued = 0
    for src in SOURCES:
        try:
            items = _fetch_feed(src["feed"])
        except Exception as e:
            print(f"[{src['name']}] feed 拉取失败: {str(e)[:100]}")
            continue
        candidates = []
        for item in items:
            if len(candidates) >= args.limit:
                break
            url = item["url"]
            if url in known:
                continue
            if not _in_window(item["published_at"], args.since_days):
                continue
            candidates.append(item)
        print(f"[{src['name']}] feed={len(items)} 条，候选（未入库+窗口内）={len(candidates)} 条")
        for item in candidates:
            if args.dry_run:
                print(f"    - {item['title'][:48]} | {item['url'][:64]} | {item['published_at'][:16]}")
                continue
            item_url = item["url"]
            body = _fetch_body(item_url)
            if body is None:
                print(f"    [跳过] 正文过短/失败: {item['title'][:40]}")
                continue
            snapshot_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
            candidate = {
                "snapshot_text": body,
                "evidence_span_hash": hashlib.sha256(item_url.encode("utf-8")).hexdigest(),
                "source_id": "official-" + snapshot_hash[:12],
                "normalized_url": item_url,
                "final_url": item_url,
                "title": item["title"],
                "institution": src["institution"],
                "level": src["level"],
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "content_type": "text/html",
            }
            result = store.enqueue_candidate(args.namespace, candidate)
            status = result.get("status")
            if status == "queued" or result.get("created"):
                total_enqueued += 1
                print(f"    [入库] {candidate['title'][:44]} ({len(body)}字) {candidate['normalized_url'][:58]}")
            else:
                print(f"    [已存在] {candidate['title'][:44]} status={status}")

    print(f"\n完成：本次入库 {total_enqueued} 条（worker 将建 draft → 审核 → 发布）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
