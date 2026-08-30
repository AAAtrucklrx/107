import * as Dialog from "@radix-ui/react-dialog";
import { AlertTriangle, Bot, Database, Inbox, LoaderCircle, X } from "lucide-react";
import type { ReactNode } from "react";
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

export function WorkspaceEmpty({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="workspace-state workspace-state--empty">
      <Inbox size={20} aria-hidden="true" />
      <strong>{title}</strong>
      {detail && <span>{detail}</span>}
    </div>
  );
}

export function ConfirmDialog({
  trigger,
  title,
  description,
  confirmLabel,
  onConfirm,
  destructive = false,
}: {
  trigger: ReactNode;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
  destructive?: boolean;
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="dialog-content confirm-dialog">
          <div className="dialog-heading">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close className="icon-button" aria-label="关闭">
              <X size={18} />
            </Dialog.Close>
          </div>
          <Dialog.Description>{description}</Dialog.Description>
          <div className="dialog-actions">
            <Dialog.Close className="secondary-button">取消</Dialog.Close>
            <Dialog.Close asChild>
              <button
                className={destructive ? "danger-button" : "command-button"}
                type="button"
                onClick={onConfirm}
              >
                {confirmLabel}
              </button>
            </Dialog.Close>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
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
