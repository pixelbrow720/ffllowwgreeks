"use client";

import { useEffect, useState } from "react";
import { Bell, Command, Play, Search, Share2 } from "lucide-react";
import { SNAPSHOT } from "@/lib/mock";
import { fmtNum, fmtUsd } from "@/lib/utils";
import { cn } from "@/lib/utils";

export function Topbar() {
  const [revealed, setRevealed] = useState(false);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      setRevealed(e.clientY < 96);
    };
    window.addEventListener("mousemove", onMove, { passive: true });
    return () => window.removeEventListener("mousemove", onMove);
  }, []);

  return (
    <>
      {/* edge hover trigger */}
      <div
        className="fixed left-0 right-0 top-0 z-40 h-3"
        onMouseEnter={() => setRevealed(true)}
      />

      {/* always-visible compact KPI strip — center-top */}
      <div
        className={cn(
          "fixed left-1/2 top-3 z-30 -translate-x-1/2 transition-opacity duration-300",
          revealed ? "opacity-0 pointer-events-none" : "opacity-100",
        )}
      >
        <div className="flex items-center gap-1 rounded-full border border-line/70 bg-bg-card/70 px-2 py-1.5 backdrop-blur-xl shadow-[0_8px_32px_-12px_rgba(0,0,0,0.6)]">
          <span className="ml-1 mr-2 inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.18em] text-ink-base">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-signal-up opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-signal-up" />
            </span>
            {SNAPSHOT.symbol}
          </span>
          <Sep />
          <KPI label="Spot" value={fmtNum(SNAPSHOT.spot, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} tone="brand" />
          <Sep />
          <KPI label="Net GEX" value={`${fmtUsd(SNAPSHOT.net_gex / 1e9, true)}B`} tone="down" />
          <Sep />
          <KPI label="DPI" value={SNAPSHOT.dpi.composite.toFixed(1)} tone="brand" />
          <Sep />
          <KPI label="Pin" value={`${(SNAPSHOT.pin.top_probability * 100).toFixed(0)}%`} tone="pin" />
        </div>
      </div>

      {/* full topbar overlay — slides down */}
      <header
        onMouseLeave={() => setRevealed(false)}
        className={cn(
          "fixed left-3 right-3 top-3 z-40 flex items-center gap-3 rounded-2xl border border-line/70",
          "bg-gradient-to-b from-bg-card/95 to-bg-card/85 backdrop-blur-xl px-3 py-2",
          "shadow-[0_30px_60px_-30px_rgba(0,0,0,0.7)]",
          "transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)]",
          revealed ? "translate-y-0 opacity-100" : "-translate-y-[120%] opacity-0 pointer-events-none",
        )}
      >
        {/* symbol pair */}
        <div className="flex items-center gap-1 rounded-full border border-line/70 bg-bg-base/50 p-1">
          <button className="rounded-full bg-bg-card px-3 py-1 text-[11px] uppercase tracking-[0.16em] font-medium text-ink-high shadow-[0_0_12px_-4px_rgba(255,42,91,0.4)]">
            {SNAPSHOT.symbol}
          </button>
          <button className="rounded-full px-3 py-1 text-[11px] uppercase tracking-[0.16em] text-ink-faint hover:text-ink-base transition-colors">
            NDX
          </button>
        </div>

        <div className="hidden md:flex items-center gap-2 rounded-full border border-line/70 bg-bg-base/40 px-3 py-1.5">
          <span className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">Session</span>
          <span className="tabnum text-[11px] text-ink-high">2026-05-27</span>
          <span className="text-[10px] uppercase tracking-[0.16em] text-signal-up">· 0DTE</span>
        </div>

        <div className="relative hidden lg:flex flex-1 max-w-sm items-center">
          <Search className="absolute left-3.5 h-3.5 w-3.5 text-ink-faint" />
          <input
            className="w-full rounded-full border border-line/70 bg-bg-base/40 pl-9 pr-16 py-1.5 text-[13px] text-ink-base placeholder:text-ink-faint focus:border-brand/40 focus:bg-bg-base/60 focus:outline-none transition-colors"
            placeholder="Jump to strike, rule, or command…"
          />
          <span className="absolute right-2 flex items-center gap-1 rounded-full border border-line/60 bg-bg-card px-2 py-0.5 text-[10px] text-ink-faint">
            <Command className="h-2.5 w-2.5" />K
          </span>
        </div>

        <div className="ml-auto hidden xl:flex items-center gap-1 rounded-full border border-line/70 bg-bg-base/30 px-2 py-1">
          <KPI label="Spot" value={fmtNum(SNAPSHOT.spot, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} tone="brand" />
          <Sep />
          <KPI label="Net GEX" value={`${fmtUsd(SNAPSHOT.net_gex / 1e9, true)}B`} tone="down" />
          <Sep />
          <KPI label="Zero γ" value={SNAPSHOT.zero_gamma.toFixed(1)} />
          <Sep />
          <KPI label="DPI" value={SNAPSHOT.dpi.composite.toFixed(1)} tone="brand" />
          <Sep />
          <KPI label="Pin" value={`${(SNAPSHOT.pin.top_probability * 100).toFixed(0)}%`} hint={`@${SNAPSHOT.pin.top_strike}`} tone="pin" />
        </div>

        <div className="flex items-center gap-1.5">
          <button className="flex h-8 w-8 items-center justify-center rounded-full border border-line/70 bg-bg-base/40 text-ink-muted hover:bg-bg-hover hover:text-ink-base transition-colors" title="Share view">
            <Share2 className="h-3.5 w-3.5" />
          </button>
          <button className="relative flex h-8 w-8 items-center justify-center rounded-full border border-line/70 bg-bg-base/40 text-ink-muted hover:bg-bg-hover hover:text-ink-base transition-colors" title="Alerts">
            <Bell className="h-3.5 w-3.5" />
            <span className="absolute top-1.5 right-1.5 h-1.5 w-1.5 rounded-full bg-brand shadow-[0_0_6px_#ff2a5b]" />
          </button>
          <button className="flex items-center gap-1.5 rounded-full bg-brand px-3.5 py-1.5 text-[11px] font-medium text-white shadow-[0_0_24px_-6px_#ff2a5b] hover:bg-brand-hi transition-colors uppercase tracking-[0.14em]">
            <Play className="h-3 w-3 fill-white" /> Replay
          </button>
        </div>
      </header>
    </>
  );
}

function Sep() {
  return <span className="h-4 w-px bg-line/60" />;
}

function KPI({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "up" | "down" | "brand" | "pin";
}) {
  const toneCls = {
    default: "text-ink-high",
    up: "text-signal-up",
    down: "text-signal-down",
    brand: "text-brand-hi",
    pin: "text-signal-pin",
  };
  return (
    <div className="flex flex-col leading-tight px-2">
      <span className="text-[9px] uppercase tracking-[0.18em] text-ink-faint">{label}</span>
      <span className={`tabnum text-[12.5px] font-medium ${toneCls[tone]}`}>
        {value}
        {hint && <span className="ml-1 text-[10px] text-ink-faint">{hint}</span>}
      </span>
    </div>
  );
}
