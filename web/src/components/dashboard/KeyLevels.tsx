"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import type { Snapshot } from "@/lib/api/types";
import { cn } from "@/lib/utils";

type LevelType = "resistance" | "support" | "flip" | "pin" | "spot";

interface DerivedLevel {
  label: string;
  price: number;
  type: LevelType;
  strength: number;
}

const TYPE_TONE: Record<LevelType, { dot: string; label: string }> = {
  // Resistance = price ceiling (call wall) — short-side concentration; render
  // with the short accent so the dot color matches what dealers do at that
  // level (sell into upside). Support = price floor (put wall) — long-side
  // concentration — render with the long accent. This inverts the prior
  // "green = positive" gut convention but matches the data semantics: the
  // dot says "what kind of dealer pressure is at this strike", not "good
  // news vs bad news".
  resistance: { dot: "bg-accent-short", label: "RES" },
  support: { dot: "bg-accent-long", label: "SUP" },
  flip: { dot: "bg-ink-muted", label: "FLIP" },
  pin: { dot: "bg-accent-warn", label: "PIN" },
  spot: { dot: "bg-ink-high", label: "NOW" },
};

export function KeyLevels({ symbol }: { symbol: "SPX" | "NDX" }) {
  const { snapshot, status, error } = useSnapshot(symbol);

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
      actions={
        <Pill tone="neutral">
          \u00B1{snapshot.expected_mv > 0 ? snapshot.expected_mv.toFixed(2) : "—"} band
        </Pill>
      }
      contentClassName="p-0"
    >
      <div>
        {levels.map((lvl, i) => {
          const isSpot = lvl.type === "spot";
          const tone = TYPE_TONE[lvl.type];
          const distPct =
            lvl.price > 0 && snapshot.spot > 0
              ? ((lvl.price - snapshot.spot) / snapshot.spot) * 100
              : 0;
          return (
            <div
              key={`${lvl.label}-${i}`}
              className={cn(
                "grid grid-cols-[10px_36px_1fr_64px_60px] items-center gap-2 border-b border-line/40 pl-3 pr-2.5 py-1.5",
                isSpot && "bg-bg-card",
              )}
            >
              <span className={cn("h-1.5 w-1.5 rounded-full", tone.dot)} />
              <span className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">
                {tone.label}
              </span>
              <span
                className={cn(
                  "text-[12px]",
                  isSpot ? "font-medium text-ink-high" : "text-ink-base",
                )}
              >
                {lvl.label}
              </span>
              <span className="tabnum text-right text-[12px] text-ink-high">
                {lvl.price.toFixed(lvl.price % 1 === 0 ? 0 : 2)}
              </span>
              <span className="tabnum text-right font-mono text-[10px] text-ink-muted">
                {isSpot
                  ? "—"
                  : `${distPct >= 0 ? "+" : "\u2212"}${Math.abs(distPct).toFixed(2)}%`}
              </span>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

// deriveLevels turns the snapshot into a sorted list of levels around the
// spot. Strength carries the rough magnitude (wall size, pin probability)
// for use in any future ladder-strength column.
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
      <div className="flex h-32 items-center justify-center font-mono text-[10.5px] uppercase tracking-[0.18em] text-accent-warn">
        {message ?? "no live state"}
      </div>
    );
  }
  return (
    <div>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-7 border-b border-line/40 bg-bg-subtle/20 animate-pulse" />
      ))}
    </div>
  );
}
