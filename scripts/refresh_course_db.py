# -*- coding: utf-8 -*-
"""评课快照一键重爬 SOP（P3-4）。

串联既有脚本：crawl_icourse list --force → detail --force ×N 分片
→ build_course_db → check_course_db（9/9）。断点续跑用 --from-stage 从指定
阶段重启；--dry-run 只打印命令；--check-only 跳过爬取只重建+校验。

执行时机：选课季前强制一次；平时可选。全量约数小时（列表 19194 门 +
详情 5667 页），日志落 scripts/data/refresh_*.log。

用法：
    py -3 scripts/refresh_course_db.py --dry-run          # 预览将执行的命令
    py -3 scripts/refresh_course_db.py                    # 全量重爬
    py -3 scripts/refresh_course_db.py --from-stage build # 断点续跑（跳过爬取）
    py -3 scripts/refresh_course_db.py --check-only       # 仅重建+校验当前快照
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
DATA_DIR = SCRIPTS / "data"
STAGES = ["preflight", "list", "detail", "build", "check"]


def _log_file() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"refresh_{time.strftime('%Y%m%d_%H%M%S')}.log"


def preflight() -> bool:
    """icourse.club 可达性探测（爬虫为公开页 requests 抓取，无需登录态）。"""
    import requests

    try:
        r = requests.get("https://icourse.club/", timeout=15,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        if r.status_code == 200:
            print(f"[preflight] icourse.club 可达（HTTP 200）")
            return True
        print(f"[preflight] 异常状态码 HTTP {r.status_code}，中止（避免半途报废缓存）")
        return False
    except Exception as e:
        print(f"[preflight] icourse.club 不可达: {e}")
        print("          请检查网络/代理后重试；无需 CDP 登录态（公开页抓取）")
        return False


def stage_commands(shards: int) -> dict[str, list[list[str]]]:
    """各阶段将执行的子命令（解释器 + 脚本 + 参数）。"""
    py = [sys.executable]
    return {
        "list": [py + [str(SCRIPTS / "crawl_icourse.py"), "list", "--force"]],
        "detail": [py + [str(SCRIPTS / "crawl_icourse.py"),
                         "detail", "--force", "--shard", str(i), "--shards", str(shards)]
                   for i in range(1, shards + 1)],
        "build": [py + [str(SCRIPTS / "build_course_db.py")]],
        "check": [py + [str(SCRIPTS / "check_course_db.py")]],
    }


def run(cmd: list[str], log_path: Path) -> int:
    """执行子命令：stdout 同屏 + 追加日志。"""
    print(f"\n$ {' '.join(cmd)}", flush=True)
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"\n$ {' '.join(cmd)}\n")
        lf.flush()
        proc = subprocess.run(cmd, cwd=str(SCRIPTS.parent))
        return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="评课快照一键重爬 SOP")
    ap.add_argument("--shards", type=int, default=4, help="detail 分片数（默认 4）")
    ap.add_argument("--from-stage", choices=STAGES, default="preflight",
                    help="断点续跑：从指定阶段开始（此前阶段跳过）")
    ap.add_argument("--check-only", action="store_true",
                    help="仅执行 build + check（跳过爬取，校验当前快照）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    args = ap.parse_args()

    cmds = stage_commands(args.shards)
    chain: list[tuple[str, list[str]]] = []
    if args.check_only:
        chain += [("build", c) for c in cmds["build"]] + [("check", c) for c in cmds["check"]]
    else:
        started = args.from_stage == "preflight"
        for st in STAGES:
            if st == args.from_stage:
                started = True
            if not started:
                continue
            if st == "preflight":
                chain.append(("preflight", []))
            else:
                chain += [(st, c) for c in cmds[st]]

    if args.dry_run:
        for st, c in chain:
            print(f"[dry-run] {st}: {' '.join(c) if c else '(内置可达性探测)'}")
        return 0

    log_path = _log_file()
    print(f"日志: {log_path}")
    t0 = time.time()
    for st, c in chain:
        if st == "preflight":
            if not preflight():
                return 1
            continue
        rc = run(c, log_path)
        if rc != 0:
            print(f"\n[ABORT] 阶段 {st} 失败（exit {rc}）——缓存保留，"
                  f"修复后用 --from-stage {st} 续跑")
            return rc
    print(f"\n[DONE] 全部阶段完成，用时 {time.time() - t0:.0f}s（{log_path.name}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
