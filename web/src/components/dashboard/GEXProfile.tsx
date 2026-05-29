"use client";

import { useState } from "react";
import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { fmtSignedAbbr } from "@/lib/utils";
import { StrikeTooltip } from "@/components/primitives/StrikeTooltip";
import type { StrikeRow as ApiStrikeRow } from "@/lib/api/types";

// VISIBLE_ROWS sized so the strike-ladder fills its 720px panel slot
// edge-to-edge without scroll. Picked symmetrically around spot —
// HALF rows above + ATM + HALF rows below — so the operator always
// sees the band that matters and the panel never has dead space.
const VISIBLE_ROWS = 23; // 11 above + 1 ATM + 11 below

interface AggRow {
  strike: number;
  gexM: number;
  gexUsd: number;
  side: "C" | "P";
  isCallWall: boolean;
  isPutWall: boolean;
  isPin: boolean;
  callRow?: ApiStrikeRow;
  putRow?: ApiStrikeRow;
  // Distance bucket — 0 = >0.5%, 1 = within 0.5%, 2 = ATM (within 0.15%)
  distBucket: 0 | 1 | 2;
}

export function GEXProfile({ symbol }: { symbol: "SPX" | "NDX" }) {
  const [hover, setHover] = useState<number | null>(null);
  const { snapshot, status, error } = useSnapshot(symbol);

  if (!snapshot) {
    return (
      <Panel
        title="GEX by Strike"
        subtitle="Dealer gamma per strike ($M notional)"
        contentClassName="p-0 flex flex-col min-h-0"
      >
        <ProfilePlaceholder status={status} message={error?.message} />
      </Panel>
    );
  }

  // Aggregate strike → net GEX, retain per-side wire rows for the
  // tooltip decomposition.
  const map = new Map<
    number,
    { net: number; netUsd: number; bySide: { C: number; P: number }; callRow?: ApiStrikeRow; putRow?: ApiStrikeRow }
  >();
  for (const s of snapshot.strikes) {
    const cur = map.get(s.strike) ?? { net: 0, netUsd: 0, bySide: { C: 0, P: 0 } };
    cur.net += s.gex_notional / 1e6;
    cur.netUsd += s.gex_notional;
    cur.bySide[s.side] += s.gex_notional / 1e6;
    if (s.side === "C") cur.callRow = s;
    else cur.putRow = s;
    map.set(s.strike, cur);
  }

  const spot = snapshot.spot;
  const all: AggRow[] = Array.from(map.entries()).map(([strike, v]) => {
    const distPct = spot > 0 ? Math.abs(strike - spot) / spot : 1;
    const distBucket: 0 | 1 | 2 = distPct < 0.0015 ? 2 : distPct < 0.005 ? 1 : 0;
    return {
      strike,
      gexM: v.net,
      gexUsd: v.netUsd,
      side: (v.bySide.C < v.bySide.P ? "P" : "C") as "C" | "P",
      isCallWall: strike === snapshot.call_wall,
      isPutWall: strike === snapshot.put_wall,
      isPin: snapshot.pin.active && strike === snapshot.pin.top_strike,
      callRow: v.callRow,
      putRow: v.putRow,
      distBucket,
    };
  });

  if (all.length === 0) {
    return (
      <Panel
        title="GEX by Strike"
        subtitle="Dealer gamma per strike ($M notional)"
        contentClassName="p-0 flex flex-col min-h-0"
      >
        <ProfilePlaceholder status="ready" empty message="snapshot has no strikes" />
      </Panel>
    );
  }

  // Pick the symmetric band around spot. Filter to strikes within ±5%
  // of spot first — the backend's top-N picker uses |dealer_pos|, which
  // can promote far-OTM LEAPS strikes with massive OI but irrelevant
  // intraday gamma. Then sort by absolute distance, slice to the visible
  // count, and re-sort descending for display.
  const NEAR_SPOT_PCT = 0.05;
  const filtered = all.filter(
    (r) => spot <= 0 || Math.abs(r.strike - spot) / spot <= NEAR_SPOT_PCT,
  );
  const pool = filtered.length >= 5 ? filtered : all;
  const sorted = [...pool].sort(
    (a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot),
  );
  const visible = sorted.slice(0, VISIBLE_ROWS).sort((a, b) => b.strike - a.strike);
  const maxAbs = Math.max(...visible.map((r) => Math.abs(r.gexM)), 1);
  const bandPct =
    spot > 0 && visible.length > 0
      ? (Math.max(
          Math.abs(visible[0].strike - spot),
          Math.abs(visible[visible.length - 1].strike - spot),
        ) /
          spot) *
        100
      : 0;

  return (
    <Panel
      title="GEX by Strike"
      subtitle={`${visible.length}/${all.length} strikes · band ±${bandPct.toFixed(2)}%`}
      actions={
        <div className="flex items-center gap-2.5 font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-muted">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1 w-3 bg-accent-long" />
            long {"\u03B3"}
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1 w-3 bg-accent-short" />
            short {"\u03B3"}
          </span>
          <Pill tone={snapshot.net_gex < 0 ? "down" : "up"}>
            net {fmtSignedAbbr(snapshot.net_gex)}
          </Pill>
        </div>
      }
      contentClassName="p-0 flex flex-col min-h-0"
    >
      <div className="relative flex flex-1 min-h-0 flex-col">
        {/* center axis indicator */}
        <div
          className="pointer-events-none absolute inset-y-0 left-[60%] w-px bg-line-strong/60"
          aria-hidden
        />

        <div className="flex flex-1 min-h-0 flex-col">
          {visible.map((r) => {
            const pct = Math.min(100, (Math.abs(r.gexM) / maxAbs) * 100);
            const isNeg = r.gexM < 0;
            const isHover = hover === r.strike;
            const isATM = r.distBucket === 2;
            const isNear = r.distBucket === 1;

            const labelTone =
              r.isCallWall
                ? "text-accent-long"
                : r.isPutWall
                  ? "text-accent-short"
                  : r.isPin
                    ? "text-accent-warn"
                    : isATM
                      ? "text-ink-high"
                      : isNear
                        ? "text-ink-base"
                        : "text-ink-muted";

            const rowBg = isATM
              ? "bg-bg-card/60 border-l border-r border-line-strong/40"
              : isNear
                ? "bg-bg-card/30"
                : "";

            return (
              <div
                key={r.strike}
                className={`group relative flex flex-1 min-h-0 items-center px-2 ${rowBg} ${isHover ? "bg-bg-hover" : ""} hover:bg-bg-hover transition-colors`}
                onMouseEnter={() => setHover(r.strike)}
                onMouseLeave={() => setHover(null)}
              >
                {/* strike label */}
                <div className="relative z-10 flex w-[60%] items-center justify-between pr-2">
                  <div className="flex items-center gap-1.5">
                    <span className={`tabnum font-mono text-[11px] ${labelTone} ${isATM ? "font-semibold" : ""}`}>
                      {r.strike}
                    </span>
                    {r.isCallWall && (
                      <span className="font-mono text-[8.5px] uppercase tracking-[0.16em] text-accent-long">
                        cw
                      </span>
                    )}
                    {r.isPutWall && (
                      <span className="font-mono text-[8.5px] uppercase tracking-[0.16em] text-accent-short">
                        pw
                      </span>
                    )}
                    {r.isPin && !r.isCallWall && !r.isPutWall && (
                      <span className="font-mono text-[8.5px] uppercase tracking-[0.16em] text-accent-warn">
                        pin
                      </span>
                    )}
                  </div>

                  {/* negative bar (extends right-to-left toward strike) */}
                  {isNeg && (
                    <div
                      className="ml-auto h-2 bg-accent-short/80"
                      style={{ width: `${pct}%`, maxWidth: "100%" }}
                    />
                  )}
                </div>

                {/* positive bar (extends left-to-right from center) */}
                <div className="relative z-10 flex w-[40%] items-center">
                  {!isNeg && (
                    <div
                      className="h-2 bg-accent-long/80"
                      style={{ width: `${pct * 0.66}%` }}
                    />
                  )}
                  <span className={`ml-auto tabnum font-mono text-[10px] ${isHover ? "text-ink-high" : "text-ink-faint"}`}>
                    {r.gexM >= 0 ? "+" : ""}
                    {r.gexM.toFixed(0)}M
                  </span>
                </div>

                {/* spot crosshair — render between the two strikes that bracket
                    spot, as a 1px accent on the lower row's top edge. */}
              </div>
            );
          })}
        </div>

        {/* spot price marker — overlay positioned where the bracketing
            strikes sit. We compute it once by finding the gap. */}
        <SpotMarker visible={visible} spot={spot} />

        {/* hover popover */}
        {hover !== null && (() => {
          const row = visible.find((r) => r.strike === hover);
          if (!row) return null;
          return (
            <div className="pointer-events-none absolute right-2 top-2 z-20">
              <StrikeTooltip
                strike={row.strike}
                spot={spot}
                netGexUsd={row.gexUsd}
                callRow={row.callRow}
                putRow={row.putRow}
                isCallWall={row.isCallWall}
                isPutWall={row.isPutWall}
                isPin={row.isPin}
                pinProb={snapshot.pin.top_probability}
                x={0}
                y={0}
              />
            </div>
          );
        })()}
      </div>

      <div className="flex shrink-0 items-center justify-between border-t border-line px-2.5 py-1.5 font-mono text-[9.5px] uppercase tracking-[0.16em] text-ink-faint">
        <span>
          spot{" "}
          <span className="tabnum text-ink-base">{spot.toFixed(2)}</span>
        </span>
        <span>
          walls{" "}
          <span className="tabnum text-accent-long">{snapshot.call_wall}</span>
          {" / "}
          <span className="tabnum text-accent-short">{snapshot.put_wall}</span>
        </span>
      </div>
    </Panel>
  );
}

// SpotMarker — thin horizontal line between the two strikes that bracket
// spot, rendered inside the strike-ladder's relative container. Computes
// the row index of the strike just above spot from the visible list and
// draws at that row's bottom edge; if spot is above all rows or below
// all rows the marker hides itself.
function SpotMarker({ visible, spot }: { visible: AggRow[]; spot: number }) {
  if (visible.length === 0) return null;
  // visible is descending in strike. Find first strike <= spot.
  let lower = -1;
  for (let i = 0; i < visible.length; i++) {
    if (visible[i].strike <= spot) {
      lower = i;
      break;
    }
  }
  if (lower <= 0) return null;
  // Row height is `flex-1` so equal share of container; place at `lower`'s
  // top-edge (which is between lower-1 and lower).
  const topPct = (lower / visible.length) * 100;
  return (
    <div
      className="pointer-events-none absolute inset-x-0 z-10 flex items-center"
      style={{ top: `${topPct}%`, transform: "translateY(-50%)" }}
    >
      <div className="h-px flex-1 border-t border-dashed border-ink-high/70" />
      <div className="ml-1 tabnum rounded-sm bg-ink-high px-1 py-px font-mono text-[9px] font-semibold text-bg-base">
        {spot.toFixed(2)}
      </div>
    </div>
  );
}

function ProfilePlaceholder({
  status,
  message,
  empty = false,
}: {
  status: string;
  message?: string;
  empty?: boolean;
}) {
  const isError = status === "error";
  return (
    <div className="flex flex-1 items-center justify-center px-3">
      {isError || empty ? (
        <span className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-ink-faint">
          {message ?? "no live state"}
        </span>
      ) : (
        <div className="h-full w-full animate-pulse bg-bg-subtle/40" />
      )}
    </div>
  );
}
