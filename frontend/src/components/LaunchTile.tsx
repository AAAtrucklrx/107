import { ArrowUpRight, CornerDownLeft, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";

interface LaunchTileProps {
  index: number;
  title: string;
  description: string;
  category: string;
  host: string;
  href: string;
  icon: LucideIcon;
}

interface StarterPromptTileProps {
  index: number;
  title: string;
  description: string;
  icon: LucideIcon;
  onSelect: () => void;
}

function tileIndex(index: number): string {
  return String(index).padStart(2, "0");
}

export function LaunchTile({ index, title, description, category, host, href, icon: Icon }: LaunchTileProps) {
  return (
    <a
      className="launch-tile launch-tile--link"
      href={href}
      target="_blank"
      rel="noreferrer"
      aria-label={`打开 ${title}`}
    >
      <span className="launch-tile__topline">
        <span className="launch-tile__index">{tileIndex(index)}</span>
        <Icon size={20} aria-hidden="true" />
      </span>
      <span className="launch-tile__content">
        <span className="launch-tile__category">{category}</span>
        <strong>{title}</strong>
        <span className="launch-tile__description">{description}</span>
      </span>
      <span className="launch-tile__footer">
        <span className="launch-tile__host" title={href}>{host}</span>
        <span className="launch-tile__trust"><ShieldCheck size={14} aria-hidden="true" />配置审核</span>
      </span>
      <ArrowUpRight className="launch-tile__action" size={17} aria-hidden="true" />
    </a>
  );
}

export function StarterPromptTile({ index, title, description, icon: Icon, onSelect }: StarterPromptTileProps) {
  return (
    <button className="launch-tile launch-tile--prompt" type="button" onClick={onSelect}>
      <span className="launch-tile__topline">
        <span className="launch-tile__index">{tileIndex(index)}</span>
        <Icon size={20} aria-hidden="true" />
      </span>
      <span className="launch-tile__content">
        <strong>{title}</strong>
        <span className="launch-tile__description">{description}</span>
      </span>
      <CornerDownLeft className="launch-tile__action" size={17} aria-hidden="true" />
    </button>
  );
}
