"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import type { Snapshot } from "@/lib/api/types";
import { cn } from "@/lib/utils";

const SYMBOL = "SPX" as const;

type LevelType = "resistance" | "support" | "flip" | "neutral" | "pin" | "spot";

interface DerivedLevel {
  label: string;
  price: number;
  type: LevelType;
  strength: number;
}

const TYPE_TONE: Record<LevelType, { dot: string; label: string }> = {
  resistance: { dot: "bg-accent-long", label: "RES" },
  support: { dot: "bg-accent-short", label: "SUP" },
  flip: { dot: "bg-signal-pin", label: "FLIP" },
  neutral: { dot: "bg-ink-faint", label: "PVT" },
  pin: { dot: "bg-brand", label: "PIN" },
  spot: { dot: "bg-ink-high", label: "NOW" },
};

export function KeyLevels() {
  const { snapshot, status, error } = useSnapshot(SYMBOL);

  if (!snapshot) {
    return (
      <Panel title="Key Levels" subtitle="Walls · flip · pin · spot">
        <Placeholder status={status} message={error?.message} />
      </Panel>
    );
  }

  const levels = deriveLevels(snapshot);

  return (
    <Panel
      title="Key Levels"
      subtitle="Walls · flip · pin · spot"
      actions={<Pill tone="brand">Expected MV ±{snapshot.expected_mv.toFixed(2)}</Pill>}
    >
      <div className="space-y-px">
        {levels.map((lvl) => {
          const isSpot = lvl.type === "spot";
          const tone = TYPE_TONE[lvl.type];
          const distPct = lvl.price > 0 && snapshot.spot > 0
            ? ((lvl.price - snapshot.spot) / snapshot.spot) * 100
            : 0;
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
                  distPct > 0 ? "text-accent-long" : distPct < 0 ? "text-accent-short" : "text-ink-faint",
                )}
              >
                {isSpot ? "—" : `${distPct >= 0 ? "+" : ""}${distPct.toFixed(2)}%`}
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

// deriveLevels turns the snapshot into a sorted list of levels around
// the spot. `strength` is a rough magnitude — wall size for walls, pin
// probability for the pin, etc. — so the row meter has something to draw.
function deriveLevels(s: Snapshot): DerivedLevel[] {
  const items: DerivedLevel[] = [];
  if (s.call_wall > 0) {
    const strength = wallStrength(s, s.call_wall, "C");
    items.push({ label: "Call Wall", price: s.call_wall, type: "resistance", strength });
  }
  if (s.zero_gamma > 0) {
    items.push({ label: "Zero Gamma", price: s.zero_gamma, type: "flip", strength: 0.7 });
  }
  if (s.pin.active && s.pin.top_strike > 0) {
    items.push({
      label: "Pin Strike",
      price: s.pin.top_strike,
      type: "pin",
      strength: clamp01(s.pin.top_probability),
    });
  }
  items.push({ label: "Spot", price: s.spot, type: "spot", strength: 1 });
  if (s.put_wall > 0) {
    const strength = wallStrength(s, s.put_wall, "P");
    items.push({ label: "Put Wall", price: s.put_wall, type: "support", strength });
  }
  // Sort high → low so the table reads top-down like a price ladder.
  return items.sort((a, b) => b.price - a.price);
}

function wallStrength(s: Snapshot, strike: number, side: "C" | "P"): number {
  let max = 0;
  let cur = 0;
  s.strikes.forEach((row) => {
    const mag = Math.abs(row.gex_notional);
    if (mag > max) max = mag;
    if (row.strike === strike && row.side === side) cur += mag;
  });
  if (max === 0) return 0.5;
  return clamp01(cur / max);
}

function clamp01(n: number): number {
  if (!Number.isFinite(n)) return 0;
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function Placeholder({ status, message }: { status: string; message?: string }) {
  if (status === "error") {
    return (
      <div className="flex h-32 items-center justify-center text-[11px] uppercase tracking-[0.18em] text-accent-warn">
        {message ?? "no live state"}
      </div>
    );
  }
  return (
    <div className="space-y-px">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-9 rounded-md bg-bg-subtle/30 animate-pulse" />
      ))}
    </div>
  );
}
