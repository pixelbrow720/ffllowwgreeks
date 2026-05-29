"use client";

import { cn, fmtSignedAbbr } from "@/lib/utils";
import type { StrikeRow } from "@/lib/api/types";

// StrikeTooltip — glassmorphic hover popover for the GEXProfile ladder.
// Pure positioning math; the parent computes (left, top) from the SVG row
// position and passes the aggregated strike data. Renders absolute inside
// the GEXProfile content cell.
//
// Inspired by 21st.dev/community/tooltips. Uses our own tokens; no Radix
// dependency. Arrow points left-to-right (anchored to the row label area)
// because rows are read right-to-left from the bar.
interface Props {
  strike: number;
  spot: number;
  netGexUsd: number;
  callRow?: StrikeRow;
  putRow?: StrikeRow;
  isCallWall: boolean;
  isPutWall: boolean;
  isPin: boolean;
  pinProb?: number;
  x: number;
  y: number;
}

export function StrikeTooltip({
  strike,
  spot,
  netGexUsd,
  callRow,
  putRow,
  isCallWall,
  isPutWall,
  isPin,
  pinProb,
  x,
  y,
}: Props) {
  const dist = spot > 0 ? ((strike - spot) / spot) * 100 : 0;

  // Aggregate dealer position across both legs at this strike.
  const dealerPos = (callRow?.dealer_pos ?? 0) + (putRow?.dealer_pos ?? 0);
  const gamma = (callRow?.gamma ?? 0) + (putRow?.gamma ?? 0);
  const charm = (callRow?.charm ?? 0) + (putRow?.charm ?? 0);
  const vanna = (callRow?.vanna ?? 0) + (putRow?.vanna ?? 0);
  const ivAvg =
    callRow && putRow
      ? (callRow.iv + putRow.iv) / 2
      : callRow?.iv ?? putRow?.iv ?? 0;

  const tag =
    (isPin && "PIN") ||
    (isCallWall && "C-WALL") ||
    (isPutWall && "P-WALL") ||
    null;
  const tagTone = isPin
    ? "text-accent-warn border-accent-warn/30 bg-accent-warn/10"
    : isCallWall
      ? "text-accent-long border-accent-long/30 bg-accent-long/10"
      : isPutWall
        ? "text-accent-short border-accent-short/30 bg-accent-short/10"
        : "";

  return (
    <div
      className="pointer-events-none absolute z-40 w-[240px] -translate-y-1/2 rounded-sm border border-brand/30 bg-bg-card/95 p-3 backdrop-blur-xl shadow-[0_18px_60px_-20px_rgba(255,42,91,0.45)]"
      style={{ left: x, top: y }}
    >
      <div className="flex items-baseline justify-between border-b border-line/60 pb-1.5">
        <div className="flex items-baseline gap-2">
          <span className="font-display text-[20px] font-medium leading-none text-ink-high tabnum">
            {strike}
          </span>
          <span
            className={cn(
              "tabnum font-mono text-[10px]",
              dist >= 0 ? "text-ink-base" : "text-ink-base",
            )}
          >
            {dist >= 0 ? "+" : "\u2212"}
            {Math.abs(dist).toFixed(2)}%
          </span>
        </div>
        {tag && (
          <span
            className={cn(
              "border px-1.5 py-px font-mono text-[9px] uppercase tracking-[0.18em]",
              tagTone,
            )}
          >
            {tag}
            {isPin && pinProb !== undefined && (
              <span className="ml-1 tabnum">{(pinProb * 100).toFixed(0)}%</span>
            )}
          </span>
        )}
      </div>

      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1.5 font-mono">
        <Field label="Net GEX" value={fmtSignedAbbr(netGexUsd)} valueTone={netGexUsd < 0 ? "short" : "long"} />
        <Field label="Dealer pos" value={dealerPos.toFixed(0)} />
        <Field label="\u0393 (gamma)" value={gamma.toFixed(3)} />
        <Field label="Charm" value={charm.toFixed(4)} />
        <Field label="Vanna" value={vanna.toFixed(3)} />
        <Field label="IV avg" value={`${(ivAvg * 100).toFixed(1)}%`} />
      </div>

      <div className="mt-2 grid grid-cols-2 gap-x-3 border-t border-line/60 pt-1.5 font-mono text-[9.5px] uppercase tracking-[0.16em]">
        <span className="text-ink-faint">
          C{" "}
          <span className="tabnum text-ink-base">
            {callRow ? fmtSignedAbbr(callRow.gex_notional, 1) : "—"}
          </span>
        </span>
        <span className="text-ink-faint">
          P{" "}
          <span className="tabnum text-ink-base">
            {putRow ? fmtSignedAbbr(putRow.gex_notional, 1) : "—"}
          </span>
        </span>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  valueTone = "default",
}: {
  label: string;
  value: string;
  valueTone?: "default" | "short" | "long";
}) {
  const tone =
    valueTone === "short"
      ? "text-accent-short"
      : valueTone === "long"
        ? "text-accent-long"
        : "text-ink-high";
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[9px] uppercase tracking-[0.18em] text-ink-faint">
        {label}
      </span>
      <span className={cn("tabnum text-[11px]", tone)}>{value}</span>
    </div>
  );
}
