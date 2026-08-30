import * as Tabs from "@radix-ui/react-tabs";
import {
  ArrowLeft,
  Check,
  ChevronRight,
  CircleDashed,
  Clock3,
  Database,
  Download,
  FileDiff,
  FileText,
  GitBranch,
  MessageSquareWarning,
  ListFilter,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldAlert,
  ShieldCheck,
  SplitSquareVertical,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ConfirmDialog, WorkspaceEmpty, WorkspaceError, WorkspaceLoading } from "../components/WorkspacePrimitives";
import { apiGet, apiMutation } from "../lib/api";
import type {
  GenerationState,
  ReviewCategory,
  ReviewChunk,
  ReviewItemDetail,
  ReviewItemSummary,
  ReviewStatus,
  SessionPayload,
  SourceTrustProposal,
} from "../types";

const statusLabels: Record<ReviewStatus, string> = {
  draft: "待审核",
  in_review: "审核中",
  approved: "已批准",
  pending_publish: "待发布",
  publish_failed: "发布失败",
  active: "已生效",
  rejected: "已拒绝",
  expired: "已过期",
  revoked: "已撤回",
};

const categories: Array<{ value: ReviewCategory; label: string; maxTtl: number }> = [
  { value: "announcement", label: "公告", maxTtl: 7 },
  { value: "dynamic_service", label: "动态办事信息", maxTtl: 30 },
  { value: "policy", label: "政策制度", maxTtl: 90 },
  { value: "stable_general", label: "稳定通识", maxTtl: 180 },
];

type WorkspaceView = "queue" | "governance" | "feedback";

function proposalDefaults(url: string): SourceTrustProposal {
  const parsed = new URL(url);
  const segments = parsed.pathname.split("/").filter(Boolean);
  const pathPrefix = segments.length > 1 ? `/${segments.slice(0, -1).join("/")}` : "/";
  return {
    host: parsed.hostname.toLowerCase(),
    path_prefix: pathPrefix,
    level: "reliable_independent",
    institution: "",
    effective_from: new Date().toISOString().slice(0, 10),
    rationale: "",
  };
}

function downloadDiff(filename: string, content: string): void {
  const blob = new Blob([content], { type: "text/x-diff;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function formatTimestamp(value: number | string | null): string {
  if (!value) return "未知时间";
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(date);
}

function currentChunks(detail: ReviewItemDetail): ReviewChunk[] {
  const versionIds = new Set(detail.versions.filter((version) => version.version_number === detail.current_version).map((version) => version.version_id));
  return detail.chunks.filter((chunk) => versionIds.has(chunk.version_id));
}

function chunkApprovalStatus(chunk: ReviewChunk): "pending" | "approved" | "rejected" {
  return chunk.approval_status ?? (Boolean(chunk.approved) ? "approved" : "pending");
}

function ReviewQueue({ items, selected, filter, onFilter, onSelect }: {
  items: ReviewItemSummary[];
  selected: string | null;
  filter: string;
  onFilter: (value: string) => void;
  onSelect: (item: ReviewItemSummary) => void;
}) {
  return (
    <aside className="review-queue">
      <div className="review-queue__header">
        <div><strong>审核队列</strong><span>{items.length} 条内容</span></div>
        <label className="queue-filter" aria-label="按状态筛选"><ListFilter size={15} />
          <select value={filter} onChange={(event) => onFilter(event.target.value)}>
            <option value="">全部状态</option>
            <option value="draft">待审核</option>
            <option value="in_review">审核中</option>
            <option value="approved">已批准</option>
            <option value="active">已生效</option>
            <option value="rejected">已拒绝</option>
          </select>
        </label>
      </div>
      <div className="review-queue__items">
        {items.length === 0 ? <WorkspaceEmpty title="当前队列为空" detail="可以切换状态筛选查看其他内容。" /> : items.map((item) => (
          <button className="review-queue-item" data-active={selected === item.item_id} type="button" key={item.item_id} onClick={() => onSelect(item)}>
            <div><span className={`review-status review-status--${item.status}`}>{statusLabels[item.status]}</span><small>{formatTimestamp(item.fetched_at)}</small></div>
            <strong>{item.title}</strong>
            <span>{new URL(item.normalized_url).hostname}</span>
            <ChevronRight size={16} />
          </button>
        ))}
      </div>
    </aside>
  );
}

export function ReviewWorkspace({ session }: { session: SessionPayload }) {
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>("queue");
  const [items, setItems] = useState<ReviewItemSummary[]>([]);
  const [detail, setDetail] = useState<ReviewItemDetail | null>(null);
  const [generation, setGeneration] = useState<GenerationState | null>(null);
  const [generationLoading, setGenerationLoading] = useState(false);
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [category, setCategory] = useState<ReviewCategory>("announcement");
  const [ttl, setTtl] = useState(7);
  const [proposal, setProposal] = useState<SourceTrustProposal | null>(null);
  const [feedback, setFeedback] = useState<Array<{
    id: number;
    answer_id: string;
    run_id: string;
    category: string;
    detail: string;
    status: string;
    created_at: string;
  }>>([]);

  const loadGeneration = useCallback(async () => {
    setGenerationLoading(true);
    try {
      const payload = await apiGet<GenerationState>("/admin/generations");
      setGeneration(payload);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取发布状态。" );
    } finally {
      setGenerationLoading(false);
    }
  }, []);

  const loadItems = useCallback(async (status = filter) => {
    setError(null);
    setLoading(true);
    try {
      const query = status ? `?status=${encodeURIComponent(status)}` : "";
      const payload = await apiGet<{ items: ReviewItemSummary[]; namespace: string }>(`/admin/review-items${query}`);
      setItems(payload.items);
      if (detail && !payload.items.some((item) => item.item_id === detail.item_id)) setDetail(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法加载审核队列。" );
    } finally {
      setLoading(false);
    }
  }, [detail, filter]);

  const openItem = useCallback(async (item: ReviewItemSummary) => {
    setError(null);
    setNotice(null);
    try {
      const payload = await apiGet<ReviewItemDetail>(`/admin/review-items/${item.item_id}`);
      setDetail(payload);
      const editable = [...payload.versions].reverse().find((version) => version.kind === "human" || version.kind === "model");
      setDraft(editable?.content_text ?? payload.raw_snapshot);
      setCategory(payload.category);
      setTtl(payload.ttl_days);
      setProposal(proposalDefaults(payload.normalized_url));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法读取审核详情。" );
    }
  }, []);

  useEffect(() => {
    void loadItems("");
    void loadGeneration();
  }, []); // queue ownership changes remount this workspace

  useEffect(() => {
    if (workspaceView !== "feedback") return;
    void apiGet<{ items: typeof feedback }>("/admin/feedback?limit=100")
      .then((payload) => setFeedback(payload.items))
      .catch((reason) => setError(reason instanceof Error ? reason.message : "无法读取回答反馈。"));
  }, [workspaceView]);

  const mutate = useCallback(async (path: string, body: Record<string, unknown> = {}) => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiMutation(path, session.csrf_token, { method: "POST", body: JSON.stringify(body) });
      const refreshed = await apiGet<ReviewItemDetail>(`/admin/review-items/${detail.item_id}`);
      setDetail(refreshed);
      await Promise.all([loadItems(filter), loadGeneration()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审核操作失败。" );
    } finally {
      setBusy(false);
    }
  }, [detail, filter, loadGeneration, loadItems, session.csrf_token]);

  const queueRefetch = useCallback(async () => {
    if (!detail) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiMutation<{ job_id: string; status: string; created: boolean }>(
        `/admin/review-items/${detail.item_id}/refetch`,
        session.csrf_token,
        { method: "POST", body: "{}" },
      );
      setNotice(result.created ? "已加入异步复抓队列。" : "该来源已有复抓任务在队列中。" );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法提交复抓任务。" );
    } finally {
      setBusy(false);
    }
  }, [detail, session.csrf_token]);

  const submitProposal = useCallback(async () => {
    if (!detail || !proposal) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiMutation(
        `/admin/review-items/${detail.item_id}/source-trust-proposals`,
        session.csrf_token,
        { method: "POST", body: JSON.stringify(proposal) },
      );
      setNotice("来源规则建议已加入 Git diff 导出队列。" );
      setProposal((current) => current ? { ...current, rationale: "" } : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法提交来源规则建议。" );
    } finally {
      setBusy(false);
    }
  }, [detail, proposal, session.csrf_token]);

  const rollbackGeneration = useCallback(async () => {
    if (!generation?.can_rollback || !generation.previous_generation_id) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiMutation<{ generation_id: string }>(
        "/admin/generations/rollback",
        session.csrf_token,
        { method: "POST", body: "{}" },
      );
      setDetail(null);
      setNotice(`已切换到 ${result.generation_id}。`);
      await Promise.all([loadItems(filter), loadGeneration()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "generation 回滚失败。" );
    } finally {
      setBusy(false);
    }
  }, [filter, generation, loadGeneration, loadItems, session.csrf_token]);

  const exportTrustProposals = useCallback(async () => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiMutation<{ filename: string; proposal_ids: string[]; diff: string }>(
        "/admin/source-trust-proposals/export",
        session.csrf_token,
        { method: "POST", body: "{}" },
      );
      downloadDiff(result.filename, result.diff);
      setNotice(`已导出 ${result.proposal_ids.length} 条来源规则建议。`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法导出来源规则建议。" );
    } finally {
      setBusy(false);
    }
  }, [session.csrf_token]);

  const chunks = useMemo(() => detail ? currentChunks(detail) : [], [detail]);
  const allReviewed = chunks.length > 0 && chunks.every((chunk) => chunkApprovalStatus(chunk) !== "pending");
  const hasApproved = chunks.some((chunk) => chunkApprovalStatus(chunk) === "approved");
  const decisionReady = allReviewed && hasApproved;
  const editable = detail?.status === "draft" || detail?.status === "in_review";
  const selectedCategory = categories.find((item) => item.value === category) ?? categories[0];
  const proposalReady = Boolean(
    proposal
    && proposal.institution.trim().length >= 2
    && proposal.rationale.trim().length >= 10,
  );

  if (loading && !items.length && !detail) return <WorkspaceLoading label="正在读取审核队列" />;
  if (error && !items.length && !detail) return <WorkspaceError message={error} onRetry={() => void loadItems()} />;

  return (
    <Tabs.Root className="review-workspace" value={workspaceView} onValueChange={(value) => setWorkspaceView(value as WorkspaceView)}>
      <header className="workspace-header review-header">
        <div><h1>知识审核</h1><span className="workspace-context">{session.principal.review_namespace === "demo" ? "演示审核空间" : "生产审核空间"}</span></div>
        <div className="review-header__right">
          <Tabs.List className="review-view-tabs" aria-label="审核视图">
            <Tabs.Trigger value="queue"><FileText size={14} />内容队列</Tabs.Trigger>
            <Tabs.Trigger value="governance"><Database size={14} />发布治理</Tabs.Trigger>
            <Tabs.Trigger value="feedback"><MessageSquareWarning size={14} />回答反馈</Tabs.Trigger>
          </Tabs.List>
          {session.principal.review_namespace === "demo" && <div className="review-demo-lock"><ShieldAlert size={15} /><span>演示审核与生产知识永久隔离</span></div>}
        </div>
      </header>
      {error && <div className="workspace-inline-error" role="alert">{error}</div>}
      {notice && <div className="review-operation-notice" role="status"><Check size={14} />{notice}</div>}
      <Tabs.Content className="review-view-content" value="feedback">
        <div className="feedback-review-list">
          <div className="content-source-row"><span>{feedback.length} 条待查看反馈</span><span className="data-source">保留 30 天</span></div>
          {feedback.length === 0 ? <WorkspaceEmpty title="当前没有回答反馈" detail="新的反馈会在此处进入核验队列。" /> : feedback.map((item) => (
            <article className="feedback-review-item" key={item.id}>
              <div><span className="review-status review-status--in_review">{item.category}</span><time>{formatTimestamp(item.created_at)}</time></div>
              <p>{item.detail || "用户仅提交了反馈分类，没有补充说明。"}</p>
              <small>回答 {item.answer_id.slice(0, 10)} · 运行 {item.run_id.slice(0, 10)}</small>
            </article>
          ))}
        </div>
      </Tabs.Content>
      <Tabs.Content className="review-view-content" value="governance">
        <div className="review-governance">
          <header className="governance-heading">
            <h2>发布状态</h2>
            <button className="icon-button governance-refresh" type="button" title="刷新发布状态" aria-label="刷新发布状态" disabled={generationLoading} onClick={() => void loadGeneration()}><RefreshCw size={16} /></button>
          </header>
          {generationLoading && !generation ? <WorkspaceLoading label="正在读取发布状态" /> : (
            <section className="generation-overview">
              <dl className="generation-metadata">
                <div><dt>命名空间</dt><dd>{generation?.namespace === "demo" ? "演示索引" : "生产索引"}</dd></div>
                <div><dt>发布时间</dt><dd>{formatTimestamp(generation?.activated_at ?? null)}</dd></div>
                <div><dt>发布队列</dt><dd data-busy={Boolean(generation?.publish_busy)}>{generation?.publish_busy ? "处理中" : "空闲"}</dd></div>
              </dl>
              <div className="generation-pointer" data-current="true">
                <span>当前 active</span>
                <code>{generation?.active_generation_id ?? "尚无有效 generation"}</code>
              </div>
              <div className="generation-pointer">
                <span>上一完整版本</span>
                <code>{generation?.previous_generation_id ?? "无可回滚版本"}</code>
              </div>
              <div className="governance-actions">
                <ConfirmDialog
                  trigger={<button className="secondary-button secondary-button--danger" type="button" disabled={busy || !generation?.can_rollback}><RotateCcw size={15} />回滚上一版本</button>}
                  title="回滚知识索引"
                  description={`将 active 指针切换到上一完整版本 ${generation?.previous_generation_id ?? ""}。审核历史不会被删除。`}
                  confirmLabel="确认回滚"
                  destructive
                  onConfirm={() => void rollbackGeneration()}
                />
              </div>
            </section>
          )}
          <section className="trust-export-row">
            <div><h2>来源规则变更</h2><code>config/source_trust.yaml · Git 审查生效</code></div>
            <button className="secondary-button" type="button" disabled={busy} onClick={() => void exportTrustProposals()}><Download size={15} />导出 Git diff</button>
          </section>
        </div>
      </Tabs.Content>
      <Tabs.Content className="review-view-content" value="queue">
      <div className="review-layout" data-detail={Boolean(detail)}>
        <ReviewQueue
          items={items}
          selected={detail?.item_id ?? null}
          filter={filter}
          onFilter={(value) => { setFilter(value); setDetail(null); void loadItems(value); }}
          onSelect={(item) => void openItem(item)}
        />
        <main className="review-detail">
          {!detail ? (
            <div className="review-detail__empty"><ShieldCheck size={27} /><strong>选择一条内容开始核验</strong><span>原文、清洗稿、差异与分块会在这里并排进入审核流程。</span></div>
          ) : (
            <>
              <button className="review-back" type="button" onClick={() => setDetail(null)}><ArrowLeft size={16} />返回队列</button>
              <div className="review-detail__heading">
                <div><span className={`review-status review-status--${detail.status}`}>{statusLabels[detail.status]}</span><h2>{detail.title}</h2><a href={detail.final_url} target="_blank" rel="noreferrer">{detail.normalized_url}</a></div>
                <div className="review-detail__commands">
                  <button className="secondary-button" disabled={busy} onClick={() => void queueRefetch()}><RefreshCw className={busy ? "is-spinning" : ""} size={14} />重新抓取</button>
                  {detail.status === "draft" && <button className="command-button" disabled={busy} onClick={() => void mutate(`/admin/review-items/${detail.item_id}/review`)}>开始审核</button>}
                  {detail.status === "publish_failed" && <button className="command-button" disabled={busy} onClick={() => void mutate(`/admin/review-items/${detail.item_id}/publish/retry`)}>重试发布</button>}
                  {detail.status === "active" && <button className="secondary-button secondary-button--danger" disabled={busy} onClick={() => void mutate(`/admin/review-items/${detail.item_id}/revoke`)}><X size={14} />撤回</button>}
                </div>
              </div>
              <dl className="review-metadata">
                <div><dt>抓取时间</dt><dd>{formatTimestamp(detail.fetched_at)}</dd></div>
                <div><dt>内容类型</dt><dd>{detail.content_type}</dd></div>
                <div><dt>当前版本</dt><dd>v{detail.current_version}</dd></div>
                <div><dt>范围</dt><dd>{detail.scope === "campus" ? "科大校园" : "通识"}</dd></div>
              </dl>
              <Tabs.Root className="review-tabs" defaultValue="cleaned">
                <Tabs.List className="tabs-list tabs-list--compact">
                  <Tabs.Trigger value="original"><FileText size={14} />原文</Tabs.Trigger>
                  <Tabs.Trigger value="cleaned"><Save size={14} />清洗稿</Tabs.Trigger>
                  <Tabs.Trigger value="diff"><FileDiff size={14} />差异</Tabs.Trigger>
                  <Tabs.Trigger value="chunks"><SplitSquareVertical size={14} />分块 {chunks.length}</Tabs.Trigger>
                  <Tabs.Trigger value="source"><GitBranch size={14} />来源治理</Tabs.Trigger>
                </Tabs.List>
                <Tabs.Content value="original"><pre className="review-text review-text--original">{detail.raw_snapshot}</pre></Tabs.Content>
                <Tabs.Content value="cleaned">
                  <textarea className="review-editor" value={draft} disabled={!editable || busy} onChange={(event) => setDraft(event.target.value)} aria-label="人工清洗稿" />
                  {editable && <div className="review-editor-actions"><span>保存会创建不可变的新版本</span><button className="secondary-button" disabled={busy || !draft.trim()} onClick={() => void mutate(`/admin/review-items/${detail.item_id}/versions`, { content: draft.trim(), chunks: draft.split(/\n\s*\n/).map((item) => item.trim()).filter(Boolean) })}><Save size={14} />保存新版本</button></div>}
                </Tabs.Content>
                <Tabs.Content value="diff"><pre className="review-text review-diff">{detail.diff || "当前清洗稿与原文无文本差异。"}</pre></Tabs.Content>
                <Tabs.Content value="chunks">
                  <div className="review-chunks">
                    {chunks.length === 0 ? <WorkspaceEmpty title="当前版本没有分块" detail="保存清洗稿的新版本后再进行分块决定。" /> : chunks.map((chunk) => (
                      <article className="review-chunk" data-approval-status={chunkApprovalStatus(chunk)} key={chunk.chunk_id}>
                        <div>
                          <span>分块 {chunk.position + 1}</span>
                          {chunkApprovalStatus(chunk) === "approved" && <em><Check size={12} />已批准</em>}
                          {chunkApprovalStatus(chunk) === "rejected" && <em data-rejected="true"><X size={12} />已排除</em>}
                        </div>
                        <p>{chunk.content_text}</p>
                        {editable && (
                          <fieldset className="chunk-decision">
                            <legend className="sr-only">分块 {chunk.position + 1} 审核决定</legend>
                            {([
                              ["pending", "待定", CircleDashed],
                              ["approved", "批准", Check],
                              ["rejected", "排除", X],
                            ] as const).map(([value, label, Icon]) => (
                              <label data-active={chunkApprovalStatus(chunk) === value} key={value}>
                                <input
                                  type="radio"
                                  name={`chunk-${chunk.chunk_id}`}
                                  value={value}
                                  checked={chunkApprovalStatus(chunk) === value}
                                  disabled={busy}
                                  onChange={() => void mutate(`/admin/review-items/${detail.item_id}/chunks/${chunk.chunk_id}`, { approval_status: value })}
                                />
                                <Icon size={14} />{label}
                              </label>
                            ))}
                          </fieldset>
                        )}
                      </article>
                    ))}
                  </div>
                </Tabs.Content>
                <Tabs.Content value="source">
                  {proposal && (
                    <section className="source-proposal">
                      <header><h3>来源规则建议</h3><span className="review-status review-status--draft">待 Git 审查</span></header>
                      <div className="source-proposal__grid">
                        <label>精确域名<input value={proposal.host} readOnly aria-readonly="true" /></label>
                        <label>栏目路径<input value={proposal.path_prefix} onChange={(event) => setProposal({ ...proposal, path_prefix: event.target.value })} /></label>
                        <label>机构名称<input value={proposal.institution} onChange={(event) => setProposal({ ...proposal, institution: event.target.value })} /></label>
                        <label>生效日期<input type="date" value={proposal.effective_from} onChange={(event) => setProposal({ ...proposal, effective_from: event.target.value })} /></label>
                      </div>
                      <fieldset className="source-level-control">
                        <legend>来源等级</legend>
                        <div>
                          <label data-active={proposal.level === "reliable_independent"}><input type="radio" name="source-level" value="reliable_independent" checked={proposal.level === "reliable_independent"} onChange={() => setProposal({ ...proposal, level: "reliable_independent" })} />独立可靠来源</label>
                          <label data-active={proposal.level === "official_primary"}><input type="radio" name="source-level" value="official_primary" checked={proposal.level === "official_primary"} onChange={() => setProposal({ ...proposal, level: "official_primary" })} />官方一手来源</label>
                        </div>
                      </fieldset>
                      <label className="source-proposal__rationale">核验依据<textarea value={proposal.rationale} maxLength={1000} onChange={(event) => setProposal({ ...proposal, rationale: event.target.value })} /></label>
                      <div className="source-proposal__actions"><span>{proposal.rationale.length} / 1000</span><button className="secondary-button" type="button" disabled={busy || !proposalReady} onClick={() => void submitProposal()}><GitBranch size={14} />加入变更建议</button></div>
                    </section>
                  )}
                </Tabs.Content>
              </Tabs.Root>
              {editable && (
                <section className="review-decision">
                  <div><h3>审核决定</h3><span>发布参数</span></div>
                  <label>内容类别<select value={category} onChange={(event) => { const value = event.target.value as ReviewCategory; setCategory(value); setTtl(categories.find((item) => item.value === value)?.maxTtl ?? 7); }}>{categories.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
                  <label>有效期<div className="ttl-input"><Clock3 size={14} /><input type="number" min={1} max={selectedCategory.maxTtl} value={ttl} onChange={(event) => setTtl(Number(event.target.value))} /><span>天 / 上限 {selectedCategory.maxTtl}</span></div></label>
                  <div className="review-decision__actions">
                    <button className="secondary-button secondary-button--danger" disabled={busy} onClick={() => void mutate(`/admin/review-items/${detail.item_id}/reject`)}><X size={15} />拒绝</button>
                    <button className="command-button" disabled={busy || !decisionReady || ttl < 1 || ttl > selectedCategory.maxTtl} onClick={() => void mutate(`/admin/review-items/${detail.item_id}/approve`, { category, ttl_days: ttl })}><ShieldCheck size={15} />批准已选分块</button>
                  </div>
                  {!decisionReady && <p className="review-decision__hint">每个分块都需明确批准或排除，且至少批准一个分块。</p>}
                </section>
              )}
            </>
          )}
        </main>
      </div>
      </Tabs.Content>
    </Tabs.Root>
  );
}
