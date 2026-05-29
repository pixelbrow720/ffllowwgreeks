"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { cn, fmtSignedAbbr } from "@/lib/utils";
import type { Snapshot } from "@/lib/api/types";

// DPILive — left-rail composite + 5-component breakdown. Replaces the
// brand-pink DPIGauge. Wired to the live snapshot. The composite number
// dominates; the components are scan-rows below.
export function DPILive({ symbol }: { symbol: "SPX" | "NDX" }) {
  const { snapshot, status, error } = useSnapshot(symbol);

  return (
    <Panel
      title="DPI"
      subtitle="Dealer Positioning Index"
      actions={<ZoneBadge snapshot={snapshot} />}
      contentClassName="p-3 flex flex-col gap-4"
    >
      {snapshot ? <Body snapshot={snapshot} /> : <Placeholder status={status} message={error?.message} />}
    </Panel>
  );
}

function Body({ snapshot }: { snapshot: Snapshot }) {
  const dpi = snapshot.dpi.composite;
  const tier = dpi >= 75 ? "FORCED" : dpi >= 50 ? "ELEVATED" : dpi >= 25 ? "BUILDING" : "STABLE";
  const tierTone =
    dpi >= 75 ? "text-accent-warn" : dpi >= 50 ? "text-ink-high" : "text-ink-muted";

  // The wire format ships every DPI component on the same 0-100 magnitude
  // scale; sign convention lives in `regime` (and net_gex). The Net γ
  // chip therefore reads its direction from regime + net_gex, while its
  // magnitude bar uses the dpi.net_gamma_sign component value.
  const gammaDir: "long" | "short" | "neutral" =
    snapshot.regime === "LONG_GAMMA"
      ? "long"
      : snapshot.regime === "SHORT_GAMMA"
        ? "short"
        : snapshot.net_gex > 0
          ? "long"
          : snapshot.net_gex < 0
            ? "short"
            : "neutral";

  const rows: { label: string; raw: number; render: "bar" | "sign" }[] = [
    { label: "Net \u03B3 sign", raw: snapshot.dpi.net_gamma_sign, render: "sign" },
    { label: "Charm velocity", raw: snapshot.dpi.charm_velocity, render: "bar" },
    { label: "Vanna sens.", raw: snapshot.dpi.vanna_sensitivity, render: "bar" },
    { label: "TTC decay", raw: snapshot.dpi.time_to_close_decay, render: "bar" },
    { label: "Flow conc.", raw: snapshot.dpi.flow_concentration, render: "bar" },
  ];

  return (
    <>
      <div className="flex items-baseline gap-3">
        <span className="tabnum text-[44px] font-medium leading-none text-ink-high">
          {dpi.toFixed(1)}
        </span>
        <div className="flex flex-col leading-tight">
          <span
            className={cn(
              "font-mono text-[10px] font-medium uppercase tracking-[0.22em]",
              tierTone,
            )}
          >
            {tier}
          </span>
          <span className="font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-faint">
            0=stable · 100=forced
          </span>
        </div>
      </div>

      {/* Composite scale */}
      <div className="space-y-1">
        <div className="relative h-1 overflow-hidden bg-bg-subtle">
          <div
            className={cn(
              "absolute inset-y-0 left-0",
              dpi >= 75 ? "bg-accent-warn" : "bg-ink-base",
            )}
            style={{ width: `${Math.min(100, Math.max(0, dpi))}%` }}
          />
          {/* 50 / 75 thresholds */}
          <span className="absolute inset-y-0 left-1/2 w-px bg-line" />
          <span className="absolute inset-y-0 left-3/4 w-px bg-accent-warn/60" />
        </div>
        <div className="flex justify-between font-mono text-[9px] uppercase tracking-[0.18em] text-ink-ghost">
          <span>0</span>
          <span>50</span>
          <span className="text-ink-faint">75</span>
          <span>100</span>
        </div>
      </div>

      {/* Components */}
      <div className="space-y-1.5">
        {rows.map((r) => (
          <div key={r.label} className="grid grid-cols-[80px_1fr_44px] items-center gap-2">
            <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
              {r.label}
            </span>
            {r.render === "sign" ? (
              <SignChip dir={gammaDir} magnitude={r.raw} />
            ) : (
              <div className="relative h-1 overflow-hidden bg-bg-subtle">
                <div
                  className="absolute inset-y-0 left-0 bg-ink-muted"
                  style={{ width: `${Math.min(100, Math.max(0, Math.abs(r.raw)))}%` }}
                />
              </div>
            )}
            <span className="tabnum text-right font-mono text-[10.5px] text-ink-base">
              {r.render === "sign"
                ? gammaDir === "neutral"
                  ? "—"
                  : gammaDir === "long"
                    ? "LONG"
                    : "SHORT"
                : r.raw.toFixed(1)}
            </span>
          </div>
        ))}
      </div>

      {/* Flow pulse footer */}
      <div className="grid grid-cols-3 gap-2 border-t border-line/70 pt-3">
        <Mini label="\u03B3" value={snapshot.flow_pulse.gamma} />
        <Mini label="Charm" value={snapshot.flow_pulse.charm} />
        <Mini label="Vanna" value={snapshot.flow_pulse.vanna} />
      </div>

      <div className="border-t border-line/70 pt-3 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
        Total flow{" "}
        <span
          className={cn(
            "tabnum",
            snapshot.flow_pulse.total < 0 ? "text-accent-short" : "text-accent-long",
          )}
        >
          {fmtSignedAbbr(snapshot.flow_pulse.total)}
        </span>
      </div>
    </>
  );
}

function SignChip({
  dir,
  magnitude,
}: {
  dir: "long" | "short" | "neutral";
  magnitude: number;
}) {
  const tone =
    dir === "long" ? "bg-accent-long" : dir === "short" ? "bg-accent-short" : "bg-ink-muted";
  return (
    <div className="relative h-1 overflow-hidden bg-bg-subtle">
      <div
        className={cn("absolute inset-y-0 left-0", tone)}
        style={{ width: `${Math.min(100, Math.max(0, Math.abs(magnitude)))}%` }}
      />
    </div>
  );
}

function Mini({ label, value }: { label: string; value: number }) {
  const tone = value < 0 ? "text-accent-short" : value > 0 ? "text-accent-long" : "text-ink-muted";
  return (
    <div className="flex flex-col gap-0.5">
      <span className="font-mono text-[9px] uppercase tracking-[0.18em] text-ink-faint">
        {label}
      </span>
      <span className={cn("tabnum text-[12px] font-medium", tone)}>
        {fmtSignedAbbr(value, 1)}
      </span>
    </div>
  );
}

function ZoneBadge({ snapshot }: { snapshot: Snapshot | null }) {
  if (!snapshot) return null;
  const z = snapshot.charm_zone;
  const tone =
    z === "PEAK" || z === "PIN"
      ? "warn"
      : z === "RISING"
        ? "neutral"
        : z === "FADING" || z === "WEAK"
          ? "neutral"
          : "neutral";
  const label = z === "UNKNOWN" ? "—" : z;
  return <Pill tone={tone}>{label}</Pill>;
}

function Placeholder({ status, message }: { status: string; message?: string }) {
  if (status === "error") {
    return (
      <div className="flex h-48 flex-col items-center justify-center gap-1">
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
    <div className="flex flex-col gap-3">
      <div className="h-12 w-32 animate-pulse bg-bg-subtle/60" />
      <div className="h-2 w-full animate-pulse bg-bg-subtle/60" />
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-3 animate-pulse bg-bg-subtle/40" />
        ))}
      </div>
    </div>
  );
}
