"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { FLOW_TAPE } from "@/lib/mock";
import { cn, fmtNum } from "@/lib/utils";

const TAG_TONE = {
  SWEEP: "text-brand-hi border-brand/30 bg-brand-dim",
  BLOCK: "text-signal-info border-signal-info/30 bg-signal-info/10",
  OPENING: "text-signal-warn border-signal-warn/30 bg-signal-warn/10",
  REPEAT: "text-ink-muted border-line bg-bg-subtle",
};

export function FlowTape() {
  return (
    <Panel
      title="Options Flow Tape"
      subtitle="Live · aggressor + intent tag"
      actions={<Pill tone="brand">10 last</Pill>}
      contentClassName="p-0 flex flex-col"
    >
      <div className="grid grid-cols-[64px_56px_72px_64px_64px_84px_64px_1fr] gap-x-3 border-b border-line bg-bg-subtle/40 px-3 py-1.5 text-[10px] uppercase tracking-[0.14em] text-ink-faint shrink-0">
        <span>Time</span>
        <span>Side</span>
        <span className="text-right">Strike</span>
        <span className="text-right">Qty</span>
        <span className="text-right">Px</span>
        <span className="text-right">Premium</span>
        <span>Aggr.</span>
        <span>Tag</span>
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {FLOW_TAPE.map((row, i) => (
          <div
            key={i}
            className={cn(
              "grid grid-cols-[64px_56px_72px_64px_64px_84px_64px_1fr] gap-x-3 border-b border-line/40 px-3 py-1.5 text-[12px] hover:bg-bg-hover",
              i === 0 && "bg-brand-dim/30",
            )}
          >
            <span className="tabnum text-ink-faint font-mono">{row.ts}</span>
            <span
              className={cn(
                "font-mono text-[11px] font-semibold",
                row.side === "C" ? "text-signal-up" : "text-signal-down",
              )}
            >
              {row.side === "C" ? "CALL" : "PUT"}
            </span>
            <span className="tabnum text-right text-ink-high">{row.strike}</span>
            <span className="tabnum text-right text-ink-base">{fmtNum(row.qty)}</span>
            <span className="tabnum text-right text-ink-muted">{row.price.toFixed(2)}</span>
            <span className="tabnum text-right text-ink-high">
              ${row.premium >= 1e6 ? `${(row.premium / 1e6).toFixed(2)}M` : `${(row.premium / 1e3).toFixed(0)}K`}
            </span>
            <span
              className={cn(
                "font-mono text-[10px] uppercase tracking-wider",
                row.aggressor === "BUY" ? "text-signal-up" : "text-signal-down",
              )}
            >
              {row.aggressor}
            </span>
            <span>
              <span
                className={cn(
                  "inline-flex rounded border px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider",
                  TAG_TONE[row.tag],
                )}
              >
                {row.tag}
              </span>
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}
