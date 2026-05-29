"use client";

import { useState } from "react";
import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { fmtSignedAbbr } from "@/lib/utils";

interface Row {
  strike: number;
  side: "C" | "P";
  gexM: number; // GEX in $M
  isCallWall: boolean;
  isPutWall: boolean;
  isPin: boolean;
}

export function GEXProfile({ symbol }: { symbol: "SPX" | "NDX" }) {
  const [hover, setHover] = useState<number | null>(null);
  const { snapshot, status, error } = useSnapshot(symbol);

  if (!snapshot) {
    return (
      <Panel title="GEX by Strike" subtitle="Dealer gamma per strike ($M notional)">
        <ProfilePlaceholder status={status} message={error?.message} />
      </Panel>
    );
  }

  // Aggregate strike → net GEX (sum across sides), keep dominant side label.
  const map = new Map<number, { net: number; bySide: { C: number; P: number } }>();
  snapshot.strikes.forEach((s) => {
    const cur = map.get(s.strike) ?? { net: 0, bySide: { C: 0, P: 0 } };
    cur.net += s.gex_notional / 1e6;
    cur.bySide[s.side] += s.gex_notional / 1e6;
    map.set(s.strike, cur);
  });

  const rows: Row[] = Array.from(map.entries())
    .map(([strike, v]) => ({
      strike,
      side: (v.bySide.C < v.bySide.P ? "P" : "C") as "C" | "P",
      gexM: v.net,
      isCallWall: strike === snapshot.call_wall,
      isPutWall: strike === snapshot.put_wall,
      isPin: snapshot.pin.active && strike === snapshot.pin.top_strike,
    }))
    .sort((a, b) => b.strike - a.strike);

  if (rows.length === 0) {
    return (
      <Panel title="GEX by Strike" subtitle="Dealer gamma per strike ($M notional)">
        <ProfilePlaceholder status="ready" empty message="snapshot has no strikes yet" />
      </Panel>
    );
  }

  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.gexM))) || 1;
  const spot = snapshot.spot;

  // SVG layout
  const W = 720;
  const rowH = 22;
  const PAD = { l: 64, r: 80, t: 14, b: 22 };
  const H = PAD.t + PAD.b + rows.length * rowH;
  const plotW = W - PAD.l - PAD.r;
  const center = PAD.l + plotW / 2;

  const xOf = (gexM: number) => center + (gexM / maxAbs) * (plotW / 2);

  // Find virtual y of spot between two adjacent strikes.
  const sortedAsc = [...rows].sort((a, b) => a.strike - b.strike);
  let spotY = PAD.t;
  for (let i = 0; i < sortedAsc.length - 1; i++) {
    const lo = sortedAsc[i].strike;
    const hi = sortedAsc[i + 1].strike;
    if (spot >= lo && spot <= hi) {
      const t = (spot - lo) / (hi - lo);
      const yLo = PAD.t + (rows.length - 1 - i) * rowH + rowH / 2;
      const yHi = PAD.t + (rows.length - 1 - (i + 1)) * rowH + rowH / 2;
      spotY = yLo + (yHi - yLo) * t;
      break;
    }
  }

  return (
    <Panel
      title="GEX by Strike"
      subtitle="Dealer gamma per strike ($M notional)"
      actions={
        <div className="flex items-center gap-2.5 font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-muted">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1 w-3 bg-accent-long" />
            long \u03B3
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-1 w-3 bg-accent-short" />
            short \u03B3
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

          {/* spot crosshair — monochrome ink-high */}
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
          {rows.map((r, i) => {
            const y = PAD.t + i * rowH;
            const cy = y + rowH / 2;
            const isHover = hover === r.strike;

            const barX = r.gexM < 0 ? xOf(r.gexM) : center;
            const barW = Math.abs(xOf(r.gexM) - center);
            const fill = r.gexM < 0 ? "url(#gexNeg)" : "url(#gexPos)";

            return (
              <g
                key={r.strike}
                onMouseEnter={() => setHover(r.strike)}
                onMouseLeave={() => setHover(null)}
                className="cursor-default"
              >
                {isHover && (
                  <rect
                    x={PAD.l - 60}
                    y={y}
                    width={W - PAD.l - PAD.r + 130}
                    height={rowH}
                    fill="#fff"
                    opacity="0.025"
                  />
                )}

                <text
                  x={PAD.l - 10}
                  y={cy + 3}
                  textAnchor="end"
                  fontSize="11"
                  fill={
                    isHover || r.isPutWall || r.isCallWall || r.isPin
                      ? "#f4f4f5"
                      : "#a1a1aa"
                  }
                  fontFamily="var(--font-jb-mono)"
                  fontWeight={r.isPutWall || r.isCallWall || r.isPin ? "600" : "400"}
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
                  y={cy - 6}
                  width={Math.max(2, barW)}
                  height={12}
                  fill={fill}
                  opacity={isHover ? 1 : 0.92}
                />

                <text
                  x={r.gexM < 0 ? barX - 6 : barX + barW + 6}
                  y={cy + 3}
                  textAnchor={r.gexM < 0 ? "end" : "start"}
                  fontSize="10"
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
