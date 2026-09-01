import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ClipboardList,
  History,
  Search,
  ShieldCheck,
  UserRound,
  Wrench,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { ConfirmDialog, WorkspaceEmpty, WorkspaceError, WorkspaceLoading } from "../components/WorkspacePrimitives";
import { apiGet, apiMutation } from "../lib/api";
import type {
  CampusToolApplication,
  CampusToolApplicationStatus,
  CampusToolAuditEntry,
  ManagedCampusTool,
  SessionPayload,
} from "../types";

type AdminToolSection = "applications" | "published" | "audit";

const categoryLabels = {
  study: "学习教务",
  life: "校园生活",
  information: "信息查询",
  community: "校园社区",
  other: "其他工具",
} as const;

const statusLabels: Record<CampusToolApplicationStatus, string> = {
  pending: "待审核",
  approved: "已通过",
  rejected: "已驳回",
};

const auditLabels: Record<string, string> = {
  application_submitted: "提交申请",
  application_approved: "审核通过",
  application_rejected: "审核驳回",
  tool_unpublished: "工具下架",
};

function querySuffix(values: Record<string, string>): string {
  const parameters = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value.trim()) parameters.set(key, value.trim()); });
  return parameters.size ? `?${parameters.toString()}` : "";
}

function readableMoment(value: number | null | undefined): string {
  if (!value) return "时间待确认";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value * 1000));
}

function readableHost(value: string): string {
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return value;
  }
}

function requestId(prefix: string): string {
  const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${suffix}`;
}

export function AdminToolsWorkspace({ session }: { session: SessionPayload }) {
  const [section, setSection] = useState<AdminToolSection>("applications");
  const [applications, setApplications] = useState<CampusToolApplication[]>([]);
  const [managedTools, setManagedTools] = useState<ManagedCampusTool[]>([]);
  const [audit, setAudit] = useState<CampusToolAuditEntry[]>([]);
  const [selected, setSelected] = useState<CampusToolApplication | null>(null);
  const [applicationStatus, setApplicationStatus] = useState("pending");
  const [toolStatus, setToolStatus] = useState("active");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<CampusToolApplication | null>(null);
  const [unpublishing, setUnpublishing] = useState<ManagedCampusTool | null>(null);
  const [reason, setReason] = useState("");

  const loadApplications = useCallback(async (status = applicationStatus, nextQuery = query) => {
    setLoading(true);
    setError(null);
    try {
      const payload = await apiGet<{ items: CampusToolApplication[]; namespace: string }>(
        `/admin/campus-tool-applications${querySuffix({ status, query: nextQuery })}`,
      );
      setApplications(payload.items);
      setSelected((current) => (
        payload.items.find((item) => item.application_id === current?.application_id)
        ?? payload.items[0]
        ?? null
      ));
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "无法加载工具申请。");
    } finally {
      setLoading(false);
    }
  }, [applicationStatus, query]);

  const loadManagedTools = useCallback(async (status = toolStatus, nextQuery = query) => {
    setLoading(true);
    setError(null);
    try {
      const payload = await apiGet<{ items: ManagedCampusTool[]; namespace: string }>(
        `/admin/campus-tools${querySuffix({ status, query: nextQuery })}`,
      );
      setManagedTools(payload.items);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "无法加载已上架工具。");
    } finally {
      setLoading(false);
    }
  }, [query, toolStatus]);

  const loadAudit = useCallback(async (nextQuery = query) => {
    setLoading(true);
    setError(null);
    try {
      const payload = await apiGet<{ items: CampusToolAuditEntry[]; namespace: string }>(
        `/admin/campus-tool-audit${querySuffix({ query: nextQuery })}`,
      );
      setAudit(payload.items);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "无法加载审计记录。");
    } finally {
      setLoading(false);
    }
  }, [query]);

  const loadCurrent = useCallback(async () => {
    if (section === "applications") await loadApplications();
    else if (section === "published") await loadManagedTools();
    else await loadAudit();
  }, [loadApplications, loadAudit, loadManagedTools, section]);

  useEffect(() => {
    void loadCurrent();
  }, [section]); // filters are submitted explicitly

  const searchCurrent = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const nextQuery = search.trim();
    setQuery(nextQuery);
    if (section === "applications") void loadApplications(applicationStatus, nextQuery);
    else if (section === "published") void loadManagedTools(toolStatus, nextQuery);
    else void loadAudit(nextQuery);
  };

  const approve = async (application: CampusToolApplication) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await apiMutation(`/admin/campus-tool-applications/${application.application_id}/approve`, session.csrf_token, {
        method: "POST",
        headers: { "X-Request-ID": requestId("campus-tool-approve") },
        body: JSON.stringify({ expected_version: application.version }),
      });
      setNotice(`“${application.name}”已通过审核并上架。`);
      await loadApplications(applicationStatus, query);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "审核操作失败。");
    } finally {
      setBusy(false);
    }
  };

  const reject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!rejecting || reason.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      await apiMutation(`/admin/campus-tool-applications/${rejecting.application_id}/reject`, session.csrf_token, {
        method: "POST",
        headers: { "X-Request-ID": requestId("campus-tool-reject") },
        body: JSON.stringify({ expected_version: rejecting.version, reason: reason.trim() }),
      });
      setNotice(`“${rejecting.name}”已驳回，原因已通知申请人。`);
      setRejecting(null);
      setReason("");
      await loadApplications(applicationStatus, query);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "驳回操作失败。");
    } finally {
      setBusy(false);
    }
  };

  const unpublish = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!unpublishing || reason.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      await apiMutation(`/admin/campus-tools/${unpublishing.tool_id}/unpublish`, session.csrf_token, {
        method: "POST",
        headers: { "X-Request-ID": requestId("campus-tool-unpublish") },
        body: JSON.stringify({ expected_version: unpublishing.version, reason: reason.trim() }),
      });
      setNotice(`“${unpublishing.name}”已下架，原因已通知申请人。`);
      setUnpublishing(null);
      setReason("");
      await loadManagedTools(toolStatus, query);
    } catch (reasonValue) {
      setError(reasonValue instanceof Error ? reasonValue.message : "下架操作失败。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="admin-workspace admin-tools-workspace">
      <header className="admin-page-header">
        <div><span>CAMPUS TOOLS</span><h1>校园工具审核</h1><p>审核用户提交，控制全校可见目录，并保留不可变操作记录。</p></div>
        <span className="admin-namespace">{session.principal.review_namespace === "production" ? "生产空间" : "演示空间"}</span>
      </header>

      <div className="admin-tool-tabs" role="tablist" aria-label="工具管理视图">
        <button type="button" role="tab" aria-selected={section === "applications"} data-active={section === "applications"} onClick={() => setSection("applications")}><ClipboardList size={16} />申请审核</button>
        <button type="button" role="tab" aria-selected={section === "published"} data-active={section === "published"} onClick={() => setSection("published")}><Wrench size={16} />已上架工具</button>
        <button type="button" role="tab" aria-selected={section === "audit"} data-active={section === "audit"} onClick={() => setSection("audit")}><History size={16} />审计记录</button>
      </div>

      <form className="admin-tool-filter" onSubmit={searchCurrent}>
        <label><Search size={16} /><input aria-label="搜索工具管理记录" type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称、链接、申请人或记录编号" /></label>
        {section === "applications" && (
          <select aria-label="申请状态" value={applicationStatus} onChange={(event) => { const value = event.target.value; setApplicationStatus(value); void loadApplications(value, query); }}>
            <option value="">全部申请</option><option value="pending">待审核</option><option value="approved">已通过</option><option value="rejected">已驳回</option>
          </select>
        )}
        {section === "published" && (
          <select aria-label="工具状态" value={toolStatus} onChange={(event) => { const value = event.target.value; setToolStatus(value); void loadManagedTools(value, query); }}>
            <option value="">全部工具</option><option value="active">已上架</option><option value="unpublished">已下架</option>
          </select>
        )}
        <button className="secondary-button" type="submit" disabled={loading}>搜索</button>
      </form>

      {notice && <div className="admin-operation-notice" role="status"><Check size={15} />{notice}</div>}
      {error && <div className="inline-alert" role="alert">{error}</div>}

      {loading && section === "applications" && !applications.length ? <WorkspaceLoading label="正在读取工具申请" /> : section === "applications" ? (
        <div className="admin-tool-review-grid" data-detail={Boolean(selected)}>
          <aside className="admin-tool-queue">
            <div className="admin-tool-queue__heading"><strong>申请队列</strong><span>{applications.length} 条</span></div>
            {applications.length ? applications.map((application) => (
              <button type="button" data-active={selected?.application_id === application.application_id} onClick={() => setSelected(application)} key={application.application_id}>
                <div><span className={`tool-status tool-status--${application.status}`}>{statusLabels[application.status]}</span><time>{readableMoment(application.created_at)}</time></div>
                <strong>{application.name}</strong>
                <span>{application.applicant_name_snapshot} · {application.applicant_principal_id}</span>
              </button>
            )) : <WorkspaceEmpty title="当前队列为空" detail="切换状态筛选可查看历史申请。" />}
          </aside>
          <main className="admin-tool-detail">
            {!selected ? <WorkspaceEmpty title="选择一条申请" detail="申请人、链接和审核操作会在这里显示。" /> : (
              <>
                <button className="admin-tool-detail__back" type="button" onClick={() => setSelected(null)}>
                  <ArrowLeft size={15} />返回申请队列
                </button>
                <div className="admin-tool-detail__heading">
                  <div><span className={`tool-status tool-status--${selected.status}`}>{statusLabels[selected.status]}</span><h2>{selected.name}</h2><a href={selected.normalized_url} target="_blank" rel="noopener noreferrer">{selected.normalized_url}<ArrowUpRight size={14} /></a></div>
                  {selected.status === "pending" && (
                    <div>
                      <button className="secondary-button secondary-button--danger" disabled={busy} type="button" onClick={() => { setReason(""); setRejecting(selected); }}><X size={15} />驳回</button>
                      <ConfirmDialog
                        trigger={<button className="command-button" disabled={busy} type="button"><ShieldCheck size={15} />通过并上架</button>}
                        title="通过校园工具申请"
                        description={`“${selected.name}”将立即进入当前环境的全校工具目录。`}
                        confirmLabel="确认通过"
                        onConfirm={() => void approve(selected)}
                      />
                    </div>
                  )}
                </div>
                <dl className="admin-tool-metadata">
                  <div><dt><UserRound size={15} />申请人</dt><dd>{selected.applicant_name_snapshot}<small>{selected.applicant_principal_id}</small></dd></div>
                  <div><dt>分类</dt><dd>{categoryLabels[selected.category]}</dd></div>
                  <div><dt>提交时间</dt><dd>{readableMoment(selected.created_at)}</dd></div>
                  <div><dt>版本</dt><dd>v{selected.version}</dd></div>
                </dl>
                <section className="admin-tool-description"><h3>功能说明</h3><p>{selected.display_description}</p></section>
                {selected.decision_reason && <div className="admin-tool-decision-reason"><strong>驳回原因</strong><p>{selected.decision_reason}</p></div>}
              </>
            )}
          </main>
        </div>
      ) : loading && section === "published" && !managedTools.length ? <WorkspaceLoading label="正在读取已上架工具" /> : section === "published" ? (
        managedTools.length ? <div className="admin-managed-tools">
          {managedTools.map((tool) => (
            <article key={tool.tool_id}>
              <div><span className={`tool-status tool-status--${tool.status}`}>{tool.status === "active" ? "已上架" : "已下架"}</span><strong>{tool.name}</strong><p>{tool.display_description}</p><a href={tool.url} target="_blank" rel="noopener noreferrer">{readableHost(tool.url)}<ArrowUpRight size={13} /></a></div>
              <div><span>{tool.applicant_name_snapshot} · {tool.applicant_principal_id}</span><time>{readableMoment(tool.published_at)}</time>{tool.unpublish_reason && <p>{tool.unpublish_reason}</p>}{tool.status === "active" && <button className="secondary-button secondary-button--danger" type="button" disabled={busy} onClick={() => { setReason(""); setUnpublishing(tool); }}>下架</button>}</div>
            </article>
          ))}
        </div> : <WorkspaceEmpty title="暂无工具记录" detail="当前筛选条件下没有工具。" />
      ) : loading && !audit.length ? <WorkspaceLoading label="正在读取审计记录" /> : audit.length ? (
        <div className="admin-tool-audit-list">
          {audit.map((entry) => (
            <article key={entry.audit_id}>
              <span className="admin-tool-audit-list__marker" aria-hidden="true" />
              <div><strong>{auditLabels[entry.action] ?? entry.action}</strong><span>{entry.actor_key}</span></div>
              <code>{entry.object_id}</code>
              {entry.reason && <p>{entry.reason}</p>}
              <time>{readableMoment(entry.created_at)}</time>
            </article>
          ))}
        </div>
      ) : <WorkspaceEmpty title="暂无审计记录" detail="提交和审核操作会在此处形成不可变记录。" />}

      <Dialog.Root open={rejecting !== null} onOpenChange={(open) => { if (!open) { setRejecting(null); setReason(""); } }}>
        <Dialog.Portal><Dialog.Overlay className="dialog-overlay" />{rejecting && <Dialog.Content className="dialog-content admin-reason-dialog"><div className="dialog-heading"><div><Dialog.Title>驳回“{rejecting.name}”</Dialog.Title><Dialog.Description>驳回原因会写入审核历史并通知申请人。</Dialog.Description></div><Dialog.Close className="icon-button" aria-label="关闭"><X size={18} /></Dialog.Close></div><form onSubmit={(event) => void reject(event)}><label>驳回原因<textarea required minLength={2} maxLength={500} rows={5} value={reason} onChange={(event) => setReason(event.target.value)} /></label><div className="dialog-actions"><Dialog.Close className="secondary-button" type="button">取消</Dialog.Close><button className="danger-button" type="submit" disabled={busy || reason.trim().length < 2}>确认驳回</button></div></form></Dialog.Content>}</Dialog.Portal>
      </Dialog.Root>

      <Dialog.Root open={unpublishing !== null} onOpenChange={(open) => { if (!open) { setUnpublishing(null); setReason(""); } }}>
        <Dialog.Portal><Dialog.Overlay className="dialog-overlay" />{unpublishing && <Dialog.Content className="dialog-content admin-reason-dialog"><div className="dialog-heading"><div><Dialog.Title>下架“{unpublishing.name}”</Dialog.Title><Dialog.Description>工具将从用户目录移除，下架原因会通知申请人。</Dialog.Description></div><Dialog.Close className="icon-button" aria-label="关闭"><X size={18} /></Dialog.Close></div><form onSubmit={(event) => void unpublish(event)}><label>下架原因<textarea required minLength={2} maxLength={500} rows={5} value={reason} onChange={(event) => setReason(event.target.value)} /></label><div className="dialog-actions"><Dialog.Close className="secondary-button" type="button">取消</Dialog.Close><button className="danger-button" type="submit" disabled={busy || reason.trim().length < 2}>确认下架</button></div></form></Dialog.Content>}</Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
