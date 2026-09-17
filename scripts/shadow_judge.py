"""Phase 2 影子对比的 LLM 判官：对每一对 A/B 答案打分并给结论。

为什么需要它：`shadow.py` 里的可核验性指标**只覆盖数字/日期类事实**，抓不到
人名/地名/表述是否准确。质量判断必须靠判官（或人工）。

用法：
    .venv/bin/python scripts/shadow_judge.py                 # 判全部待判记录
    .venv/bin/python scripts/shadow_judge.py --limit 10
    .venv/bin/python scripts/shadow_judge.py --show 3        # 顺带打印前 3 组的原始答案

结果写回 shadow 库的 judge 列（同一行），并打印汇总。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import BaseModel  # noqa: E402

from xiaowo_web.evidence.shadow import ShadowStore  # noqa: E402
from xiaowo_web.settings import WebSettings  # noqa: E402

# 判官每条证据只看这么多字（够核对事实，又不至于把上下文撑爆）
_EVIDENCE_CHARS = 600

_JUDGE_PROMPT = """你是校园智能助手「小蜗」的回答质量判官。下面同一个问题的两个答案：
- 方案A：百度智能搜索生成（端点自己总结；它**披露给我们的证据**较短）
- 方案B：我们自己检索（证据更长）+ 自己的提示词合成

请分别打分（1~5，5 最好），并给出胜者。

评审维度：
1. accuracy：事实是否准确、有无明显错误或编造。
2. completeness：是否回答了用户问的关键点（漏掉要点要扣分）。
3. grounded：答案里的事实/数字是否能在**它自己那次检索给出的证据**里找到。证据里没有的
   具体数字/日期/名称视为**无依据**（就算内容碰巧对，也要扣分）。
4. usefulness：作为校园助手的回答是否好读、可执行、不啰嗦。

注意：方案A与方案B看到的证据不同，**只按各自证据判断 grounded**，不要因为B证据多就加分，
也不要因为A答案长就加分。两边都答不出来时判 tie 并说明。

只输出一个 JSON 对象，严格符合：
{{"a_accuracy":1,"b_accuracy":1,"a_completeness":1,"b_completeness":1,
"a_grounded":1,"b_grounded":1,"a_usefulness":1,"b_usefulness":1,
"winner":"A|B|tie","reason":"两三句中文说明，指出关键差异与漏项"}}

用户问题：
{question}

===== 方案A 的答案 =====
{a_answer}

===== 方案A 那次检索披露的证据 =====
{a_evidence}

===== 方案B 的答案 =====
{b_answer}

===== 方案B 那次检索披露的证据 =====
{b_evidence}
"""


class _Verdict(BaseModel):
    a_accuracy: int = 0
    b_accuracy: int = 0
    a_completeness: int = 0
    b_completeness: int = 0
    a_grounded: int = 0
    b_grounded: int = 0
    a_usefulness: int = 0
    b_usefulness: int = 0
    winner: str = "tie"
    reason: str = ""


def _ensure_columns(store: ShadowStore) -> None:
    with sqlite3.connect(store.path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(shadow_runs)")}
        if "judge_json" not in cols:
            conn.execute("ALTER TABLE shadow_runs ADD COLUMN judge_json TEXT")
            conn.commit()


def _clip(text: str, limit: int) -> str:
    text = str(text or "")
    return text if len(text) <= limit else text[:limit] + "…（截断）"


def _evidence_for(refs_json: str, *, limit: int = 6) -> str:
    try:
        refs = json.loads(refs_json or "[]")
    except (TypeError, ValueError):
        refs = []
    lines = []
    for index, ref in enumerate(refs[:limit], start=1):
        if isinstance(ref, dict):
            title = str(ref.get("title") or "")[:60]
            url = str(ref.get("url") or "")
            content = _clip(str(ref.get("content") or ""), _EVIDENCE_CHARS)
        else:      # 兼容早期只存 URL 的记录
            title, url, content = "", str(ref), ""
        lines.append(f"[{index}] {title} {url}\n{content}".strip())
    return "\n\n".join(lines) or "（无）"


def judge_one(row: dict) -> _Verdict:
    from langchain_core.prompts import ChatPromptTemplate
    from utils.llm_client import create_llm

    prompt = _JUDGE_PROMPT.format(
        question=row["question"],
        a_answer=_clip(row["a_answer"], 2500),
        a_evidence=_evidence_for(row["a_refs"]),
        b_answer=_clip(row["b_answer"], 2500),
        b_evidence=_evidence_for(row["b_refs"]),
    )
    llm = create_llm(temperature=0)
    structured = llm.with_structured_output(_Verdict, method="json_mode")
    result = structured.invoke(prompt)
    if isinstance(result, _Verdict):
        return result
    if isinstance(result, dict):
        return _Verdict(**result)
    return _Verdict(**{k: getattr(result, k, 0) for k in _Verdict.model_fields})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--show", type=int, default=0)
    parser.add_argument("--redo", action="store_true", help="重判已有结果的记录")
    args = parser.parse_args()

    settings = WebSettings.from_env()
    store = ShadowStore(settings.review_db_path.parent / "shadow_compare.db")
    store.initialize()
    _ensure_columns(store)

    rows = [r for r in store.rows(limit=500) if r.get("b_answer")][::-1]  # 时间正序
    if not args.redo:
        rows = [r for r in rows if not r.get("judge_json")]
    if args.limit:
        rows = rows[: args.limit]
    print(f"待判 {len(rows)} 组")
    if not rows:
        return

    verdicts: list[tuple[dict, _Verdict]] = []
    for index, row in enumerate(rows, 1):
        try:
            verdict = judge_one(row)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{index}/{len(rows)}] 判官失败: {type(exc).__name__}: {str(exc)[:80]}")
            continue
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "UPDATE shadow_runs SET judge_json = ? WHERE run_id = ?",
                (json.dumps(verdict.model_dump(), ensure_ascii=False), row["run_id"]),
            )
            conn.commit()
        verdicts.append((row, verdict))
        print(
            f"  [{index}/{len(rows)}] {row['question'][:24]:26} "
            f"胜者={verdict.winner:4} A(准确{verdict.a_accuracy}/完整{verdict.a_completeness}/有据{verdict.a_grounded}) "
            f"B(准确{verdict.b_accuracy}/完整{verdict.b_completeness}/有据{verdict.b_grounded})"
        )
        if index <= args.show:
            print(f"\n    ── A 的答案 ──\n    {_clip(row['a_answer'], 400).replace(chr(10), ' ')}")
            print(f"    ── B 的答案 ──\n    {_clip(row['b_answer'], 400).replace(chr(10), ' ')}")
            print(f"    ── 判官理由 ──\n    {verdict.reason}\n")

    if not verdicts:
        return
    n = len(verdicts)

    def avg(getter):
        return sum(getter(v) for _r, v in verdicts) / n

    wins = {"A": 0, "B": 0, "tie": 0}
    for _r, v in verdicts:
        wins[v.winner if v.winner in wins else "tie"] += 1
    print(f"\n{'='*70}\nLLM 判官汇总（{n} 组，1~5 分）\n{'='*70}")
    print(f"{'维度':<14}{'方案A':>10}{'方案B':>10}")
    for label, key in (("准确", "accuracy"), ("完整", "completeness"),
                       ("有依据", "grounded"), ("有用性", "usefulness")):
        print(f"{label:<14}{avg(lambda v, k=key: getattr(v, f'a_{k}')):>10.2f}"
              f"{avg(lambda v, k=key: getattr(v, f'b_{k}')):>10.2f}")
    print(f"\n胜者：A {wins['A']} ｜ B {wins['B']} ｜ 平 {wins['tie']}")
    print("\n判官给出的关键差异（前 8 条）：")
    for row, v in verdicts[:8]:
        print(f"  · [{v.winner}] {row['question'][:26]:28} {v.reason[:110]}")


if __name__ == "__main__":
    main()
