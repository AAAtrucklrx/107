import { AlertTriangle, Bot, Database, LoaderCircle } from "lucide-react";
import type { DataSource } from "../types";

export function WorkspaceLoading({ label }: { label: string }) {
  return <div className="workspace-state" role="status"><LoaderCircle className="spin" size={21} /><span>{label}</span></div>;
}

export function WorkspaceError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="workspace-state workspace-state--error" role="alert">
      <AlertTriangle size={20} />
      <span>{message}</span>
      <button className="secondary-button" type="button" onClick={onRetry}>重试</button>
    </div>
  );
}

export function SourceBadge({ source }: { source: DataSource }) {
  return (
    <span className="data-source" data-stale={source.stale || undefined} data-demo={source.demo || undefined}>
      <Database size={12} />{source.label}
    </span>
  );
}

export function AskXiaowoButton({ question, onAsk, compact = false }: {
  question: string;
  onAsk: (question: string) => void;
  compact?: boolean;
}) {
  return (
    <button className={`ask-xiaowo ${compact ? "ask-xiaowo--compact" : ""}`} type="button" onClick={() => onAsk(question)}>
      <Bot size={14} />问问小蜗
    </button>
  );
}

export function Limitations({ items }: { items?: string[] }) {
  if (!items?.length) return null;
  return <ul className="workspace-limitations">{items.map((item) => <li key={item}>{item}</li>)}</ul>;
}
