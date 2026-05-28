"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { SNAPSHOT } from "@/lib/mock";

const ZONE_PILL: Record<string, { tone: "warn" | "brand" | "info" | "pin"; label: string }> = {
  WEAK: { tone: "info", label: "Weak" },
  RISING: { tone: "warn", label: "Rising" },
  PEAK: { tone: "brand", label: "Peak" },
  FADING: { tone: "info", label: "Fading" },
  PIN: { tone: "pin", label: "Pin" },
};

export function DPIGauge() {
  const dpi = SNAPSHOT.dpi.composite;
  const pct = Math.min(100, Math.max(0, dpi));
  const radius = 84;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference - (pct / 100) * circumference;
  const zone = ZONE_PILL[SNAPSHOT.charm_zone];

  const breakdown = [
    { label: "Net γ sign", value: SNAPSHOT.dpi.net_gamma_sign === -1 ? "SHORT" : "LONG", raw: Math.abs(SNAPSHOT.dpi.net_gamma_sign) },
    { label: "Charm velocity", value: SNAPSHOT.dpi.charm_velocity.toFixed(2), raw: SNAPSHOT.dpi.charm_velocity },
    { label: "Vanna sens.", value: SNAPSHOT.dpi.vanna_sensitivity.toFixed(2), raw: SNAPSHOT.dpi.vanna_sensitivity },
    { label: "TTC decay", value: SNAPSHOT.dpi.time_to_close_decay.toFixed(2), raw: SNAPSHOT.dpi.time_to_close_decay },
    { label: "Flow conc.", value: SNAPSHOT.dpi.flow_concentration.toFixed(2), raw: SNAPSHOT.dpi.flow_concentration },
  ];

  return (
    <Panel
      title="DPI — Dealer Positioning Index"
      subtitle="Composite of 5 signals · 0=stable, 100=forced"
      actions={
        <>
          <Pill tone="brand">Live</Pill>
          <Pill tone={zone.tone}>Charm: {zone.label}</Pill>
        </>
      }
    >
      <div className="flex items-center gap-6">
        <div className="relative shrink-0">
          <svg width="200" height="200" viewBox="0 0 200 200" className="dpi-glow">
            <defs>
              <linearGradient id="dpiGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#22c55e" />
                <stop offset="50%" stopColor="#f59e0b" />
                <stop offset="100%" stopColor="#ff2a5b" />
              </linearGradient>
            </defs>
            <circle
              cx="100"
              cy="100"
              r={radius}
              fill="none"
              stroke="#1c1c20"
              strokeWidth="14"
            />
            <circle
              cx="100"
              cy="100"
              r={radius}
              fill="none"
              stroke="url(#dpiGrad)"
              strokeWidth="14"
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={dashOffset}
              transform="rotate(-90 100 100)"
              style={{ transition: "stroke-dashoffset 600ms ease" }}
            />
            {/* tick marks */}
            {Array.from({ length: 11 }).map((_, i) => {
              const angle = (i / 10) * 360 - 90;
              const rad = (angle * Math.PI) / 180;
              const x1 = 100 + Math.cos(rad) * (radius - 18);
              const y1 = 100 + Math.sin(rad) * (radius - 18);
              const x2 = 100 + Math.cos(rad) * (radius - 24);
              const y2 = 100 + Math.sin(rad) * (radius - 24);
              return (
                <line key={i} x1={x1} y1={y1} x2={x2} y2={y2} stroke="#3a3a40" strokeWidth="1" />
              );
            })}
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="tabnum text-5xl font-medium text-ink-high">{dpi.toFixed(1)}</span>
            <span className="mt-1 text-[10px] uppercase tracking-[0.2em] text-brand-hi">FORCED</span>
          </div>
        </div>

        <div className="flex-1 space-y-2.5">
          {breakdown.map((b) => (
            <div key={b.label} className="flex items-center gap-3">
              <span className="w-28 text-xs text-ink-muted">{b.label}</span>
              <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-bg-subtle">
                <div
                  className="absolute inset-y-0 left-0 bg-gradient-to-r from-brand-lo to-brand-hi"
                  style={{ width: `${Math.min(100, b.raw * 100)}%` }}
                />
              </div>
              <span className="tabnum w-14 text-right text-xs text-ink-base">{b.value}</span>
            </div>
          ))}
        </div>
      </div>
    </Panel>
  );
}
