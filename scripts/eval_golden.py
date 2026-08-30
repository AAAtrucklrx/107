# -*- coding: utf-8 -*-
"""B8: 50 题金标评测 + LLM-as-judge 评分。

用法: .venv\\Scripts\\python.exe scripts/eval_golden.py [--limit N] [--report docs/金标评测报告.md]
- 逐题走 8850 demo 身份提问, 收集回答;
- 关键词硬检(required 全含 / forbidden 全不含);
- LLM-as-judge 按 相关性/准确性/完整性/格式 四维 0-5 打分;
- 输出汇总报告(每题一行 + 分类统计)。
"""
import io
import json
import sys
import time
import urllib.request
import http.cookiejar
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = "http://114.214.241.119:8850"
GOLDEN = ROOT / "scripts" / "golden50.json"
REPORT = ROOT / "docs" / "金标评测报告.md"

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def req(path, method="GET", body=None, headers=None, timeout=180):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    with opener.open(r, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8")


def ask(question):
    st, body = req("/api/v1/auth/session")
    csrf = json.loads(body)["csrf_token"]
    st, body = req("/api/v1/auth/demo", "POST", {}, {"X-CSRF-Token": csrf, "Origin": BASE})
    csrf = json.loads(body)["csrf_token"]
    st, body = req("/api/v1/chat/runs", "POST", {"question": question, "mode": "local"},
                   {"X-CSRF-Token": csrf, "Origin": BASE})
    rid = json.loads(body)["run_id"]
    st, body = req(f"/api/v1/chat/runs/{rid}/events")
    segs = []
    for line in body.splitlines():
        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            if ev.get("type") == "answer.segment":
                segs.append((ev.get("data") or {}).get("markdown", ""))
    return segs[0] if segs else ""


def judge(question, answer):
    """LLM-as-judge: 四维 0-5 打分。失败返回 None(不计分只留关键词结果)。"""
    from agents.qa.nodes import COMPOSE_PROMPT  # noqa: F401  (确保 QA 模块可导入)
    from utils.llm_client import create_llm
    from langchain_core.prompts import ChatPromptTemplate
    llm = create_llm(temperature=0.0)
    prompt = ChatPromptTemplate.from_messages([
        ("system", (
            "你是严格的校园助手评测员。对【用户问题】与【助手回答】按四个维度各打 0-5 分"
            "(整数, 5=优秀), 并给出 60 字内中文点评。\n"
            "维度: 相关性(是否正面回答问题) / 准确性(是否编造数据, 工具没查到的如实说没有) / "
            "完整性(要点是否齐全) / 格式(分段、列表、来源标注是否清晰)。\n"
            "只输出 JSON 对象, 结构为: scores 字段包含 相关性、准确性、完整性、格式 四个整数, "
            "comment 字段为点评字符串。"
        )),
        ("human", "用户问题: {question}\n\n助手回答:\n{answer}"),
    ])
    from agents.qa.nodes import _parse_json_loose
    response = (prompt | llm).invoke({"question": question, "answer": answer[:3000]})
    data = _parse_json_loose(response.content or "")
    if not data or "scores" not in data:
        return None
    scores = data["scores"]
    return {
        "scores": {k: int(v) for k, v in scores.items() if isinstance(v, (int, float))},
        "comment": str(data.get("comment") or "")[:60],
    }


def ask_with_rate_limit(question, pause=7.0):
    """限速提问: 平台 20 req/min, 每题间隔 pause 秒(生成本身也会打平台)。"""
    answer = ask(question)
    time.sleep(pause)
    return answer


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题(调试用)")
    parser.add_argument("--ids", default="", help="只跑指定题 id(逗号分隔, 如 G01,G13)")
    parser.add_argument("--report", default=str(REPORT))
    args = parser.parse_args()

    items = json.loads(GOLDEN.read_text(encoding="utf-8"))
    if args.ids:
        wanted = {token.strip() for token in args.ids.split(",") if token.strip()}
        items = [item for item in items if item["id"] in wanted]
    elif args.limit:
        items = items[:args.limit]

    # 登录一次
    st, body = req("/api/v1/auth/session")
    csrf = json.loads(body)["csrf_token"]
    st, body = req("/api/v1/auth/demo", "POST", {}, {"X-CSRF-Token": csrf, "Origin": BASE})

    rows = []
    t0 = time.time()
    for i, item in enumerate(items, start=1):
        start = time.time()
        answer = ask_with_rate_limit(item["question"])
        elapsed = time.time() - start
        kw_ok = all(k in answer for k in item.get("required", [])) and not any(
            k in answer for k in item.get("forbidden", []))
        judged = None
        for attempt in range(3):
            try:
                judged = judge(item["question"], answer) if answer else None
                break
            except Exception as exc:  # 429 限流等: 等 70s 重试
                text = f"{type(exc).__name__} {exc}"
                wait = 70 if "429" in text or "Rate limit" in text else 15
                print(f"  judge 失败({type(exc).__name__}), {wait}s 后重试 {attempt + 1}/3")
                time.sleep(wait)
        time.sleep(4)
        score = sum(judged["scores"].values()) / max(len(judged["scores"]), 1) if judged else None
        rows.append({**item, "answer": answer, "elapsed": elapsed,
                     "kw_ok": kw_ok, "judged": judged, "score": score})
        print(f"[{i:02d}/{len(items)}] {item['id']} {item['question'][:18]} "
              f"kw={'OK' if kw_ok else 'FAIL'} score={score if score is not None else '-'} "
              f"({elapsed:.0f}s)", flush=True)
        if len(items) <= 10:
            print(f"    答: {answer[:120].replace(chr(10), ' ')}", flush=True)
    total = time.time() - t0

    # 报告
    passed = [r for r in rows if r["kw_ok"] and r.get("score") is not None and r["score"] >= 3.5]
    lines = [
        "# 金标评测报告(50 题)",
        "",
        f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M')} · 耗时 {total:.0f}s · "
        f"通过 {len(passed)}/{len(rows)}(关键词全中且 LLM 均分≥3.5)",
        "",
        "| ID | 分类 | 问题 | 关键词 | LLM 均分 | 各维得分 | 点评 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        j = r["judged"]
        scores = j["scores"] if j else {}
        dims = "/".join(str(scores.get(d, "-")) for d in ("相关性", "准确性", "完整性", "格式"))
        score_text = f"{r['score']:.1f}" if r.get("score") is not None else "-"
        lines.append(
            f"| {r['id']} | {r['category']} | {r['question']} | "
            f"{'✅' if r['kw_ok'] else '❌'} | {score_text} | {dims} | "
            f"{(j['comment'] if j else '')} |"
        )
    lines += ["", "## 分类统计", ""]
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)
    lines.append("| 分类 | 题数 | 通过 | 均分 |")
    lines.append("|---|---|---|---|")
    for cat, rs in by_cat.items():
        ok = sum(1 for r in rs if r["kw_ok"] and r.get("score") is not None and r["score"] >= 3.5)
        scored = [r["score"] for r in rs if r.get("score") is not None]
        avg = sum(scored) / max(len(scored), 1)
        lines.append(f"| {cat} | {len(rs)} | {ok} | {avg:.1f} |")
    Path(args.report).write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入: {args.report}")
    print(f"汇总: 通过 {len(passed)}/{len(rows)}")


if __name__ == "__main__":
    main()
