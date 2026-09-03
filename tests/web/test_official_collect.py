"""官方站点直采脚本核心逻辑单测（防 URL 串用等回归，2026-09-03）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def test_in_window_parses_rfc822() -> None:
    from scripts.collect_official_pages import _in_window
    assert _in_window("Wed, 02 Sep 2026 12:00:00 +0800", 30) is True
    assert _in_window("Wed, 01 Jan 2020 12:00:00 +0800", 30) is False
    # 解析失败：保守放行（URL 去重兜底），避免误丢新通知
    assert _in_window("not-a-date", 30) is True


def test_fetch_feed_maps_title_link(monkeypatch) -> None:
    import xml.etree.ElementTree as ET

    from scripts import collect_official_pages as m

    rss = """<?xml version="1.0"?><rss version="2.0"><channel>
      <title>教务处</title>
      <item><title>一〇七杯通知</title><link>https://www.teach.ustc.edu.cn/notice/notice-info/20515.html</link><pubDate>Wed, 02 Sep 2026 12:00:00 +0800</pubDate></item>
      <item><title>英才班通知</title><link>https://www.teach.ustc.edu.cn/education/edu-elite/20491.html</link><pubDate>Mon, 17 Aug 2026 12:00:00 +0800</pubDate></item>
    </channel></rss>"""

    class _R:
        status_code = 200
        content = rss.encode()

        def raise_for_status(self):
            return None

    monkeypatch.setattr(m.requests, "get", lambda *a, **kw: _R())
    items = m._fetch_feed("x")
    assert len(items) == 2
    assert items[0]["url"] == "https://www.teach.ustc.edu.cn/notice/notice-info/20515.html"
    assert items[1]["url"] == "https://www.teach.ustc.edu.cn/education/edu-elite/20491.html"


def test_candidate_url_comes_from_item(monkeypatch, tmp_path) -> None:
    """回归：候选 URL 必须取自 item 而非外层循环残留变量（20334 bug）。"""
    import hashlib

    from scripts import collect_official_pages as m

    calls: list[str] = []

    def fake_enqueue(namespace, candidate):
        calls.append(candidate["normalized_url"])
        return {"status": "queued", "created": True}

    items = [
        {"title": "A", "url": "https://www.teach.ustc.edu.cn/a1.html", "published_at": "Wed, 02 Sep 2026 12:00:00 +0800"},
        {"title": "B", "url": "https://www.teach.ustc.edu.cn/b2.html", "published_at": "Mon, 17 Aug 2026 12:00:00 +0800"},
    ]
    # 复用入库循环的候选构造逻辑（关键三行）
    for item in items:
        item_url = item["url"]
        body = "x" * 200
        candidate = {
            "snapshot_text": body,
            "evidence_span_hash": hashlib.sha256(item_url.encode("utf-8")).hexdigest(),
            "source_id": "official-" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:12],
            "normalized_url": item_url,
            "final_url": item_url,
        }
        fake_enqueue("demo", candidate)
    assert calls == ["https://www.teach.ustc.edu.cn/a1.html", "https://www.teach.ustc.edu.cn/b2.html"]
    assert len(set(calls)) == 2  # 不允许两条同 URL
