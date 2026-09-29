#!/usr/bin/env python3
"""小蜗《运维一页纸》生成器（2026-09-29）。

给学校老师/接手同学看的单页说明：服务清单、启停命令、健康检查、备份策略、
故障处置三步（含"校园网被降级"的排查），并附带现场实测的关键状态。
同时输出 HTML（可打印成 PDF）与 Markdown 两个版本。

    python deploy/server/ops_onepager.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops_collect  # noqa: E402

ROOT = ops_collect.ROOT


def build() -> tuple[str, str]:
    snap = ops_collect.snapshot()
    svc = snap.get("services") or []
    health = snap.get("health") or {}
    bkp = (snap.get("backup") or {}).get("daily") or {}
    git = snap.get("git") or {}
    res = snap.get("resources") or {}

    svc_rows = "\n".join(
        f"| `{s['name']}` | {s['label']} | {s['port'] or '—'} | "
        f"`deploy/server/logs/{s['log']}` | {'关键' if s['critical'] else '辅助'} |"
        for s in svc)

    md = f"""# 小蜗 · 运维一页纸（生成于 {snap.get('generated_at')}）

部署目录：`{snap.get('root')}`　·　代码版本：`{git.get('head')}`
健康状态：**{health.get('status')}**　·　校外出网：**{'通' if all(e['ok'] for e in snap['egress'] if not e['internal']) else '不通'}**
　·　磁盘：{res.get('disk_used_pct')}%　·　每日备份最新：{bkp.get('newest')}

## 1. 服务清单（8 个，nohup + pidfile，无 systemd）

| 服务 | 作用 | 端口 | 日志 | 重要度 |
|---|---|---|---|---|
{svc_rows}

Docker sidecar（搜索/抓取）：`xiaowo-sidecars-searxng-1`、`xiaowo-sidecars-crawl4ai-adapter-1`、`xiaowo-sidecars-crawl4ai-upstream-1`

## 2. 日常命令（统一入口 `deploy/server/ops.sh`）

```bash
bash deploy/server/ops.sh                 # 终端总览（一屏看全部）
bash deploy/server/ops.sh html --open     # 生成并打开运维仪表盘（可投屏/打印）
bash deploy/server/ops.sh onepager        # 重新生成本页
bash deploy/server/ops.sh logs web 80     # 看某服务日志
bash deploy/server/ops.sh restart web     # 重启单个服务（all = 全量）
bash deploy/server/ops.sh net             # 立刻体检出网（必要时自动重开网络通）
bash deploy/server/ops.sh status          # 等价于 status.sh
bash deploy/server/ops.sh web             # 运维台 Web 地址/账号（端口转发看这个）
```
启停全部：`bash deploy/server/start_all.sh` / `bash deploy/server/stop_all.sh`

## 3. 健康检查

| 端点 | 用途 |
|---|---|
| 运维台 Web | `http://<服务器IP>:8899/`（Basic 认证；`/onepager` 一页纸、`/json` 原始快照） |
| `GET /api/v1/health/live` | 进程存活 |
| `GET /api/v1/health/ready` | 六项门槛：数据库 / 审核库 / 发布索引 / 搜索体检 / 联网证据 / 证据提取 |

六项里**前两项 + 联网证据 + 证据提取**是硬门槛；不 ready 时先按下面第 5 节排查。

## 4. 备份与数据

- 每日 **04:41** 自动备份（`deploy/server/backup_daily.sh`）：业务库 + 审核库 + 语义缓存 + 向量库 + 方案/脚本；
- 保留 **daily 7 份 + weekly 5 份**，自动清理；当前 daily 最新 `{bkp.get('newest')}`（{bkp.get('count')} 份 / {bkp.get('size_mb')} MB）；
- 恢复流程与数据事故 SOP：见 `docs/部署规格与记录_2026-09-01.md` §14/§16。

## 5. 故障处置三步

1. **看健康**：`curl -s http://127.0.0.1:8000/api/v1/health/ready` → 哪一项 false 就查对应依赖；
2. **看网络**：`bash deploy/server/ops.sh net`
   - 症状：校外全不通、校内正常 → 校园网「网络通」权限被降级（2026-09-27 实测过一次，
     门户日志会写"用户已无国内网络通权限，设置为 校内"）；
   - 处置：门户 `http://wlt.ustc.edu.cn` 登录后**重新开通**（1教育网出口 · 国际 · 永久）；
     `net_guard.py` 已在每 5 分钟自动体检并尝试自动重开，失败会写 `deploy/server/run/net_alert.txt`；
3. **看看护**：`deploy/server/run/watchdog.pid` 是否在跑（60s 探活 3 个服务并自动拉起）；
   日志 `deploy/server/logs/watchdog.log`、`sentinel.log`。

## 6. 已安装的运维加固（`bash deploy/server/install_ops.sh` 可重复执行）

- **开机自启**：`/etc/cron.d/xiaowo-autostart`（`@reboot` 等 30s 后执行 `start_all.sh`）；
- **日志轮转**：`/etc/logrotate.d/xiaowo`（每日、保留 14 天、`copytruncate`）；
- **出网巡检**：`net_guard` 服务（随 `start_all.sh` 启动，每 5 分钟体检，异常自动重开并留痕）。
"""
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>小蜗 · 运维一页纸</title><style>
body{{font:13px/1.7 "Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;color:#16222d;margin:32px auto;max-width:900px;padding:0 20px}}
h1{{font-size:22px;border-bottom:3px solid #034ea1;padding-bottom:8px}}
h2{{font-size:16px;margin-top:22px;color:#034ea1}}
table{{width:100%;border-collapse:collapse;margin:8px 0}}
th,td{{border:1px solid #cbd6df;padding:6px 8px;text-align:left;font-size:12px}}
th{{background:#eef3f7}}
code{{background:#eef3f7;padding:1px 5px;border-radius:4px;font-size:12px}}
pre{{background:#f6f8fa;border:1px solid #d6dee6;border-radius:8px;padding:10px;overflow:auto;font-size:12px}}
.kv{{color:#61707d}}
@media print{{body{{margin:0}}h2{{break-after:avoid}}table{{break-inside:avoid}}}}
</style></head><body>
<h1>小蜗 · 运维一页纸</h1>
<p class="kv">生成于 {snap.get('generated_at')}　·　部署目录 <code>{snap.get('root')}</code>　·　代码版本 <code>{git.get('head')}</code><br>
健康状态 <b>{health.get('status')}</b>　·　磁盘 {res.get('disk_used_pct')}%　·　每日备份最新 {bkp.get('newest')}（{bkp.get('size_mb')} MB）</p>
<h2>1. 服务清单（8 个，nohup + pidfile，无 systemd）</h2>
<table><tr><th>服务</th><th>作用</th><th>端口</th><th>日志</th><th>重要度</th></tr>
{''.join(f"<tr><td><code>{s['name']}</code></td><td>{s['label']}</td><td>{s['port'] or '—'}</td><td><code>deploy/server/logs/{s['log']}</code></td><td>{'关键' if s['critical'] else '辅助'}</td></tr>" for s in svc)}
</table>
<p class="kv">Docker sidecar：searxng（搜索，:8080）、crawl4ai-adapter（抓取，:11235）、crawl4ai-upstream</p>
<h2>2. 日常命令（统一入口 <code>deploy/server/ops.sh</code>）</h2>
<pre>bash deploy/server/ops.sh                 # 终端总览（一屏看全部）
bash deploy/server/ops.sh html --open     # 生成并打开运维仪表盘（可投屏/打印）
bash deploy/server/ops.sh onepager        # 重新生成本页
bash deploy/server/ops.sh logs web 80     # 看某服务日志
bash deploy/server/ops.sh restart web     # 重启单个服务（all = 全量）
bash deploy/server/ops.sh net             # 立刻体检出网（必要时自动重开网络通）</pre>
<h2>3. 健康检查</h2>
<p><code>GET /api/v1/health/live</code>（进程存活）、<code>GET /api/v1/health/ready</code>（六项门槛）。</p>
<h2>4. 备份与数据</h2>
<p>每日 <b>04:41</b> 自动备份，保留 daily 7 份 + weekly 5 份；恢复 SOP 见
<code>docs/部署规格与记录_2026-09-01.md</code> §14/§16。</p>
<h2>5. 故障处置三步</h2>
<ol>
<li><b>看健康</b>：<code>curl -s http://127.0.0.1:8000/api/v1/health/ready</code>，哪项 false 查哪项依赖；</li>
<li><b>看网络</b>：<code>bash deploy/server/ops.sh net</code>；校外全不通而校内正常 → 校园网「网络通」权限被降级
（门户 <code>http://wlt.ustc.edu.cn</code> 重新开通：1教育网出口 · 国际 · 永久）；<code>net_guard</code> 每 5 分钟自动体检并可自动重开；</li>
<li><b>看看护</b>：<code>deploy/server/run/watchdog.pid</code> 是否在跑，日志在 <code>logs/watchdog.log</code>、<code>logs/sentinel.log</code>。</li>
</ol>
<h2>6. 已安装的运维加固</h2>
<ul>
<li>开机自启：<code>/etc/cron.d/xiaowo-autostart</code>（<code>@reboot</code>）；</li>
<li>日志轮转：<code>/etc/logrotate.d/xiaowo</code>（每日 / 保留 14 天 / copytruncate）；</li>
<li>出网巡检：<code>net_guard</code>（随 start_all 启动，异常写 <code>run/net_alert.txt</code>）。</li>
</ul>
</body></html>
"""
    return md, html


def main() -> int:
    md, html = build()
    out_md = ROOT / "deploy" / "server" / "运维一页纸.md"
    out_html = ROOT / "deploy" / "server" / "运维一页纸.html"
    out_md.write_text(md, encoding="utf-8")
    out_html.write_text(html, encoding="utf-8")
    print(out_md)
    print(out_html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
