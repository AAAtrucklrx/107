"""教师名清洗（2026-09-29）：合教拆分、括号碎片、噪声词、外教名。"""
from __future__ import annotations

from utils.teacher_name import clean_names, clean_one


def test_split_combined_names():
    assert clean_names("刘世勇, 何卫东") == ["刘世勇", "何卫东"]
    assert clean_names("王官武、俞书宏、邓兆祥") == ["王官武", "俞书宏", "邓兆祥"]
    assert clean_names("计永胜/石攀") == ["计永胜", "石攀"]


def test_strip_bracket_fragments():
    # 评课社区抓取残留（本次实测到的两条真实脏名字）
    assert clean_one("I）（刘国柱") == "刘国柱"
    assert clean_one("英）（严以京") == "严以京"
    assert clean_one("（张三）") == "张三"
    assert clean_one("李四(合教)") == "李四"


def test_noise_and_english_names():
    assert clean_names("王小明 等") == ["王小明"]
    assert clean_names("待定") == []            # 纯噪声丢弃，不回退
    assert clean_names("Li Ming") == ["Li Ming"]  # 外教名整段保留
    assert clean_names("") == []


def test_dedupe_and_order_preserved():
    assert clean_names("张三、张三、李四") == ["张三", "李四"]
    assert clean_names(["王五", "王五"]) == ["王五"]
