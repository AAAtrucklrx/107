"""小蜗开发流水线 — LangGraph 节点实现。

节点: 计划 → 执行 → 测试 → 报告 → 决策（循环执行直到通过或达到轮次上限）
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from . import config
from .config import PROJECT_ROOT
from .llm import ask

# ---------------- 上下文 ----------------

def collect_context(state: dict) -> dict:
    """收集项目快照：git 状态、目录结构、README 摘要，作为计划与决策的上下文。"""
    parts: list[str] = []
    try:
        git = subprocess.run(
            ["git", "status", "--short"], cwd=PROJECT_ROOT,
            capture_output=True, text=True, timeout=30,
        )
        parts.append("## Git 工作区状态\n" + (git.stdout or "(clean)").strip())
    except Exception as e:  # noqa: BLE001
        parts.append(f"## Git 状态获取失败: {e}")

    # 目录结构（顶层 + 关键子目录一层）
    tree: list[str] = []
    for entry in sorted(PROJECT_ROOT.iterdir()):
        if entry.name.startswith(".") or entry.name in config.EXCLUDED_COMPILE_DIRS:
            continue
        if entry.is_dir():
            subs = [p.name for p in sorted(entry.iterdir())[:12] if not p.name.startswith(".")]
            tree.append(f"{entry.name}/ ({len(subs)} 项: {', '.join(subs)})")
        else:
            tree.append(entry.name)
    parts.append("## 项目结构\n" + "\n".join(tree))

    readme = PROJECT_ROOT / "README.md"
    if readme.exists():
        parts.append("## README 摘要\n" + readme.read_text(encoding="utf-8")[:1500])

    return {"project_context": "\n\n".join(parts)}


# ---------------- 计划 ----------------

def plan_node(state: dict) -> dict:
    system = (
        "你是小蜗（科大校园智能助手）项目的开发规划者。"
        "基于项目上下文与任务，输出结构化开发计划。"
        "计划必须包含：目标、影响模块（文件级）、实施步骤（编号，每步含验收标准）、风险与验证方式。"
        "用 Markdown 输出，控制在 800 字内。"
    )
    user = f"# 任务\n{state['task']}\n\n# 项目上下文\n{state.get('project_context', '')}"
    plan = ask(system, user)
    return {"plan": plan}


# ---------------- 执行 ----------------

def _run_cli(cmd: list[str], cwd: Path, timeout: int) -> str:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return out.strip()[-8000:]


def _hash_file(p: Path) -> str:
    """文件内容指纹（前 1MB 的 SHA-256 前缀），用于追踪 untracked 文件变化。"""
    if not p.exists() or p.is_dir():
        return "missing"
    try:
        with open(p, "rb") as fh:
            return hashlib.sha256(fh.read(1 << 20)).hexdigest()[:12]
    except OSError:
        return "unreadable"


def _git_snapshot() -> dict[str, str]:
    """工作区快照：{相对路径: 内容哈希}，覆盖 tracked 修改与 untracked 文件。"""
    snap: dict[str, str] = {}
    try:
        dl = subprocess.run(["git", "diff", "--name-only"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30)
        for f in (dl.stdout or "").splitlines():
            if f.strip():
                snap[f.strip()] = _hash_file(PROJECT_ROOT / f.strip())
    except Exception:  # noqa: BLE001
        pass
    try:
        ul = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30)
        for f in (ul.stdout or "").splitlines():
            if f.strip():
                snap[f.strip()] = _hash_file(PROJECT_ROOT / f.strip())
    except Exception:  # noqa: BLE001
        pass
    return snap


def execute_node(state: dict) -> dict:
    """按计划（或修改指令）调用 Claude/Codex CLI 执行。"""
    executor = state.get("executor", "claude")
    round_no = int(state.get("round", 0))

    if round_no == 1:
        instruction = state["plan"]
    else:
        instruction = state.get("fix_instruction") or state["plan"]

    prompt = (
        f"你在小蜗项目（{PROJECT_ROOT}）工作。请严格按下面的开发计划执行，"
        f"完成代码修改后简要说明改了什么文件与为什么。\n\n{instruction}\n\n"
        "【安全约束，必须遵守】\n"
        "1. 只允许创建或修改任务明确要求的文件，禁止改动其他任何文件；\n"
        "2. 禁止删除任何文件（包括未跟踪文件），除非任务明确要求删除；\n"
        "3. 禁止运行 git clean / git checkout -- / git restore / git stash / git reset --hard 等破坏性命令；\n"
        "4. 禁止修改 scripts/dev_pipeline/ 目录（开发流水线自身源码）与 .qoder/canvases/ 目录（报告产物）；\n"
        "5. 如果任务与这些约束冲突，只做任务允许的部分并在说明中注明。"
    )

    if executor == "claude":
        cmd = [
            config.CLAUDE_BIN, "-p", prompt,
            "--output-format", "text",
            "--permission-mode", "acceptEdits",
        ]
    elif executor == "codex":
        cmd = [
            config.CODEX_BIN, "exec",
            "-s", "workspace-write",
            prompt,
        ]
    else:
        raise ValueError(f"未知执行器: {executor}")

    # 变更摘要（执行前后内容哈希快照差集，排除项目历史未提交改动）
    before = _git_snapshot()
    output = _run_cli(cmd, PROJECT_ROOT, config.EXEC_TIMEOUT_SEC)
    after = _git_snapshot()

    new_files = sorted(f for f in after if f not in before)
    changed = sorted(f for f in after if f in before and after[f] != before[f])
    deleted = sorted(f for f in before if f not in after)
    touched = new_files + changed + deleted

    stat_lines: list[str] = []
    for f in new_files:
        try:
            fs = subprocess.run(
                ["git", "diff", "--stat", "--", f], cwd=PROJECT_ROOT,
                capture_output=True, text=True, timeout=30,
            )
            line = (fs.stdout or "").strip()
            stat_lines.append(line if line else f"{f} (new)")
        except Exception:  # noqa: BLE001
            stat_lines.append(f"{f} (new)")
    stat_lines.extend(f"{f} (modified)" for f in changed)
    stat_lines.extend(f"{f} (deleted)" for f in deleted)
    diff_stat = "\n".join(stat_lines) or "本轮无文件变更"

    changes = list(state.get("changes") or [])
    changes.append({
        "round": round_no,
        "executor": executor,
        "diff_stat": diff_stat,
        "files": touched,
    })

    return {
        "exec_output": output,
        "changes": changes,
        "diff_stat": diff_stat,
        "files": touched,
    }


# ---------------- 测试 ----------------

def test_node(state: dict) -> dict:
    """真实测试：运行项目测试脚本 + 全量语法编译检查。"""
    outputs: list[str] = []

    # 1. 项目测试脚本
    for script in config.TEST_SCRIPTS:
        if script.exists():
            try:
                outputs.append(f"$ python {script.name}")
                outputs.append(_run_cli([sys.executable, str(script)], PROJECT_ROOT, config.EXEC_TIMEOUT_SEC))
            except subprocess.TimeoutExpired:
                outputs.append(f"[超时] {script.name}")

    # 2. 语法编译检查（跳过数据/虚拟环境目录）
    py_files = [
        str(p) for p in PROJECT_ROOT.rglob("*.py")
        if not any(part in config.EXCLUDED_COMPILE_DIRS for part in p.parts)
    ]
    if py_files:
        try:
            cc = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", *py_files],
                cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120,
            )
            outputs.append(f"$ python -m compileall ({len(py_files)} 个 py 文件)")
            outputs.append("语法检查通过" if cc.returncode == 0 else f"语法错误:\n{cc.stderr[:2000]}")
        except subprocess.TimeoutExpired:
            outputs.append("[超时] compileall")

    return {"test_output": "\n\n".join(outputs)}


# ---------------- 报告 ----------------

def report_node(state: dict) -> dict:
    system = (
        "你是小蜗项目的开发报告撰写者。根据任务、计划、执行结果、测试输出，"
        "撰写一份简洁的开发报告（Markdown）。结构："
        "## 任务 / ## 计划要点 / ## 执行摘要（文件+变更）/ ## 测试结果 / ## 遗留问题。"
        "控制在 600 字内，实事求是，不夸大。"
    )
    user = (
        f"# 任务\n{state['task']}\n\n"
        f"# 计划\n{state.get('plan', '')}\n\n"
        f"# 执行输出\n{state.get('exec_output', '')[:3000]}\n\n"
        f"# 变更文件\n{', '.join(state.get('files') or [])}\n\n"
        f"# 测试输出\n{state.get('test_output', '')[:3000]}"
    )
    return {"report_md": ask(system, user)}


def canvas_node(state: dict) -> dict:
    """基于报告与结构化数据生成 Qoder canvas 报告（.canvas.tsx）。"""
    from .report_canvas import render_canvas

    path = render_canvas(state)
    return {"canvas_path": str(path)}


# ---------------- 决策 ----------------

def decide_node(state: dict) -> dict:
    system = (
        "你是小蜗开发循环的验收决策者。根据任务、执行与测试结果判断是否通过。"
        "输出 JSON（不要其他文字）："
        '{"verdict": "pass" 或 "fail", "issues": ["问题1", "问题2"], "fix_instruction": "给下一轮执行的修改指令（fail 时必填，具体到文件与做法；pass 时填空字符串）"}'
    )
    user = (
        f"# 任务\n{state['task']}\n\n"
        f"# 计划\n{state.get('plan', '')}\n\n"
        f"# 执行输出（最近一轮）\n{state.get('exec_output', '')[:2500]}\n\n"
        f"# 变更文件\n{', '.join(state.get('files') or [])}\n\n"
        f"# 测试输出\n{state.get('test_output', '')[:2500]}\n\n"
        f"# 当前轮次 {state.get('round', 1)} / 上限 {state.get('max_rounds', 3)}"
    )
    raw = ask(system, user, temperature=0.0)

    import json
    import re

    verdict, issues, fix = "fail", [], ""
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
        verdict = str(data.get("verdict", "fail")).lower()
        issues = [str(i) for i in data.get("issues", [])]
        fix = str(data.get("fix_instruction", ""))
    except Exception:  # noqa: BLE001
        if "pass" in raw.lower():
            verdict = "pass"

    update: dict = {"verdict": verdict, "issues": issues, "fix_instruction": fix}
    # fail 且未达轮次上限时递增 round，供下一轮 execute 使用
    if verdict != "pass":
        max_rounds = int(state.get("max_rounds", 3))
        round_no = int(state.get("round", 1))
        if round_no < max_rounds:
            update["round"] = round_no + 1
    return update
