"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { useAlertLog } from "@/lib/api/history";
import { useSocketStatus } from "@/lib/ws/useLiveSocket";
import { cn, formatAlertMessage } from "@/lib/utils";

const SEV_LABEL: Record<"crit" | "warn" | "info", string> = {
  crit: "CRIT",
  warn: "WARN",
  info: "INFO",
};

export function SignalLog({ symbol }: { symbol: "SPX" | "NDX" }) {
  const log = useAlertLog(symbol);
  const wsStatus = useSocketStatus();
  const wsLive = wsStatus === "open";

  return (
    <Panel
      title="Signal Log"
      subtitle={`${symbol} · rules triggered this session`}
      actions={
        <Pill tone="neutral">
          {log.length} {log.length === 1 ? "event" : "events"}
        </Pill>
      }
      contentClassName="p-0 flex flex-col"
    >
      <div className="grid grid-cols-[72px_64px_96px_1fr] gap-x-3 border-b border-line bg-bg-subtle/20 px-3 py-1 font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-faint">
        <span>Time</span>
        <span>Sev</span>
        <span>Rule</span>
        <span>Detail</span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {log.length === 0 ? (
          <EmptyState wsLive={wsLive} />
        ) : (
          log.map((a, i) => (
            <div
              key={`${a.ts_ns}-${i}`}
              className={cn(
                "grid grid-cols-[72px_64px_96px_1fr] items-baseline gap-x-3 border-b border-line/40 px-3 py-1.5 hover:bg-bg-hover",
                i === 0 && "bg-bg-card",
              )}
            >
              <span className="tabnum font-mono text-[10.5px] text-ink-muted">{a.ts}</span>
              <span
                className={cn(
                  "inline-flex w-fit items-center gap-1.5 border px-1 py-px font-mono text-[9px] font-medium uppercase tracking-[0.18em]",
                  a.severity === "crit"
                    ? "border-accent-short/30 text-accent-short bg-accent-short/10"
                    : a.severity === "warn"
                      ? "border-accent-warn/30 text-accent-warn bg-accent-warn/10"
                      : "border-line text-ink-muted bg-bg-subtle/40",
                )}
              >
                <span
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    a.severity === "crit"
                      ? "bg-accent-short"
                      : a.severity === "warn"
                        ? "bg-accent-warn"
                        : "bg-ink-muted",
                  )}
                />
                {SEV_LABEL[a.severity]}
              </span>
              <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
                {a.kind}
              </span>
              <span className="text-[11.5px] text-ink-base leading-relaxed">{formatAlertMessage(a.message)}</span>
            </div>
          ))
        )}
      </div>
    </Panel>
  );
}

function EmptyState({ wsLive }: { wsLive: boolean }) {
  return (
    <div className="flex h-full min-h-[100px] flex-col items-center justify-center gap-1 px-6 text-center">
      <span className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-ink-faint">
        {wsLive ? "listening for triggers" : "waiting for backend"}
      </span>
      <span className="text-[10.5px] text-ink-faint">
        Alerts appear here as rules fire on the live state stream.
      </span>
    </div>
  );
}
