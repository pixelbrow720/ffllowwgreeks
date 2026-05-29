"use client";

import { useEffect, useState } from "react";
import { useSnapshot } from "@/lib/api/snapshot";
import { useSocketStatus } from "@/lib/ws/useLiveSocket";
import { useSpotHistory } from "@/lib/api/history";
import { cn, fmtNum, fmtSignedNum, fmtSignedAbbr } from "@/lib/utils";

type Sym = "SPX" | "NDX";

// RegimeStrip — fixed 56px topbar. Always visible, monochrome, dense.
// One scan-line that answers "where are we and what regime are we in".
export function RegimeStrip({
  symbol,
  onSymbolChange,
}: {
  symbol: Sym;
  onSymbolChange: (s: Sym) => void;
}) {
  const { snapshot, status, error } = useSnapshot(symbol);
  const series = useSpotHistory(symbol);
  const wsStatus = useSocketStatus();
  const [now, setNow] = useState<string>("");

  useEffect(() => {
    const tick = () => {
      const d = new Date();
      const hh = String(d.getHours()).padStart(2, "0");
      const mm = String(d.getMinutes()).padStart(2, "0");
      const ss = String(d.getSeconds()).padStart(2, "0");
      setNow(`${hh}:${mm}:${ss}`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  const wsLive = wsStatus === "open";
  const ready = snapshot !== null;
  const showError = status === "error" && !snapshot;

  const spot = snapshot?.spot ?? 0;
  const first = series[0]?.spot ?? spot;
  const delta = ready && first > 0 ? spot - first : 0;
  const pct = first > 0 ? (delta / first) * 100 : 0;
  const trendUp = delta >= 0;

  const regimeLabel =
    snapshot?.regime === "SHORT_GAMMA"
      ? "SHORT \u03B3"
      : snapshot?.regime === "LONG_GAMMA"
        ? "LONG \u03B3"
        : snapshot?.regime === "NEUTRAL"
          ? "NEUTRAL"
          : "—";

  const regimeTone =
    snapshot?.regime === "SHORT_GAMMA"
      ? "text-accent-short"
      : snapshot?.regime === "LONG_GAMMA"
        ? "text-accent-long"
        : "text-ink-base";

  return (
    <header className="fixed inset-x-0 top-0 z-40 flex h-14 items-stretch border-b border-line bg-bg-base/95 backdrop-blur">
      <div className="flex w-60 shrink-0 items-center gap-3 border-r border-line px-4">
        <span className="font-mono text-[13px] font-semibold tracking-tight text-ink-high">
          flow<span className="text-ink-muted">greeks</span>
        </span>
        <span className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ink-faint">
          read · the · dealer
        </span>
      </div>

      <div className="flex shrink-0 items-stretch border-r border-line">
        {(["SPX", "NDX"] as const).map((s) => {
          const active = s === symbol;
          return (
            <button
              key={s}
              onClick={() => onSymbolChange(s)}
              className={cn(
                "px-4 font-mono text-[11px] uppercase tracking-[0.2em] transition-colors",
                active
                  ? "bg-bg-card text-ink-high"
                  : "text-ink-faint hover:bg-bg-hover hover:text-ink-base",
              )}
            >
              {s}
            </button>
          );
        })}
      </div>

      <div className="flex shrink-0 items-center gap-5 border-r border-line px-5">
        <Field label="Spot">
          {showError ? (
            <span className="tabnum text-2xl font-medium leading-none text-ink-faint">—</span>
          ) : ready ? (
            <span className="tabnum text-2xl font-medium leading-none text-ink-high">
              {fmtNum(spot, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          ) : (
            <Skeleton w={96} h={20} />
          )}
        </Field>
        <Field label="\u0394 session">
          {ready && first > 0 ? (
            <span
              className={cn(
                "tabnum text-sm font-medium",
                trendUp ? "text-accent-long" : "text-accent-short",
              )}
            >
              {fmtSignedNum(delta, 2)}{" "}
              <span className="text-[11px] text-ink-muted">
                ({pct >= 0 ? "+" : "\u2212"}
                {Math.abs(pct).toFixed(2)}%)
              </span>
            </span>
          ) : ready ? (
            <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-ink-faint">
              waiting first tick
            </span>
          ) : (
            <Skeleton w={108} h={14} />
          )}
        </Field>
      </div>

      <div className="flex flex-1 items-center gap-6 px-5">
        <Field label="Regime">
          <span
            className={cn(
              "font-mono text-[12px] font-medium uppercase tracking-[0.2em]",
              regimeTone,
            )}
          >
            {regimeLabel}
          </span>
        </Field>
        <Field label="Zero \u03B3">
          <Mono>{snapshot?.zero_gamma ? snapshot.zero_gamma.toFixed(1) : "—"}</Mono>
        </Field>
        <Field label="Net GEX">
          {ready ? (
            <span
              className={cn(
                "tabnum font-mono text-[13px] font-medium",
                (snapshot?.net_gex ?? 0) < 0 ? "text-accent-short" : "text-accent-long",
              )}
            >
              {fmtSignedAbbr(snapshot?.net_gex ?? 0)}
            </span>
          ) : (
            <Mono>—</Mono>
          )}
        </Field>
        <Field label="DPI">
          <Mono accent={(snapshot?.dpi.composite ?? 0) >= 75 ? "warn" : "default"}>
            {ready ? (snapshot?.dpi.composite ?? 0).toFixed(1) : "—"}
          </Mono>
        </Field>
        <Field label="Pin">
          <Mono accent={(snapshot?.pin.top_probability ?? 0) >= 0.6 ? "warn" : "default"}>
            {ready
              ? `${snapshot?.pin.top_strike ?? 0} · ${((snapshot?.pin.top_probability ?? 0) * 100).toFixed(0)}%`
              : "—"}
          </Mono>
        </Field>
      </div>

      <div className="flex shrink-0 items-center gap-4 border-l border-line px-4">
        <Field label="Local">
          <Mono>{now || "—"}</Mono>
        </Field>
        <Field label="Pipeline">
          <span className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-[0.18em]">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                wsLive ? "bg-accent-long" : "bg-ink-faint",
              )}
            />
            <span className={wsLive ? "text-accent-long" : "text-ink-faint"}>
              {wsLive ? "LIVE" : showError ? "OFFLINE" : wsStatus.toUpperCase()}
            </span>
          </span>
        </Field>
        {showError && error?.code && (
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-accent-warn">
            {error.code}
          </span>
        )}
      </div>
    </header>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-1 leading-none">
      <span className="font-mono text-[9px] uppercase tracking-[0.22em] text-ink-faint">
        {label}
      </span>
      <span className="leading-none">{children}</span>
    </div>
  );
}

function Mono({
  children,
  accent = "default",
}: {
  children: React.ReactNode;
  accent?: "default" | "warn";
}) {
  return (
    <span
      className={cn(
        "tabnum font-mono text-[13px] font-medium",
        accent === "warn" ? "text-accent-warn" : "text-ink-high",
      )}
    >
      {children}
    </span>
  );
}

function Skeleton({ w, h }: { w: number; h: number }) {
  return (
    <span
      className="inline-block animate-pulse rounded-sm bg-bg-subtle/60"
      style={{ width: w, height: h }}
    />
  );
}
