import * as Collapsible from "@radix-ui/react-collapsible";
import { ChevronDown, ExternalLink, FileCheck2, Globe2, ShieldCheck } from "lucide-react";
import { useState } from "react";
import type { Source } from "../types";

const levelLabels: Record<string, string> = {
  official_primary: "官方一手",
  reliable_independent: "独立可靠",
  general: "一般来源",
  unverified: "待核验",
  local: "本地资料",
};

function formatTime(value: string | null): string {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function SourceList({ sources }: { sources: Source[] }) {
  const [open, setOpen] = useState(false);
  if (!sources.length) return null;
  const shortTitle = (title: string) => (title.length > 16 ? `${title.slice(0, 16)}…` : title);
  return (
    <Collapsible.Root className="source-section" open={open} onOpenChange={setOpen}>
      <div className="source-pills">
        {sources.map((source) => (
          <button
            key={source.source_id}
            type="button"
            className="source-pill"
            title={source.title}
            onClick={() => setOpen(true)}
          >
            <span className="source-pill__num">{source.citation}</span>
            {shortTitle(source.title)}
          </button>
        ))}
      </div>
      <Collapsible.Trigger className="source-section__trigger">
        <span>
          <FileCheck2 size={16} aria-hidden="true" />
          来源 {sources.length}
        </span>
        <ChevronDown size={16} data-open={open} aria-hidden="true" />
      </Collapsible.Trigger>
      <Collapsible.Content className="source-section__content">
        <ol className="source-list">
          {sources.map((source) => (
            <li key={source.source_id} className="source-item">
              <span className="source-item__number">{source.citation}</span>
              <div className="source-item__body">
                <div className="source-item__title">
                  {source.display_url ? (
                    <a href={source.display_url} target="_blank" rel="noreferrer">
                      {source.title}
                      <ExternalLink size={13} aria-label="在新窗口打开" />
                    </a>
                  ) : source.title}
                </div>
                <div className="source-item__meta">
                  <span><Globe2 size={12} />{source.institution || source.domain || "本地知识库"}</span>
                  <span className={`source-level source-level--${source.level}`}>
                    <ShieldCheck size={12} />{levelLabels[source.level] ?? source.level}
                  </span>
                  <span>{source.validity === "valid" ? "有效" : source.validity}</span>
                </div>
                <dl className="source-item__dates">
                  <div><dt>发布</dt><dd>{formatTime(source.published_at)}</dd></div>
                  <div><dt>抓取</dt><dd>{formatTime(source.fetched_at)}</dd></div>
                </dl>
              </div>
            </li>
          ))}
        </ol>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}
