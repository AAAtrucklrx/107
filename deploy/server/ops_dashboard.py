#!/usr/bin/env python3
"""小蜗运维仪表盘渲染（2026-09-29）。

把 `ops_collect.py` 的 JSON 快照渲染成**单文件 HTML**（不依赖任何外网资源，可双击打开、
可打印成 PDF 给学校老师看）。用法：

    python deploy/server/ops_dashboard.py [--out dashboard.html] [--refresh 30] [--snapshot x.json]

--refresh N 会写入 <meta http-equiv="refresh" content="N">，配合 `ops.sh watch`
（每 N 秒重新生成同一文件）即可自动刷新。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops_collect  # noqa: E402

OK = "#1a7f4b"
BAD = "#c0392b"
WARN = "#a96e17"
MUTED = "#61707d"


def _badge(text: str, ok: bool | None, warn: bool = False) -> str:
    color = OK if ok else (WARN if warn else BAD)
    return (f'<span class="badge" style="--c:{color}">'
            f'<span class="dot"></span>{html.escape(text)}</span>')


def _esc(v) -> str:
    return html.escape(str(v if v is not None else "-"))


def _card(title: str, body: str, sub: str = "") -> str:
    sub_html = f'<div class="sub">{html.escape(sub)}</div>' if sub else ""
    return f'<section class="card"><h2>{html.escape(title)}{sub_html}</h2>{body}</section>'


def render(snap: dict, refresh: int = 0) -> str:
    services = snap.get("services") or []
    alive = [s for s in services if s.get("alive")]
    crit_down = [s for s in services if s.get("critical") and not s.get("alive")]
    health = snap.get("health") or {}
    checks = health.get("checks") or []
    egress = snap.get("egress") or []
    external = [e for e in egress if not e.get("internal")]
    net_bad = [e for e in external if not e.get("ok")]
    portal = snap.get("portal") or {}
    backup = snap.get("backup") or {}
    daily = (backup.get("daily") or {})
    resources = snap.get("resources") or {}
    sentinel = snap.get("sentinel") or {}
    git = snap.get("git") or {}
    traffic = snap.get("traffic") or {}

    # ── 总览徽章 ─────────────────────────────────────────────
    summary = [
        _badge(f"服务 {len(alive)}/{len(services)} 运行", not crit_down,
               warn=bool(crit_down) and len(alive) == len(services)),
        _badge(f"健康门槛 {'就绪' if health.get('status') == 'ready' else '未就绪'}",
               health.get("status") == "ready"),
        _badge(f"校外出网 {'通' if not net_bad else '不通'}", not net_bad),
        _badge(f"搜索体检 {'正常' if sentinel.get('search_ok') else '异常'}",
               bool(sentinel.get("search_ok"))),
        _badge(f"备份 {daily.get('newest') or '无'}",
               (daily.get("age_hours") or 999) < 30),
    ]

    # ── 健康门槛 ─────────────────────────────────────────────
    if checks:
        chips = "".join(_badge(f"{c.get('label')}", bool(c.get("ok"))) for c in checks)
    else:
        chips = _badge("无法读取 /health/ready", False)
    health_body = (f'<div class="chips">{chips}</div>'
                   f'<p class="kv">live=<b>{_esc(health.get("live"))}</b> · version=<b>{_esc(health.get("version"))}</b>'
                   f' · ready=<b>{_esc(health.get("status"))}</b></p>')
    if health.get("error"):
        health_body += f'<p class="err">读取健康端点报错：{_esc(health["error"])}</p>'

    # ── 服务表 ───────────────────────────────────────────────
    rows = []
    for s in services:
        state = _badge("运行中", True) if s.get("alive") else _badge("已停止", False)
        port = (str(s["port"]) if s.get("port") else "—") + (
            "" if s.get("port_open", True) or not s.get("alive") else " ⚠未监听")
        rows.append(
            "<tr>"
            f'<td><b>{_esc(s.get("name"))}</b><div class="muted">{_esc(s.get("label"))}</div></td>'
            f'<td>{state}</td><td class="num">{_esc(s.get("pid"))}</td>'
            f'<td class="num">{_esc(s.get("uptime"))}</td><td class="num">{_esc(port)}</td>'
            f'<td class="num">{round((s.get("log_size") or 0) / 1e6, 1)} MB</td>'
            "</tr>")
    services_body = ("<table><thead><tr><th>服务</th><th>状态</th><th>PID</th><th>运行时长</th>"
                     "<th>端口</th><th>日志</th></tr></thead><tbody>"
                     + "".join(rows) + "</tbody></table>")

    # ── 依赖与网络 ───────────────────────────────────────────
    crow = "".join(
        f'<tr><td>{_esc(c.get("name"))}</td><td>{_badge(_esc(c.get("status")), bool(c.get("healthy")), warn="unhealthy" not in str(c.get("status")))}</td></tr>'
        for c in (snap.get("containers") or []))
    cont_body = ("<table><thead><tr><th>Docker 容器</th><th>状态</th></tr></thead><tbody>"
                 + crow + "</tbody></table>")
    erow = "".join(
        f'<tr><td>{_esc(e.get("label"))}</td>'
        f'<td>{_badge(("可达 " + str(e.get("code"))) if e.get("ok") else (e.get("error") or "超时"), bool(e.get("ok")))}</td>'
        f'<td class="num">{_esc(e.get("latency_ms"))} ms</td></tr>'
        for e in egress)
    portal_line = ""
    if portal.get("known", True) and portal.get("exit"):
        portal_line = (f'<p class="kv">校园网「网络通」：出口 <b>{_esc(portal.get("exit"))}</b> · '
                       f'权限 <b>{_esc(portal.get("scope"))}</b> · 检查于 {_esc(portal.get("checked_at"))}</p>')
    egress_body = (f'<table><thead><tr><th>探测目标</th><th>结果</th><th>耗时</th></tr></thead><tbody>{erow}</tbody></table>'
                   + portal_line)

    # ── 备份 ─────────────────────────────────────────────────
    def _bk(d: dict) -> str:
        return (f'<p class="kv">{_badge("近期", (d.get("age_hours") or 999) < 30)} '
                f'共 <b>{_esc(d.get("count"))}</b> 份 · 最新 <b>{_esc(d.get("newest"))}</b> · '
                f'距今 <b>{_esc(d.get("age_hours"))}</b> 小时 · 体积 <b>{_esc(d.get("size_mb"))}</b> MB</p>')
    backup_body = ("<h3>每日</h3>" + _bk(daily) + "<h3>每周</h3>" + _bk(backup.get("weekly") or {})
                   + f'<p class="muted">最近一条备份日志：{_esc(backup.get("last_log"))}</p>')

    # ── 资源 ─────────────────────────────────────────────────
    mem_pct = 0
    if resources.get("mem_total_mb"):
        mem_pct = round((1 - resources["mem_avail_mb"] / resources["mem_total_mb"]) * 100, 1)
    bar = lambda pct, color: (f'<div class="bar"><span style="width:{max(0, min(100, pct))}%;'
                              f'background:{color}"></span></div>')
    res_body = (
        f'<p class="kv">负载 <b>{_esc(resources.get("load"))}</b>（{_esc(resources.get("cpu_count"))} 核） · '
        f'内存 <b>{_esc(resources.get("mem_avail_mb"))}</b> / {_esc(resources.get("mem_total_mb"))} MB 可用（{mem_pct}% 已用）</p>'
        + bar(mem_pct, OK if mem_pct < 80 else BAD)
        + f'<p class="kv">磁盘 <b>{_esc(resources.get("disk_used_gb"))}</b> / {_esc(resources.get("disk_total_gb"))} GB'
          f'（{_esc(resources.get("disk_used_pct"))}%）</p>'
        + bar(resources.get("disk_used_pct") or 0, OK if (resources.get("disk_used_pct") or 0) < 80 else BAD))

    # ── 代码 / 使用量 / 数据 ─────────────────────────────────
    code_body = (f'<p class="kv">当前版本：<b>{_esc(git.get("head"))}</b> · '
                 f'未提交改动 <b>{_esc(git.get("dirty"))}</b> 个文件 · 未跟踪 <b>{_esc(git.get("untracked"))}</b> 项</p>'
                 f'<p class="kv">累计消息 <b>{_esc(traffic.get("messages"))}</b> 条 · 会话 '
                 f'<b>{_esc(traffic.get("conversations"))}</b> 个 · 最近活动 <b>{_esc(traffic.get("latest"))}</b></p>')
    drow = "".join(f'<tr><td>{_esc(d.get("path"))}</td><td class="num">{_esc(d.get("size_mb"))} MB</td></tr>'
                   for d in (snap.get("data_files") or []))
    data_body = ("<table><thead><tr><th>数据文件</th><th>体积</th></tr></thead><tbody>"
                 + drow + "</tbody></table>")

    # ── 最近错误 ─────────────────────────────────────────────
    errs = snap.get("recent_errors") or []
    if errs:
        blocks = []
        for e in errs:
            samples = "".join(f"<li>{_esc(s)}</li>" for s in (e.get("samples") or []))
            blocks.append(f'<details><summary>{_esc(e.get("name"))} —— 近 400 行命中 '
                          f'{_esc(e.get("count"))} 条</summary><ul>{samples}</ul></details>')
        err_html = "".join(blocks)
    else:
        err_html = '<p class="muted">各服务日志近 400 行内未命中 ERROR / 超时 / 异常关键词。</p>'

    refresh_meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ""
    css = """
:root{--ink:#16222d;--muted:#61707d;--line:#d6dee6;--bg:#f4f7fa;--card:#fff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.6 "Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif}
header{padding:20px 28px;background:#034ea1;color:#fff}
header h1{margin:0;font-size:20px}
header .meta{opacity:.85;font-size:12px;margin-top:4px}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:0}
.badge{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--c);color:var(--c);
  border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600;background:#fff}
.badge .dot{width:7px;height:7px;border-radius:50%;background:var(--c)}
main{padding:18px 28px 40px;display:grid;gap:16px;grid-template-columns:repeat(auto-fit,minmax(420px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card h2{margin:0 0 10px;font-size:15px;border-bottom:1px solid var(--line);padding-bottom:8px}
.card h2 .sub{font-weight:400;color:var(--muted);font-size:12px;margin-left:8px}
.card h3{margin:10px 0 4px;font-size:13px;color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:#eef3f7;color:var(--muted);font-size:12px}
td.num{text-align:right;white-space:nowrap}
.muted{color:var(--muted);font-size:12px}
.kv{margin:6px 0}
.bar{height:8px;background:#e8eef3;border-radius:6px;overflow:hidden;margin:4px 0 10px}
.bar span{display:block;height:100%}
.err{color:#c0392b}
details{margin:6px 0}
summary{cursor:pointer;font-weight:600}
ul{margin:6px 0 0 18px;padding:0}
li{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12px;color:#33414d}
footer{padding:0 28px 28px;color:var(--muted);font-size:12px}
@media print{body{background:#fff}header{background:#034ea1 !important;-webkit-print-color-adjust:exact}
  main{grid-template-columns:1fr 1fr}.card{break-inside:avoid}}
"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">{refresh_meta}
<title>小蜗运维台 · {html.escape(snap.get("generated_at", ""))}</title><style>{css}</style></head>
<body>
<header>
  <h1>小蜗 · 运维台</h1>
  <div class="meta">生成时间 {html.escape(snap.get("generated_at", ""))} · 部署目录 {html.escape(snap.get("root", ""))}
   · 本页由 deploy/server/ops.sh 生成（只读采集，不触碰任何服务）</div>
  <div class="chips" style="margin-top:10px">{"".join(summary)}</div>
</header>
<main>
  {_card("健康门槛", health_body, "GET /api/v1/health/ready 六项")}
  {_card("服务", services_body, f"{len(alive)}/{len(services)} 运行")}
  {_card("依赖容器", cont_body, "sidecar（搜索 / 证据抓取）")}
  {_card("出网与校园网", egress_body, "校外不通时先查「网络通」权限")}
  {_card("备份", backup_body, "每日 04:41 · daily 7 份 + weekly 5 份")}
  {_card("资源", res_body)}
  {_card("代码与使用量", code_body)}
  {_card("数据文件", data_body)}
  {_card("最近错误", err_html, "仅统计日志关键词，不代表服务故障")}
</main>
<footer>
  常用：<code>bash deploy/server/ops.sh</code>（终端总览） ·
  <code>ops.sh html</code>（生成本页） · <code>ops.sh watch 30</code>（自动刷新） ·
  <code>ops.sh logs web 80</code>（看日志） · <code>ops.sh restart web</code>（重启单服务）<br>
  故障处置三步见《运维一页纸》：readiness 是否 ready → 出网是否通（网络通权限）→ 看护是否在跑（watchdog）。
</footer>
</body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="渲染小蜗运维仪表盘（单文件 HTML）")
    ap.add_argument("--out", default="deploy/server/ops_dashboard.html")
    ap.add_argument("--snapshot", help="用已有 JSON 快照渲染（默认现场采集）")
    ap.add_argument("--refresh", type=int, default=0, help="自动刷新秒数（配合 ops.sh watch）")
    args = ap.parse_args()
    snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8")) if args.snapshot else ops_collect.snapshot()
    out = Path(args.out)
    if not out.is_absolute():
        out = ops_collect.ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(snap, refresh=args.refresh), encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
