"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { useAlertLog } from "@/lib/api/history";
import { useSocketStatus } from "@/lib/ws/useLiveSocket";
import { cn } from "@/lib/utils";

const SYMBOL = "SPX" as const;

const SEV_LABEL: Record<"crit" | "warn" | "info", string> = {
  crit: "CRIT",
  warn: "WARN",
  info: "INFO",
};

export function SignalLog() {
  const log = useAlertLog(SYMBOL);
  const wsStatus = useSocketStatus();
  const wsLive = wsStatus === "open";

  return (
    <Panel
      title="Signal Log"
      subtitle="Rules triggered this session"
      actions={<Pill tone="brand">{log.length} events</Pill>}
      contentClassName="p-0 flex flex-col"
    >
      <div className="flex-1 min-h-0 overflow-y-auto">
        {log.length === 0 ? (
          <EmptyState wsLive={wsLive} />
        ) : (
          log.map((a, i) => (
            <div
              key={`${a.ts_ns}-${i}`}
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
                    ? "border-accent-short/30 text-accent-short bg-accent-short/10"
                    : a.severity === "warn"
                      ? "border-accent-warn/30 text-accent-warn bg-accent-warn/10"
                      : "border-signal-info/30 text-signal-info bg-signal-info/10",
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    a.severity === "crit"
                      ? "bg-accent-short"
                      : a.severity === "warn"
                        ? "bg-accent-warn"
                        : "bg-signal-info",
                  )}
                />
                {SEV_LABEL[a.severity]}
              </span>
              <span className="text-[10px] uppercase tracking-[0.12em] text-ink-faint font-mono">
                {a.kind}
              </span>
              <span className="text-xs text-ink-base leading-relaxed">{a.message}</span>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}

function EmptyState({ wsLive }: { wsLive: boolean }) {
  return (
    <div className="flex h-full min-h-[120px] flex-col items-center justify-center gap-2 px-6 text-center">
      <span className="text-[10.5px] uppercase tracking-[0.18em] text-ink-faint">
        {wsLive ? "Listening for triggers" : "Waiting for backend"}
      </span>
      <span className="text-[11px] text-ink-faint">
        Alerts appear here as rules fire on the live state stream.
      </span>
    </div>
  );
}
