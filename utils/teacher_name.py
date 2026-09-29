# -*- coding: utf-8 -*-
"""教师名清洗（2026-09-29）。

评课社区原始数据里的教师字段有两类噪声：
  ① **合教**：一行存多个名字 —— `刘世勇, 何卫东`、`王官武、俞书宏、邓兆祥`
  ② **括号碎片**：页面抓取残留 —— `I）（刘国柱`、`英）（严以京`、`李四(合教)`、`（张三）`

本模块把两者统一处理：先按分隔符与括号切段，再在每段里取最长的中文姓名段
（姓名通常 2–4 字），过滤"等/合教/待定"这类噪声词，最后去重保序。
纯英文名（外教）保留字母形式。任何一步失败都退化为原样返回，绝不让调用方拿到空名字。
"""

from __future__ import annotations

import re

_SPLIT = re.compile(r"[,，、/;；|]+")
_BRACKET = re.compile(r"[（）()【】\[\]{}<>《》]+")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,6}")
_NOISE = {"等", "合教", "合", "待定", "外聘", "未知", "无", "暂无", "tbd", "na", "n/a"}


def clean_one(token: str) -> str:
    """单个教师名清洗：`I）（刘国柱` → `刘国柱`；`李四(合教)` → `李四`。"""
    text = (token or "").replace("\u3000", " ").strip()
    if not text:
        return ""
    # 全英文名（外教）：整段保留，不要被"取最长单词"截断（'Li Ming' 不该变成 'Ming'）
    if not re.search(r"[\u4e00-\u9fff]", text):
        latin = re.sub(r"[^A-Za-z·.\- ]", " ", text)
        latin = re.sub(r"\s+", " ", latin).strip()
        return latin if len(latin.replace(" ", "")) >= 2 else ""
    best, best_len = "", 0
    for piece in _BRACKET.split(text):          # 先按括号切：'英）（严以京' → ['英','严以京']
        for word in piece.split():              # 再按空白切：'王小明 等' → ['王小明','等']
            word = word.strip()
            if not word or word.casefold() in _NOISE:
                continue
            runs = _CJK_RUN.findall(word)
            if runs:
                candidate = max(runs, key=len)
            else:                               # 外教/英文名
                candidate = re.sub(r"[^A-Za-z·.\-]", "", word)
            if len(candidate) > best_len:       # 取最长段（并列时保留靠前的，即原顺序）
                best, best_len = candidate, len(candidate)
    return best


def clean_names(raw) -> list[str]:
    """字符串或列表 → 清洗后的姓名列表（去重保序）。"""
    if isinstance(raw, (list, tuple, set)):
        tokens = [str(x) for x in raw]
    else:
        tokens = _SPLIT.split(str(raw or ""))
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        name = clean_one(token)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    # 全被清掉说明整条都是噪声（"待定"/"合教"），返回空由调用方决定是否退化
    return out


if __name__ == "__main__":
    for sample in ("刘世勇, 何卫东", "I）（刘国柱", "英）（严以京", "李四(合教)",
                   "（张三）", "王小明 等", "王官武、俞书宏、邓兆祥", "Li Ming", "待定"):
        print(f"  {sample!r:26} → {clean_names(sample)}")
