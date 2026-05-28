"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { KEY_LEVELS, SNAPSHOT } from "@/lib/mock";
import { cn } from "@/lib/utils";

const TYPE_TONE = {
  resistance: { dot: "bg-signal-up", label: "RES" },
  support: { dot: "bg-signal-down", label: "SUP" },
  flip: { dot: "bg-signal-pin", label: "FLIP" },
  neutral: { dot: "bg-ink-faint", label: "PVT" },
  pin: { dot: "bg-brand", label: "PIN" },
  spot: { dot: "bg-ink-high", label: "NOW" },
};

export function KeyLevels() {
  return (
    <Panel
      title="Key Levels"
      subtitle="Walls · flip · pin · spot"
      actions={<Pill tone="brand">Expected MV ±{SNAPSHOT.expected_mv}</Pill>}
    >
      <div className="space-y-px">
        {KEY_LEVELS.map((lvl) => {
          const isSpot = lvl.type === "spot";
          const tone = TYPE_TONE[lvl.type];
          const distPct = lvl.dist;
          return (
            <div
              key={lvl.label}
              className={cn(
                "group flex items-center gap-3 rounded-md px-2.5 py-2 transition-colors",
                isSpot
                  ? "border border-brand/30 bg-brand-dim"
                  : "hover:bg-bg-hover",
              )}
            >
              <span className={cn("h-2 w-2 shrink-0 rounded-full", tone.dot)} />
              <span className="w-10 text-[10px] uppercase tracking-[0.14em] text-ink-faint">
                {tone.label}
              </span>
              <span
                className={cn(
                  "flex-1 text-sm",
                  isSpot ? "text-ink-high font-medium" : "text-ink-base",
                )}
              >
                {lvl.label}
              </span>
              <span className="tabnum text-sm text-ink-high">
                {lvl.price.toFixed(lvl.price % 1 === 0 ? 0 : 2)}
              </span>
              <span
                className={cn(
                  "tabnum w-16 text-right text-xs",
                  distPct > 0 ? "text-signal-up" : distPct < 0 ? "text-signal-down" : "text-ink-faint",
                )}
              >
                {distPct === 0 ? "—" : `${distPct >= 0 ? "+" : ""}${distPct.toFixed(2)}%`}
              </span>
              <div className="hidden md:block w-16">
                <div className="h-1 overflow-hidden rounded-full bg-bg-subtle">
                  <div
                    className={cn("h-full", tone.dot)}
                    style={{ width: `${lvl.strength * 100}%`, opacity: 0.8 }}
                  />
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
