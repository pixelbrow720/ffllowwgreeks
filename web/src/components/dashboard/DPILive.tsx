"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { cn, fmtSignedAbbr } from "@/lib/utils";
import type { Snapshot } from "@/lib/api/types";

// DPILive — left-rail composite + 5-component breakdown. The DPI
// composite number is the focal point of the entire dashboard:
//   - 64px font-display, gradient text when FORCED (≥75)
//   - panel surface lifts to glass-brand when FORCED (decorative chrome,
//     not data — per CLAUDE.md brand pink rule)
//   - components use ink-base bars; only the magnitude bar above 75 burns
//     accent-warn
// The footer adds a flow-pulse mini-row + a session-meta scan line so the
// 720px tall left rail isn't half empty.
export function DPILive({ symbol }: { symbol: "SPX" | "NDX" }) {
  const { snapshot, status, error } = useSnapshot(symbol);
  const dpi = snapshot?.dpi.composite ?? 0;
  const focal = snapshot !== null && dpi >= 75;

  return (
    <Panel
      title="DPI"
      subtitle="Dealer Positioning Index"
      tone={focal ? "glass-brand" : "default"}
      actions={<ZoneBadge snapshot={snapshot} />}
      contentClassName="p-3 flex flex-col gap-3.5"
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
  const focal = dpi >= 75;

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
      {/* hero composite — 64px font-display */}
      <div className="relative flex items-baseline gap-3 pt-1">
        {focal && (
          <div className="pointer-events-none absolute -inset-x-2 -inset-y-1 -z-10 rounded-md bg-gradient-to-br from-brand/20 via-transparent to-transparent blur-2xl" />
        )}
        <span
          className={cn(
            "font-display tabnum text-[58px] font-medium leading-[0.92] tracking-[-0.03em]",
            focal ? "text-gradient-brand" : "text-ink-high",
          )}
        >
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

      {/* composite scale */}
      <div className="space-y-1">
        <div className="relative h-1 overflow-hidden bg-bg-subtle">
          <div
            className={cn(
              "absolute inset-y-0 left-0 transition-all duration-500",
              dpi >= 75 ? "bg-accent-warn" : "bg-ink-base",
            )}
            style={{ width: `${Math.min(100, Math.max(0, dpi))}%` }}
          />
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

      {/* components */}
      <div className="space-y-1.5">
        {rows.map((r) => (
          <div key={r.label} className="grid grid-cols-[78px_1fr_44px] items-center gap-2">
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

      {/* flow pulse footer */}
      <div className="grid grid-cols-3 gap-2 border-t border-line/70 pt-2.5">
        <Mini label="\u03B3" value={snapshot.flow_pulse.gamma} />
        <Mini label="Charm" value={snapshot.flow_pulse.charm} />
        <Mini label="Vanna" value={snapshot.flow_pulse.vanna} />
      </div>

      {/* total flow + session meta */}
      <div className="space-y-1.5 border-t border-line/70 pt-2.5">
        <div className="flex items-center justify-between font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
          <span>Total flow</span>
          <span
            className={cn(
              "tabnum text-[13px] font-medium",
              snapshot.flow_pulse.total < 0 ? "text-accent-short" : "text-accent-long",
            )}
          >
            {fmtSignedAbbr(snapshot.flow_pulse.total)}
          </span>
        </div>
        <div className="flex items-center justify-between font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-faint">
          <span>Charm v</span>
          <span className="tabnum text-ink-base">
            {snapshot.charm_velocity_raw.toFixed(4)}/min
          </span>
        </div>
        <div className="flex items-center justify-between font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-faint">
          <span>Expected mv</span>
          <span className="tabnum text-ink-base">
            {snapshot.expected_mv > 0 ? `\u00B1${snapshot.expected_mv.toFixed(2)}` : "—"}
          </span>
        </div>
        <div className="flex items-center justify-between font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-faint">
          <span>Basis</span>
          <span className="tabnum text-ink-base">
            {snapshot.basis_smooth.toFixed(2)}
            <span className="ml-1 text-ink-ghost">{snapshot.fut_front_sym || ""}</span>
          </span>
        </div>
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
