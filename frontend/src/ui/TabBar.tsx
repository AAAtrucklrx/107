import type { LucideIcon } from "lucide-react";
import type { Workspace } from "../types";

export interface NavigationItem { id: Workspace; label: string; icon: LucideIcon }

export function TabBar({ items, current, onSelect }: { items: NavigationItem[]; current: Workspace; onSelect: (workspace: Workspace) => void }) {
  return <nav className="scaffold-tab-bar" aria-label="底部导航">{items.map(({ id, label, icon: Icon }) =>
    <button type="button" key={id} aria-current={current === id ? "page" : undefined} onClick={() => onSelect(id)}>
      <Icon size={21} strokeWidth={1.8} aria-hidden="true" /><span>{label}</span>
    </button>
  )}</nav>;
}
