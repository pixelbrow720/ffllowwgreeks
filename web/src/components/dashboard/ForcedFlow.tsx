"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { FORCED_FLOW_SCENARIOS, SNAPSHOT } from "@/lib/mock";
import { cn } from "@/lib/utils";

function abbrSigned(n: number) {
  const abs = Math.abs(n);
  const sign = n >= 0 ? "+" : "−";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(0)}M`;
  return `${sign}$${abs.toFixed(0)}`;
}

export function ForcedFlow() {
  const topStrike = SNAPSHOT.pin.candidates[0];
  return (
    <Panel
      title="Forced-Flow Simulator"
      subtitle="What dealers MUST do if spot/vol moves"
      actions={<Pill tone="brand">Live · refreshed 0.4s ago</Pill>}
    >
      <div className="space-y-2">
        {FORCED_FLOW_SCENARIOS.map((s, i) => {
          const isMajor = i === 1;
          const dir = s.net_pressure < 0 ? "SELL" : "BUY";
          const dirColor = s.net_pressure < 0 ? "text-accent-short" : "text-accent-long";
          const pctOfDay = Math.min(100, (Math.abs(s.net_pressure) / 3e9) * 100);
          return (
            <div
              key={i}
              className={cn(
                "rounded-md border p-3 transition-colors",
                isMajor ? "border-brand/40 bg-brand-dim/40" : "border-line bg-bg-subtle/30",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint">
                    {String(i + 1).padStart(2, "0")}
                  </span>
                  <span className="text-[13px] text-ink-base">{s.label}</span>
                </div>
                <span className={cn("tabnum text-[13px] font-medium", dirColor)}>
                  {abbrSigned(s.net_pressure)}
                </span>
              </div>
              <div className="mt-2 flex items-center gap-3">
                <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-bg-base">
                  <div
                    className={cn(
                      "absolute inset-y-0 left-0 rounded-full",
                      s.net_pressure < 0
                        ? "bg-gradient-to-r from-accent-short/60 to-accent-short"
                        : "bg-gradient-to-r from-accent-long/60 to-accent-long",
                    )}
                    style={{ width: `${pctOfDay}%` }}
                  />
                </div>
                <span className={cn("tabnum text-[10px] uppercase tracking-[0.14em]", dirColor)}>
                  {dir}
                </span>
                <span className="font-mono text-[10px] uppercase text-ink-faint tracking-wider">
                  charm aid {abbrSigned(s.charm_aid)}
                </span>
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4 rounded-md border border-signal-pin/20 bg-signal-pin/5 px-3 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] uppercase tracking-[0.18em] text-signal-pin">Pin candidate · top</span>
          <span className="tabnum text-[11px] text-ink-high">{topStrike.strike}</span>
        </div>
        <div className="mt-1 text-[11px] text-ink-muted">
          {(topStrike.probability * 100).toFixed(0)}% pin probability ·
          γ-strength {topStrike.gamma_strength.toFixed(2)} · flow {topStrike.flow_persistence.toFixed(2)}
        </div>
      </div>
    </Panel>
  );
}
