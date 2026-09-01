import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import { ArrowUpRight, Bell, Check, Plus, Search, ShieldCheck, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { SourceBadge, WorkspaceEmpty, WorkspaceError, WorkspaceLoading } from "../components/WorkspacePrimitives";
import { apiGet, apiMutation } from "../lib/api";
import type {
  CampusToolApplicationStatus,
  CampusToolApplicationsMine,
  CampusToolCategory,
  CampusToolNotifications,
  CampusToolsDirectory,
  SessionPayload,
} from "../types";

type ToolView = "directory" | "mine";

const categoryLabels: Record<CampusToolCategory, string> = {
  study: "学习教务",
  life: "校园生活",
  information: "信息查询",
  community: "校园社区",
  other: "其他工具",
};

const statusLabels: Record<CampusToolApplicationStatus, string> = {
  pending: "待审核",
  approved: "已通过",
  rejected: "已驳回",
};

function requestSuffix(query: string, category: string): string {
  const parameters = new URLSearchParams();
  if (query.trim()) parameters.set("query", query.trim());
  if (category) parameters.set("category", category);
  return parameters.size ? `?${parameters.toString()}` : "";
}

function readableHost(value: string): string {
  try {
    return new URL(value).hostname.replace(/^www\./, "");
  } catch {
    return "公开链接";
  }
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

function nextRequestId(prefix: string): string {
  const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}-${suffix}`;
}

export function CampusToolsView({ session }: { session: SessionPayload }) {
  const authenticated = session.principal.authenticated;
  const [activeView, setActiveView] = useState<ToolView>("directory");
  const [directory, setDirectory] = useState<CampusToolsDirectory | null>(null);
  const [directoryLoading, setDirectoryLoading] = useState(true);
  const [directoryError, setDirectoryError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [mine, setMine] = useState<CampusToolApplicationsMine | null>(null);
  const [notifications, setNotifications] = useState<CampusToolNotifications | null>(null);
  const [applicationStatus, setApplicationStatus] = useState("");
  const [mineLoading, setMineLoading] = useState(false);
  const [mineError, setMineError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    url: "",
    description: "",
    category: "study" as CampusToolCategory,
  });

  const loadDirectory = useCallback(async (nextQuery = "", nextCategory = "") => {
    setDirectoryLoading(true);
    setDirectoryError(null);
    setQuery(nextQuery.trim());
    try {
      setDirectory(await apiGet<CampusToolsDirectory>(
        `/campus/tools${requestSuffix(nextQuery, nextCategory)}`,
      ));
    } catch (reason) {
      setDirectoryError(reason instanceof Error ? reason.message : "无法加载校园工具。");
    } finally {
      setDirectoryLoading(false);
    }
  }, []);

  const loadMine = useCallback(async (status = "") => {
    if (!authenticated) return;
    setMineLoading(true);
    setMineError(null);
    try {
      const suffix = status ? `?status=${encodeURIComponent(status)}` : "";
      const [applications, notices] = await Promise.all([
        apiGet<CampusToolApplicationsMine>(`/campus/tools/applications/mine${suffix}`),
        apiGet<CampusToolNotifications>("/campus/tools/notifications"),
      ]);
      setMine(applications);
      setNotifications(notices);
    } catch (reason) {
      setMineError(reason instanceof Error ? reason.message : "无法加载申请记录。");
    } finally {
      setMineLoading(false);
    }
  }, [authenticated]);

  useEffect(() => {
    void loadDirectory();
  }, [loadDirectory]);

  useEffect(() => {
    if (authenticated) void loadMine();
  }, [authenticated, loadMine]);

  const categories = directory?.categories ?? (Object.keys(categoryLabels) as CampusToolCategory[]);
  const filtered = Boolean(query || category);
  const unreadNotifications = useMemo(
    () => notifications?.items.filter((item) => item.read_at === null) ?? [],
    [notifications],
  );

  const openApplicationDialog = () => {
    setNotice(null);
    if (!authenticated) {
      setNotice("登录或进入演示身份后，才能提交校园工具申请。");
      return;
    }
    setSubmitError(null);
    setDialogOpen(true);
  };

  const submitApplication = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!authenticated || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await apiMutation("/campus/tools/applications", session.csrf_token, {
        method: "POST",
        headers: { "X-Request-ID": nextRequestId("campus-tool-submit") },
        body: JSON.stringify(form),
      });
      setForm({ name: "", url: "", description: "", category: "study" });
      setDialogOpen(false);
      setNotice("申请已提交，管理员审核通过后会面向全校展示。");
      setActiveView("mine");
      await loadMine(applicationStatus);
    } catch (reason) {
      setSubmitError(reason instanceof Error ? reason.message : "提交失败，请稍后重试。");
    } finally {
      setSubmitting(false);
    }
  };

  const markNotificationRead = async (notificationId: string) => {
    try {
      await apiMutation(`/campus/tools/notifications/${notificationId}/read`, session.csrf_token, {
        method: "POST",
        body: "{}",
      });
      await loadMine(applicationStatus);
    } catch (reason) {
      setMineError(reason instanceof Error ? reason.message : "无法更新通知状态。");
    }
  };

  return (
    <Tabs.Root className="campus-tools-view" value={activeView} onValueChange={(value) => setActiveView(value as ToolView)}>
      <div className="campus-tools-subnav">
        <Tabs.List className="segmented-control" aria-label="校园工具视图">
          <Tabs.Trigger value="directory">全部工具</Tabs.Trigger>
          <Tabs.Trigger value="mine">
            我的申请
            {authenticated && (mine?.unread_count ?? 0) > 0 && <span>{mine?.unread_count}</span>}
          </Tabs.Trigger>
        </Tabs.List>
        <button className="command-button" type="button" onClick={openApplicationDialog}>
          <Plus size={16} />提交工具
        </button>
      </div>

      {notice && <div className="campus-tools-notice" role="status">{notice}</div>}

      <Tabs.Content value="directory">
        <form className="campus-tools-filter" onSubmit={(event) => { event.preventDefault(); void loadDirectory(search, category); }}>
          <label className="campus-tools-search">
            <Search size={16} aria-hidden="true" />
            <input
              type="search"
              aria-label="搜索校园工具"
              value={search}
              placeholder="搜索工具名称、说明或域名"
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <label className="compact-select">类别
            <select
              value={category}
              onChange={(event) => {
                const next = event.target.value;
                setCategory(next);
                void loadDirectory(search, next);
              }}
            >
              <option value="">全部</option>
              {categories.map((item) => <option key={item} value={item}>{categoryLabels[item]}</option>)}
            </select>
          </label>
          <button className="secondary-button" type="submit" disabled={directoryLoading}>搜索</button>
          {filtered && (
            <button
              className="text-command"
              type="button"
              onClick={() => {
                setSearch("");
                setCategory("");
                void loadDirectory();
              }}
            >
              清除筛选
            </button>
          )}
        </form>

        {directoryLoading && !directory ? <WorkspaceLoading label="正在加载校园工具" /> : !directory ? (
          <WorkspaceError message={directoryError ?? "校园工具暂不可用。"} onRetry={() => void loadDirectory(search, category)} />
        ) : (
          <>
            <div className="content-source-row campus-tools-source">
              <span>{filtered ? `${directory.items.length} 个匹配工具` : `${directory.items.length} 个已审核工具`}</span>
              <SourceBadge source={directory.source} />
            </div>
            {directory.items.length ? (
              <div className="campus-tool-grid">
                {directory.items.map((tool) => (
                  <a
                    className="campus-tool-card"
                    href={tool.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`打开 ${tool.name}`}
                    key={tool.tool_id}
                  >
                    <span className="campus-tool-card__topline">
                      <span>{categoryLabels[tool.category]}</span>
                      <ArrowUpRight size={17} aria-hidden="true" />
                    </span>
                    <strong>{tool.name}</strong>
                    <p>{tool.display_description}</p>
                    <span className="campus-tool-card__footer">
                      <span>{readableHost(tool.url)}</span>
                      <span><ShieldCheck size={14} aria-hidden="true" />管理员审核</span>
                    </span>
                  </a>
                ))}
              </div>
            ) : <WorkspaceEmpty title="暂无已上架工具" detail="当前筛选条件下没有通过审核的校园工具。" />}
          </>
        )}
      </Tabs.Content>

      <Tabs.Content value="mine">
        {!authenticated ? (
          <WorkspaceEmpty title="登录后查看申请" detail="匿名用户可以浏览已上架工具，登录后可提交并追踪审核结果。" />
        ) : mineLoading && !mine ? <WorkspaceLoading label="正在加载你的申请" /> : !mine ? (
          <WorkspaceError message={mineError ?? "申请记录暂不可用。"} onRetry={() => void loadMine(applicationStatus)} />
        ) : (
          <div className="campus-tool-applications">
            <section className="campus-tool-notifications" aria-labelledby="campus-tool-notifications-title">
              <div className="campus-tool-section-heading">
                <div><Bell size={17} /><h2 id="campus-tool-notifications-title">站内通知</h2></div>
                <span>{unreadNotifications.length} 条未读</span>
              </div>
              {notifications?.items.length ? (
                <div className="campus-tool-notification-list">
                  {notifications.items.map((item) => (
                    <article data-unread={item.read_at === null || undefined} key={item.notification_id}>
                      <div><strong>{item.title}</strong><time>{readableMoment(item.created_at)}</time></div>
                      <p>{item.body}</p>
                      {item.read_at === null && (
                        <button className="text-command" type="button" onClick={() => void markNotificationRead(item.notification_id)}>
                          <Check size={14} />标为已读
                        </button>
                      )}
                    </article>
                  ))}
                </div>
              ) : <p className="campus-tool-section-empty">暂无审核通知</p>}
            </section>

            <section aria-labelledby="campus-tool-applications-title">
              <div className="campus-tool-section-heading">
                <div><h2 id="campus-tool-applications-title">申请记录</h2></div>
                <label className="compact-select">状态
                  <select
                    value={applicationStatus}
                    onChange={(event) => {
                      const next = event.target.value;
                      setApplicationStatus(next);
                      void loadMine(next);
                    }}
                  >
                    <option value="">全部</option>
                    <option value="pending">待审核</option>
                    <option value="approved">已通过</option>
                    <option value="rejected">已驳回</option>
                  </select>
                </label>
              </div>
              {mineError && <div className="inline-alert" role="alert">{mineError}</div>}
              {mine.items.length ? (
                <div className="campus-tool-application-list">
                  {mine.items.map((application) => (
                    <article key={application.application_id}>
                      <div className="campus-tool-application-list__main">
                        <div>
                          <span className={`tool-status tool-status--${application.status}`}>{statusLabels[application.status]}</span>
                          {application.tool_status === "unpublished" && <span className="tool-status tool-status--unpublished">已下架</span>}
                        </div>
                        <strong>{application.name}</strong>
                        <p>{application.display_description}</p>
                        <a href={application.normalized_url} target="_blank" rel="noopener noreferrer">{readableHost(application.normalized_url)}</a>
                      </div>
                      <div className="campus-tool-application-list__meta">
                        <span>{categoryLabels[application.category]}</span>
                        <time>{readableMoment(application.created_at)}</time>
                        {application.decision_reason && <p><b>驳回原因：</b>{application.decision_reason}</p>}
                        {application.unpublish_reason && <p><b>下架原因：</b>{application.unpublish_reason}</p>}
                      </div>
                    </article>
                  ))}
                </div>
              ) : <WorkspaceEmpty title="暂无申请记录" detail="提交的工具会先进入管理员审核队列。" />}
            </section>
          </div>
        )}
      </Tabs.Content>

      <Dialog.Root open={dialogOpen} onOpenChange={setDialogOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="dialog-content campus-tool-submit-dialog">
            <div className="dialog-heading">
              <div><Dialog.Title>提交校园工具</Dialog.Title><Dialog.Description>审核通过后将在当前环境中面向全校展示。</Dialog.Description></div>
              <Dialog.Close className="icon-button" aria-label="关闭"><X size={18} /></Dialog.Close>
            </div>
            <form onSubmit={(event) => void submitApplication(event)}>
              <label>工具名称
                <input required maxLength={80} value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} />
              </label>
              <label>HTTPS 链接
                <input required type="url" inputMode="url" placeholder="https://example.ustc.edu.cn/" value={form.url} onChange={(event) => setForm((current) => ({ ...current, url: event.target.value }))} />
              </label>
              <label>工具分类
                <select value={form.category} onChange={(event) => setForm((current) => ({ ...current, category: event.target.value as CampusToolCategory }))}>
                  {(Object.keys(categoryLabels) as CampusToolCategory[]).map((item) => <option key={item} value={item}>{categoryLabels[item]}</option>)}
                </select>
              </label>
              <label>功能说明（可选）
                <textarea maxLength={240} rows={4} value={form.description} onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))} />
              </label>
              {submitError && <div className="inline-alert" role="alert">{submitError}</div>}
              <div className="dialog-actions">
                <Dialog.Close className="secondary-button" type="button">取消</Dialog.Close>
                <button className="command-button" type="submit" disabled={submitting}>{submitting ? "正在提交" : "提交审核"}</button>
              </div>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </Tabs.Root>
  );
}
