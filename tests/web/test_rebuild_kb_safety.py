# -*- coding: utf-8 -*-
"""`_nuke_chroma_db` 必须保留「嵌套的独立 Chroma 实例」。

背景（2026-09-15 实测）：审核发布索引 `XIAOWO_PUBLISHED_CHROMA_DIR` 默认就嵌在
`XIAOWO_CHROMA_DIR` 里面（线上是 `knowledge/chroma_db/web_approved`，20M）。
原先的实现是"清空整个目录"，于是**照着文档/提示文案跑 `rebuild_kb.py --yes`**
会把发布索引一起删掉，`readiness.approved_index` 随即变红。

修法：`_nuke_chroma_db` 跳过「子目录自带 chroma.sqlite3」的项（那是另一个库，
不可能是本次要重建的 FAQ 索引），并返回被保留的名字供调用方核对。
"""
from __future__ import annotations

from pathlib import Path

from knowledge.vector_store import _nuke_chroma_db


def _mk_nested_chroma(root: Path, name: str) -> Path:
    """造一个"独立的 Chroma 实例"目录（自带 chroma.sqlite3 + 向量段目录）。"""
    d = root / name
    (d / "segment-0000").mkdir(parents=True, exist_ok=True)
    (d / "chroma.sqlite3").write_bytes(b"nested")
    return d


def _mk_faq_index(root: Path) -> Path:
    """造 FAQ 索引的本体（sqlite 在根目录 + 一个向量段目录）。"""
    (root / "chroma.sqlite3").write_bytes(b"faq")
    seg = root / "cad10654-0000-0000-0000-000000000000"
    seg.mkdir()
    (seg / "data.bin").write_bytes(b"v")
    return seg


def test_nested_chroma_instance_is_preserved(tmp_path):
    _mk_faq_index(tmp_path)
    published = _mk_nested_chroma(tmp_path, "web_approved")

    skipped = _nuke_chroma_db(str(tmp_path))

    assert "web_approved" in skipped
    assert (published / "chroma.sqlite3").exists()
    assert (published / "segment-0000").is_dir()


def test_faq_index_files_are_removed(tmp_path):
    seg = _mk_faq_index(tmp_path)
    _mk_nested_chroma(tmp_path, "web_approved")

    _nuke_chroma_db(str(tmp_path))

    assert not (tmp_path / "chroma.sqlite3").exists()
    assert not seg.exists()


def test_keep_argument_is_honoured(tmp_path):
    keep = tmp_path / "another_db"
    keep.mkdir()
    (keep / "data.bin").write_bytes(b"x")

    skipped = _nuke_chroma_db(str(tmp_path), keep=["another_db"])

    assert "another_db" in skipped
    assert (keep / "data.bin").exists()


def test_plain_subdir_is_still_cleared(tmp_path):
    junk = tmp_path / "tmp_cache"
    junk.mkdir()
    (junk / "a").write_bytes(b"x")

    skipped = _nuke_chroma_db(str(tmp_path))

    assert skipped == []
    assert not junk.exists()


def test_rebuild_kb_detects_nested_instances(tmp_path):
    """`rebuild_kb._nested_chroma_dirs` 只认「自带 chroma.sqlite3 的目录」。"""
    from rebuild_kb import _nested_chroma_dirs

    _mk_nested_chroma(tmp_path, "web_approved")
    (tmp_path / "plain_dir").mkdir()

    assert _nested_chroma_dirs(str(tmp_path)) == {"web_approved"}
