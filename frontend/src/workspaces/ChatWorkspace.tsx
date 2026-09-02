import * as Collapsible from "@radix-ui/react-collapsible";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  AlertTriangle,
  ArrowDown,
  Bot,
  Brain,
  CalendarRange,
  Check,
  ChevronDown,
  Clipboard,
  Database,
  Globe2,
  History,
  Library,
  Menu,
  MessageSquarePlus,
  Moon,
  Pencil,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  Square,
  Sun,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StarterPromptTile } from "../components/LaunchTile";
import { ConfirmDialog } from "../components/WorkspacePrimitives";
import { starterPromptsFor } from "../data/starterPrompts";
import type { StarterPromptIcon } from "../data/starterPrompts";
import {
  clearLocalConversations,
  deleteLocalConversation,
  listLocalConversations,
  putLocalConversation,
  type LocalConversation,
} from "../lib/anonymousHistory";
import { apiDelete, apiGet, apiMutation, streamRunEvents } from "../lib/api";
import type {
  AcademicSchedule,
  ChatMessage,
  ChatRunCreated,
  ConversationDetail,
  ConversationSummary,
  PublicConfig,
  RetrievalMode,
  SessionPayload,
  Source,
  SseEnvelope,
  Theme,
  ThoughtStep,
} from "../types";

const loadRenderedAnswer = () => import("../components/RenderedAnswer");
const RenderedAnswer = lazy(() => loadRenderedAnswer().then((module) => ({ default: module.RenderedAnswer })));
const loadClaimCheckList = () => import("../components/ClaimCheckList");
const ClaimCheckList = lazy(() => loadClaimCheckList().then((module) => ({ default: module.ClaimCheckList })));

interface ChatWorkspaceProps {
  config: PublicConfig;
  session: SessionPayload;
  theme?: Theme;
  onThemeToggle?: () => void;
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

const starterPromptIcons: Record<StarterPromptIcon, LucideIcon> = {
  calendar: CalendarRange,
  rules: Clipboard,
  reviews: ThumbsUp,
  services: Globe2,
  activities: Sparkles,
  library: Library,
  schedule: CalendarRange,
  grades: Check,
  program: Database,
  recommend: Search,
  conflict: AlertTriangle,
  agenda: History,
};

function nowIso(): string {
  return new Date().toISOString();
}

function greetingParts(now = new Date()): { greet: string; date: string } {
  const hour = now.getHours();
  const greet = hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  const date = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long" }).format(now);
  return { greet, date };
}

function withStage(message: ChatMessage, stage: string): ChatMessage {
  return {
    ...message,
    stage,
    stages: message.stages?.includes(stage) ? message.stages : [...(message.stages ?? []), stage],
  };
}

interface TodayCourse {
  name: string;
  start: string;
  end: string;
  location: string;
}

function todaysCourses(schedule: AcademicSchedule): TodayCourse[] {
  if (schedule.current_week == null) return [];
  const jsDay = new Date().getDay();
  const weekday = jsDay === 0 ? 7 : jsDay;
  return schedule.courses
    .flatMap((course) =>
      (course.meetings ?? [])
        .filter((meeting) => meeting.weekday === weekday && meeting.week_numbers.includes(schedule.current_week as number))
        .map((meeting) => ({
          name: course.course_name ?? "课程",
          start: meeting.start_time,
          end: meeting.end_time,
          location: meeting.location,
        })),
    )
    .sort((a, b) => a.start.localeCompare(b.start));
}

function TodayStrip({ session }: { session: SessionPayload }) {
  const [schedule, setSchedule] = useState<AcademicSchedule | null>(null);
  const personal = session.capabilities.personal_academic;

  useEffect(() => {
    if (!personal) return;
    let alive = true;
    Promise.resolve(apiGet<AcademicSchedule>("/academic/schedule"))
      .then((data) => {
        if (alive && data) setSchedule(data);
      })
      .catch(() => {
        /* 静默：条仅在有数据时出现 */
      });
    return () => {
      alive = false;
    };
  }, [personal]);

  if (!personal || !schedule) return null;
  const courses = todaysCourses(schedule);
  return (
    <section className="today-strip" aria-label="今日课程">
      <b>今日{schedule.semester ? ` · ${schedule.semester}` : ""}{schedule.current_week != null ? ` 第${schedule.current_week}周` : ""}</b>
      {courses.length === 0 ? (
        <span className="today-strip__free">今日无课</span>
      ) : (
        courses.map((course, index) => (
          <span key={`${course.start}-${course.name}-${index}`} className={`course-chip cc${(index % 5) + 1}`}>
            {course.start} {course.name} · {course.location}
          </span>
        ))
      )}
    </section>
  );
}

function randomId(prefix: string): string {
  // crypto.randomUUID 仅在 HTTPS/localhost 安全上下文可用(HTTP 部署如 8850 会抛错),
  // 用时间戳+随机数组合替代,不依赖安全上下文。
  const rand = typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function"
    ? crypto.getRandomValues(new Uint32Array(1))[0].toString(36)
    : Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now().toString(36)}-${rand}`;
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
  onDelete,
  onClear,
}: {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onClear: () => void;
}) {
  return (
    <div className="chat-history">
      <div className="chat-history__header">
        <strong>最近记录</strong>
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
            <ConfirmDialog
              title="删除这条会话？"
              description={`“${conversation.title}”删除后无法恢复。`}
              confirmLabel="删除会话"
              destructive
              onConfirm={() => onDelete(conversation.conversation_id)}
              trigger={(
                <button className="history-row__delete" type="button" aria-label={`删除会话 ${conversation.title}`}>
                  <Trash2 size={15} />
                </button>
              )}
            />
          </div>
        ))}
      </div>
      {conversations.length > 0 && (
        <ConfirmDialog
          title="清空全部历史？"
          description="当前账号或浏览器中的全部会话都会被永久删除。"
          confirmLabel="清空全部"
          destructive
          onConfirm={onClear}
          trigger={(
            <button className="text-command text-command--danger chat-history__clear" type="button">
              <Trash2 size={15} />清空历史
            </button>
          )}
        />
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
    <div className="retrieval-control" role="radiogroup" aria-label="资料范围">
      {options.map((option) => {
        const Icon = option.icon;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={value === option.value}
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

function StageStrip({ message }: { message: ChatMessage }) {
  if (message.status === "completed") return null;
  const stages = message.stages?.length ? message.stages : message.stage ? [message.stage] : [];
  if (!stages.length) return null;
  return (
    <div className="stage-strip" role="status">
      {stages.map((stage, index) => {
        const current = index === stages.length - 1;
        const label = stageLabels[stage] ?? "正在处理";
        return <span key={`${stage}-${index}`} className={current ? "stage-chip doing" : "stage-chip done"}>{label}</span>;
      })}
    </div>
  );
}

// B1: 打字机渲染 — 流式期间逐块渐显 markdown, 完成后一次全显
function TypewrittenAnswer({ message }: { message: ChatMessage }) {
  const active = message.status === "streaming";
  const full = message.content;
  const [shown, setShown] = useState(active ? 0 : full.length);
  useEffect(() => {
    if (!active) {
      setShown((prev) => (prev === full.length ? prev : full.length));
      return;
    }
    let raf = 0;
    const tick = () => {
      setShown((prev) => {
        if (prev >= full.length) return prev;
        const step = Math.max(4, Math.ceil(full.length / 150));
        return Math.min(full.length, prev + step);
      });
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [active, full]);
  return (
    <RenderedAnswer
      content={full.slice(0, shown)}
      sources={message.sources ?? []}
    />
  );
}

const thoughtDecisionLabels: Record<string, string> = {
  call_tool: "调用工具",
  retrieve: "检索资料",
  clarify: "追问澄清",
  compose: "直接回答",
};

// B5: 编辑态输入框
function UserMessageEditor({ message, onCancel, onSave }: {
  message: ChatMessage;
  onCancel: (message: ChatMessage) => void;
  onSave: (message: ChatMessage, text: string) => void;
}) {
  const [text, setText] = useState(message.content);
  return (
    <div className="user-message-editor">
      <textarea
        aria-label="编辑问题"
        value={text}
        onChange={(event) => setText(event.target.value)}
        rows={2}
        maxLength={8000}
        autoFocus
      />
      <div className="user-message-editor__actions">
        <button className="secondary-button" type="button" onClick={() => onCancel(message)}>取消</button>
        <button className="command-button" type="button" disabled={!text.trim()} onClick={() => onSave(message, text)}>重新生成</button>
      </div>
    </div>
  );
}

// B2: 可折叠思考过程卡(只展示决策与理由, 不露提示词)
function ThoughtCard({ thoughts }: { thoughts: ThoughtStep[] }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible.Root className="thought-card" open={open} onOpenChange={setOpen}>
      <Collapsible.Trigger className="thought-card__trigger" aria-label="展开思考过程">
        <span><Brain size={14} />思考过程 {thoughts.length} 步</span>
        <ChevronDown size={14} data-open={open} aria-hidden="true" />
      </Collapsible.Trigger>
      <Collapsible.Content className="thought-card__content">
        <ol className="thought-card__steps">
          {thoughts.map((thought) => (
            <li key={thought.round}>
              <span className="thought-card__badge">{thoughtDecisionLabels[thought.decision] ?? thought.decision}</span>
              <span className="thought-card__reason">{thought.reason || `第 ${thought.round} 轮决策`}</span>
            </li>
          ))}
        </ol>
      </Collapsible.Content>
    </Collapsible.Root>
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
            <Dialog.Title>帮助小蜗校准答案</Dialog.Title>
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

export function ChatWorkspace({ config, session, theme, onThemeToggle, seededQuestion, onSeedConsumed }: ChatWorkspaceProps) {
  const authenticated = session.capabilities.server_history;
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [localConversations, setLocalConversations] = useState<LocalConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<RetrievalMode>("auto");
  const [historyOpen, setHistoryOpen] = useState(false);
  
    // 顶条/移动顶栏的历史按钮通过全局事件打开抽屉（状态在本组件内）
    useEffect(() => {
      const open = () => setHistoryOpen(true);
      window.addEventListener("xiaowo:open-history", open);
      return () => window.removeEventListener("xiaowo:open-history", open);
    }, []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starterPromptsSuppressed, setStarterPromptsSuppressed] = useState(Boolean(seededQuestion));
  const streamController = useRef<AbortController | null>(null);
  const activeRun = useRef<string | null>(null);
  const messageScroll = useRef<HTMLDivElement | null>(null);
  const composerInput = useRef<HTMLTextAreaElement | null>(null);
  const followOutput = useRef(true);
  const endAnchor = useRef<HTMLDivElement | null>(null);
  const [showJumpToLatest, setShowJumpToLatest] = useState(false);
  const starterPrompts = useMemo(
    () => starterPromptsFor(session.capabilities.personal_academic),
    [session.capabilities.personal_academic],
  );

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
    setStarterPromptsSuppressed(Boolean(seededQuestion));
    void reloadHistory().catch((reason) => setError(reason instanceof Error ? reason.message : "无法读取历史。"));
    return () => streamController.current?.abort();
  }, [reloadHistory, session.principal.id]);

  useEffect(() => {
    if (seededQuestion) {
      setDraft(seededQuestion);
      setStarterPromptsSuppressed(true);
      onSeedConsumed?.();
    }
  }, [onSeedConsumed, seededQuestion]);

  const selectStarterPrompt = useCallback((question: string) => {
    setDraft(question);
    window.setTimeout(() => {
      composerInput.current?.focus();
      composerInput.current?.setSelectionRange(question.length, question.length);
    }, 0);
  }, []);

  useEffect(() => {
    if (messages.length === 0) {
      if (messageScroll.current) messageScroll.current.scrollTop = 0;
      return;
    }
    if (!followOutput.current) return;
    endAnchor.current?.scrollIntoView({ behavior: busy ? "auto" : "smooth", block: "end" });
  }, [busy, messages]);

  const handleMessageScroll = useCallback(() => {
    const element = messageScroll.current;
    if (!element) return;
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
    if (nearBottom === followOutput.current) return;
    followOutput.current = nearBottom;
    setShowJumpToLatest(!nearBottom);
  }, []);

  const jumpToLatest = useCallback(() => {
    followOutput.current = true;
    setShowJumpToLatest(false);
    endAnchor.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, []);

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
      updateAssistant(assistantId, (message) => withStage(message, String(data.stage ?? "queued")));
      return;
    }
    if (event.type === "source.found") {
      updateAssistant(assistantId, (message) => ({
        ...message,
        sources: [...(message.sources ?? []).filter((source) => source.source_id !== data.source_id), data as unknown as Source],
      }));
      return;
    }
    if (event.type === "thought.step") {
      const thought = data as unknown as ThoughtStep;
      updateAssistant(assistantId, (message) => ({
        ...message,
        thoughts: [
          ...(message.thoughts ?? []).filter((item) => item.round !== thought.round),
          thought,
        ].sort((a, b) => a.round - b.round),
      }));
      return;
    }
    if (event.type === "answer.segment") {
      updateAssistant(assistantId, (message) => ({
        ...withStage(message, "answering"),
        content: `${message.content}${String(data.markdown ?? "")}`,
      }));
      return;
    }
    if (event.type === "answer.completed") {
      activeRun.current = null;
      setBusy(false);
      setMessages((current) => {
        const next = current.map((message) => message.id === assistantId ? {
          ...withStage(message, "completed"),
          answerId: String(data.answer_id ?? ""),
          status: "completed" as const,
          sources: (data.sources as Source[] | undefined) ?? message.sources ?? [],
          claims: (data.claims as ChatMessage["claims"]) ?? [],
          limitations: (data.limitations as string[] | undefined) ?? [],
          terminalReason: String(data.terminal_reason ?? "completed"),
          truncated: Boolean(data.truncated),
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
      updateAssistant(assistantId, (message) => ({ ...withStage(message, "cancelled"), status: "cancelled", content: message.content || "已停止生成。" }));
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
    followOutput.current = true;
    setShowJumpToLatest(false);
    void loadRenderedAnswer();
    const localId = authenticated ? activeId : (activeId ?? randomId("local"));
    if (!authenticated && !activeId) setActiveId(localId);
    const userMessage: ChatMessage = { id: randomId("user"), role: "user", content: clean, createdAt: nowIso(), mode: selectedMode, status: "completed" };
    const assistantId = randomId("assistant");
    const assistantMessage: ChatMessage = { id: assistantId, role: "assistant", content: "", createdAt: nowIso(), mode: selectedMode, stage: "queued", stages: ["queued"], status: "streaming" };
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
    followOutput.current = true;
    setShowJumpToLatest(false);
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
    setStarterPromptsSuppressed(false);
    followOutput.current = true;
    setShowJumpToLatest(false);
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
      setMessages((current) => current.map((message) => message.runId === runId ? { ...withStage(message, "cancelled"), status: "cancelled" } : message));
    }
  }, [session.csrf_token]);

  const lastUserQuestion = useCallback((assistantIndex: number) => {
    for (let index = assistantIndex - 1; index >= 0; index -= 1) {
      if (messages[index].role === "user") return messages[index].content;
    }
    return "";
  }, [messages]);

  // B4: 回答触顶截断 → 以续写指令发起新一轮(历史含原问答, LLM 从断处继续)
  const continueGenerating = useCallback((message: ChatMessage) => {
    const original = lastUserQuestion(messages.indexOf(message));
    void submitQuestion(
      `请从刚才的回答截断处继续往下写（不要重复已经写过的内容）：\n${original}`,
      message.mode ?? "auto",
    );
  }, [lastUserQuestion, messages, submitQuestion]);

  // B5: 编辑已发问题重新生成(本地截断到该问题, 服务端同一会话追加新回答)
  const startEdit = useCallback((message: ChatMessage) => {
    updateAssistant(message.id, (item) => ({ ...item, editing: true }));
  }, [updateAssistant]);

  const cancelEdit = useCallback((message: ChatMessage) => {
    updateAssistant(message.id, (item) => ({ ...item, editing: false }));
  }, [updateAssistant]);

  const saveEdit = useCallback((message: ChatMessage, newText: string) => {
    const cleaned = newText.trim();
    if (!cleaned) return;
    const index = messages.indexOf(message);
    if (index < 0) return;
    setMessages((current) => current.slice(0, index));
    void submitQuestion(cleaned, mode);
  }, [messages, mode, submitQuestion]);

  const historyProps = useMemo(() => ({
    conversations,
    activeId,
    onSelect: (id: string) => void selectConversation(id).catch((reason) => setError(reason instanceof Error ? reason.message : "无法打开会话。")),
    onNew: newConversation,
    onDelete: (id: string) => void deleteConversation(id).catch((reason) => setError(reason instanceof Error ? reason.message : "删除失败。")),
    onClear: () => void clearHistory().catch((reason) => setError(reason instanceof Error ? reason.message : "清空失败。")),
  }), [activeId, clearHistory, conversations, deleteConversation, newConversation, selectConversation]);

  return (
    <section className={"chat-workspace" + (conversations.length === 0 ? " chat-workspace--solo" : "")}>
      {conversations.length > 0 && (
        <aside className="chat-history-pane"><HistoryPanel {...historyProps} /></aside>
      )}
      <div className="conversation-pane">
        <header className="conversation-header conversation-header--greeting">
          <button className="icon-button conversation-header__history" type="button" onClick={() => setHistoryOpen(true)} aria-label="打开会话历史"><Menu size={18} /></button>
          <div className="conversation-greeting">
            <strong>{greetingParts().greet}，{session.principal.profile?.name || (session.principal.authenticated ? "同学" : "游客")}</strong>
            <span>{greetingParts().date}{activeId ? " · 当前对话" : " · 问小蜗"}</span>
          </div>
          <div className="conversation-header__right">
            <Tooltip.Root>
              <Tooltip.Trigger asChild>
                <button className="icon-button" type="button" onClick={historyProps.onNew} aria-label="新建对话">
                  <MessageSquarePlus size={17} />
                </button>
              </Tooltip.Trigger>
              <Tooltip.Portal><Tooltip.Content className="tooltip">新建对话</Tooltip.Content></Tooltip.Portal>
            </Tooltip.Root>
            <div className="privacy-note"><History size={13} />{authenticated ? "服务端保留 90 天" : "仅保存在此浏览器"}</div>
            {onThemeToggle && (
              <Tooltip.Root>
                <Tooltip.Trigger asChild>
                  <button className="icon-button" type="button" onClick={onThemeToggle} aria-label={theme === "light" ? "深色主题" : "浅色主题"}>
                    {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
                  </button>
                </Tooltip.Trigger>
                <Tooltip.Portal><Tooltip.Content className="tooltip">{theme === "light" ? "深色主题" : "浅色主题"}</Tooltip.Content></Tooltip.Portal>
              </Tooltip.Root>
            )}
          </div>
        </header>
        {error && <div className="conversation-error" role="alert"><AlertTriangle size={15} />{error}</div>}
        <div className="message-scroll" ref={messageScroll} onScroll={handleMessageScroll} aria-label="对话内容">
          {messages.length === 0 ? (
            <div className="chat-empty">
              {!starterPromptsSuppressed && (
                <section className="starter-prompts" aria-labelledby="starter-prompts-title">
                  <h2 id="starter-prompts-title" className="sr-only">常见问题</h2>
                  <div className="starter-prompt-grid">
                    {starterPrompts.map((prompt, index) => (
                      <StarterPromptTile
                        key={prompt.id}
                        index={index + 1}
                        title={prompt.title}
                        description={prompt.description}
                        icon={starterPromptIcons[prompt.icon]}
                        onSelect={() => selectStarterPrompt(prompt.question)}
                      />
                    ))}
                  </div>
                </section>
              )}
              <TodayStrip session={session} />
            </div>
          ) : messages.map((message, index) => (
            <article key={message.id} className={`message message--${message.role}`}>
              <div className="message__identity">{message.role === "assistant" ? <><Bot size={15} />小蜗</> : "你"}</div>
              <div className="message__body">
                {message.role === "assistant" && <StageStrip message={message} />}
                {message.role === "user" && (message.editing ? (
                  <UserMessageEditor message={message} onCancel={cancelEdit} onSave={saveEdit} />
                ) : (
                  <div className="user-message">
                    {message.content && <div className="markdown-body"><p>{message.content}</p></div>}
                    {!busy && (
                      <button
                        className="message-action message-edit-button"
                        type="button"
                        onClick={() => startEdit(message)}
                        aria-label="编辑并重新生成"
                        title="编辑并重新生成"
                      >
                        <Pencil size={13} />
                      </button>
                    )}
                  </div>
                ))}
                {message.role === "assistant" && !!message.thoughts?.length && (
                  <ThoughtCard thoughts={message.thoughts} />
                )}
                {message.role === "assistant" && (message.content || !!message.sources?.length) && (
                  <Suspense fallback={<div className="message-render-loading" role="status" aria-label="正在呈现回答"><span /><span /></div>}>
                    <TypewrittenAnswer message={message} />
                  </Suspense>
                )}
                {message.role === "assistant" && message.truncated && message.status !== "streaming" && (
                  <div className="truncated-bar">
                    <span>回答较长，已生成到输出上限。</span>
                    <button type="button" disabled={busy} onClick={() => void continueGenerating(message)}>继续生成</button>
                  </div>
                )}
                {message.status === "failed" && <div className="message-failure"><AlertTriangle size={15} />本次回答未完成</div>}
                {!!message.claims?.some((claim) => claim.status === "conflict") && (
                  <div className="claim-conflict"><AlertTriangle size={15} /><span><strong>信息存在分歧</strong>请结合下方来源核验，不以模型猜测替代证据。</span></div>
                )}
                {message.role === "assistant" && !!message.claims?.length && message.status !== "streaming" && (
                  <Suspense fallback={null}>
                    <ClaimCheckList claims={message.claims} />
                  </Suspense>
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
        {showJumpToLatest && (
          <button className="jump-to-latest" type="button" onClick={jumpToLatest} aria-label="回到最新">
            <ArrowDown size={18} />
          </button>
        )}
        <form className="composer" onSubmit={(event) => { event.preventDefault(); void submitQuestion(draft, mode); }}>
          <textarea
            ref={composerInput}
            aria-label="向小蜗提问"
            placeholder="问问小蜗：课表、成绩、选课、空教室、校园办事……"
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
