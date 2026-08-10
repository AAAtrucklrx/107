"""小蜗开发流水线 — Qoder canvas 报告生成。

将流水线状态渲染为 `.canvas.tsx` 文件（Qoder Canvas 面板预览）。
组件库: qoder/canvas (ReportShell / MetricsGrid / Timeline / Table / Callout / Tag)
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .config import CANVAS_DIR

_HEADER = '''import {
  ReportShell, ReportSection,
  Stack, Row, H1, Text, Divider,
  MetricsGrid, Tag, Table, Callout,
  Timeline,
} from "qoder/canvas";
import type { TimelineEvent, MetricItem } from "qoder/canvas";
'''


def _js(s: str) -> str:
    """转义为 JSX 字符串字面量（过滤反引号/换行/反斜杠与 markdown 符号）。"""
    s = (s or "").replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")
    s = re.sub(r"[#*_>]|```", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _summary(text: str, limit: int = 200) -> str:
    return _js((text or "")[:limit])


def render_canvas(state: dict) -> Path:
    """由流水线最终状态生成 canvas 报告，返回文件路径。"""
    verdict = state.get("verdict", "fail")
    tone = "success" if verdict == "pass" else "warning"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = CANVAS_DIR / f"pipeline-{stamp}.canvas.tsx"

    changes = state.get("changes") or []
    files = list(dict.fromkeys(state.get("files") or []))
    issues = state.get("issues") or []
    round_no = int(state.get("round", 1))
    max_rounds = int(state.get("max_rounds", 3))
    executor = state.get("executor", "claude")

    metrics: list[dict] = [
        {"label": "开发任务", "value": _summary(state.get("task", ""), 24), "unit": "已完成" if verdict == "pass" else "未通过"},
        {"label": "执行轮次", "value": f"{round_no}/{max_rounds}", "unit": f"执行器 {executor}"},
        {"label": "变更文件", "value": str(len(files)), "unit": "个文件"},
        {"label": "验收结论", "value": "PASS" if verdict == "pass" else "FAIL", "unit": tone},
    ]

    timeline_events: list[dict] = []
    for i, ch in enumerate(changes, start=1):
        timeline_events.append({
            "id": f"r{i}",
            "timestamp": f"轮次 {i}",
            "title": f"执行（{ch.get('executor', '?')}）",
            "description": _summary((ch.get("diff_stat") or "无 git 变更记录")[:120]),
            "state": "completed",
            "tone": "success",
        })
    if not timeline_events:
        timeline_events.append({
            "id": "r0", "timestamp": "未执行", "title": "流水线未产生变更",
            "description": "执行节点没有输出文件变更", "state": "upcoming", "tone": "neutral",
        })

    rows_files = [[_js(f), "修改"] for f in files[:30]] or [["（无变更文件）", "-"]]

    callout_tone = "success" if verdict == "pass" else "warning"
    callout_title = "验收通过" if verdict == "pass" else "验收未通过（遗留问题）"
    issues_text = "；".join(_js(i) for i in issues[:10]) or "无"
    test_sum = _summary(state.get("test_output", ""), 300)

    body = f"""export default function PipelineReport() {{
  const headlineMetrics: MetricItem[] = {json.dumps(metrics, ensure_ascii=False)};
  const timelineEvents: TimelineEvent[] = {json.dumps(timeline_events, ensure_ascii=False)};
  const fileRows = {json.dumps(rows_files, ensure_ascii=False)};

  return (
    <ReportShell width="wide" ariaLabel="小蜗开发流水线报告">
      <Stack gap={{20}}>
        <header>
          <Stack gap={{8}}>
            <H1>小蜗开发流水线 — 执行报告</H1>
            <Text size="small" tone="secondary">
              {_summary(state.get("report_md", ""), 160)}
            </Text>
            <MetricsGrid variant="header" columns={{4}} items={{headlineMetrics}} />
          </Stack>
        </header>

        <ReportSection title="执行时间线" description="每轮执行与变更统计" divided>
          <Timeline events={{timelineEvents}} density="default" />
        </ReportSection>

        <ReportSection title="变更文件" description="本轮流水线修改的文件" divided>
          <Table headers={{["文件", "操作"]}} rows={{fileRows}} density="compact" />
        </ReportSection>

        <ReportSection title="测试结果" description="真实测试命令输出摘要" divided>
          <Stack gap={{12}}>
            <Text size="small">{test_sum}</Text>
          </Stack>
        </ReportSection>

        <ReportSection title="验收结论" description="决策节点判定" divided>
          <Stack gap={{12}}>
            <Callout tone="{callout_tone}" title="{callout_title}">
              遗留问题：{issues_text}
            </Callout>
            <Row gap={{8}} wrap>
              <Tag tone="{tone}">{'PASS' if verdict == 'pass' else 'FAIL'}</Tag>
              <Tag tone="info">{executor}</Tag>
              <Tag tone="info">round {round_no}/{max_rounds}</Tag>
              <Tag tone="info">deepseek-v4-flash</Tag>
            </Row>
          </Stack>
        </ReportSection>

        <Divider />
        <Text size="small" tone="secondary">
          生成于 {datetime.now().strftime("%Y-%m-%d %H:%M")} · 小蜗开发流水线 v0.1.0
        </Text>
      </Stack>
    </ReportShell>
  );
}}
"""

    out.write_text(_HEADER + body, encoding="utf-8")
    return out
