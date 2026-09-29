#!/usr/bin/env python3
"""小蜗审核积压 dry-run 报告（2026-09-29）。

背景：2026-09-27 校园网降级导致 LLM 不可达，入库任务的「清洗 + AI 预审」反复失败 →
重试耗尽变 `dead`（实测 504 个），它们的草稿永久留在待审队列。运行期开关
`review_auto_approve` 已是 true，但**只对新入库生效**，存量不会自动补。

本脚本**只读**（审核库以 `mode=ro` 打开，绝不写数据），产出：
  ① 待审规模与分布（namespace × category × 来源级别）
  ② 自动批准的三道门槛漏斗：类别白名单 → 来源等级 official_primary → AI 四项判定
  ③ dead 入库任务的失败归因（错误码 + 时间分布）
  ④ 可选 `--sample N`：对候选条目真跑一次 AI 预审，估计通过率
  ⑤ 写入 Markdown 报告（默认 deploy/server/审核积压_dryrun_<日期>.md）

用法：
    python deploy/server/review_backfill.py                      # 全量报告（不调 LLM）
    python deploy/server/review_backfill.py --sample 10          # 另抽样 10 条跑 AI 预审
    python deploy/server/review_backfill.py --sample 5 --out /tmp/r.md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "review.db"
sys.path.insert(0, str(ROOT))

# 门槛直接取 live 政策（2026-09-29 已放宽），避免报告与实现漂移
try:
    from xiaowo_web.worker.pre_review import (
        _AUTO_APPROVE_CATEGORIES as AUTO_CATEGORIES,
        _AUTO_APPROVE_LEVELS as AUTO_LEVELS,
        _AUTO_APPROVE_TTL_DAYS as AUTO_TTL,
        _STABLE_REQUIRED_CATEGORIES as STABLE_REQUIRED,
    )
except Exception:  # noqa: BLE001 — 报告脚本不因导入失败而不可用
    AUTO_CATEGORIES, AUTO_LEVELS = ("policy", "stable_general"), ("official_primary",)
    AUTO_TTL, STABLE_REQUIRED = {"policy": 90, "stable_general": 90}, ("policy", "stable_general")
CST = 8 * 3600


def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _fmt(ts) -> str:
    try:
        return time.strftime("%m-%d %H:%M", time.localtime(float(ts) + CST - time.timezone + 0))
    except Exception:  # noqa: BLE001
        return "-"


def _fmt_day(ts) -> str:
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(float(ts) + CST))
    except Exception:  # noqa: BLE001
        return "-"


def collect() -> dict:
    conn = _connect_ro()
    out: dict = {}
    out["chunks"] = {r["approval_status"]: r["n"] for r in conn.execute(
        "select approval_status, count(*) n from review_chunks group by approval_status")}
    out["items"] = [dict(r) for r in conn.execute(
        "select namespace, status, count(*) n from review_items group by namespace, status")]
    # 来源级别：job payload_json 里的 level（按 snapshot_id 关联）
    snap_level: dict[str, str] = {}
    for r in conn.execute("select payload_json from ingestion_jobs"):
        try:
            p = json.loads(r["payload_json"])
        except Exception:  # noqa: BLE001
            continue
        sid = str(p.get("snapshot_id") or "")
        if sid:
            snap_level[sid] = str(p.get("level") or "")
    drafts = [dict(r) for r in conn.execute(
        "select item_id, namespace, category, status, snapshot_id, title, created_at "
        "from review_items where status in ('draft','in_review')")]
    for d in drafts:
        d["level"] = snap_level.get(str(d.get("snapshot_id") or ""), "")
    # 来源等级重建（2026-09-29）：历史条目的 level 随 job 记录丢失 → 用 snapshot URL 反查来源信任表
    try:
        sys.path.insert(0, str(ROOT))
        from xiaowo_web.evidence.trust import SourceTrustStore
        trust = SourceTrustStore()
        urls = {r["snapshot_id"]: (r["normalized_url"] or r["final_url"] or "")
                for r in conn.execute("select snapshot_id, normalized_url, final_url from web_snapshots")}
        rebuilt = 0
        for d in drafts:
            if str(d.get("level") or "").strip():
                continue
            url = urls.get(str(d.get("snapshot_id") or ""), "")
            if not url:
                continue
            try:
                decision = trust.classify_url_without_dns(url)
                lvl = str(getattr(decision, "level", "") or "")
            except Exception:  # noqa: BLE001
                lvl = ""
            if lvl:
                d["level"] = lvl
                d["level_rebuilt"] = True
                rebuilt += 1
        out["level_rebuilt"] = rebuilt
    except Exception as e:  # noqa: BLE001
        out["level_rebuilt"] = 0
        out["level_rebuild_error"] = f"{type(e).__name__}: {e}"

    out["drafts"] = drafts
    out["matrix"] = Counter((d["namespace"], d["category"], d["level"] or "(未知)") for d in drafts)
    out["dead_jobs"] = [dict(r) for r in conn.execute(
        "select last_error_code, created_at, updated_at, namespace from ingestion_jobs where status='dead'")]
    out["jobs"] = {r["status"]: r["n"] for r in conn.execute(
        "select status, count(*) n from ingestion_jobs group by status")}
    out["generations"] = [dict(r) for r in conn.execute(
        "select namespace, generation_id, created_at from publish_generations "
        "order by created_at desc limit 6")]
    out["active"] = [dict(r) for r in conn.execute("select * from active_index_state")]
    out["runtime"] = {(r["namespace"], r["key"]): r["value"] for r in conn.execute(
        "select * from review_runtime_settings")}
    conn.close()
    return out


def _level_ok(level: str) -> bool:
    return str(level or "").strip().casefold() in AUTO_LEVELS


def funnel(data: dict) -> dict:
    drafts = data["drafts"]
    by_cat = [d for d in drafts if d["category"] in AUTO_CATEGORIES]
    by_level = [d for d in by_cat if _level_ok(d.get("level"))]
    return {
        "total": len(drafts),
        "category_ok": len(by_cat),
        "level_ok": len(by_level),
        "candidates": by_level,
    }


def sample_review(candidates: list[dict], n: int) -> list[dict]:
    """对候选条目真跑一次 AI 预审（只读；neighbors 传空 → 重复/冲突判定偏保守）。"""
    from xiaowo_web.settings import WebSettings
    from xiaowo_web.worker.pre_review import PreReviewer
    settings = WebSettings.from_env()
    if not getattr(settings, "review_pre_review", False):
        return [{"error": "settings.review_pre_review 为 false，预审未启用"}]
    if not (settings.evidence_extractor_model or "").strip():
        return [{"error": "未配置 evidence_extractor_model"}]
    reviewer = PreReviewer(settings.evidence_extractor_model)
    conn = _connect_ro()
    picks: list[dict] = []
    # 跨 namespace/类别轮流取样，避免全落在同一类
    buckets: dict[tuple, list[dict]] = {}
    for c in candidates:
        buckets.setdefault((c["namespace"], c["category"]), []).append(c)
    idx = 0
    while len(picks) < n and any(buckets.values()):
        for key, bucket in list(buckets.items()):
            if bucket and len(picks) < n:
                picks.append(bucket.pop(0))
        idx += 1
        if idx > 100:
            break
    results = []
    for item in picks:
        row = conn.execute(
            "select content_text from review_versions rv join review_items ri on ri.item_id=rv.item_id "
            "where rv.item_id=? and rv.version_number=ri.current_version",
            (item["item_id"],)).fetchone()
        text = (row["content_text"] if row else "") or ""
        try:
            v = reviewer.review(text[:6000], {"level": item["level"], "category": item["category"]},
                                neighbors=[])
            results.append({
                "namespace": item["namespace"], "category": item["category"], "level": item["level"],
                "title": (item.get("title") or "")[:60], "chars": len(text),
                "stability": v.stability, "sensitivity": v.sensitivity, "duplication": v.duplication,
                "relevance": v.relevance,
                "eligible": v.auto_approve_eligible(level=item["level"], category=item["category"]),
                "judged": v.judged, "reason": (v.reason or "")[:120],
                "fallback": v.fallback_reason or "",
            })
        except Exception as e:  # noqa: BLE001
            results.append({"namespace": item["namespace"], "category": item["category"],
                            "title": (item.get("title") or "")[:60], "error": f"{type(e).__name__}: {e}"})
    conn.close()
    return results


def render(data: dict, fn: dict, samples: list[dict]) -> str:
    d = data
    lines: list[str] = []
    lines.append(f"# 小蜗审核积压 dry-run 报告（{time.strftime('%Y-%m-%d %H:%M')}）\n")
    lines.append("> 只读报告：审核库以 `mode=ro` 打开，未修改任何数据。\n")

    lines.append("## 1. 规模概览\n")
    ck = d["chunks"]
    lines.append(f"- 审核块：pending **{ck.get('pending', 0)}** · approved {ck.get('approved', 0)} · rejected {ck.get('rejected', 0)}")
    tot = sum(r["n"] for r in d["items"])
    lines.append(f"- 条目合计 **{tot}**：")
    for r in sorted(d["items"], key=lambda x: -x["n"]):
        lines.append(f"  - {r['namespace']} · {r['status']}：{r['n']}")
    lines.append(f"- 入库任务：{d['jobs']}")
    rt = d.get("runtime") or {}
    lines.append(f"- 运行期设置：`review_auto_approve` = "
                 f"{rt.get(('demo','review_auto_approve')) or rt.get(('production','review_auto_approve')) or '(未设置)'}"
                 "（只对新入库生效，存量需回填）")
    lines.append("")

    lines.append("## 2. 自动批准门槛漏斗\n")
    lines.append("现行规则（2026-09-17 定版；**2026-09-29 用户决定放宽为激进档**）："
                 f"分类 ∈ `{'` / `'.join(AUTO_CATEGORIES)}`；"
                 f"`{'` / `'.join(STABLE_REQUIRED)}` 必须 stable，其余两类允许 volatile（靠短 TTL 过期）；"
                 f"来源等级 `{'` / `'.join(l or '未知' for l in AUTO_LEVELS)}`（**不再卡等级**）；"
                 "安全与相关仍严格：`sensitive=clean`、`on_topic`、`unique`。"
                 f"TTL：{AUTO_TTL}。\n")
    lines.append(f"| 门槛 | 通过条目数 |")
    lines.append(f"|---|---|")
    lines.append(f"| 全部待审（draft + in_review） | {fn['total']} |")
    lines.append(f"| ① 分类白名单（policy / stable_general） | {fn['category_ok']} |")
    lines.append(f"| ② 来源等级（现行：`{'` / `'.join(AUTO_LEVELS) or '不限'}`，"
                 f"其中重建自 URL 的 {d.get('level_rebuilt', 0)} 条） | **{fn['level_ok']}** |")
    lines.append(f"| ③ AI 四项判定（见抽样） | 待估 |")
    lines.append("")
    lines.append("分布（namespace × 分类 × 来源级别）：\n")
    lines.append("| namespace | 分类 | 来源级别 | 条目 |")
    lines.append("|---|---|---|---|")
    for (ns, cat, lvl), n in sorted(d["matrix"].items(), key=lambda kv: -kv[1]):
        mark = " ✅" if (cat in AUTO_CATEGORIES and _level_ok(lvl)) else ""
        lines.append(f"| {ns} | {cat} | {lvl}{mark} | {n} |")
    lines.append("")

    lines.append("## 3. dead 入库任务归因\n")
    err = Counter((r.get("last_error_code") or "(空)") for r in d["dead_jobs"])
    lines.append("| 失败码 | 数量 |")
    lines.append("|---|---|")
    for k, v in err.most_common(10):
        lines.append(f"| `{k}` | {v} |")
    byday = Counter(_fmt_day(r.get("created_at") or 0) for r in d["dead_jobs"])
    lines.append("\n按创建日期（UTC+8）：\n")
    lines.append("| 日期 | dead 任务 |")
    lines.append("|---|---|")
    for k, v in sorted(byday.items(), reverse=True)[:10]:
        lines.append(f"| {k} | {v} |")
    lines.append("")

    lines.append("## 4. 发布状态\n")
    for g in d["generations"]:
        lines.append(f"- {g['namespace']} · {g['generation_id']} · {_fmt(g['created_at'])}")
    for a in d["active"]:
        lines.append(f"- active（{a['namespace']}）：{a.get('generation_id')}")
    lines.append("")

    if samples:
        lines.append("## 5. AI 预审抽样结果\n")
        if samples and samples[0].get("error"):
            lines.append(f"- 抽样失败：{samples[0]['error']}")
        else:
            ok = [s for s in samples if s.get("eligible")]
            lines.append(f"抽样 **{len(samples)}** 条（跨 namespace/分类轮取；`neighbors` 传空 → "
                         f"重复/冲突判定偏保守），其中 **{len(ok)} 条**通过 AI 判定可自动批准。\n")
            lines.append("| namespace | 分类 | 来源级别 | 静态门槛 | 标题 | 稳定 | 敏感 | 重复 | 相关 | AI 资格 | 理由 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for s in samples:
                if s.get("error"):
                    lines.append(f"| {s.get('namespace','-')} | {s.get('category','-')} | "
                                 f"{(s.get('title') or '')[:28]} | - | - | - | - | ❌ | {s['error'][:60]} |")
                    continue
                lines.append(f"| {s['namespace']} | {s['category']} | {s.get('level') or '(未知)'} | "
                             f"{'✅' if s.get('static_gate') else '❌'} | {s['title'][:24]} | {s['stability']} | "
                             f"{s['sensitivity']} | {s['duplication']} | {s['relevance']} | "
                             f"{'✅' if s['eligible'] else '❌'} | {s['reason'][:50]} |")
            valid = [s for s in samples if not s.get("error")]
            ai_only = [s for s in valid if s.get("eligible")]
            would_be = [s for s in valid if s.get("static_gate") or s.get("eligible")]
            rate = len(ok) / max(1, len(valid))
            lines.append(f"\n- 抽样 {len(valid)} 条中：**AI 判定通过（四项全绿）= {len(ai_only)} 条**（{rate:.0%}）")
            lines.append(f"- 其中**同时满足静态门槛**（分类白名单 + official_primary）= "
                         f"{len([s for s in valid if s.get('static_gate') and s.get('eligible')])} 条")
            lines.append("- 若把来源等级门槛放宽（允许 general 级），**AI 判定本身能过的** = "
                         f"{len([s for s in valid if s.get('eligible')])} 条/{len(valid)}"
                         "（仅供决策参考：放宽会牺牲来源权威性，风险自担）")
    lines.append("")
    lines.append("## 6. 结论与建议\n")
    lines.append(f"1. 存量待审 **{fn['total']}** 条，其中分类+来源双门槛通过 **{fn['level_ok']}** 条；"
                 f"`announcement`（时效 7 天）与 `dynamic_service`（30 天）按既定策略**必须人工**，AI 不自动入库。")
    lines.append(f"2. 积压主因是 **{len(d['dead_jobs'])} 个 dead 入库任务**（断网期 LLM 不可达导致重试耗尽）；"
                 "其草稿已落库，不需要重新抓取即可回填。")
    lines.append("3. 下一步（需批准）：`--apply` 回填 → 备份 review.db → 逐条 AI 预审 → 合格即走"
                 "**与人工相同链路**自动批准并排队发布 → 出审计记录。")
    return "\n".join(lines) + "\n"


def _backup_db(tag: str) -> Path:
    """用 SQLite 在线备份 API 复制一份审核库（写操作前必做）。"""
    out_dir = ROOT / "data" / "backups" / "manual"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"review_{tag}_{time.strftime('%Y%m%d_%H%M%S')}.db"
    src = sqlite3.connect(str(DB))
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return dest


def _neighbors(conn: sqlite3.Connection, title: str, limit: int = 3) -> list[str]:
    """轻量"已有片段"查找：用标题里最长的词做 LIKE，命中已批准块（够用于重复/冲突判定）。"""
    import re as _re
    tokens = _re.findall(r"[\u4e00-\u9fff]{3,}|[A-Za-z]{4,}", title or "")
    token = max(tokens, key=len) if tokens else (title or "")[:6]
    if not token:
        return []
    rows = conn.execute(
        "select content_text from review_chunks where approval_status='approved' "
        "and content_text like ? limit ?", (f"%{token}%", limit)).fetchall()
    return [(r["content_text"] or "")[:500] for r in rows]


def apply_backfill(args) -> int:
    """回填：备份 → 逐条 AI 预审 → 合格自动批准（同人工链路）／跑题与重复自动拒绝。"""
    sys.path.insert(0, str(ROOT))
    from xiaowo_web.settings import WebSettings
    from xiaowo_web.review import ReviewStore
    from xiaowo_web.worker.pre_review import PreReviewer

    settings = WebSettings.from_env()
    if not getattr(settings, "review_pre_review", False):
        print("预审未启用（settings.review_pre_review=false），中止")
        return 1
    store = ReviewStore(settings)
    reviewer = PreReviewer(settings.evidence_extractor_model)
    batch = args.batch or time.strftime("%m%d-%H%M")
    backup = _backup_db("pre_backfill")
    print(f"[备份] {backup}")

    data = collect()
    drafts = [d for d in data["drafts"]
              if (not args.namespace or d["namespace"] == args.namespace)]
    drafts = drafts[: args.limit] if args.limit else drafts
    conn = _connect_ro()
    stats: Counter = Counter()
    lines = [f"# 回填批次 {batch}（{time.strftime('%Y-%m-%d %H:%M')}）", "",
             f"- 备份：`{backup}`", f"- 范围：namespace={args.namespace or 'all'} · 上限 {args.limit or '不限'} · "
             f"共处理 {len(drafts)} 条", f"- 拒绝跑题/重复：{'是' if args.reject_bad else '否'}", ""]
    for i, d in enumerate(drafts, 1):
        ns, item_id, cat = d["namespace"], d["item_id"], d["category"]
        row = conn.execute(
            "select content_text from review_versions rv join review_items ri on ri.item_id=rv.item_id "
            "where rv.item_id=? and rv.version_number=ri.current_version", (item_id,)).fetchone()
        text = (row["content_text"] if row else "") or ""
        try:
            v = reviewer.review(text[:6000], {"level": d["level"], "category": cat},
                                neighbors=_neighbors(conn, d.get("title") or ""))
        except Exception as e:  # noqa: BLE001
            stats["error"] += 1
            lines.append(f"- ⚠️ {item_id}（{cat}）预审失败：{type(e).__name__}: {e}")
            continue
        outcome, detail = "left", v.reason[:70]
        try:
            if v.off_topic or v.duplication == "duplicate":
                if args.reject_bad:
                    store.reject_item(ns, item_id, actor_key="system:auto-backfill",
                                      request_id=f"auto-reject:backfill-{batch}")
                    outcome = "rejected"
            elif v.auto_approve_eligible(level=d["level"], category=cat):
                store.auto_approve_item(
                    ns, item_id, category=cat, ttl_days=AUTO_TTL.get(cat, 7),
                    actor_key="system:auto-backfill",
                    request_id=f"auto-approve:backfill-{batch}")
                outcome = "approved"
        except Exception as e:  # noqa: BLE001
            outcome, detail = "error", f"{type(e).__name__}: {e}"
        stats[outcome] += 1
        lines.append(f"- [{i}/{len(drafts)}] {outcome:8} {ns:10} {cat:16} "
                     f"{(d.get('title') or '')[:34]} — {detail}")
        print(f"[{i}/{len(drafts)}] {outcome} {cat} {(d.get('title') or '')[:30]}")
        time.sleep(max(0.0, args.sleep))
    conn.close()
    lines += ["", "## 汇总", "", f"- 批准 {stats['approved']} · 拒绝 {stats['rejected']} · "
              f"留人工 {stats['left']} · 失败 {stats['error']}",
              f"- 回滚：`python deploy/server/review_backfill.py --revoke-batch {batch}`"]
    out = ROOT / "deploy" / "server" / f"审核回填_batch_{batch}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[-6:]))
    print(f"[报告] {out}")
    return 0


def revoke_batch(batch: str) -> int:
    """按批次回滚：把该批次自动批准的条目撤下（发布链表由 store 处理）。"""
    sys.path.insert(0, str(ROOT))
    from xiaowo_web.settings import WebSettings
    from xiaowo_web.review import ReviewStore
    store = ReviewStore(WebSettings.from_env())
    conn = _connect_ro()
    rows = conn.execute(
        "select distinct namespace, object_id from review_audit "
        "where action='auto_approve' and object_type='review_item' and request_id like ?",
        (f"auto-approve:backfill-{batch}%",)).fetchall()
    conn.close()
    print(f"[回滚] 批次 {batch}：命中 {len(rows)} 条")
    for r in rows:
        try:
            store.revoke_item(r["namespace"], r["object_id"], actor_key="system:auto-backfill",
                              request_id=f"revoke:backfill-{batch}")
            print(f"  revoked {r['object_id']}")
        except Exception as e:  # noqa: BLE001
            print(f"  失败 {r['object_id']}: {type(e).__name__}: {e}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="小蜗审核积压 dry-run 报告（只读）")
    ap.add_argument("--sample", type=int, default=0, help="抽样跑 AI 预审的条数（0 = 不调 LLM）")
    ap.add_argument("--out", default="", help="报告输出路径（默认 deploy/server/审核积压_dryrun_<日期>.md）")
    ap.add_argument("--apply", action="store_true", help="真正回填（备份 → AI 预审 → 自动批准/拒绝）")
    ap.add_argument("--limit", type=int, default=0, help="--apply 时最多处理多少条（0=不限）")
    ap.add_argument("--namespace", default="", help="--apply 时限定 namespace（demo/production）")
    ap.add_argument("--no-reject-bad", dest="reject_bad", action="store_false",
                    help="--apply 时不自动拒绝跑题/重复（只批准）")
    ap.add_argument("--batch", default="", help="批次号（默认时间戳；回滚时用它）")
    ap.add_argument("--sleep", type=float, default=0.4, help="每条之间的间隔秒（限速，默认 0.4）")
    ap.add_argument("--revoke-batch", default="", help="按批次号回滚该批自动批准的条目")
    args = ap.parse_args()

    if args.revoke_batch:
        return revoke_batch(args.revoke_batch)
    if args.apply:
        return apply_backfill(args)

    if not DB.is_file():
        print(f"找不到审核库：{DB}")
        return 1
    data = collect()
    fn = funnel(data)
    samples = sample_review(data["drafts"], args.sample) if args.sample else []
    for s_item in samples:
        s_item["static_gate"] = (s_item.get("category") in AUTO_CATEGORIES
                                 and _level_ok(s_item.get("level", "")))
    report = render(data, fn, samples)
    out = Path(args.out) if args.out else (ROOT / "deploy" / "server" /
                                           f"审核积压_dryrun_{time.strftime('%Y%m%d')}.md")
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"[报告] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
