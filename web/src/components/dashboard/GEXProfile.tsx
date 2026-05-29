"use client";

import { useState } from "react";
import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { fmtSignedAbbr } from "@/lib/utils";
import { StrikeTooltip } from "@/components/primitives/StrikeTooltip";
import type { StrikeRow as ApiStrikeRow } from "@/lib/api/types";

interface Row {
  strike: number;
  side: "C" | "P";
  gexM: number; // GEX in $M
  gexUsd: number; // GEX in raw $
  isCallWall: boolean;
  isPutWall: boolean;
  isPin: boolean;
  callRow?: ApiStrikeRow;
  putRow?: ApiStrikeRow;
  // amplifyTier — 0 = far, 1 = near (within ±0.5%), 2 = at-the-money row
  amplifyTier: 0 | 1 | 2;
}

export function GEXProfile({ symbol }: { symbol: "SPX" | "NDX" }) {
  const [hover, setHover] = useState<{ strike: number; y: number } | null>(null);
  const { snapshot, status, error } = useSnapshot(symbol);

  if (!snapshot) {
    return (
      <Panel title="GEX by Strike" subtitle="Dealer gamma per strike ($M notional)">
        <ProfilePlaceholder status={status} message={error?.message} />
      </Panel>
    );
  }

  // Aggregate strike → net GEX (sum across sides), keep dominant side label,
  // and retain the original wire rows so the tooltip can decompose by leg.
  const map = new Map<
    number,
    {
      net: number;
      netUsd: number;
      bySide: { C: number; P: number };
      callRow?: ApiStrikeRow;
      putRow?: ApiStrikeRow;
    }
  >();
  snapshot.strikes.forEach((s) => {
    const cur = map.get(s.strike) ?? {
      net: 0,
      netUsd: 0,
      bySide: { C: 0, P: 0 },
    };
    cur.net += s.gex_notional / 1e6;
    cur.netUsd += s.gex_notional;
    cur.bySide[s.side] += s.gex_notional / 1e6;
    if (s.side === "C") cur.callRow = s;
    else cur.putRow = s;
    map.set(s.strike, cur);
  });

  const spot = snapshot.spot;
  const rows: Row[] = Array.from(map.entries())
    .map(([strike, v]) => {
      const distPct = spot > 0 ? Math.abs(strike - spot) / spot : 1;
      const amplifyTier: 0 | 1 | 2 =
        distPct < 0.0015 ? 2 : distPct < 0.005 ? 1 : 0;
      return {
        strike,
        side: (v.bySide.C < v.bySide.P ? "P" : "C") as "C" | "P",
        gexM: v.net,
        gexUsd: v.netUsd,
        isCallWall: strike === snapshot.call_wall,
        isPutWall: strike === snapshot.put_wall,
        isPin: snapshot.pin.active && strike === snapshot.pin.top_strike,
        callRow: v.callRow,
        putRow: v.putRow,
        amplifyTier,
      };
    })
    .sort((a, b) => b.strike - a.strike);

  if (rows.length === 0) {
    return (
      <Panel title="GEX by Strike" subtitle="Dealer gamma per strike ($M notional)">
        <ProfilePlaceholder status="ready" empty message="snapshot has no strikes yet" />
      </Panel>
    );
  }

  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.gexM))) || 1;

  // SVG layout. Variable row height per amplifyTier:
  // tier 2 (ATM) = 26, tier 1 (near spot) = 24, tier 0 = 20.
  const W = 720;
  const PAD = { l: 64, r: 80, t: 14, b: 22 };

  let runningY = PAD.t;
  const rowsWithY = rows.map((r) => {
    const h = r.amplifyTier === 2 ? 26 : r.amplifyTier === 1 ? 24 : 20;
    const y = runningY;
    runningY += h;
    return { ...r, y, h };
  });
  const H = runningY + PAD.b;
  const plotW = W - PAD.l - PAD.r;
  const center = PAD.l + plotW / 2;

  const xOf = (gexM: number) => center + (gexM / maxAbs) * (plotW / 2);

  // Find virtual y of spot between two adjacent strikes.
  const sortedAsc = [...rowsWithY].sort((a, b) => a.strike - b.strike);
  let spotY = PAD.t;
  for (let i = 0; i < sortedAsc.length - 1; i++) {
    const lo = sortedAsc[i];
    const hi = sortedAsc[i + 1];
    if (spot >= lo.strike && spot <= hi.strike) {
      const t = (spot - lo.strike) / (hi.strike - lo.strike);
      const yLo = lo.y + lo.h / 2;
      const yHi = hi.y + hi.h / 2;
      spotY = yLo + (yHi - yLo) * t;
      break;
    }
  }

  // Tooltip positioning — anchor to right of the visible bar area.
  const tooltipRow = hover
    ? rowsWithY.find((r) => r.strike === hover.strike)
    : null;

  return (
    <Panel
      title="GEX by Strike"
      subtitle="Dealer gamma per strike ($M notional)"
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
      <div className="relative flex-1 min-h-0 overflow-y-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          width={W}
          height={H}
          className="block w-full"
          preserveAspectRatio="xMidYMin meet"
        >
          <defs>
            <linearGradient id="gexNeg" x1="1" y1="0" x2="0" y2="0">
              <stop offset="0%" stopColor="#ef4444" stopOpacity="0.95" />
              <stop offset="100%" stopColor="#ef4444" stopOpacity="0.18" />
            </linearGradient>
            <linearGradient id="gexPos" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#10b981" stopOpacity="0.18" />
              <stop offset="100%" stopColor="#10b981" stopOpacity="0.95" />
            </linearGradient>
          </defs>

          {/* center axis */}
          <line
            x1={center}
            y1={PAD.t - 6}
            x2={center}
            y2={H - PAD.b + 6}
            stroke="#3a3a40"
            strokeOpacity="0.7"
          />
          <text
            x={center}
            y={H - PAD.b + 14}
            textAnchor="middle"
            fontSize="9"
            fill="#71717a"
            fontFamily="var(--font-jb-mono)"
            letterSpacing="2"
          >
            $0
          </text>

          {/* x scale ticks */}
          {[-1, -0.5, 0.5, 1].map((mul) => {
            const v = mul * maxAbs;
            const x = xOf(v);
            return (
              <g key={mul}>
                <line
                  x1={x}
                  y1={PAD.t - 4}
                  x2={x}
                  y2={H - PAD.b + 4}
                  stroke="#26262a"
                  strokeOpacity="0.5"
                  strokeDasharray="2 4"
                />
                <text
                  x={x}
                  y={H - PAD.b + 14}
                  textAnchor="middle"
                  fontSize="9"
                  fill="#52525b"
                  fontFamily="var(--font-jb-mono)"
                >
                  {v >= 0 ? "+" : ""}
                  {Math.round(v)}M
                </text>
              </g>
            );
          })}

          {/* spot crosshair — monochrome ink-high, prominent */}
          <line
            x1={PAD.l}
            y1={spotY}
            x2={W - PAD.r}
            y2={spotY}
            stroke="#f4f4f5"
            strokeWidth="1"
            strokeDasharray="3 4"
            opacity="0.85"
          />
          <rect
            x={W - PAD.r + 4}
            y={spotY - 8}
            width={68}
            height={16}
            fill="#f4f4f5"
            opacity="0.95"
          />
          <text
            x={W - PAD.r + 38}
            y={spotY + 3}
            textAnchor="middle"
            fontSize="10"
            fill="#08080a"
            fontFamily="var(--font-jb-mono)"
            fontWeight="600"
          >
            {spot.toFixed(2)}
          </text>

          {/* rows */}
          {rowsWithY.map((r) => {
            const y = r.y;
            const cy = y + r.h / 2;
            const isHover = hover?.strike === r.strike;

            const barX = r.gexM < 0 ? xOf(r.gexM) : center;
            const barW = Math.abs(xOf(r.gexM) - center);
            const fill = r.gexM < 0 ? "url(#gexNeg)" : "url(#gexPos)";
            const barH = r.amplifyTier === 2 ? 16 : r.amplifyTier === 1 ? 14 : 12;

            // Near-spot rows get a subtle inkLayer tint for amplification.
            const tintOpacity =
              r.amplifyTier === 2 ? 0.06 : r.amplifyTier === 1 ? 0.03 : 0;

            const labelFill =
              isHover || r.isPutWall || r.isCallWall || r.isPin
                ? "#f4f4f5"
                : r.amplifyTier > 0
                  ? "#e4e4e7"
                  : "#a1a1aa";
            const labelFontSize = r.amplifyTier === 2 ? 12.5 : r.amplifyTier === 1 ? 11.5 : 11;
            const labelWeight = r.isPutWall || r.isCallWall || r.isPin || r.amplifyTier === 2 ? "600" : "400";

            return (
              <g
                key={r.strike}
                onMouseEnter={() => setHover({ strike: r.strike, y: cy })}
                onMouseLeave={() => setHover(null)}
                className="cursor-default"
              >
                {/* row tint for ATM amplification */}
                {tintOpacity > 0 && (
                  <rect
                    x={PAD.l - 60}
                    y={y}
                    width={W - PAD.l - PAD.r + 130}
                    height={r.h}
                    fill="#fff"
                    opacity={tintOpacity}
                  />
                )}

                {isHover && (
                  <rect
                    x={PAD.l - 60}
                    y={y}
                    width={W - PAD.l - PAD.r + 130}
                    height={r.h}
                    fill="#fff"
                    opacity="0.04"
                  />
                )}

                <text
                  x={PAD.l - 10}
                  y={cy + 3}
                  textAnchor="end"
                  fontSize={labelFontSize}
                  fill={labelFill}
                  fontFamily="var(--font-jb-mono)"
                  fontWeight={labelWeight}
                >
                  {r.strike}
                </text>

                {(r.isCallWall || r.isPutWall) && (
                  <text
                    x={PAD.l - 56}
                    y={cy + 3}
                    fontSize="8.5"
                    fill={r.isCallWall ? "#10b981" : "#ef4444"}
                    fontFamily="var(--font-jb-mono)"
                    letterSpacing="1"
                  >
                    {r.isCallWall ? "C-WALL" : "P-WALL"}
                  </text>
                )}
                {r.isPin && !r.isCallWall && !r.isPutWall && (
                  <text
                    x={PAD.l - 56}
                    y={cy + 3}
                    fontSize="8.5"
                    fill="#f59e0b"
                    fontFamily="var(--font-jb-mono)"
                    letterSpacing="1"
                  >
                    PIN
                  </text>
                )}

                <rect
                  x={barX}
                  y={cy - barH / 2}
                  width={Math.max(2, barW)}
                  height={barH}
                  fill={fill}
                  opacity={isHover ? 1 : 0.92}
                />

                <text
                  x={r.gexM < 0 ? barX - 6 : barX + barW + 6}
                  y={cy + 3}
                  textAnchor={r.gexM < 0 ? "end" : "start"}
                  fontSize={r.amplifyTier === 2 ? 11 : 10}
                  fill={isHover ? "#f4f4f5" : "#71717a"}
                  fontFamily="var(--font-jb-mono)"
                  fontWeight="500"
                >
                  {r.gexM >= 0 ? "+" : ""}
                  {r.gexM.toFixed(0)}M
                </text>
              </g>
            );
          })}
        </svg>

        {/* Hover popover. Position relative to the scrollable container,
            so left/top map to the SVG's natural coords scaled by the
            container width ratio. We keep it simple: anchor near the
            right side of the bar area. */}
        {tooltipRow && hover && (
          <div className="pointer-events-none absolute inset-0">
            <StrikeTooltipPosition
              row={tooltipRow}
              spot={spot}
              snapshot={snapshot}
              svgH={H}
            />
          </div>
        )}
      </div>

      <div className="flex shrink-0 items-center justify-between border-t border-line px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">
        <span>
          pin{" "}
          <span className="tabnum text-accent-warn">@{snapshot.pin.top_strike}</span>{" "}
          · prob{" "}
          <span className="tabnum text-ink-base">
            {(snapshot.pin.top_probability * 100).toFixed(0)}%
          </span>
        </span>
        <span>
          walls — call{" "}
          <span className="tabnum text-accent-long">{snapshot.call_wall}</span>
          {" · "}put{" "}
          <span className="tabnum text-accent-short">{snapshot.put_wall}</span>
        </span>
      </div>
    </Panel>
  );
}

// StrikeTooltipPosition — translates the SVG-coord row position into the
// container's CSS coords. The SVG renders with width=100% so the natural
//-to-css ratio is just `containerWidth / W`. The popover gets positioned
// to the right of the row label area for hover affordance.
function StrikeTooltipPosition({
  row,
  spot,
  snapshot,
  svgH,
}: {
  row: ReturnType<typeof Object.assign> & {
    strike: number;
    gexUsd: number;
    isCallWall: boolean;
    isPutWall: boolean;
    isPin: boolean;
    callRow?: ApiStrikeRow;
    putRow?: ApiStrikeRow;
    y: number;
    h: number;
  };
  spot: number;
  snapshot: ReturnType<typeof useSnapshot>["snapshot"];
  svgH: number;
}) {
  // Place tooltip vertically at the row mid-y, horizontally a bit right of
  // the strike-label column so it doesn't cover the bar. Convert from SVG
  // coords to container percentage.
  const cy = row.y + row.h / 2;
  // Anchor at ~52% across the SVG horizontally and use percentage so the
  // popover stays correctly placed regardless of container width.
  const leftPct = 52;
  const topPct = (cy / svgH) * 100;

  // If row is in the bottom third, flip popover above to avoid clipping.
  const flipUp = topPct > 70;

  return (
    <div
      className="absolute"
      style={{
        left: `${leftPct}%`,
        top: `${topPct}%`,
        transform: flipUp ? "translate(0, -100%)" : "translate(0, 0)",
      }}
    >
      <StrikeTooltip
        strike={row.strike}
        spot={spot}
        netGexUsd={row.gexUsd}
        callRow={row.callRow}
        putRow={row.putRow}
        isCallWall={row.isCallWall}
        isPutWall={row.isPutWall}
        isPin={row.isPin}
        pinProb={snapshot?.pin.top_probability}
        x={0}
        y={0}
      />
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
    <div className="flex h-64 items-center justify-center px-3">
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
