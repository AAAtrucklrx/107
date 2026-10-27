import { HomeWelcome } from "../components/HomeWelcome";
import * as Collapsible from "@radix-ui/react-collapsible";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import { AlertTriangle, ArrowDown, Bot, Brain, CalendarRange, Check, ChevronDown, Clipboard, Database, Globe2, History, Library, Menu, Plus, ChevronRight, MapPin, MessageSquarePlus, Moon, Pencil, RotateCcw, Search, Send, Sparkles, Square, Sun, ThumbsDown, ThumbsUp, Trash2, X, LogIn } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { familyCharacters, useAppearance } from "../appearance/AppearanceProvider";
import { Sheet } from "../ui/Sheet";
import { Skeleton } from "../ui/Skeleton";
import { useVisualViewport } from "../hooks/useVisualViewport";
import { emitMascot } from "../mascot/events";
import "../styles/chat.css";
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
import { ApiClientError, apiDelete, apiGet, apiMutation, streamRunEvents } from "../lib/api";
import type {
  AcademicSchedule,
  ChatMessage,
  ChatRunCreated,
  CampusActivity,
  ConversationDetail,
  ConversationSummary,
  PublicConfig,
  RetrievalMode,
  SessionPayload,
  Source,
  SseEnvelope,
  Theme,
  StructuredBlock,
  ThoughtStep,
} from "../types";

// 占位流式文案（与后端 manager 的 placeholder 段一致）：首段 answer.delta 到达时被替换
const STREAM_PLACEHOLDER = "小蛙助手正在为你整理答案，请稍候…";

const loadRenderedAnswer = () => import("../components/RenderedAnswer");
const RenderedAnswer = lazy(() => loadRenderedAnswer().then((module) => ({ default: module.RenderedAnswer })));
const loadClaimCheckList = () => import("../components/ClaimCheckList");
const ClaimCheckList = lazy(() => loadClaimCheckList().then((module) => ({ default: module.ClaimCheckList })));

interface ChatWorkspaceProps {
  active?: boolean;
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
  action: "处理中",
  completed: "回答完成",
  cancelled: "已停止",
};

const pocketPrompts = [
  { title: "今日课表", question: "请查询我今天的课表，并按时间列出课程和地点。", icon: CalendarRange },
  { title: "找自习室", question: "帮我找一个适合自习的空教室。", icon: MapPin },
  { title: "选课建议", question: "我想了解选课建议，可以从哪些方面考虑？", icon: Search },
];

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
  const hour = Number(new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone: "Asia/Shanghai" }).format(now));
  const greet = hour < 12 ? "早上好" : hour < 18 ? "下午好" : "晚上好";
  const date = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "long", timeZone: "Asia/Shanghai" }).format(now);
  return { greet, date };
}

function withStage(message: ChatMessage, stage: string, actionMessage?: string): ChatMessage {
  return {
    ...message,
    stage,
    stages: message.stages?.includes(stage) ? message.stages : [...(message.stages ?? []), stage],
    stageMessage: actionMessage ?? message.stageMessage ?? "",
  };
}

interface TodayCourse {
  name: string;
  start: string;
  end: string;
  location: string;
}

function todaysCourses(schedule: AcademicSchedule | null): TodayCourse[] {
  if (!schedule || schedule.current_week == null) return [];
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

function TodayPopup({ open, onClose, session }: { open: boolean; onClose: () => void; session: SessionPayload }) {
  const [schedule, setSchedule] = useState<AcademicSchedule | null>(null);
  // 2026-09-05 审核修复：区分「加载中/失败/真的无课」三态，加载与失败不再闪现「今日无课」
  const [scheduleLoaded, setScheduleLoaded] = useState(false);
  const [activities, setActivities] = useState<CampusActivity[] | null>(null);
  const personal = session.capabilities.personal_academic;

  useEffect(() => {
    if (!open || !personal) return;
    let alive = true;
    setScheduleLoaded(false);
    Promise.resolve(apiGet<AcademicSchedule>("/academic/schedule"))
      .then((data) => { if (alive && data) setSchedule(data); })
      .catch(() => { /* 静默：失败按 spec 显示「今日无课」（不再无限加载态） */ })
      .finally(() => { if (alive) setScheduleLoaded(true); });
    Promise.resolve(apiGet<{ items?: CampusActivity[] }>("/campus/activities?time_window=今日&limit=3"))
      .then((data) => { if (alive && data && Array.isArray(data.items)) setActivities(data.items); })
      .catch(() => { /* 静默 */ });
    return () => { alive = false; };
  }, [open, personal]);

  if (!open || !personal) return null;
  const courses = todaysCourses(schedule);
  return (
    // 2026-09-05 审核修复：改用项目统一的 Radix Dialog（与 FeedbackDialog 一致），
    // 白得 Esc 关闭/焦点陷阱/aria-modal/背景滚动锁定；遮罩点击与 X/知道了关闭不变
    <Dialog.Root open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content today-popup" aria-describedby={undefined}>
          <div className="dialog-heading">
            <Dialog.Title asChild>
              <h2>今日{schedule?.semester ? ` · ${schedule.semester}` : ""}{schedule?.current_week != null ? ` 第${schedule.current_week}周` : ""}</h2>
            </Dialog.Title>
            <Dialog.Close className="dialog-close" aria-label="关闭">×</Dialog.Close>
          </div>
          <div className="today-popup-section">
            <div className="today-popup-section-title">📚 今日课程</div>
            {!scheduleLoaded ? (
              <div className="today-popup-empty">正在加载今日日程…</div>
            ) : courses.length === 0 ? (
              <div className="today-popup-empty">今日无课</div>
            ) : (
              <ul className="today-popup-list">
                {courses.slice(0, 5).map((course, index) => (
                  <li key={`${course.start}-${course.name}-${index}`}>
                    <b>{course.start}</b> {course.name} · {course.location}
                  </li>
                ))}
              </ul>
            )}
          </div>
          {activities && activities.length > 0 && (
            <div className="today-popup-section">
              <div className="today-popup-section-title">🎯 今日可以报名</div>
              <ul className="today-popup-list today-popup-activities">
                {activities.slice(0, 3).map((item, index) => (
                  <li key={`${item.start_time}-${item.title}-${index}`} title={String(item.location ?? "")}>
                    <span className="today-popup-activity-name">📌 {item.title}</span>
                    <span className="today-popup-activity-meta">
                      {item.start_time ? String(item.start_time).slice(11, 16) : ""}
                      {item.location ? ` · ${item.location}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="today-popup-footer">
            <Dialog.Close className="button button--primary">知道了</Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
    structured: (message.metadata as { structured?: StructuredBlock[] } | undefined)?.structured,
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
  // 2026-09-16：三选一（自动/联网/本地）收敛成一个「联网搜索」开关。
  // 「纯联网」不再是可选模式——联网只在**本地答不出来**时自动兜底；
  // 需要强制联网核一遍时，用回答下方的「强制联网重答」。
  const online = webEnabled && value !== "local";
  return (
    <div className="retrieval-control" role="group" aria-label="联网搜索">
      <button
        type="button"
        role="switch"
        aria-checked={online}
        data-active={online}
        disabled={disabled || !webEnabled}
        title={webEnabled
          ? (online ? "已开启：本地答不出来时会联网补充" : "已关闭：只用本地资料")
          : "联网服务当前未启用"}
        onClick={() => {
          if (!webEnabled) return;
          onChange(online ? "local" : "auto");
        }}
      >
        <Globe2 size={13} />联网搜索 · {online ? "开" : "关"}
      </button>
    </div>
  );
}

function StageStrip({ message }: { message: ChatMessage }) {
  if (message.status === "completed") return null;
  const stages = message.stages?.length ? message.stages : message.stage ? [message.stage] : [];
  if (!stages.length) return null;
  return (
    <details className="stage-progress">
      <summary><span role="status">{stageLabels[stages.at(-1) ?? ""] ?? "正在处理"}</span><ChevronDown size={14} aria-hidden="true" /></summary>
      <div className="stage-strip">
      {stages.map((stage, index) => {
        const current = index === stages.length - 1;
        const label = stageLabels[stage] ?? "正在处理";
        const note = current ? message.stageMessage ?? "" : "";
        return (
          <span key={`${stage}-${index}`} className={current ? "stage-chip doing" : "stage-chip done"} title={note}>
            {label}{note ? ` · ${note}` : ""}
          </span>
        );
      })}
      </div>
    </details>
  );
}

// B1: 打字机渲染 — 流式期间逐块渐显 markdown, 完成后一次全显
function TypewrittenAnswer({ message, onAsk }: { message: ChatMessage; onAsk?: (question: string) => void }) {
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
    <>
      {(message.structured ?? []).map((table, index) => (
        <DataTable key={index} table={table} onAsk={onAsk} />
      ))}
      <RenderedAnswer
        content={full.slice(0, shown)}
        sources={message.sources ?? []}
      />
      {active && <span className="streaming-cursor" aria-label="正在生成" />}
    </>
  );
}

/** 给分档位的文字取值（用于语义分级）。 */
const GIVE_SCORE_HIGH = new Set(["超好", "很好", "好"]);
const GIVE_SCORE_LOW = new Set(["杀手", "很差", "差"]);

/** 数据卡单元格的语义分级：评分/给分列按档位返回 high|mid|low。
 *  颜色只作辅助——单元格里的**数字或文字本身**仍是主要线索（WCAG 1.4.1），
 *  所以 mid 不着色，且不做任何数值改写。 */
function cellLevel(column: string, cell: string): "high" | "mid" | "low" | undefined {
  const value = cell.trim();
  if (!value || value === "—") return undefined;
  if (/评分|均分/.test(column)) {
    const score = Number.parseFloat(value);
    if (!Number.isFinite(score) || score <= 0) return undefined;
    if (score >= 9) return "high";
    if (score < 6) return "low";
    return "mid";
  }
  if (column.includes("给分")) {
    if (GIVE_SCORE_HIGH.has(value)) return "high";
    if (GIVE_SCORE_LOW.has(value)) return "low";
    return "mid";
  }
  return undefined;
}

function DataTable({ table, onAsk }: { table: StructuredBlock; onAsk?: (question: string) => void }) {
  // 2026-09-22：课程类卡片可点 —— 点击某行/快捷按钮会把该课的追问问句填进输入框
  //（只填充不自动发送，同学可以改完再发）。没有回调时保持原来的纯展示表格。
  const questions = table.row_questions ?? [];
  const interactive = Boolean(onAsk) && questions.some(Boolean);
  const ask = (question: string | null | undefined) => {
    if (onAsk && question) onAsk(question);
  };
  return (
    <div className="structured-table" role="table" aria-label={table.title}>
      <div className="structured-table-title">
        {table.title}
        {interactive && <span className="structured-table-hint">点击任意一行可继续追问</span>}
      </div>
      <table>
        <thead>
          <tr>{table.columns.map((column, i) => <th key={i}>{column}</th>)}</tr>
        </thead>
        <tbody>
          {table.rows.map((row, ri) => {
            const question = questions[ri] ?? null;
            const rowInteractive = Boolean(onAsk && question);
            return (
              <tr
                key={ri}
                className={rowInteractive ? "structured-table-row--ask" : undefined}
                tabIndex={rowInteractive ? 0 : undefined}
                aria-label={rowInteractive ? `点击追问：${question}` : undefined}
                title={rowInteractive ? `点击追问：${question}` : undefined}
                onClick={rowInteractive ? () => ask(question) : undefined}
                onKeyDown={rowInteractive ? (event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    ask(question);
                  }
                } : undefined}
              >
                {row.map((cell, ci) => (
                  <td key={ci} data-level={cellLevel(table.columns[ci] ?? "", cell)}>{cell}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
      {Boolean(onAsk && table.actions?.length) && (
        <div className="structured-table-actions">
          {table.actions?.map((action, i) => (
            <button
              key={i}
              type="button"
              className="structured-table-action"
              onClick={() => ask(action.question)}
            >
              {action.label}
            </button>
          ))}
        </div>
      )}
    </div>
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
  const [error, setError] = useState<string | null>(null);
  if (!message.answerId || !message.runId) return null;

  const submit = async () => {
    setStatus("sending");
    setError(null);
    try {
      await apiMutation(`/answers/${message.answerId}/feedback`, csrfToken, {
        method: "POST",
        body: JSON.stringify({ run_id: message.runId, category, detail }),
      });
      setStatus("sent");
    } catch (reason) {
      // 2026-09-17 修 P1-2：原来没有 try/catch —— 提交失败（如 422 说明含个人信息、
      // 网络错误）会让弹窗永远停在「提交中」，按钮禁用且没有任何提示，用户只能关掉重来。
      setStatus("idle");
      setError(reason instanceof Error ? reason.message : "反馈提交失败，请稍后重试。");
    }
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
            <Dialog.Title>帮助小蛙助手校准答案</Dialog.Title>
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
              {error && <p className="feedback-error" role="alert">{error}</p>}
              <div className="dialog-actions"><Dialog.Close className="secondary-button">取消</Dialog.Close><button className="command-button" disabled={status === "sending"} onClick={() => void submit()}>提交</button></div>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function ChatWorkspace({ config, session, theme, onThemeToggle, seededQuestion, onSeedConsumed, active = true }: ChatWorkspaceProps) {
  useVisualViewport();
  const { style, character } = useAppearance();
  const selectedCharacter = familyCharacters.find((member) => member.id === character) ?? familyCharacters[0];
  const authenticated = session.capabilities.server_history;
  // 今日弹窗（2026-09-05）：登录（个人课表能力）打开即弹；演示阶段每次打开都显示
  const [todayPopupOpen, setTodayPopupOpen] = useState<boolean>(Boolean(session.capabilities.personal_academic));
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [localConversations, setLocalConversations] = useState<LocalConversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [mode, setMode] = useState<RetrievalMode>("auto");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const toolsReturnFocus = useRef<HTMLElement | null>(null);
  const historyReturnFocus = useRef<HTMLElement | null>(null);
  const openHistory = useCallback(() => {
    historyReturnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setHistoryOpen(true);
  }, []);
  
    // 顶条/移动顶栏的历史按钮通过全局事件打开抽屉（状态在本组件内）
    useEffect(() => {
      const open = openHistory;
      window.addEventListener("xiaowo:open-history", open);
      return () => window.removeEventListener("xiaowo:open-history", open);
    }, [openHistory]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [starterPromptsSuppressed, setStarterPromptsSuppressed] = useState(Boolean(seededQuestion));
  const streamController = useRef<AbortController | null>(null);
  const requestGeneration = useRef(0);
  const activeRun = useRef<string | null>(null);
  const messageScroll = useRef<HTMLDivElement | null>(null);
  const composerInput = useRef<HTMLTextAreaElement | null>(null);
  const composerForm = useRef<HTMLFormElement | null>(null);
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
    setBusy(false);
    activeRun.current = null;
    setStarterPromptsSuppressed(Boolean(seededQuestion));
    void reloadHistory().catch((reason) => setError(reason instanceof Error ? reason.message : "无法读取历史。"));
    return () => { requestGeneration.current++; streamController.current?.abort(); emitMascot("idle"); };
  }, [reloadHistory, session.principal.id]);

  useEffect(() => {
    if (seededQuestion) {
      setDraft(seededQuestion);
      setStarterPromptsSuppressed(true);
      onSeedConsumed?.();
    }
  }, [onSeedConsumed, seededQuestion]);

  // Grow with the draft, with internal scrolling after six lines.
  useEffect(() => {
    const input = composerInput.current;
    if (!input || !active) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(132, Math.max(44, input.scrollHeight))}px`;
  }, [draft, active]);

  useEffect(() => {
    const form = composerForm.current;
    if (!form || typeof ResizeObserver === "undefined") return;
    const update = () => document.documentElement.style.setProperty("--composer-height", `${form.getBoundingClientRect().height + 20}px`);
    const observer = new ResizeObserver(update);
    observer.observe(form);
    update();
    return () => { observer.disconnect(); document.documentElement.style.removeProperty("--composer-height"); };
  }, []);

  const selectStarterPrompt = useCallback((question: string) => {
    setDraft(question);
    window.setTimeout(() => {
      composerInput.current?.focus();
      composerInput.current?.setSelectionRange(question.length, question.length);
    }, 0);
  }, []);

  useEffect(() => {
    if (!active) return;
    if (messages.length === 0) {
      if (messageScroll.current) messageScroll.current.scrollTop = 0;
      return;
    }
    if (!followOutput.current) return;
    endAnchor.current?.scrollIntoView({ behavior: busy ? "auto" : "smooth", block: "end" });
  }, [active, busy, messages]);

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
      const stage = String(data.stage ?? "queued");
      const note = String(data.message ?? "");
      updateAssistant(assistantId, (message) => withStage(message, stage, note));
      return;
    }
    if (event.type === "data.table") {
      const table = data as unknown as StructuredBlock;
      updateAssistant(assistantId, (message) => {
        // 同一张卡服务端可能推两次（act 节点实时推 + run 结束重推），按内容指纹覆盖去重。
        // 键必须含内容指纹：只用 title|source_tool|行数 会把「内容不同但同形」的卡误删。
        const key = (t: StructuredBlock) =>
          `${t.title}|${t.source_tool ?? ""}|${JSON.stringify(t.rows ?? [])}`;
        const existing = (message.structured ?? []).filter((t) => key(t) !== key(table));
        return { ...message, structured: [...existing, table] };
      });
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
    if (event.type === "answer.delta") {
      // compose 增量流式：正文 token 逐段拼接（占位文案被首段 delta 替换）
      updateAssistant(assistantId, (message) => {
        const prev = message.content === STREAM_PLACEHOLDER ? "" : message.content;
        return { ...withStage(message, "answering"), content: prev + String(data.delta ?? "") };
      });
      return;
    }
    if (event.type === "answer.segment") {
      // 占位段与最终段均为替换语义：流式已拼出的内容由最终段/answer.completed 覆盖
      const markdown = String(data.markdown ?? "");
      updateAssistant(assistantId, (message) => ({
        ...withStage(message, "answering"),
        content: data.placeholder === true ? STREAM_PLACEHOLDER : (markdown || message.content),
      }));
      return;
    }
    if (event.type === "answer.completed") {
      const terminalReason = String(data.terminal_reason ?? "completed");
      const incomplete = ["EVIDENCE_INSUFFICIENT", "CRAWL_BLOCKED", "GENERATION_TIMEOUT_PARTIAL"].includes(terminalReason);
      emitMascot(incomplete ? "error" : "success", incomplete ? { message: "诶？这次还没查到完整结果。" } : undefined);
      activeRun.current = null;
      setBusy(false);
      // 2026-09-04：最终 claims 文本替换占位段（"小蛙助手正在为你整理答案…"）
      const finalClaims = (data.claims as Array<{ text?: string }> | undefined) ?? [];
      const finalText = finalClaims.find((claim) => typeof claim.text === "string" && claim.text.trim())?.text ?? "";
      setMessages((current) => {
        const next = current.map((message) => message.id === assistantId ? {
          ...withStage(message, "completed"),
          content: finalText || message.content || "",
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
      emitMascot("idle");
      activeRun.current = null;
      setBusy(false);
      updateAssistant(assistantId, (message) => ({ ...withStage(message, "cancelled"), status: "cancelled", content: message.content || "已停止生成。" }));
      return;
    }
    if (event.type === "run.failed") {
      emitMascot("error", { message: "刚才没接上，再试一次吧。" });
      activeRun.current = null;
      setBusy(false);
      updateAssistant(assistantId, (message) => ({ ...message, status: "failed", content: String(data.message ?? "回答失败，请重试。") }));
    }
  }, [authenticated, persistLocal, reloadHistory, updateAssistant]);

  const submitQuestion = useCallback(async (question: string, selectedMode: RetrievalMode) => {
    const clean = question.trim();
    if (!clean || busy) return;
    const generation = ++requestGeneration.current;
    const current = () => generation === requestGeneration.current;
    const controller = new AbortController();
    streamController.current = controller;
    emitMascot("think");
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
        signal: controller.signal,
        body: JSON.stringify({
          question: clean,
          mode: selectedMode,
          conversation_id: authenticated ? activeId : null,
        }),
      });
      if (!current() || controller.signal.aborted) return;
      activeRun.current = created.run_id;
      if (authenticated && created.conversation_id) setActiveId(created.conversation_id);
      updateAssistant(assistantId, (message) => ({ ...message, runId: created.run_id, mode: created.requested_mode }));
      let lastEventId = 0;
      let terminal = false;
      for (let attempt = 0; attempt < 3 && !terminal; attempt += 1) {
        try {
          await streamRunEvents(created.events_url, {
            signal: controller.signal,
            afterEventId: lastEventId,
            onEvent: (event) => {
              if (!current() || controller.signal.aborted) return;
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
      if (!current()) return;
      if ((reason as DOMException)?.name === "AbortError") { emitMascot("idle"); return; }
      activeRun.current = null;
      setBusy(false);
      const message = reason instanceof Error ? reason.message : "请求失败，请重试。";
      setError(message);
      // 2026-09-17 修 P3-10：匿名问个人数据时后端返回 401 AUTH_REQUIRED —— 这是**设计好的引导**，
      // 不是故障。原来一律渲染成"本次回答未完成"，看着像坏了，而且没告诉用户怎么登录。
      const loginRequired = reason instanceof ApiClientError && reason.code === "AUTH_REQUIRED";
      emitMascot(loginRequired ? "idle" : "error", { message: loginRequired ? "连接学校账号后，我就能帮你查啦。" : "刚才没接上，再试一次吧。" });
      updateAssistant(assistantId, (item) => ({
        ...item,
        content: message,
        status: loginRequired ? "login_required" : "failed",
      }));
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
    const generation = requestGeneration.current;
    if (!runId) {
      requestGeneration.current++;
      streamController.current?.abort();
      setBusy(false);
      emitMascot("idle");
      setMessages((current) => current.map((message) => message.status === "streaming" ? { ...withStage(message, "cancelled"), status: "cancelled", content: "已停止生成。" } : message));
      return;
    }
    try {
      await apiMutation(`/chat/runs/${runId}/cancel`, session.csrf_token, { method: "POST", body: "{}" });
    } catch (reason) {
      if (generation === requestGeneration.current) setError(reason instanceof Error ? reason.message : "未能通知服务停止，已在本机停止接收。");
    } finally {
      if (generation !== requestGeneration.current || activeRun.current !== runId) return;
      requestGeneration.current++;
      emitMascot("idle");
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
    <>
    <TodayPopup open={active && todayPopupOpen} onClose={() => setTodayPopupOpen(false)} session={session} />
    <section className="chat-workspace">
      <div className="conversation-pane">
        {(messages.length > 0 || starterPromptsSuppressed) && <header className="conversation-header">
          <button className="icon-button" type="button" onClick={openHistory} aria-label="打开会话历史"><Menu size={18} /></button>
          <span className="conversation-title">{conversations.find((item) => item.conversation_id === activeId)?.title || "和小蛙聊聊"}</span>
          <button className="icon-button" type="button" onClick={historyProps.onNew} aria-label="新建对话"><MessageSquarePlus size={18} /></button>
        </header>}
        {error && <div className="conversation-error" role="alert"><AlertTriangle size={15} />{error}</div>}
        <div className="message-scroll" ref={messageScroll} onScroll={handleMessageScroll} aria-label="对话内容">
          {messages.length === 0 ? (
            <div className="chat-empty">
              <HomeWelcome personal={!!session.capabilities.personal_academic} greeting={greetingParts().greet} name={session.jw_identity?.name || session.principal.profile?.name || (session.principal.authenticated ? "同学" : undefined)} date={greetingParts().date} />
              {!starterPromptsSuppressed && (
                <section className="starter-prompts" aria-labelledby="starter-prompts-title">
                  <h2 id="starter-prompts-title" className="sr-only">常见问题</h2>
                  <div className="pocket-shortcuts">
                    {pocketPrompts.map((prompt) => <button key={prompt.title} type="button" onClick={() => selectStarterPrompt(prompt.question)}>
                      <prompt.icon size={20} aria-hidden="true" /><span>{prompt.title}</span>
                    </button>)}
                  </div>
                </section>
              )}
              {conversations[0] && <section className="pocket-recent" aria-labelledby="pocket-recent-title">
                <div className="pocket-recent__heading"><h2 id="pocket-recent-title">最近聊过</h2><button type="button" onClick={openHistory}>全部<ChevronRight size={14} /></button></div>
                <button className="pocket-recent__item" type="button" onClick={() => historyProps.onSelect(conversations[0].conversation_id)}>
                  <span>{conversations[0].title}</span><ChevronRight size={16} aria-hidden="true" />
                </button>
              </section>}
            </div>
          ) : messages.map((message, index) => (
            <article key={message.id} className={`message message--${message.role}`}>
              <div className="message__identity">{message.role === "assistant" ? <>{style === "frog" ? <img className="message__avatar" src={selectedCharacter.src} alt="" width="28" height="28" /> : <Bot size={15} />}小蛙助手</> : "你"}</div>
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
                  <Suspense fallback={<Skeleton label="正在呈现回答" lines={2} />}>
                    <TypewrittenAnswer message={message} onAsk={selectStarterPrompt} />
                  </Suspense>
                )}
                {message.role === "assistant" && message.truncated && message.status !== "streaming" && (
                  <div className="truncated-bar">
                    <span>回答较长，已生成到输出上限。</span>
                    <button type="button" disabled={busy} onClick={() => void continueGenerating(message)}>继续生成</button>
                  </div>
                )}
                {message.status === "failed" && <div className="message-failure"><AlertTriangle size={15} />本次回答未完成</div>}
                {message.status === "login_required" && (
                  <div className="message-login-required" role="note">
                    <LogIn size={15} />
                    <span>{message.content || "个人学业问题需要先登录。"}</span>
                    <small>前往「我的」连接学校账号，登录后重试。</small>
                  </div>
                )}
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
                    <button className="message-action message-action--web" type="button" disabled={busy || !config.features.web_search} title="忽略本地答案，强制联网重答一遍" onClick={() => void submitQuestion(lastUserQuestion(index), "web")} aria-label="强制联网重答"><Globe2 size={15} /></button>
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
        <form className="composer" ref={composerForm} onSubmit={(event) => { event.preventDefault(); void submitQuestion(draft, mode); }}>
          <button className="composer-tools" type="button" aria-label="校园工具与搜索设置" title={mode === "local" ? "校园工具 · 联网关闭" : "校园工具 · 联网开启"} onClick={() => { toolsReturnFocus.current = document.activeElement as HTMLElement; setToolsOpen(true); }}><Plus size={21} /></button>
          <textarea
            ref={composerInput}
            aria-label="向小蛙助手提问"
            placeholder="想问什么，尽管说"
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
          {busy ? (
              <button className="send-button send-button--stop" type="button" onClick={() => void stop()} aria-label="停止回答"><Square size={15} fill="currentColor" /></button>
            ) : (
              <button className="send-button" type="submit" disabled={!draft.trim()} aria-label="发送"><Send size={17} /></button>
          )}
        </form>
      </div>

      <Sheet title="校园工具" open={active && toolsOpen} onOpenChange={setToolsOpen} returnFocusRef={toolsReturnFocus}>
        <div className="pocket-tools">
          <div className="pocket-tools__search"><RetrievalControl value={mode} disabled={busy} webEnabled={config.features.web_search} onChange={setMode} /><small>{config.features.web_search ? "需要时补充公开网络资料" : "联网服务暂未启用"}</small></div>
          <div className="pocket-tools__list">{starterPrompts.map((prompt) => {
            const Icon = starterPromptIcons[prompt.icon];
            return <button type="button" key={prompt.id} onClick={() => { setToolsOpen(false); selectStarterPrompt(prompt.question); }}><Icon size={18} /><span>{prompt.title}</span><ChevronRight size={16} /></button>;
          })}</div>
          {onThemeToggle && <button className="pocket-tools__theme" type="button" onClick={onThemeToggle}>{theme === "light" ? <Moon size={17} /> : <Sun size={17} />}{theme === "light" ? "深色主题" : "浅色主题"}</button>}
        </div>
      </Sheet>
      <Sheet title="会话历史" description={authenticated ? "保留最近的对话，随时继续。" : "对话仅保存在当前浏览器。"} open={active && historyOpen} onOpenChange={setHistoryOpen} returnFocusRef={historyReturnFocus}>
        <HistoryPanel {...historyProps} />
      </Sheet>
    </section>
    </>
  );
}
