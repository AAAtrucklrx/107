import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

export function Empty({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return <div className="ui-empty"><Inbox size={28} aria-hidden="true" /><h2>{title}</h2>{detail && <p>{detail}</p>}{action}</div>;
}
