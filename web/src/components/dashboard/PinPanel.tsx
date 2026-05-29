"use client";

import { Panel } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { cn, fmtRate, fmtSignedAbbr } from "@/lib/utils";
import type { Snapshot } from "@/lib/api/types";

// PinPanel — right-rail strip. Pin candidate, expected move, charm zone.
// When pin probability ≥ 50% the panel surface lifts to glass-warn —
// matches the rule book's amber=pin convention but as decorative chrome,
// not data ink.
export function PinPanel({ symbol }: { symbol: "SPX" | "NDX" }) {
  const { snapshot, status, error } = useSnapshot(symbol);
  const hot = snapshot?.pin.active === true && snapshot.pin.top_probability >= 0.5;

  return (
    <Panel
      title="Pin · Move · Charm"
      subtitle="0DTE forced-flow signals"
      tone={hot ? "glass-warn" : "default"}
      contentClassName="p-0 flex flex-col"
    >
      {snapshot ? <Body snapshot={snapshot} /> : <Placeholder status={status} message={error?.message} />}
    </Panel>
  );
}

function Body({ snapshot }: { snapshot: Snapshot }) {
  const pinProb = snapshot.pin.top_probability;
  const pinStrike = snapshot.pin.top_strike;
  const distPct =
    pinStrike > 0 && snapshot.spot > 0
      ? ((pinStrike - snapshot.spot) / snapshot.spot) * 100
      : 0;
  const pinHot = pinProb >= 0.5 && snapshot.pin.active;
  const expectedMv = snapshot.expected_mv;
  const zone = snapshot.charm_zone;
  // Zone color discipline:
  //   PEAK  + PIN     → warn (the operator must act now)
  //   FADING          → ink-muted (situation cooling, not an alarm)
  //   RISING          → ink-base
  //   WEAK / UNKNOWN  → ink-ghost
  // Per CLAUDE.md, brand pink is decorative ambient ONLY — never on a
  // data-state label like a charm zone.
  const zoneTone =
    zone === "PEAK" || zone === "PIN"
      ? "text-accent-warn"
      : zone === "FADING"
        ? "text-ink-muted"
        : zone === "RISING"
          ? "text-ink-base"
          : "text-ink-ghost";
  const charmVel = snapshot.charm_velocity_raw;

  return (
    <>
      {/* Pin section */}
      <Section
        label="Pin candidate"
        accent={pinHot ? "warn" : undefined}
      >
        <div className="flex items-baseline justify-between">
          <div className="flex items-baseline gap-2">
            <span className="font-display tabnum text-[34px] font-medium leading-none tracking-[-0.02em] text-ink-high">
              {pinStrike > 0 ? pinStrike : "—"}
            </span>
            <span className="tabnum text-[11px] text-ink-muted">
              {pinStrike > 0 && snapshot.spot > 0
                ? `${distPct >= 0 ? "+" : "\u2212"}${Math.abs(distPct).toFixed(2)}%`
                : ""}
            </span>
          </div>
          <span
            className={cn(
              "font-display tabnum text-[28px] font-medium leading-none tracking-[-0.02em]",
              pinHot ? "text-accent-warn" : "text-ink-base",
            )}
          >
            {(pinProb * 100).toFixed(0)}%
          </span>
        </div>

        <div className="mt-2 grid grid-cols-3 gap-1 font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">
          <Sub label="\u03B3 str." value={firstCandFmt(snapshot, "gamma_strength")} />
          <Sub label="dist" value={firstCandFmt(snapshot, "distance_factor")} />
          <Sub label="flow" value={firstCandFmt(snapshot, "flow_persistence")} />
        </div>
      </Section>

      <div className="h-px bg-line" />

      {/* Expected move */}
      <Section label="Expected move (1\u03C3 to close)">
        <div className="flex items-baseline gap-3">
          <span className="font-display tabnum text-[28px] font-medium leading-none tracking-[-0.02em] text-ink-high">
            {expectedMv > 0 ? `\u00B1${expectedMv.toFixed(2)}` : "—"}
          </span>
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
            band
          </span>
        </div>
        {snapshot.spot > 0 && expectedMv > 0 && (
          <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-ink-muted">
            <span className="tabnum">{(snapshot.spot - expectedMv).toFixed(2)}</span>
            <span className="text-ink-ghost">spot {snapshot.spot.toFixed(2)}</span>
            <span className="tabnum">{(snapshot.spot + expectedMv).toFixed(2)}</span>
          </div>
        )}
      </Section>

      <div className="h-px bg-line" />

      {/* Charm zone */}
      <Section label="Charm zone">
        <div className="flex items-baseline justify-between">
          <span
            className={cn(
              "font-mono text-[18px] font-medium uppercase tracking-[0.2em] leading-none",
              zoneTone,
            )}
          >
            {zone === "UNKNOWN" ? "—" : zone}
          </span>
          <span className="tabnum text-[12px] text-ink-base">
            {fmtRate(charmVel)}
          </span>
        </div>
        <div className="mt-2 flex gap-px">
          {(["WEAK", "RISING", "PEAK", "FADING"] as const).map((z) => {
            const active = z === zone;
            return (
              <span
                key={z}
                className={cn(
                  "flex-1 py-1 text-center font-mono text-[8.5px] uppercase tracking-[0.18em]",
                  active
                    ? z === "PEAK"
                      ? "bg-accent-warn/15 text-accent-warn shadow-[inset_0_0_0_1px_rgba(245,158,11,0.25)]"
                      : "bg-bg-card text-ink-high"
                    : "bg-bg-subtle/30 text-ink-ghost",
                )}
              >
                {z}
              </span>
            );
          })}
        </div>
      </Section>

      <div className="h-px bg-line" />

      {/* Forced-flow row */}
      <Section label="Forced-flow proxy">
        <div className="flex items-baseline justify-between">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
            net pulse
          </span>
          <span
            className={cn(
              "font-display tabnum text-[20px] font-medium leading-none tracking-[-0.02em]",
              snapshot.flow_pulse.total < 0 ? "text-accent-short" : "text-accent-long",
            )}
          >
            {fmtSignedAbbr(snapshot.flow_pulse.total)}
          </span>
        </div>
        <div className="mt-2 grid grid-cols-3 gap-2">
          <PulseBar label="\u03B3" value={snapshot.flow_pulse.gamma} />
          <PulseBar label="charm" value={snapshot.flow_pulse.charm} />
          <PulseBar label="vanna" value={snapshot.flow_pulse.vanna} />
        </div>
      </Section>
    </>
  );
}

function Section({
  label,
  accent,
  children,
}: {
  label: string;
  accent?: "warn";
  children: React.ReactNode;
}) {
  return (
    <div className="px-3 py-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ink-faint">
          {label}
        </span>
        {accent === "warn" && (
          <span className="font-mono text-[9px] uppercase tracking-[0.22em] text-accent-warn">
            HOT
          </span>
        )}
      </div>
      {children}
    </div>
  );
}

function Sub({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span>{label}</span>
      <span className="tabnum text-ink-base">{value}</span>
    </div>
  );
}

function PulseBar({ label, value }: { label: string; value: number }) {
  const pos = value >= 0;
  const mag = Math.abs(value);
  const pct = mag > 0 ? Math.min(100, (Math.log10(mag + 1) / 11) * 100) : 0;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-ink-faint">
          {label}
        </span>
        <span
          className={cn(
            "tabnum text-[10px]",
            pos ? "text-accent-long" : "text-accent-short",
          )}
        >
          {fmtSignedAbbr(value, 1)}
        </span>
      </div>
      <div className="h-1 bg-bg-subtle">
        <div
          className={cn("h-full", pos ? "bg-accent-long/70" : "bg-accent-short/70")}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function firstCandFmt(s: Snapshot, key: "gamma_strength" | "distance_factor" | "flow_persistence"): string {
  const c = s.pin.candidates?.[0];
  if (!c) return "—";
  const v = c[key];
  if (typeof v !== "number" || !Number.isFinite(v)) return "—";
  return v.toFixed(2);
}

function Placeholder({ status, message }: { status: string; message?: string }) {
  if (status === "error") {
    return (
      <div className="flex h-48 flex-col items-center justify-center gap-1 px-4 text-center">
        <span className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-accent-warn">
          backend unreachable
        </span>
        {message && (
          <span className="text-[10.5px] text-ink-faint">{message}</span>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-3 p-3">
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="space-y-1.5">
          <div className="h-2 w-24 animate-pulse bg-bg-subtle/60" />
          <div className="h-6 animate-pulse bg-bg-subtle/40" />
        </div>
      ))}
    </div>
  );
}
