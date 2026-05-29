"use client";

import { useEffect, useState } from "react";
import { useSnapshot } from "@/lib/api/snapshot";
import { useSocketStatus } from "@/lib/ws/useLiveSocket";
import { useSpotHistory } from "@/lib/api/history";
import { cn, fmtNum, fmtSignedNum, fmtSignedAbbr } from "@/lib/utils";

type Sym = "SPX" | "NDX";

// RegimeStrip — fixed 56px topbar. Always visible, monochrome at rest,
// brand-tinted at FORCED state. Replaces the prior austere strip with a
// composition that rhymes with the landing FloatingPanel:
//   - left: brandmark + symbol dock (pill toggle, 21st.dev dock pattern)
//   - center: spot ticker (font-display hero number, like landing)
//   - right: dense regime + zero γ + DPI + pin scan-line
//   - far-right: live indicator + clock
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

  const dpi = snapshot?.dpi.composite ?? 0;
  const forced = dpi >= 75;

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
    <header
      className={cn(
        "fixed inset-x-0 top-0 z-40 flex h-14 items-stretch border-b backdrop-blur-xl transition-colors duration-500",
        forced
          ? "border-brand/30 bg-bg-base/85"
          : "border-line bg-bg-base/90",
      )}
    >
      {/* brand-tint overlay when FORCED — lives only in chrome */}
      {forced && (
        <div className="pointer-events-none absolute inset-0 -z-10 bg-gradient-to-r from-brand/[0.08] via-transparent to-brand/[0.04]" />
      )}

      {/* brandmark */}
      <div className="flex w-60 shrink-0 items-center gap-3 border-r border-line/70 px-4">
        <div className="flex items-center gap-2">
          <span className="relative flex h-1.5 w-1.5">
            <span
              className={cn(
                "absolute inline-flex h-full w-full rounded-full opacity-75",
                wsLive ? "animate-ping bg-brand" : "bg-ink-faint",
              )}
            />
            <span
              className={cn(
                "relative inline-flex h-1.5 w-1.5 rounded-full",
                wsLive ? "bg-brand" : "bg-ink-faint",
              )}
            />
          </span>
          <span className="font-mono text-[13px] font-semibold tracking-tight text-ink-high">
            flow<span className="text-ink-muted">greeks</span>
          </span>
        </div>
        <span className="font-mono text-[9.5px] uppercase tracking-[0.22em] text-ink-faint">
          read · the · dealer
        </span>
      </div>

      {/* symbol dock — pill toggle */}
      <div className="flex shrink-0 items-center border-r border-line/70 px-3">
        <div className="relative inline-flex items-center rounded-full border border-line bg-bg-card/80 p-0.5 backdrop-blur">
          {(["SPX", "NDX"] as const).map((s) => {
            const active = s === symbol;
            return (
              <button
                key={s}
                onClick={() => onSymbolChange(s)}
                className={cn(
                  "relative rounded-full px-3.5 py-1 font-mono text-[10.5px] uppercase tracking-[0.2em] transition-all duration-200",
                  active
                    ? "bg-brand/10 text-ink-high shadow-[inset_0_0_0_1px_rgba(255,42,91,0.3)]"
                    : "text-ink-faint hover:text-ink-base",
                )}
              >
                {s}
              </button>
            );
          })}
        </div>
      </div>

      {/* spot ticker — font-display hero number */}
      <div className="flex shrink-0 items-center gap-5 border-r border-line/70 px-5">
        <Field label="Spot">
          {showError ? (
            <span className="tabnum text-2xl font-medium leading-none text-ink-faint">—</span>
          ) : ready ? (
            <span className="font-display tabnum text-[26px] font-medium leading-none tracking-tight text-ink-high">
              {fmtNum(spot, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          ) : (
            <Skeleton w={108} h={22} />
          )}
        </Field>
        <Field label="\u0394 session">
          {ready && first > 0 ? (
            <span
              className={cn(
                "tabnum text-sm font-medium leading-none",
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

      {/* regime + zero γ + net GEX + DPI + pin scan-line */}
      <div className="flex flex-1 items-center gap-6 px-5">
        <Field label="Regime">
          <span
            className={cn(
              "font-mono text-[12px] font-medium uppercase leading-none tracking-[0.2em]",
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
                "tabnum font-mono text-[13px] font-medium leading-none",
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
          <span
            className={cn(
              "tabnum font-mono text-[13px] font-medium leading-none",
              forced ? "text-accent-warn" : "text-ink-high",
            )}
          >
            {ready ? dpi.toFixed(1) : "—"}
            {forced && (
              <span className="ml-1.5 font-mono text-[9px] uppercase tracking-[0.22em] text-accent-warn">
                FORCED
              </span>
            )}
          </span>
        </Field>
        <Field label="Pin">
          <Mono accent={(snapshot?.pin.top_probability ?? 0) >= 0.6 ? "warn" : "default"}>
            {ready
              ? `${snapshot?.pin.top_strike ?? 0} · ${((snapshot?.pin.top_probability ?? 0) * 100).toFixed(0)}%`
              : "—"}
          </Mono>
        </Field>
      </div>

      {/* clock + pipeline */}
      <div className="flex shrink-0 items-center gap-4 border-l border-line/70 px-4">
        <Field label="Local">
          <Mono>{now || "—"}</Mono>
        </Field>
        <Field label="Pipeline">
          <span className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase leading-none tracking-[0.18em]">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                wsLive ? "bg-accent-long shadow-[0_0_8px_rgba(16,185,129,0.6)]" : "bg-ink-faint",
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
        "tabnum font-mono text-[13px] font-medium leading-none",
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
