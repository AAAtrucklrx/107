"""采集科大相关微信公众号文章 → 写入审核库（待你审核）。

用法（服务器沙箱）：
    .venv/bin/python scripts/collect_wechat_articles.py --limit 3 --queries 8 [--ocr 12]

流程：搜索（搜狗微信，自持会话）→ 解析 → 抓正文 → （图主导时）OCR →
按科大相关性过滤去重 → enqueue_candidate 写入 review.db 的 demo 命名空间 →
打印文章清单（题目/公众号/链接/字数）——由用户人工审核（审核工作区或此清单结算）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
import time

sys.path.insert(0, str(__file__).rsplit("/", 2)[0] + "/../".replace("/../", "/"))  # noqa: E402

for line in open(f"{os.path.dirname(__file__)}/../.env", encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from xiaowo_web.evidence.wechat import (  # noqa: E402
    WechatClient,
    article_content_hash,
    build_markdown,
    is_official_account,
)

DEFAULT_QUERIES = [
    "中国科学技术大学 2026",
    "科大 2026 通知",
    "中国科学技术大学 通知 2026",
    "科大 开学 2026",
    "中科大 招生 2026",
    "中科大 开学季",
    "科大 校园 新闻 2026",
    "蜗壳小道消息 2026",
    "中国科大 就业",
    "中国科大 竞赛",
    "中国科大 学术报告",
    "中国科大 校历",
    "中国科大 校友",
    "中科大 社团",
    "科大 图书馆 2026",
    "科大 奖学金 2026",
    "中国科大 实验室",
    "科大 宿舍 餐饮",
    "中国科大 研究生 2026",
    "科大 创新 创业 2026",
]

# 时效判定：含 2026年 / published 2026 / 全文无任何旧年标记（20XX年）→ 视为新文
_YEAR_RE = __import__("re").compile(r"(20\d{2})年")


def _is_new_enough(article) -> bool:
    full = f"{article.title} {article.markdown}"
    if "2026年" in full:
        return True
    years = set(_YEAR_RE.findall(full))
    if not years:
        return True  # 无旧年标记（如"9月1日新发"）→ 按新文处理
    return False


def _is_campus_related(article) -> bool:
    haystack = f"{article.title} {article.author} {article.markdown[:600]}"
    return any(k in haystack for k in ("中国科学技术大学", "中国科大", "中科大", "USTC", "蜗壳"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3, help="每个关键词最多采集几篇")
    parser.add_argument("--queries", type=int, default=8)
    parser.add_argument("--ocr", type=int, default=12, help="总 OCR 图片预算")
    parser.add_argument("--namespace", default="demo")
    args = parser.parse_args()

    from xiaowo_web.review import ReviewStore
    from xiaowo_web.settings import WebSettings

    settings = WebSettings.from_env()
    store = ReviewStore(settings)
    store.initialize()

    client = WechatClient()

    async def run() -> list[dict]:
        articles: dict[str, object] = {}
        total_ocr = args.ocr
        for query in DEFAULT_QUERIES[: args.queries]:
            got = await client.collect_many(query, limit=args.limit, ocr_budget=min(6, total_ocr))
            total_ocr = max(0, total_ocr - 6)
            for a in got:
                key = hashlib.sha256(f"{a.title}|{a.author}".encode()).hexdigest()[:16]
                if key not in articles:
                    articles[key] = a
            await asyncio.sleep(1.0)

        enqueued: list[dict] = []
        for a in articles.values():
            if not (_is_campus_related(a) and _is_new_enough(a)):
                continue
            text = build_markdown(a.markdown, a.ocr_spans)
            if len(text) < 100:
                continue
            digest = article_content_hash(a)
            enqueued.append({
                "snapshot_text": text,
                "evidence_span_hash": hashlib.sha256(text.encode()).hexdigest(),
                "source_id": "wechat-" + digest[:12],
                "normalized_url": a.url,
                "final_url": a.url,
                "title": a.title,
                "institution": a.author or "微信公众号",
                "level": "official_primary" if is_official_account(a.author) else "unverified",
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "content_type": "text/html",
            })
        return enqueued

    candidates = asyncio.run(run())

    # 跳过已入库（标题归一化比对，防与已批/已入队文章重复）
    import re as _re
    import sqlite3 as _sql

    def _norm_title(t: str) -> str:
        return _re.sub(r"[\s，。！？、；：（）()·—-]+", "", t)
    db = _sql.connect(str(settings.review_db_path))
    existing_titles = {_norm_title(r[0] or "") for r in db.execute(
        "SELECT title FROM review_items WHERE namespace=?", (args.namespace,)).fetchall()}
    before = len(candidates)
    seen = set()
    kept = []
    for c in candidates:
        nt = _norm_title(c["title"])
        if nt in existing_titles or nt in seen:
            continue
        seen.add(nt)
        kept.append(c)
    candidates = kept
    print(f"[去重] {before} → {len(candidates)}（排除已入库/批内重复 {before - len(candidates)}）")
    ok = 0
    for candidate in candidates:
        try:
            store.enqueue_candidate(args.namespace, candidate)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[skip] {candidate['title'][:40]} -> {type(exc).__name__}: {str(exc)[:80]}")

    print("=" * 72)
    print(f"采集完成：检索到 {len(candidates)} 篇（滤空/去重后），写入审核库 {ok} 篇（namespace={args.namespace}）")
    print("=" * 72)
    for i, candidate in enumerate(candidates, 1):
        level = {"official_primary": "【官方】", "unverified": "      "}
        print(f"{i:>2}. {level[candidate['level']]} {candidate['title'][:52]}")
        print(f"     公众号: {candidate['institution'][:24]} | 正文{len(candidate['snapshot_text'])}字 | {candidate['normalized_url'][:58]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
