"""
小蜗 — QA 评估套件
遍历 scripts/qa_eval_cases.py 中的用例，调用统一 QA 入口 run_qa，
按工具调用 + 回答要点 + 澄清行为规则判定 pass/fail，输出中文报告。

用法: python scripts/qa_eval.py
环境: 需设置 USTC_API_KEY 或 LLM_API_KEY（未设置时降级为确定性规则，通过率可能偏低）
退出码: 通过率 >= 80% 返回 0，否则返回 1
"""

import logging
import os
import sys
from pathlib import Path

# 兼容 USTC_API_KEY（dev pipeline 命名）与 LLM_API_KEY（应用 .env 命名），须在导入 config 前设置
if not os.environ.get("LLM_API_KEY") and os.environ.get("USTC_API_KEY"):
    os.environ["LLM_API_KEY"] = os.environ["USTC_API_KEY"]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qa_eval_cases import EVAL_CASES  # noqa: E402
from utils.logger import get_logger  # noqa: E402

log = get_logger("xiaowo.qa_eval")
logging.getLogger("xiaowo").setLevel(logging.WARNING)  # 压掉 QA 图内部 INFO 日志，保持报告可读

from agents.qa.graph import run_qa  # noqa: E402

PASS_RATE_THRESHOLD = 0.8
ANSWER_PREVIEW_LEN = 200


# ── 显示宽度工具（CJK 按 2 列宽）──────────────────

def _display_width(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _display_width(s))


def _truncate(s: str, width: int) -> str:
    if _display_width(s) <= width:
        return s
    out, w = "", 0
    for ch in s:
        cw = 2 if ord(ch) > 0x2E80 else 1
        if w + cw > width - 1:
            break
        out += ch
        w += cw
    return out + "…"


# ── 环境检查 ─────────────────────────────────────

def _check_env() -> None:
    if os.environ.get("LLM_API_KEY") or os.environ.get("USTC_API_KEY"):
        return
    print("[提示] 未检测到 USTC_API_KEY / LLM_API_KEY 环境变量，LLM 决策将降级为确定性规则，通过率可能偏低。")
    print("       建议设置后重跑：Windows `set USTC_API_KEY=xxx` / Linux `export USTC_API_KEY=xxx`")


# ── 单用例执行与判定 ──────────────────────────────

def _judge(case: dict, result: dict) -> dict:
    """按用例规则判定 pass/fail，返回 (pass, reason)"""
    answer = result.get("answer") or ""
    decision = result.get("decision") or ""
    tools = [t.get("tool", "") for t in (result.get("tool_results") or [])]
    clarify = (result.get("clarify_question") or "").strip()

    if case.get("expected_clarify"):
        if decision == "clarify" and clarify:
            return True, f"澄清提问: {clarify[:40]}"
        return False, f"期望 clarify 决策且追问非空，实际 decision={decision!r} clarify={clarify[:30]!r}"

    # 澄清也是有效回答：最终以追问收尾（如工具缺参后引导补充信息）视为通过
    if decision == "clarify" and clarify:
        return True, f"澄清追问收尾: {clarify[:40]}"

    points = case.get("expected_points") or []
    point_hit = any(p in answer for p in points)
    expected = case.get("expected_tool") or ""
    if not expected:
        # 不期望调用工具（闲聊/敏感拒绝）
        if tools:
            return False, f"不应调用工具，实际调用: {tools}"
        if decision != "compose":
            return False, f"期望 compose 决策，实际 decision={decision!r}"
        if not point_hit:
            return False, f"回答未包含期望要点 {points}: {answer[:80]!r}"
        return True, "未调用工具，直接回应"

    exp_list = [expected] if isinstance(expected, str) else list(expected)
    hit = [t for t in tools if t in exp_list]
    if not hit:
        if case.get("tool_optional"):
            # 知识问答：候选召回足够时直接合成属正常路径
            if decision == "compose" and result.get("candidates_found"):
                if point_hit:
                    return True, "候选召回足够，直接合成（未调用工具）"
                return False, f"候选合成但回答未包含期望要点 {points}: {answer[:80]!r}"
            return False, f"期望工具 {exp_list} 未被调用且候选未命中，实际工具: {tools or '无'}"
        return False, f"期望工具 {exp_list} 未被调用，实际工具: {tools or '无'}"
    if not point_hit:
        return False, f"已调用 {hit} 但回答未包含期望要点 {points}: {answer[:80]!r}"
    return True, f"工具调用正确，回答包含要点"


def run_single_case(case: dict) -> dict:
    try:
        result = run_qa(case["question"], module_signal=case.get("module", "自动判断"))
        passed, reason = _judge(case, result)
        return {"case": case, "result": result, "pass": passed, "reason": reason}
    except Exception as e:
        log.error(f"用例执行异常: {case.get('question')}: {e}")
        return {"case": case, "result": None, "pass": False, "reason": f"执行异常: {e}"}


# ── 报告输出 ─────────────────────────────────────

def print_table(results: list[dict]) -> None:
    headers = ["#", "问题", "意图", "决策", "轮次", "工具", "结果"]
    widths = [4, 24, 8, 10, 6, 22, 6]
    sep = "-" * (sum(widths) + len(widths) * 3 + 1)
    print(sep)
    print(" | ".join(_pad(h, w) for h, w in zip(headers, widths)))
    print(sep)
    for r in results:
        case = r["case"]
        res = r.get("result") or {}
        tools = ",".join(t.get("tool", "") for t in (res.get("tool_results") or [])) or "-"
        row = [
            str(r["index"]),
            _truncate(case["question"], widths[1]),
            _truncate(res.get("intent") or "-", widths[2]),
            _truncate(res.get("decision") or "-", widths[3]),
            str(res.get("rounds") or 0),
            _truncate(tools, widths[5]),
            "通过" if r["pass"] else "失败",
        ]
        print(" | ".join(_pad(c, w) for c, w in zip(row, widths)))
    print(sep)


def print_summary(results: list[dict]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    rate = passed / total if total else 0.0
    print()
    print("=" * 60)
    print(f"总用例数: {total}")
    print(f"通过: {passed} ({rate:.1%})")
    print(f"通过率: {rate:.1%}（阈值 {PASS_RATE_THRESHOLD:.0%}）")
    print(f"状态: {'达标' if rate >= PASS_RATE_THRESHOLD else '未达标'}")
    print("=" * 60)

    failed = [r for r in results if not r["pass"]]
    if failed:
        print("\n失败详情:")
        for r in failed:
            case = r["case"]
            answer = ((r.get("result") or {}).get("answer") or "") or "（无回答）"
            print(f"[{r['index']:02d}] {case['question']}")
            print(f"      原因: {r['reason']}")
            print(f"      回答: {answer[:ANSWER_PREVIEW_LEN]}")
        print()


def main() -> int:
    _check_env()
    print(f"开始评估 {len(EVAL_CASES)} 个用例（QA LangGraph）...\n")
    results = []
    for i, case in enumerate(EVAL_CASES, 1):
        r = run_single_case(case)
        r["index"] = i
        results.append(r)
        print(f"  [{i:02d}/{len(EVAL_CASES)}] {'PASS' if r['pass'] else 'FAIL'} {case['question']}")

    print()
    print_table(results)
    print_summary(results)

    passed = sum(1 for r in results if r["pass"])
    rate = passed / len(results) if results else 0.0
    return 0 if rate >= PASS_RATE_THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
