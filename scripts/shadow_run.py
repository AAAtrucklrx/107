"""Phase 2 影子对比批跑：A（智能搜索生成）vs B（纯搜索 + 自研合成）。

**离线跑，不走聊天接口** —— 因此不会给审核队列灌数据（聊天接口会触发进料）。
结果写进 `data/shadow_compare.db`，与生产影子记录同库。

用法：
    .venv/bin/python scripts/shadow_run.py                 # 内置 30 题
    .venv/bin/python scripts/shadow_run.py --limit 5       # 只跑前 5 题
    .venv/bin/python scripts/shadow_run.py --questions q.txt   # 一行一题
    .venv/bin/python scripts/shadow_run.py --report        # 只看已有记录的汇总

⚠️ 口径说明：本脚本的 A 固定为**智能搜索生成**（带 model），与生产影子钩子一致
（钩子只在 A 确实走 smart 时触发）。生产里"科大"类问题可能先走微信公众号分支并直接确认
答案——那类不会进本对比，也不属于"要不要替换 smart"这个问题。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from xiaowo_web.evidence.clients import BaiduSearchClient  # noqa: E402
from xiaowo_web.evidence.pipeline import EvidencePipeline  # noqa: E402
from xiaowo_web.evidence.shadow import ShadowComparer, ShadowStore  # noqa: E402
from xiaowo_web.settings import WebSettings  # noqa: E402

QUESTIONS = [
    "中国科学技术大学转专业政策是怎样的？",
    "中国科学技术大学图书馆的开放时间是什么？",
    "2026年国家助学贷款政策有什么新变化？",
    "中国科学技术大学2027届毕业生秋季校园招聘安排",
    "中国科学技术大学校园卡补办流程是什么？",
    "中国科学技术大学校医院怎么挂号？",
    "中国科学技术大学奖学金评定条件是什么？",
    "中国科学技术大学2026年研究生招生简章",
    "中国科学技术大学退补选规则是什么？",
    "中国科学技术大学食堂开放时间",
    "中国科学技术大学宿舍用电规定",
    "中国科学技术大学体育课选课要求",
    "中国科学技术大学推免保研条件",
    "中国科学技术大学学费标准2026",
    "中国科学技术大学新生报到流程",
    "中国科学技术大学教务处联系方式",
    "中国科学技术大学期末考试安排",
    "中国科学技术大学转专业申请时间",
    "中国科学技术大学辅修双学位政策",
    "中国科学技术大学出国交流项目",
    "中国科学技术大学心理咨询预约方式",
    "中国科学技术大学快递点在哪里",
    "中国科学技术大学校车时刻表",
    "中国科学技术大学体育馆预约方式",
    "中国科学技术大学学籍证明怎么办",
    "中国科学技术大学毕业学分要求",
    "中国科学技术大学研究生选导师流程",
    "中国科学技术大学实验室安全规定",
    "中国科学技术大学2026-2027学年校历",
    "中国科学技术大学2027年寒假放假安排",
]


def report(store: ShadowStore) -> None:
    """打印已有记录的 A/B 汇总。"""
    rows = [r for r in store.rows(limit=500) if r.get("b_error") is None and r.get("b_answer")]
    if not rows:
        print("没有可汇总的记录（可能都失败了）")
        return

    def avg(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else 0.0

    a_lat = avg([r["a_latency"] for r in rows])
    b_search = avg([r["b_search_latency"] for r in rows])
    b_compose = avg([r["b_compose_latency"] for r in rows])
    a_chars = avg([r["a_ref_chars"] for r in rows])
    b_chars = avg([r["b_ref_chars"] for r in rows])
    a_ver = [r["a_verifiable"] for r in rows if r["a_verifiable"] is not None]
    b_ver = [r["b_verifiable"] for r in rows if r["b_verifiable"] is not None]
    a_ans = avg([len(r["a_answer"] or "") for r in rows])
    b_ans = avg([len(r["b_answer"] or "") for r in rows])
    b_faster = sum(1 for r in rows if (r["b_search_latency"] or 0) + (r["b_compose_latency"] or 0) < (r["a_latency"] or 0))
    b_more_ver = sum(
        1 for r in rows
        if r["b_verifiable"] is not None and r["a_verifiable"] is not None
        and r["b_verifiable"] > r["a_verifiable"]
    )
    a_more_ver = sum(
        1 for r in rows
        if r["b_verifiable"] is not None and r["a_verifiable"] is not None
        and r["a_verifiable"] > r["b_verifiable"]
    )

    print(f"\n{'='*74}\n影子对比汇总（{len(rows)} 题，source 混合）\n{'='*74}")
    print(f"{'指标':<26}{'方案A 智能生成':>20}{'方案B 纯搜索+自研合成':>24}")
    print(f"{'-'*70}")
    print(f"{'端到端耗时(秒)':<26}{a_lat:>20.1f}{b_search + b_compose:>24.1f}")
    print(f"{'  ├ 检索':<26}{'（端点内部）':>20}{(f'{b_search:.1f}'):>24}")
    print(f"{'  └ 合成':<26}{'（端点内部）':>20}{(f'{b_compose:.1f}'):>24}")
    print(f"{'可见证据总字数':<26}{a_chars:>20.0f}{b_chars:>24.0f}")
    print(f"{'答案平均字数':<26}{a_ans:>20.0f}{b_ans:>24.0f}")
    print(f"{'可核验比例(数字/日期类)':<26}{(sum(a_ver)/len(a_ver) if a_ver else 0):>20.3f}"
          f"{(sum(b_ver)/len(b_ver) if b_ver else 0):>24.3f}")
    print(f"\nB 更快: {b_faster}/{len(rows)} 题")
    print(f"B 可核验性更高: {b_more_ver} 题 ｜ A 更高: {a_more_ver} 题")

    print(f"\n{'-'*70}\nA 答案里**证据中找不到**的数字/日期（越多越可疑）：")
    for r in sorted(rows, key=lambda x: -(len(x["a_unsupported"] or "")))[:5]:
        import json
        miss = json.loads(r["a_unsupported"] or "[]")
        if miss:
            print(f"  [A] {r['question'][:30]:32} {miss[:6]}")
    print(f"\nB 答案里**证据中找不到**的数字/日期：")
    for r in sorted(rows, key=lambda x: -(len(x["b_unsupported"] or "")))[:5]:
        import json
        miss = json.loads(r["b_unsupported"] or "[]")
        if miss:
            print(f"  [B] {r['question'][:30]:32} {miss[:6]}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--questions", type=str, default="")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--db", type=str, default="")
    args = parser.parse_args()

    settings = WebSettings.from_env()
    store = ShadowStore(args.db or (settings.review_db_path.parent / "shadow_compare.db"))
    store.initialize()
    if args.report:
        report(store)
        return

    questions = QUESTIONS
    if args.questions:
        questions = [q.strip() for q in Path(args.questions).read_text(encoding="utf-8").splitlines() if q.strip()]
    if args.limit:
        questions = questions[: args.limit]

    search = BaiduSearchClient(
        settings.baidu_api_key,
        base_url=settings.baidu_base_url,
        timeout=settings.search_timeout_seconds,
        model=settings.baidu_smart_model,
    )
    comparer = ShadowComparer(store, search)
    print(f"共 {len(questions)} 题 → A(智能生成) vs B(纯搜索+自研合成)，写入 {store.path}")
    try:
        for index, question in enumerate(questions, 1):
            started = time.time()
            try:
                answer, references = await search.generate(
                    EvidencePipeline._smart_prompt(question), model=settings.baidu_smart_model
                )
                latency = time.time() - started
                err = None
            except Exception as exc:  # noqa: BLE001
                answer, references, latency, err = "", [], time.time() - started, f"{type(exc).__name__}: {exc}"
            if err:
                print(f"  [{index}/{len(questions)}] A 失败: {question[:28]} → {err[:60]}")
                continue
            await comparer.compare(
                question=question,
                a_answer=answer,
                a_references=references,
                a_latency=latency,
                a_limitations=[] if not err else [err],
                source="batch",
            )
            row = store.rows(limit=1)[0]
            b_total = (row["b_search_latency"] or 0) + (row["b_compose_latency"] or 0)
            print(
                f"  [{index}/{len(questions)}] {question[:26]:28} "
                f"A {latency:5.1f}s/{len(answer):5}字/{row['a_ref_chars'] or 0:5}字证据 "
                f"| B {b_total:5.1f}s/{(len(row['b_answer'] or '')):5}字/{row['b_ref_chars'] or 0:5}字证据 "
                f"| 可核验 A={row['a_verifiable']} B={row['b_verifiable']}"
                + (f" | B错误={row['b_error'][:40]}" if row["b_error"] else "")
            )
    finally:
        await search.close()

    report(store)


if __name__ == "__main__":
    asyncio.run(main())
