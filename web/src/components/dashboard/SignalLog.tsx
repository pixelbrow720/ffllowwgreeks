"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { ALERTS } from "@/lib/mock";
import { cn } from "@/lib/utils";

const SEV: Record<string, { tone: "down" | "warn" | "info"; dot: string; label: string }> = {
  crit: { tone: "down", dot: "bg-signal-down", label: "CRIT" },
  warn: { tone: "warn", dot: "bg-signal-warn", label: "WARN" },
  info: { tone: "info", dot: "bg-signal-info", label: "INFO" },
};

export function SignalLog() {
  return (
    <Panel
      title="Signal Log"
      subtitle="Rules triggered this session"
      actions={<Pill tone="brand">{ALERTS.length} events</Pill>}
      contentClassName="p-0 flex flex-col"
    >
      <div className="flex-1 min-h-0 overflow-y-auto">
        {ALERTS.map((a, i) => {
          const s = SEV[a.severity];
          return (
            <div
              key={i}
              className={cn(
                "group grid grid-cols-[64px_56px_72px_1fr] items-start gap-3 border-b border-line/40 px-3 py-2.5 hover:bg-bg-hover",
                i === 0 && "bg-brand-dim/20",
              )}
            >
              <span className="tabnum text-[10px] text-ink-faint font-mono">{a.ts}</span>
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 rounded border px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider",
                  a.severity === "crit"
                    ? "border-signal-down/30 text-signal-down bg-signal-down/10"
                    : a.severity === "warn"
                      ? "border-signal-warn/30 text-signal-warn bg-signal-warn/10"
                      : "border-signal-info/30 text-signal-info bg-signal-info/10",
                )}
              >
                <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />
                {s.label}
              </span>
              <span className="text-[10px] uppercase tracking-[0.12em] text-ink-faint font-mono">
                {a.kind}
              </span>
              <span className="text-xs text-ink-base leading-relaxed">{a.message}</span>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
