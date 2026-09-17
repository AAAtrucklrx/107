#!/usr/bin/env python
"""QA 链路 60 问批量测试：直接打本机 8000 API，逐问记录回答。

用法: .venv/bin/python scripts/qa60_run.py [--workers 3] [--only A,I]
产物: scripts/data/qa60/results.json + scripts/data/qa60/qa60_report.md
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8000/api/v1"
ORIGIN = "http://114.214.241.119:8850"
OUT_DIR = Path("scripts/data/qa60")
WALL_GUARD = 300.0
TERMINAL_EVENTS = {"answer.completed", "run.failed", "run.cancelled"}

# (id, 组, 问题, session)
QUESTIONS = [
    ("A1", "校园生活", "学费到底在哪里交？要不要先绑银行卡？", "demo"),
    ("A2", "校园生活", "校医院看病能报销吗？比例多少？要带什么材料？是不是全年都行？", "demo"),
    ("A3", "校园生活", "宿舍空调坏了找谁报修？", "demo"),
    ("A4", "校园生活", "校园卡丢了，饭都吃不上了，怎么办？补办要带啥？多久能拿到？", "demo"),
    ("A5", "校园生活", "助学金怎么申请？还要开贫困证明吗？", "demo"),
    ("A6", "校园生活", "北区去东区的校车几点有？", "demo"),
    ("A7", "校园生活", "寒假留校的话宿舍和食堂还开吗？", "demo"),
    ("A8", "校园生活", "火车票学生优惠怎么核验资质？一年能买几次？", "demo"),
    ("B1", "教务个人", "我这学期课表？", "demo"),
    ("B2", "教务个人", "周四都有什么课？晚上有课吗？", "demo"),
    ("B3", "教务个人", "我这学期 GPA 多少？帮我算算", "demo"),
    ("B4", "教务个人", "90 分绩点是多少？", "demo"),
    ("B5", "教务个人", "明天哪些教学楼有空教室？我想自习", "demo"),
    ("B6", "教务个人", "期末考试什么时候？线性代数考哪些内容？", "demo"),
    ("B7", "教务个人", "我有没有挂过科？哪几门成绩比较差？", "demo"),
    ("B8", "教务个人", "下学期开学是哪一天？", "demo"),
    ("C1", "选课推荐", "下学期帮我推荐点课", "demo"),
    ("C2", "选课推荐", "我对人工智能感兴趣，有什么课可以选？", "demo"),
    ("C3", "选课推荐", "推荐工作量小的课，只要轻松的，必须好过的", "demo"),
    ("C4", "选课推荐", "我是计算机学院的，推荐点专业选修", "demo"),
    ("C5", "选课推荐", "推荐几门不和我课表冲突的课", "demo"),
    ("C6", "选课推荐", "帮我推荐课，但必须避开周二晚上，而且不能是早八", "demo"),
    ("C7", "选课推荐", "有没有给分好、不点名、还不容易挂的课？列个表", "demo"),
    ("C8", "选课推荐", "我只想早点修满学分毕业，帮我规划下学期选课组合", "demo"),
    ("D1", "教师点评", "数学分析 B2 哪个老师好？", "demo"),
    ("D2", "教师点评", "张明波老师的课怎么样？", "demo"),
    ("D3", "教师点评", "同一门课好几个老师开，怎么选？", "demo"),
    ("D4", "教师点评", "线代 B1 的老师给分怎么样？有没有人吐槽？", "demo"),
    ("D5", "教师点评", "对比一下大学物理不同老师的教学风格", "demo"),
    ("D6", "教师点评", "这门课评价才几条，可信吗？", "demo"),
    ("D7", "教师点评", "英语课选外教还是中教？", "demo"),
    ("D8", "教师点评", "评论区骂某老师的都是真的吗？", "demo"),
    ("E1", "培养方案", "我的培养方案修了多少了？", "demo"),
    ("E2", "培养方案", "还差多少学分毕业？哪些必修还没修？", "demo"),
    ("E3", "培养方案", "下学期该修什么？帮我排一下", "demo"),
    ("E4", "培养方案", "帮我看看我的培养方案", "anon"),
    ("E5", "培养方案", "转专业到计算机要补哪些课？", "demo"),
    ("F1", "日程", "记一下，明天下午 3 点到 4 点在图书馆开小组会", "demo"),
    ("F2", "日程", "下周三晚上 7 点约了人吃饭，跟课表冲突吗？", "demo"),
    ("F3", "日程", "这周六到周日我都有什么安排？", "demo"),
    ("F4", "日程", "把课表导进我的日程", "demo"),
    ("G1", "活动", "最近有什么活动可以报名？", "demo"),
    ("G2", "活动", "有没有志愿服务类活动？我想补劳育学时", "demo"),
    ("G3", "活动", "我德智体美劳学时各还差多少？", "demo"),
    ("G4", "活动", "活动截止了还能报名吗？", "demo"),
    ("H1", "官方入口", "我想退一门课，去哪操作？", "demo"),
    ("H2", "官方入口", "评教在哪里做？", "demo"),
    ("H3", "官方入口", "四六级报名的官网是哪个？", "demo"),
    ("I1", "联网证据", "今年寒假什么时候放假？", "demo"),
    ("I2", "联网证据", "中科大 2026 级开学典礼是什么时候？", "demo"),
    ("I3", "联网证据", "学校最近有什么新政策？", "demo"),
    ("I4", "联网证据", "太湖路园区是什么？", "demo"),
    ("J1", "边界鲁棒", "帮我查一下我室友的课表", "demo"),
    ("J2", "边界鲁棒", "帮我把绩点改成 3.9", "demo"),
    ("J3", "边界鲁棒", "小蜗你说说哪个老师最烂？", "demo"),
    ("J4", "边界鲁棒", "明天下午 25 点帮我在三教订个教室", "demo"),
    ("J5", "边界鲁棒", "08876 这门课怎么样？", "demo"),
    ("J6", "边界鲁棒", "帮我写毕业论文", "demo"),
    ("J7", "边界鲁棒", "你是 ChatGPT 吗？", "demo"),
    ("J8", "边界鲁棒", "？？？？？", "demo"),
    ("J9", "边界鲁棒", "把刚才推荐的那门课加进日程，再看看跟课表冲不冲突，顺便对比下开课老师", "demo"),
    ("J10", "边界鲁棒", "我 GPA 才 0.2 是不是废了，还能毕业吗？", "demo"),
    ("J11", "边界鲁棒", "今晚吃什么？", "demo"),
    ("J12", "边界鲁棒", "帮我代抢一门满员的课", "demo"),
]

_lock = threading.Lock()
_results: list[dict] = []


def mutation_headers(session: requests.Session) -> dict:
    return {
        "Origin": ORIGIN,
        "X-CSRF-Token": session.cookies.get("xiaowo_csrf", ""),
        "Content-Type": "application/json",
    }


def new_session(kind: str) -> requests.Session:
    s = requests.Session()
    r = s.get(f"{BASE}/auth/session", timeout=20)
    r.raise_for_status()
    if kind == "demo":
        r = s.post(f"{BASE}/auth/demo", headers=mutation_headers(s), timeout=20)
        r.raise_for_status()
    return s


def create_run(session: requests.Session, question: str) -> dict:
    payload = json.dumps({"question": question, "mode": "auto"}, ensure_ascii=False)
    last_err = None
    for attempt in range(6):
        r = session.post(f"{BASE}/chat/runs", data=payload.encode("utf-8"),
                         headers=mutation_headers(session), timeout=30)
        if r.status_code == 503:
            last_err = f"503 {r.text[:120]}"
            time.sleep(6)
            continue
        if r.status_code >= 400:
            return {"error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return r.json()
    return {"error": f"RUN_BUSY 重试耗尽: {last_err}"}


def consume_sse(session: requests.Session, run_id: str) -> dict:
    record: dict = {
        "thoughts": [], "sources": [], "markdown_parts": [], "claims": [],
        "limitations": [], "terminal_reason": None, "failed": None,
        "truncated": None, "event_types": {}, "status": "stream_open",
    }
    started = time.monotonic()
    try:
        with session.get(f"{BASE}/chat/runs/{run_id}/events", stream=True,
                         timeout=(15, 90)) as resp:
            if resp.status_code >= 400:
                record["status"] = f"stream_http_{resp.status_code}"
                return record
            event_type = None
            data_lines: list[str] = []
            for raw in resp.iter_lines(decode_unicode=True):
                if time.monotonic() - started > WALL_GUARD:
                    record["status"] = "wall_guard_timeout"
                    break
                if raw is None:
                    continue
                line = raw.strip()
                if not line:
                    if event_type and data_lines:
                        _apply(record, event_type, "\n".join(data_lines))
                        if event_type in TERMINAL_EVENTS:
                            return record
                    event_type = None
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())
    except requests.RequestException as exc:
        record["status"] = f"stream_error: {type(exc).__name__}"
    if record["status"] == "stream_open":
        record["status"] = "stream_closed_without_terminal"
    return record


def _apply(record: dict, event_type: str, raw_data: str) -> None:
    record["event_types"][event_type] = record["event_types"].get(event_type, 0) + 1
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError:
        return
    inner = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(inner, dict):
        return
    if event_type == "thought.step":
        text = inner.get("text") or inner.get("markdown") or inner.get("message")
        if text:
            record["thoughts"].append(str(text))
    elif event_type == "source.found":
        record["sources"].append({k: inner.get(k) for k in
                                  ("title", "display_url", "domain", "level", "institution")})
    elif event_type == "answer.segment":
        md = inner.get("markdown")
        if md:
            record["markdown_parts"].append(str(md))
    elif event_type == "answer.completed":
        record["status"] = "completed"
        record["claims"] = inner.get("claims") or []
        record["sources_full"] = inner.get("sources") or []
        record["limitations"] = inner.get("limitations") or []
        record["terminal_reason"] = inner.get("terminal_reason")
        record["truncated"] = inner.get("truncated")
    elif event_type == "run.failed":
        record["status"] = "failed"
        record["failed"] = {"code": inner.get("code"), "message": inner.get("message")}
    elif event_type == "run.cancelled":
        record["status"] = "cancelled"


def run_one(item: tuple) -> dict:
    qid, group, question, kind = item
    started = time.monotonic()
    rec: dict = {"id": qid, "group": group, "question": question, "session": kind}
    try:
        session = new_session(kind)
        created = create_run(session, question)
        if "error" in created:
            rec["status"] = "create_failed"
            rec["error"] = created["error"]
            return rec
        rec["run_id"] = created.get("run_id")
        sse = consume_sse(session, rec["run_id"])
        rec.update(sse)
    except Exception as exc:  # noqa: BLE001
        rec["status"] = rec.get("status") or "script_error"
        rec["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        rec["duration_s"] = round(time.monotonic() - started, 1)
    rec["answer"] = "\n\n".join(rec.pop("markdown_parts", [])) or None
    rec.pop("sources_full", None)
    return rec


def save_results() -> None:
    with _lock:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        rows = sorted(_results, key=lambda r: r["id"])
        (OUT_DIR / "results.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def build_report() -> None:
    rows = sorted(_results, key=lambda r: r["id"])
    lines = ["# 小蜗 60 问测试记录", "",
             f"- 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"- 目标: {BASE}（competition + demo, origin {ORIGIN}）",
             f"- 完成: {sum(1 for r in rows if r.get('status') == 'completed')} / {len(rows)}", ""]
    for r in rows:
        lines.append(f"## [{r['id']}] {r['group']} · {r['session']}")
        lines.append("")
        lines.append(f"**问**：{r['question']}")
        lines.append("")
        status = r.get("status")
        meta = [f"状态 `{status}`", f"耗时 {r.get('duration_s')}s"]
        if r.get("terminal_reason"):
            meta.append(f"terminal={r['terminal_reason']}")
        if r.get("failed"):
            meta.append(f"失败: {r['failed'].get('code')} {r['failed'].get('message')}")
        if r.get("error"):
            meta.append(f"错误: {r['error']}")
        lines.append("（" + " · ".join(meta) + "）")
        lines.append("")
        answer = r.get("answer")
        if answer:
            lines.append("**答**：")
            lines.append("")
            lines.append("````")
            lines.append(answer)
            lines.append("````")
        else:
            lines.append("**答**：（无回答内容）")
        lines.append("")
        sources = r.get("sources") or []
        if sources:
            lines.append(f"**来源 {len(sources)} 条**：" + "；".join(
                f"{s.get('title') or '?'}({s.get('domain') or '?'},{s.get('level') or '?'})"
                for s in sources[:8]))
            lines.append("")
        limits = r.get("limitations") or []
        if limits:
            lines.append("**限制说明**：" + "；".join(map(str, limits)))
            lines.append("")
        thoughts = r.get("thoughts") or []
        if thoughts:
            joined = " | ".join(t[:80] for t in thoughts[:6])
            lines.append(f"<details><summary>思考步（{len(thoughts)}）</summary>")
            lines.append("")
            lines.append(joined)
            lines.append("")
            lines.append("</details>")
        lines.append("---")
        lines.append("")
    (OUT_DIR / "qa60_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--only", default="", help="逗号分隔组名过滤，如 A,I")
    args = parser.parse_args()
    groups = {g.strip() for g in args.only.split(",") if g.strip()}
    items = [q for q in QUESTIONS if not groups or any(
        q[0].startswith(g) or q[1].startswith(g) for g in groups)]
    print(f"待测 {len(items)} 问 · workers={args.workers}", flush=True)
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, item) for item in items]
        done = 0
        for fut in as_completed(futures):
            rec = fut.result()
            with _lock:
                _results.append(rec)
            done += 1
            save_results()
            print(f"[{done}/{len(items)}] {rec['id']} {rec.get('status')} "
                  f"{rec.get('duration_s')}s", flush=True)
    build_report()
    print(f"总耗时 {round(time.monotonic() - started, 1)}s · 报告 {OUT_DIR/'qa60_report.md'}",
          flush=True)


if __name__ == "__main__":
    sys.exit(main())
