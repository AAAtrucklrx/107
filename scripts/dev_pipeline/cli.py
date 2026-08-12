"""小蜗开发流水线 — CLI 入口。

用法（在 F:\\小蜗 项目内）:
    python -m scripts.dev_pipeline run "任务描述" --executor claude --rounds 3
    python -m scripts.dev_pipeline status
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# 允许以 `python scripts/dev_pipeline/cli.py` 或 `python -m scripts.dev_pipeline` 运行
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from dev_pipeline.config import ensure_dirs, PROJECT_ROOT  # type: ignore
    from dev_pipeline.graph import run_pipeline  # type: ignore
else:
    from .config import PROJECT_ROOT, ensure_dirs
    from .graph import run_pipeline


def _banner() -> None:
    print("=" * 60)
    print(f"🐌 小蜗开发流水线 (LangGraph) · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"   项目: {PROJECT_ROOT}")
    print("=" * 60)


def cmd_run(args: argparse.Namespace) -> int:
    ensure_dirs()
    _banner()
    print(f"任务: {args.task}")
    print(f"执行器: {args.executor} · 轮次上限: {args.rounds}\n")

    state = run_pipeline(args.task, executor=args.executor, max_rounds=args.rounds)

    print("\n" + "-" * 60)
    print(f"验收: {state.get('verdict', 'fail').upper()} · 轮次: {state.get('round')}/{args.rounds}")
    if state.get("issues"):
        print("遗留问题:")
        for i in state.get("issues", []):
            print(f"  - {i}")
    print(f"Markdown 报告: {state.get('report_md', '')[:200]}...")
    if state.get("canvas_path"):
        print(f"Canvas 报告: {state['canvas_path']}")
    print("-" * 60)

    if args.json:
        dump = {k: v for k, v in state.items() if k in (
            "task", "plan", "verdict", "issues", "fix_instruction",
            "round", "max_rounds", "canvas_path", "exec_output", "test_output", "report_md",
        )}
        print("\n" + json.dumps(dump, ensure_ascii=False, indent=2, default=str))
    return 0 if state.get("verdict") == "pass" else 1


def cmd_status(args: argparse.Namespace) -> int:
    from .config import CANVAS_DIR, REPORT_DIR

    print(f"项目根目录: {PROJECT_ROOT}")
    print(f"Canvas 报告目录: {CANVAS_DIR}")
    print(f"Markdown 报告目录: {REPORT_DIR}")
    for d in (CANVAS_DIR, REPORT_DIR):
        if d.exists():
            files = sorted(d.glob("*"))
            print(f"\n{d.name}/ ({len(files)} 个文件)")
            for f in files[-8:]:
                print(f"  {f.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev_pipeline", description="小蜗开发流水线")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="运行一轮完整的开发流水线")
    p_run.add_argument("task", help="开发任务描述")
    p_run.add_argument("--executor", choices=["claude", "codex", "qoder"], default="claude", help="执行器 CLI")
    p_run.add_argument("--rounds", type=int, default=3, help="决策循环上限")
    p_run.add_argument("--json", action="store_true", help="额外输出 JSON 状态")
    p_run.set_defaults(func=cmd_run)

    p_st = sub.add_parser("status", help="查看流水线报告目录状态")
    p_st.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
