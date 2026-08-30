import * as Collapsible from "@radix-ui/react-collapsible";
import { AlertTriangle, BadgeCheck, ChevronDown, FileQuestion, ShieldCheck } from "lucide-react";
import { useState } from "react";
import type { Claim } from "../types";

const statusMeta = {
  confirmed: { label: "已核实", cls: "claim-check__badge claim-check__badge--confirmed" },
  conflict: { label: "存在分歧", cls: "claim-check__badge claim-check__badge--conflict" },
  insufficient: { label: "证据不足", cls: "claim-check__badge claim-check__badge--insufficient" },
} as const;

const statusIcons = {
  confirmed: BadgeCheck,
  conflict: AlertTriangle,
  insufficient: FileQuestion,
} as const;

function clip(text: string, limit = 140): string {
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

/**
 * ④ 引用逐条核查: 每条声明带状态徽标(已核实/存在分歧/证据不足)与对应来源编号。
 * 数据来自 evidence 管线的 claims(answer.completed 事件), 本地模式为单条整答声明。
 */
export function ClaimCheckList({ claims }: { claims: Claim[] }) {
  const [open, setOpen] = useState(false);
  if (!claims.length) return null;
  return (
    <Collapsible.Root className="claim-check-section" open={open} onOpenChange={setOpen}>
      <Collapsible.Trigger className="claim-check-section__trigger" aria-label="展开逐条核查">
        <span><ShieldCheck size={14} />逐条核查 {claims.length} 条</span>
        <ChevronDown size={14} data-open={open} aria-hidden="true" />
      </Collapsible.Trigger>
      <Collapsible.Content className="claim-check-section__content">
        <ol className="claim-check-list">
          {claims.map((claim) => {
            const meta = statusMeta[claim.status] ?? statusMeta.insufficient;
            const Icon = statusIcons[claim.status] ?? FileQuestion;
            const citations = [...new Set((claim.evidence ?? []).map((item) => item.citation).filter(Boolean))];
            return (
              <li key={claim.claim_id} className="claim-check">
                <span className={`${meta.cls}`}><Icon size={12} />{meta.label}</span>
                <div className="claim-check__body">
                  <span className="claim-check__text">{clip(claim.text)}</span>
                  {citations.length > 0 && (
                    <span className="claim-check__sources">来源 [{citations.join("][")}]</span>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}
