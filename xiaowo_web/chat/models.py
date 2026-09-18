"""Publicly safe chat runner contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Callable
from typing import Any

from xiaowo_web.auth.models import Principal


@dataclass(frozen=True, slots=True)
class QaRunRequest:
    run_id: str
    question: str
    requested_mode: str
    effective_mode: str
    principal: Principal
    conversation_id: str | None
    chat_history: list[dict[str, str]] = field(default_factory=list)
    emit_stage: Callable[[str, str], None] | None = None
    # 阶段2：结构化卡片事件（工具完成即推，先于正文）
    emit_table: Callable[[dict], None] | None = None
    # compose 增量流式：正文 token 推流（answer.delta 事件，前端拼接显示）
    emit_delta: Callable[[str], None] | None = None


@dataclass(slots=True)
class AnswerBundle:
    markdown: str
    claims: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    terminal_reason: str = "completed"
    ingestion_candidates: list[dict[str, Any]] = field(default_factory=list)
    # B2: think 决策过程(前端折叠卡展示); B4: LLM 输出触顶截断标记(前端"继续生成")
    thoughts: list[dict[str, Any]] = field(default_factory=list)
    # 阶段1 结构化数据卡：工具结果表格（成绩/课表/考试/选课），不经 LLM 重述
    structured: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    # 语义缓存用的 chunk content hash（2026-09-18）：本地 runner 在"缓存写入交给外层
    # runner"时把它带出来，发布激活时的定向失效才不会因为拿不到 hash 而失效
    cache_source_hashes: list[str] = field(default_factory=list)
    # 本条是"缓存命中"时的类型（"" / "local" / "web"）：web 类命中可直接复用，
    # local 类命中仍需判定器把关（可能是"未收录"的半成品）——2026-09-18
    cache_kind: str = ""
    # 本地链路的结构信号（2026-09-16）：供上层判断"本地到底有没有答出来"。
    # 字段：candidates_found / candidate_count / top_score / tool_used。
    # ⚠️ 实测只有 `candidates_found is False`（知识库压根没召回）是**可靠**判据；
    #    `top_score` **不能**用来区分"答得出/答不出"（实测两组分数几乎重叠：
    #    答得出 0.50~0.60，答不出 0.44~0.58），不要据此设阈值。
    retrieval: dict[str, Any] = field(default_factory=dict)
    # 联网引用里、待**后台抓整页后入审核库**的 URL（2026-09-16）。
    # 为什么不是直接给 ingestion_candidates：smart 的 references 只给片段，而
    # `ReviewStore.enqueue_candidate` 硬性要求整页 `snapshot_text` → 必须补一次抓取，
    # 且不能阻塞回答，故只传 URL、由 manager 起后台任务。
    ingestion_urls: list[str] = field(default_factory=list)
    # 联网引用的**检索摘要**（url → {"content","title"}，2026-09-17）。
    # 有些站点 robots.txt 明令禁止抓取（实测 mp.weixin.qq.com 是 `Disallow: /`，微博同因），
    # 抓正文必然失败；而检索器返回的摘要本身就是**原文片段**，可兜底入审核库，
    # 但必须**显式标注"仅摘要"**（见 content_type/title），绝不冒充整页。
    ingestion_snippets: dict[str, dict[str, str]] = field(default_factory=dict)
    # 智能搜索**原样返回的 references**（2026-09-17 影子对比用）。
    # 只用于后台记录 A/B 对比指标（如"答案里有多少事实能在证据中找到"），
    # **不进 SSE 响应**：`manager._complete` 是按字段显式拼载荷的，不会 dump 整个 bundle。
    web_references: list[dict[str, Any]] = field(default_factory=list)
