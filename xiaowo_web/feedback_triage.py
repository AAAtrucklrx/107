"""用户回答反馈 → 自动转动作（2026-09-17）。

背景：反馈功能此前**只写不读、读完也做不了任何事**——`status` 永远停在 `'open'`，
全库没有任何代码会改它，也没有任何下游消费。但它天然带 `run_id`，能回溯到
**那次回答实际用了哪些来源**（`answer.completed` 事件里的 `sources`）。这里把它接上
**现成通道**：

| 反馈类型 | 自动动作 | 用到的现成能力 |
|---|---|---|
| `helpful` | 直接办结（正面信号） | — |
| `outdated` | 对该次回答的来源逐个**排队复抓** | `ReviewStore.queue_refetch`（复抓发现内容变化会自动重新进审核） |
| `source_issue` | 对来源逐个提交**信任降级提案** | `ReviewStore.create_source_trust_proposal`（管理页可导出 Git diff） |
| `incorrect` | 附上来源清单转人工核对（**不自动改知识**） | — |
| `other` | 保持待处理 | — |

原则：
1. **只做能自动做的事**，做不到就退回 `open` 并如实写明原因——绝不假装处理过；
2. **不把用户填的自由文本写进提案**：信任提案会被导出成 Git diff 提交到仓库，
   把用户原话带进去等于把用户文本写进代码库（也可能夹带个人信息）。提案里只放
   反馈编号与固定说明，细节留库内由人工看；
3. 任何异常都不冒泡——反馈已经落库了，分诊失败不能把用户的提交变成报错。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any
from urllib.parse import urlsplit

from utils.logger import get_logger

log = get_logger(__name__)

# 反馈命名空间 → 审核库命名空间（匿名会话没有审核命名空间，不做自动动作）
_REVIEW_NAMESPACE = {"demo": "demo", "production": "production"}


def _host_and_prefix(url: str) -> tuple[str, str]:
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    prefix = path.rsplit("/", 1)[0] + "/" if "/" in path[1:] else "/"
    return host, prefix


def triage_feedback(
    *,
    review_store: Any,
    feedback_id: int,
    namespace: str,
    category: str,
    sources: list[dict[str, Any]],
) -> tuple[str, str]:
    """返回 (status, resolution)。status ∈ open/in_progress/handled/ignored。"""
    if category == "helpful":
        return "handled", "用户标记「有帮助」，无需处理。"
    if category == "other":
        return "open", ""

    urls = [
        str(source.get("display_url") or "").strip()
        for source in (sources or [])
        if str(source.get("display_url") or "").strip()
    ]
    if not urls:
        return "open", "该次回答没有可追溯的联网来源（未附独立引用），需人工查看。"

    review_ns = _REVIEW_NAMESPACE.get(namespace)
    if review_ns is None or review_store is None:
        return "open", f"该次回答涉及 {len(urls)} 条来源，但当前命名空间无法自动处理，需人工查看。"

    matched: list[tuple[str, str]] = []
    for url in urls:
        try:
            item_id = review_store.find_item_id_by_url(review_ns, url)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"反馈分诊查来源失败 {url[:60]}: {exc}")
            item_id = None
        if item_id:
            matched.append((str(item_id), url))

    if not matched:
        return "open", (
            f"该次回答的 {len(urls)} 条来源都还没进审核库，无法自动复抓或建提案，需人工处理。"
        )

    if category == "outdated":
        queued = 0
        for item_id, _url in matched:
            try:
                review_store.queue_refetch(
                    review_ns, item_id, "system:feedback", f"feedback:{feedback_id}"
                )
                queued += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(f"反馈分诊排队复抓失败 item={item_id}: {exc}")
        if queued:
            return "in_progress", (
                f"已对 {queued}/{len(matched)} 条来源排队复抓；"
                "复抓若发现内容变化，会作为新候选重新进审核队列。"
            )
        return "open", "排队复抓失败（来源可能已在复抓中或状态不允许），需人工处理。"

    if category == "source_issue":
        proposed = 0
        today = _dt.date.today().isoformat()
        for item_id, url in matched:
            host, prefix = _host_and_prefix(url)
            if not host:
                continue
            try:
                # ⚠️ rationale 里**不放用户原话**（提案会导出成 Git diff 提交到仓库）
                review_store.create_source_trust_proposal(
                    review_ns,
                    item_id,
                    {
                        "host": host,
                        "path_prefix": prefix,
                        "level": "general",
                        "institution": "",
                        "effective_from": today,
                        "rationale": f"用户反馈该来源有问题（反馈编号 {feedback_id}），建议降级为普通来源。",
                    },
                    "system:feedback",
                    f"feedback:{feedback_id}",
                )
                proposed += 1
            except Exception as exc:  # noqa: BLE001
                log.warning(f"反馈分诊建提案失败 item={item_id}: {exc}")
        if proposed:
            return "in_progress", (
                f"已就 {proposed}/{len(matched)} 条来源提交降级提案；"
                "在「发布治理」页可导出 Git diff 后生效。"
            )
        return "open", "提交来源降级提案失败，需人工处理。"

    if category == "incorrect":
        return "in_progress", (
            f"已附上该次回答用到的 {len(matched)} 条来源，需人工核对答案是否正确；"
            "确认有误时对来源重新审核或提交降级提案。"
        )

    return "open", ""
