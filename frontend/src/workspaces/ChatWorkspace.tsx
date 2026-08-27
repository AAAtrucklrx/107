import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  AlertTriangle,
  Bot,
  Check,
  Clipboard,
  Database,
  Globe2,
  History,
  Menu,
  MessageSquarePlus,
  RotateCcw,
  Search,
  Send,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  clearLocalConversations,
  deleteLocalConversation,
  listLocalConversations,
  putLocalConversation,
  type LocalConversation,
} from "../lib/anonymousHistory";
import { apiDelete, apiGet, apiMutation, streamRunEvents } from "../lib/api";
import type {
  ChatMessage,
  ChatRunCreated,
  ConversationDetail,
  ConversationSummary,
  PublicConfig,
  RetrievalMode,
  SessionPayload,
  Source,
  SseEnvelope,
} from "../types";

const loadRenderedAnswer = () => import("../components/RenderedAnswer");
const RenderedAnswer = lazy(() => loadRenderedAnswer().then((module) => ({ default: module.RenderedAnswer })));

interface ChatWorkspaceProps {
  config: PublicConfig;
  session: SessionPayload;
  seededQuestion?: string;
  onSeedConsumed?: () => void;
}

const stageLabels: Record<string, string> = {
  queued: "已进入队列",
  local_retrieval: "检索本地资料",
  web_search: "联网搜索",
  web_fetch: "读取公开页面",
  evidence_check: "核验证据",
  answering: "组织回答",
  completed: "回答完成",
  cancelled: "已停止",
};

const promptSeeds = [
  "查询本学期校历安排",
  "推荐适合人工智能专业的课程",
  "科大有哪些常用办事入口？",
];

function nowIso(): string {
  return new Date().toISOString();
}

function randomId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

function toChatMessages(detail: ConversationDetail): ChatMessage[] {
  return detail.messages.map((message) => ({
    id: message.message_id,
    role: message.role,
    content: message.content,
    createdAt: message.created_at,
    runId: message.run_id ?? undefined,
    answerId: message.metadata?.answer_id,
    mode: message.metadata?.mode,
    claims: message.metadata?.claims,
    sources: message.metadata?.sources,
    limitations: message.metadata?.limitations,
    status: "completed",
  }));
}

function HistoryPanel({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onClear,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onClear: () => void;
}) {
  return (
    <div className="chat-history">
      <div className="chat-history__header">
        <div>
          <span className="eyebrow">会话</span>
          <strong>最近记录</strong>
        </div>
        <Tooltip.Root>
          <Tooltip.Trigger asChild>
            <button className="icon-button" type="button" onClick={onNew} aria-label="新建对话">
              <MessageSquarePlus size={17} />
            </button>
          </Tooltip.Trigger>
          <Tooltip.Portal><Tooltip.Content className="tooltip">新建对话</Tooltip.Content></Tooltip.Portal>
        </Tooltip.Root>
      </div>
      <div className="chat-history__list">
        {conversations.length === 0 ? (
          <div className="chat-history__empty">暂无历史记录</div>
        ) : conversations.map((conversation) => (
          <div className="history-row" data-active={conversation.conversation_id === activeId} key={conversation.conversation_id}>
            <button type="button" onClick={() => onSelect(conversation.conversation_id)}>
              <span>{conversation.title}</span>
              <small>{new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit" }).format(new Date(conversation.updated_at))}</small>
            </button>
            <button className="history-row__delete" type="button" onClick={() => onDelete(conversation.conversation_id)} aria-label={`删除会话 ${conversation.title}`}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
      {conversations.length > 0 && (
        <button className="text-command text-command--danger chat-history__clear" type="button" onClick={onClear}>
          <Trash2 size={14} />清空历史
        </button>
      )}
    </div>
  );
}

function RetrievalControl({ value, disabled, webEnabled, onChange }: {
  value: RetrievalMode;
  disabled: boolean;
  webEnabled: boolean;
  onChange: (mode: RetrievalMode) => void;
}) {
  const options: Array<{ value: RetrievalMode; label: string; icon: typeof Search; disabled?: boolean }> = [
    { value: "auto", label: "自动", icon: Search },
    { value: "web", label: "联网", icon: Globe2, disabled: !webEnabled },
    { value: "local", label: "本地", icon: Database },
  ];
  return (
    <div className="retrieval-control" aria-label="资料范围">
      {options.map((option) => {
        const Icon = option.icon;
        return (
          <button
            key={option.value}
            type="button"
            data-active={value === option.value}
            disabled={disabled || option.disabled}
            title={option.disabled ? "联网服务当前未启用" : undefined}
            onClick={() => onChange(option.value)}
          >
            <Icon size={13} />{option.label}
          </button>
        );
      })}
    </div>
  );
}

function StageStrip({ stage }: { stage?: string }) {
  if (!stage || stage === "completed") return null;
  const isWeb = stage === "web_search" || stage === "web_fetch";
  return (
    <div className="stage-strip" role="status" aria-live="polite">
      <span className="stage-strip__pulse" />
      {isWeb ? <Globe2 size={14} /> : <Search size={14} />}
      <span>{stageLabels[stage] ?? "正在处理"}</span>
    </div>
  );
}

function FeedbackDialog({
  message,
  csrfToken,
  initialCategory,
}: {
  message: ChatMessage;
  csrfToken: string;
  initialCategory: "helpful" | "incorrect";
}) {
  const [open, setOpen] = useState(false);
  const [category, setCategory] = useState(initialCategory);
  const [detail, setDetail] = useState("");
  const [status, setStatus] = useState<"idle" | "sending" | "sent">("idle");
  if (!message.answerId || !message.runId) return null;

  const submit = async () => {
    setStatus("sending");
    await apiMutation(`/answers/${message.answerId}/feedback`, csrfToken, {
      method: "POST",
      body: JSON.stringify({ run_id: message.runId, category, detail }),
    });
    setStatus("sent");
  };
  const PositiveIcon = initialCategory === "helpful" ? ThumbsUp : ThumbsDown;
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button className="message-action" type="button" aria-label={initialCategory === "helpful" ? "回答有帮助" : "回答有问题"}>
          <PositiveIcon size={15} />
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content feedback-dialog">
          <div className="dialog-heading">
            <div><span className="eyebrow">回答反馈</span><Dialog.Title>帮助小蜗校准答案</Dialog.Title></div>
            <Dialog.Close className="icon-button" aria-label="关闭"><X size={17} /></Dialog.Close>
          </div>
          {status === "sent" ? (
            <div className="feedback-success"><Check size={20} /><span>反馈已记录</span></div>
          ) : (
            <>
              <label className="field-label" htmlFor={`feedback-${message.id}`}>问题类型</label>
              <select id={`feedback-${message.id}`} value={category} onChange={(event) => setCategory(event.target.value as typeof category)}>
                <option value="helpful">有帮助</option>
                <option value="incorrect">内容不正确</option>
                <option value="outdated">信息已过期</option>
                <option value="source_issue">来源有问题</option>
                <option value="other">其他</option>
              </select>
              <label className="field-label" htmlFor={`feedback-detail-${message.id}`}>补充说明（可选）</label>
              <textarea id={`feedback-detail-${message.id}`} maxLength={1000} value={detail} onChange={(event) => setDetail(event.target.value)} rows={4} />
              <div className="dialog-actions"><Dialog.Close className="secondary-button">取消</Dialog.Close><button className="command-button" disabled={status === "sending"} onClick={() => void submit()}>提交</button></div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ChatWorkspace({ config, session, seededQuestion, onSeedConsumed }: ChatWorkspaceProps) {
  const authenticated = session.capabilities.server_history;
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [localConversations, setLocalConversations] = useState<LocalConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<RetrievalMode>("auto");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamController = useRef<AbortController | null>(null);
  const activeRun = useRef<string | null>(null);
  const endAnchor = useRef<HTMLDivElement | null>(null);

  const reloadHistory = useCallback(async () => {
    if (authenticated) {
      const payload = await apiGet<{ items: ConversationSummary[] }>("/conversations?limit=50");
      setConversations(payload.items);
    } else {
      const items = await listLocalConversations();
      setLocalConversations(items);
      setConversations(items);
    }
  }, [authenticated]);

  useEffect(() => {
    setActiveId(null);
    setMessages([]);
    void reloadHistory().catch((reason) => setError(reason instanceof Error ? reason.message : "无法读取历史。"));
    return () => streamController.current?.abort();
  }, [reloadHistory, session.principal.id]);

  useEffect(() => {
    if (seededQuestion) {
      setDraft(seededQuestion);
      onSeedConsumed?.();
    }
  }, [onSeedConsumed, seededQuestion]);

  useEffect(() => {
    endAnchor.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const persistLocal = useCallback(async (nextMessages: ChatMessage[], conversationId: string, selectedMode: RetrievalMode) => {
    const existing = localConversations.find((item) => item.conversation_id === conversationId);
    const timestamp = nowIso();
    const title = nextMessages.find((item) => item.role === "user")?.content.slice(0, 48) || "新对话";
    await putLocalConversation({
      conversation_id: conversationId,
      title,
      created_at: existing?.created_at ?? timestamp,
      updated_at: timestamp,
      mode: selectedMode,
      messages: nextMessages,
    });
    await reloadHistory();
  }, [localConversations, reloadHistory]);

  const updateAssistant = useCallback((messageId: string, updater: (message: ChatMessage) => ChatMessage) => {
    setMessages((current) => current.map((message) => message.id === messageId ? updater(message) : message));
  }, []);

  const handleEvent = useCallback((event: SseEnvelope, assistantId: string, conversationId: string | null, selectedMode: RetrievalMode) => {
    const data = event.data as Record<string, unknown>;
    if (event.type === "stage.changed" || event.type === "run.created") {
      updateAssistant(assistantId, (message) => ({ ...message, stage: String(data.stage ?? "queued") }));
      return;
    }
    if (event.type === "source.found") {
      updateAssistant(assistantId, (message) => ({
        ...message,
        sources: [...(message.sources ?? []).filter((source) => source.source_id !== data.source_id), data as unknown as Source],
      }));
      return;
    }
    if (event.type === "answer.segment") {
      updateAssistant(assistantId, (message) => ({
        ...message,
        stage: "answering",
        content: `${message.content}${String(data.markdown ?? "")}`,
      }));
      return;
    }
    if (event.type === "answer.completed") {
      activeRun.current = null;
      setBusy(false);
      setMessages((current) => {
        const next = current.map((message) => message.id === assistantId ? {
          ...message,
          answerId: String(data.answer_id ?? ""),
          stage: "completed",
          status: "completed" as const,
          sources: (data.sources as Source[] | undefined) ?? message.sources ?? [],
          claims: (data.claims as ChatMessage["claims"]) ?? [],
          limitations: (data.limitations as string[] | undefined) ?? [],
          terminalReason: String(data.terminal_reason ?? "completed"),
        } : message);
        if (!authenticated && conversationId) void persistLocal(next, conversationId, selectedMode);
        return next;
      });
      if (authenticated) void reloadHistory();
      return;
    }
    if (event.type === "run.cancelled") {
      activeRun.current = null;
      setBusy(false);
      updateAssistant(assistantId, (message) => ({ ...message, stage: "cancelled", status: "cancelled", content: message.content || "已停止生成。" }));
      return;
    }
    if (event.type === "run.failed") {
      activeRun.current = null;
      setBusy(false);
      updateAssistant(assistantId, (message) => ({ ...message, status: "failed", content: String(data.message ?? "回答失败，请重试。") }));
    }
  }, [authenticated, persistLocal, reloadHistory, updateAssistant]);

  const submitQuestion = useCallback(async (question: string, selectedMode: RetrievalMode) => {
    const clean = question.trim();
    if (!clean || busy) return;
    setError(null);
    setBusy(true);
    setDraft("");
    void loadRenderedAnswer();
    const localId = authenticated ? activeId : (activeId ?? randomId("local"));
    if (!authenticated && !activeId) setActiveId(localId);
    const userMessage: ChatMessage = { id: randomId("user"), role: "user", content: clean, createdAt: nowIso(), mode: selectedMode, status: "completed" };
    const assistantId = randomId("assistant");
    const assistantMessage: ChatMessage = { id: assistantId, role: "assistant", content: "", createdAt: nowIso(), mode: selectedMode, stage: "queued", status: "streaming" };
    setMessages((current) => [...current, userMessage, assistantMessage]);

    try {
      const created = await apiMutation<ChatRunCreated>("/chat/runs", session.csrf_token, {
        method: "POST",
        body: JSON.stringify({
          question: clean,
          mode: selectedMode,
          conversation_id: authenticated ? activeId : null,
        }),
      });
      activeRun.current = created.run_id;
      if (authenticated && created.conversation_id) setActiveId(created.conversation_id);
      updateAssistant(assistantId, (message) => ({ ...message, runId: created.run_id, mode: created.requested_mode }));
      const controller = new AbortController();
      streamController.current = controller;
      let lastEventId = 0;
      let terminal = false;
      for (let attempt = 0; attempt < 3 && !terminal; attempt += 1) {
        try {
          await streamRunEvents(created.events_url, {
            signal: controller.signal,
            afterEventId: lastEventId,
            onEvent: (event) => {
              lastEventId = Math.max(lastEventId, event.id);
              terminal = ["answer.completed", "run.cancelled", "run.failed"].includes(event.type);
              handleEvent(event, assistantId, localId, selectedMode);
            },
          });
        } catch (streamError) {
          if (controller.signal.aborted) throw streamError;
          if (attempt >= 2) throw streamError;
        }
        if (!terminal && !controller.signal.aborted) {
          await new Promise((resolve) => window.setTimeout(resolve, 250 * (attempt + 1)));
        }
      }
      if (!terminal && !controller.signal.aborted) {
        throw new Error("回答流意外中断，请按原模式重试。");
      }
    } catch (reason) {
      if ((reason as DOMException)?.name === "AbortError") return;
      activeRun.current = null;
      setBusy(false);
      const message = reason instanceof Error ? reason.message : "请求失败，请重试。";
      setError(message);
      updateAssistant(assistantId, (item) => ({ ...item, content: message, status: "failed" }));
    }
  }, [activeId, authenticated, busy, handleEvent, session.csrf_token, updateAssistant]);

  const selectConversation = useCallback(async (conversationId: string) => {
    if (busy) return;
    setError(null);
    setActiveId(conversationId);
    setHistoryOpen(false);
    if (authenticated) {
      const detail = await apiGet<ConversationDetail>(`/conversations/${conversationId}`);
      setMessages(toChatMessages(detail));
    } else {
      const item = localConversations.find((value) => value.conversation_id === conversationId);
      setMessages(item?.messages ?? []);
      if (item) setMode(item.mode);
    }
  }, [authenticated, busy, localConversations]);

  const newConversation = useCallback(() => {
    if (busy) return;
    setActiveId(null);
    setMessages([]);
    setHistoryOpen(false);
    setDraft("");
  }, [busy]);

  const deleteConversation = useCallback(async (conversationId: string) => {
    if (authenticated) await apiDelete(`/conversations/${conversationId}`, session.csrf_token);
    else await deleteLocalConversation(conversationId);
    if (activeId === conversationId) newConversation();
    await reloadHistory();
  }, [activeId, authenticated, newConversation, reloadHistory, session.csrf_token]);

  const clearHistory = useCallback(async () => {
    if (authenticated) await apiDelete("/conversations", session.csrf_token);
    else await clearLocalConversations();
    newConversation();
    await reloadHistory();
  }, [authenticated, newConversation, reloadHistory, session.csrf_token]);

  const stop = useCallback(async () => {
    const runId = activeRun.current;
    if (!runId) return;
    try {
      await apiMutation(`/chat/runs/${runId}/cancel`, session.csrf_token, { method: "POST", body: "{}" });
    } finally {
      streamController.current?.abort();
      activeRun.current = null;
      setBusy(false);
      setMessages((current) => current.map((message) => message.runId === runId ? { ...message, stage: "cancelled", status: "cancelled" } : message));
    }
  }, [session.csrf_token]);

  const lastUserQuestion = useCallback((assistantIndex: number) => {
    for (let index = assistantIndex - 1; index >= 0; index -= 1) {
      if (messages[index].role === "user") return messages[index].content;
    }
    return "";
  }, [messages]);

  const historyProps = useMemo(() => ({
    conversations,
    activeId,
    onSelect: (id: string) => void selectConversation(id).catch((reason) => setError(reason instanceof Error ? reason.message : "无法打开会话。")),
    onNew: newConversation,
    onDelete: (id: string) => void deleteConversation(id).catch((reason) => setError(reason instanceof Error ? reason.message : "删除失败。")),
    onClear: () => void clearHistory().catch((reason) => setError(reason instanceof Error ? reason.message : "清空失败。")),
  }), [activeId, clearHistory, conversations, deleteConversation, newConversation, selectConversation]);

  return (
    <section className="chat-workspace">
      <aside className="chat-history-pane"><HistoryPanel {...historyProps} /></aside>
      <div className="conversation-pane">
        <header className="conversation-header">
          <button className="icon-button conversation-header__history" type="button" onClick={() => setHistoryOpen(true)} aria-label="打开会话历史"><Menu size={18} /></button>
          <div><span className="eyebrow">问小蜗</span><strong>{activeId ? "当前对话" : "新对话"}</strong></div>
          <div className="privacy-note"><History size={13} />{authenticated ? "服务端保留 90 天" : "仅保存在此浏览器"}</div>
        </header>
        {error && <div className="conversation-error" role="alert"><AlertTriangle size={15} />{error}</div>}
        <div className="message-scroll" aria-live="polite">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <div className="chat-empty__mark"><Bot size={27} /></div>
              <h1>今天从哪里开始？</h1>
              <div className="prompt-seeds">
                {promptSeeds.map((prompt) => <button type="button" key={prompt} onClick={() => setDraft(prompt)}>{prompt}</button>)}
              </div>
            </div>
          ) : messages.map((message, index) => (
            <article key={message.id} className={`message message--${message.role}`}>
              <div className="message__identity">{message.role === "assistant" ? <><Bot size={15} />小蜗</> : "你"}</div>
              <div className="message__body">
                {message.role === "assistant" && <StageStrip stage={message.stage} />}
                {message.role === "user" && message.content && <div className="markdown-body"><p>{message.content}</p></div>}
                {message.role === "assistant" && (message.content || !!message.sources?.length) && (
                  <Suspense fallback={<div className="message-render-loading" role="status" aria-label="正在呈现回答"><span /><span /></div>}>
                    <RenderedAnswer content={message.content} sources={message.sources ?? []} />
                  </Suspense>
                )}
                {message.status === "failed" && <div className="message-failure"><AlertTriangle size={15} />本次回答未完成</div>}
                {!!message.claims?.some((claim) => claim.status === "conflict") && (
                  <div className="claim-conflict"><AlertTriangle size={15} /><span><strong>信息存在分歧</strong>请结合下方来源核验，不以模型猜测替代证据。</span></div>
                )}
                {!!message.limitations?.length && (
                  <ul className="limitations">{message.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
                )}
                {message.role === "assistant" && message.status !== "streaming" && (
                  <div className="message-actions">
                    <button className="message-action" type="button" onClick={() => void navigator.clipboard.writeText(message.content)} aria-label="复制回答"><Clipboard size={15} /></button>
                    <button className="message-action" type="button" disabled={busy} onClick={() => void submitQuestion(lastUserQuestion(index), message.mode ?? "auto")} aria-label="按原模式重试"><RotateCcw size={15} /></button>
                    <button className="message-action message-action--web" type="button" disabled={busy || !config.features.web_search} onClick={() => void submitQuestion(lastUserQuestion(index), "web")} aria-label="联网重试"><Globe2 size={15} /></button>
                    <FeedbackDialog message={message} csrfToken={session.csrf_token} initialCategory="helpful" />
                    <FeedbackDialog message={message} csrfToken={session.csrf_token} initialCategory="incorrect" />
                  </div>
                )}
              </div>
            </article>
          ))}
          <div ref={endAnchor} />
        </div>
        <form className="composer" onSubmit={(event) => { event.preventDefault(); void submitQuestion(draft, mode); }}>
          <textarea
            aria-label="向小蜗提问"
            placeholder="向小蜗提问"
            maxLength={8000}
            rows={1}
            value={draft}
            disabled={busy}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void submitQuestion(draft, mode);
              }
            }}
          />
          <div className="composer__footer">
            <RetrievalControl value={mode} disabled={busy} webEnabled={config.features.web_search} onChange={setMode} />
            {busy ? (
              <button className="send-button send-button--stop" type="button" onClick={() => void stop()} aria-label="停止回答"><Square size={15} fill="currentColor" /></button>
            ) : (
              <button className="send-button" type="submit" disabled={!draft.trim()} aria-label="发送"><Send size={17} /></button>
            )}
          </div>
        </form>
      </div>

      <Dialog.Root open={historyOpen} onOpenChange={setHistoryOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="history-drawer">
            <Dialog.Title className="sr-only">会话历史</Dialog.Title>
            <Dialog.Close className="icon-button history-drawer__close" aria-label="关闭"><X size={18} /></Dialog.Close>
            <HistoryPanel {...historyProps} />
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
