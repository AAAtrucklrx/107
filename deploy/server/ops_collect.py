#!/usr/bin/env python3
"""小蜗运维快照采集（2026-09-29）。

把「服务 / 端口 / 容器 / 健康门槛 / 哨兵 / 出网 / 备份 / 资源 / 代码版本 / 使用量 / 最近错误」
汇总成一份 JSON，供 `ops_dashboard.py` 渲染单文件 HTML、`ops.sh` 打印终端总览。

只读：不启停任何服务、不写业务数据；唯一的写动作是把快照写到 --out（默认 stdout）。
只用标准库（不依赖 psutil 等第三方包），便于在任何环境直接跑。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import socket
import subprocess
import sqlite3
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "deploy" / "server" / "run"
LOG_DIR = ROOT / "deploy" / "server" / "logs"

# name, 中文说明, pidfile, 端口(可空), 日志, 是否关键
SERVICES = [
    ("web", "Web 主服务（SPA + API + SSE）", "web.pid", 8000, "web.log", True),
    ("worker", "审核 / 发布 worker", "worker.pid", None, "worker.log", True),
    ("streamlit", "Streamlit 回退入口", "streamlit.pid", 8502, "streamlit.log", False),
    ("watchdog", "看护循环（60s 探活并拉起）", "watchdog.pid", None, "watchdog.log", True),
    ("sentinel", "哨兵（5min 搜索/索引体检）", "sentinel.pid", None, "sentinel.log", True),
    ("net_guard", "出网守卫（5min 体检+自动重开网络通）", "net_guard.pid", None, "net_guard.log", True),
    ("ops_web", "运维台 Web（本页服务）", "ops_web.pid", 8899, "ops_web.log", False),
    ("backup", "每日备份循环（04:41）", "backup.pid", None, "backup.log", True),
    ("icourse_loop", "评课数据增量刷新", "icourse_loop.pid", None, "icourse_refresh.log", False),
    ("official_collect", "官网/公众号采集", "official_collect.pid", None, "official_collect.log", False),
]

CONTAINERS = ["xiaowo-sidecars-searxng-1", "xiaowo-sidecars-crawl4ai-adapter-1",
              "xiaowo-sidecars-crawl4ai-upstream-1"]

# 出网探测目标：校内（应始终可达）与校外（依赖校园网「网络通」权限）
EGRESS_TARGETS = [
    ("ustc", "校内 www.ustc.edu.cn", "https://www.ustc.edu.cn", True),
    ("deepseek", "LLM api.deepseek.com", "https://api.deepseek.com", False),
    ("baidu", "搜索 www.baidu.com", "https://www.baidu.com", False),
    ("qianfan", "千帆搜索 API", "https://qianfan.baidubce.com", False),
]


def _run(cmd: list[str], timeout: float = 10.0) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001 — 采集失败不该让整份快照挂掉
        return ""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def _etime(pid: int) -> str:
    return _run(["ps", "-o", "etime=", "-p", str(pid)]) or "-"


def collect_services() -> list[dict]:
    rows = []
    for name, label, pidfile, port, logfile, critical in SERVICES:
        pf = RUN_DIR / pidfile
        pid = None
        if pf.is_file():
            try:
                pid = int(pf.read_text(encoding="utf-8").strip())
            except Exception:  # noqa: BLE001
                pid = None
        alive = bool(pid and _pid_alive(pid))
        entry = {
            "name": name, "label": label, "pid": pid, "alive": alive,
            "uptime": _etime(pid) if alive else "-",
            "port": port, "log": logfile, "critical": critical,
            "log_size": (LOG_DIR / logfile).stat().st_size if (LOG_DIR / logfile).is_file() else 0,
        }
        if port:
            entry["port_open"] = _port_open(port)
        rows.append(entry)
    return rows


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def collect_containers() -> list[dict]:
    out = _run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"])
    rows = []
    seen = {}
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            seen[parts[0]] = parts[1]
    for name in CONTAINERS:
        status = seen.get(name, "未找到")
        rows.append({"name": name, "status": status, "healthy": "healthy" in status.lower() or "up" in status.lower()})
    return rows


def _http_json(url: str, timeout: float = 20.0) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — 仅本机回环
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return {"_error": f"{type(e).__name__}: {e}"}


def collect_health() -> dict:
    live = _http_json("http://127.0.0.1:8000/api/v1/health/live", timeout=5.0) or {}
    ready = _http_json("http://127.0.0.1:8000/api/v1/health/ready", timeout=40.0) or {}
    checks = ready.get("checks") or {}
    labels = {
        "database": "主数据库", "review_database": "审核库", "approved_index": "发布索引",
        "search_quality": "搜索体检", "web_evidence": "联网证据", "evidence_extractor": "证据提取",
    }
    return {
        "live": live.get("status") or "-",
        "version": live.get("version") or "-",
        "status": ready.get("status") or "-",
        "checks": [{"key": k, "label": labels.get(k, k), "ok": bool(v)} for k, v in checks.items()],
        "error": ready.get("_error"),
    }


def collect_egress() -> list[dict]:
    rows = []
    for key, label, url, internal in EGRESS_TARGETS:
        t0 = time.time()
        code = None
        err = ""
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=6.0) as resp:  # noqa: S310
                code = resp.status
        except Exception as e:  # noqa: BLE001 — 4xx/5xx 也算可达（连接成功）
            text = str(e)
            m = re.search(r"\b(\d{3})\b", text)
            code = int(m.group(1)) if m else None
            err = "" if code else type(e).__name__
        rows.append({
            "key": key, "label": label, "internal": internal,
            "ok": code is not None, "code": code, "error": err,
            "latency_ms": int((time.time() - t0) * 1000),
        })
    return rows


def collect_portal() -> dict:
    """校园网「网络通」当前出口/权限（读 net_guard 写下的状态文件，缺失则标未知）。"""
    path = RUN_DIR / "net_state.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"known": False}
    return {"known": False}


def collect_sentinel() -> dict:
    path = RUN_DIR / "sentinel_state.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return {}


def collect_resources() -> dict:
    load = os.getloadavg()
    mem_total = mem_avail = 0
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    mem_total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    mem_avail = int(line.split()[1]) // 1024
    except OSError:
        pass
    du = shutil.disk_usage("/")
    return {
        "load": [round(x, 2) for x in load],
        "cpu_count": os.cpu_count() or 0,
        "mem_total_mb": mem_total, "mem_avail_mb": mem_avail,
        "disk_total_gb": round(du.total / 1e9, 1), "disk_used_gb": round(du.used / 1e9, 1),
        "disk_used_pct": round(du.used / du.total * 100, 1),
    }


def collect_backup() -> dict:
    daily = ROOT / "data" / "backups" / "daily"
    weekly = ROOT / "data" / "backups" / "weekly"
    def _info(path: Path) -> dict:
        if not path.is_dir():
            return {"count": 0, "newest": None, "age_hours": None, "size_mb": 0}
        items = sorted([p for p in path.iterdir() if p.is_dir()], key=lambda p: p.name)
        newest = items[-1] if items else None
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) if path.is_dir() else 0
        age = None
        if newest is not None:
            age = round((time.time() - newest.stat().st_mtime) / 3600, 1)
        return {"count": len(items), "newest": newest.name if newest else None,
                "age_hours": age, "size_mb": round(size / 1e6, 1)}
    last_line = ""
    log = LOG_DIR / "backup.log"
    if log.is_file():
        try:
            last_line = log.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1][:200]
        except Exception:  # noqa: BLE001
            last_line = ""
    return {"daily": _info(daily), "weekly": _info(weekly), "last_log": last_line}


def collect_git() -> dict:
    return {
        "head": _run(["git", "-C", str(ROOT), "log", "-1", "--oneline"]),
        "dirty": len([l for l in _run(["git", "-C", str(ROOT), "status", "--porcelain"]).splitlines()
                      if not l.startswith("??")]),
        "untracked": len([l for l in _run(["git", "-C", str(ROOT), "status", "--porcelain"]).splitlines()
                          if l.startswith("??")]),
    }


def collect_traffic() -> dict:
    db = ROOT / "database" / "xiaowo.db"
    out = {"messages": 0, "conversations": 0, "latest": "-"}
    if not db.is_file():
        return out
    try:
        conn = sqlite3.connect(str(db), timeout=5)
        out["messages"] = conn.execute("select count(*) from web_messages").fetchone()[0]
        out["conversations"] = conn.execute("select count(*) from web_conversations").fetchone()[0]
        latest = conn.execute("select max(created_at) from web_messages").fetchone()[0]
        if latest:
            out["latest"] = time.strftime("%m-%d %H:%M", time.localtime(float(latest)))
        conn.close()
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:120]
    return out


def collect_recent_errors(limit: int = 3) -> list[dict]:
    rows = []
    for name, _label, _pid, _port, logfile, _crit in SERVICES:
        path = LOG_DIR / logfile
        if not path.is_file():
            continue
        try:
            tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
        except Exception:  # noqa: BLE001
            continue
        hits = [l for l in tail if re.search(r"ERROR|Traceback|失败|异常|timeout|timed out", l, re.I)]
        if hits:
            rows.append({"name": name, "count": len(hits), "samples": [h[:180] for h in hits[-limit:]]})
    return rows


def collect_data_files() -> list[dict]:
    files = [
        ROOT / "database" / "xiaowo.db", ROOT / "data" / "course_data.db",
        ROOT / "data" / "review.db", ROOT / "data" / "semantic_cache.db",
    ]
    rows = []
    for f in files:
        rows.append({"path": str(f.relative_to(ROOT)), "exists": f.is_file(),
                     "size_mb": round(f.stat().st_size / 1e6, 1) if f.is_file() else 0})
    chroma = ROOT / "knowledge" / "chroma_db"
    size = sum(p.stat().st_size for p in chroma.rglob("*") if p.is_file()) if chroma.is_dir() else 0
    rows.append({"path": "knowledge/chroma_db", "exists": chroma.is_dir(), "size_mb": round(size / 1e6, 1)})
    return rows


def snapshot() -> dict:
    """并发采集各维度（2026-09-29：串行时 readiness 40s + 出网 4×6s 会让页面等近 1 分钟）。"""
    jobs = {
        "services": collect_services, "containers": collect_containers, "health": collect_health,
        "egress": collect_egress, "portal": collect_portal, "sentinel": collect_sentinel,
        "resources": collect_resources, "backup": collect_backup, "git": collect_git,
        "traffic": collect_traffic, "data_files": collect_data_files,
        "recent_errors": collect_recent_errors,
    }
    out: dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {pool.submit(fn): name for name, fn in jobs.items()}
        for fut in concurrent.futures.as_completed(futures):
            name = futures[fut]
            try:
                out[name] = fut.result()
            except Exception as e:  # noqa: BLE001 — 单项失败不影响整份快照
                out[name] = {"_error": f"{type(e).__name__}: {e}"}
    out.update({
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generated_ts": time.time(),
        "root": str(ROOT),
    })
    return out


def net_ok() -> bool:
    """供 net_guard / watchdog 复用的轻量出网判定。"""
    for _key, _label, url, internal in EGRESS_TARGETS:
        if internal:
            continue
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=6.0):  # noqa: S310
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="采集小蜗运维快照")
    ap.add_argument("--out", help="写入文件（默认打印到 stdout）")
    ap.add_argument("--net-only", action="store_true", help="只做出网判定，输出 ok/ng")
    args = ap.parse_args()
    if args.net_only:
        print("ok" if net_ok() else "ng")
        return 0
    data = json.dumps(snapshot(), ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(data, encoding="utf-8")
    else:
        print(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
