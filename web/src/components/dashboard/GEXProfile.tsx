"use client";

import { useState } from "react";
import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { fmtUsd } from "@/lib/utils";

const SYMBOL = "SPX" as const;

interface Row {
  strike: number;
  side: "C" | "P";
  gexM: number;     // GEX in $M
  isWall: boolean;
}

export function GEXProfile() {
  const [hover, setHover] = useState<number | null>(null);
  const { snapshot, status, error } = useSnapshot(SYMBOL);

  if (!snapshot) {
    return (
      <Panel
        title="GEX Profile · Strikes"
        subtitle="Dealer gamma exposure ($M notional, per strike)"
      >
        <ProfilePlaceholder status={status} message={error?.message} />
      </Panel>
    );
  }

  // Aggregate strike → net GEX (sum across sides), keep dominant side label
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
      side: v.bySide.C < v.bySide.P ? "P" : ("C" as "C" | "P"),
      gexM: v.net,
      isWall: strike === snapshot.call_wall || strike === snapshot.put_wall,
    }))
    .sort((a, b) => b.strike - a.strike);

  if (rows.length === 0) {
    return (
      <Panel
        title="GEX Profile · Strikes"
        subtitle="Dealer gamma exposure ($M notional, per strike)"
      >
        <ProfilePlaceholder status="ready" message="snapshot has no strikes" empty />
      </Panel>
    );
  }

  const maxAbs = Math.max(...rows.map((r) => Math.abs(r.gexM))) || 1;
  const spot = snapshot.spot;

  // SVG layout
  const W = 720;
  const rowH = 28;
  const PAD = { l: 70, r: 84, t: 18, b: 26 };
  const H = PAD.t + PAD.b + rows.length * rowH;
  const plotW = W - PAD.l - PAD.r;
  const center = PAD.l + plotW / 2;

  const xOf = (gexM: number) =>
    center + (gexM / maxAbs) * (plotW / 2);

  // Find virtual y of spot between two adjacent strikes
  const sortedAsc = [...rows].sort((a, b) => a.strike - b.strike);
  let spotY = PAD.t;
  for (let i = 0; i < sortedAsc.length - 1; i++) {
    const lo = sortedAsc[i].strike;
    const hi = sortedAsc[i + 1].strike;
    if (spot >= lo && spot <= hi) {
      const t = (spot - lo) / (hi - lo);
      // sortedAsc[i] is at row index = rows.length - 1 - i (since rows is desc)
      const yLo = PAD.t + (rows.length - 1 - i) * rowH + rowH / 2;
      const yHi = PAD.t + (rows.length - 1 - (i + 1)) * rowH + rowH / 2;
      spotY = yLo + (yHi - yLo) * t;
      break;
    }
  }

  return (
    <Panel
      title="GEX Profile · Strikes"
      subtitle="Dealer gamma exposure ($M notional, per strike)"
      actions={
        <div className="flex items-center gap-2">
          <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-ink-muted">
            <span className="h-1.5 w-3 rounded-full bg-accent-long" />
            Long γ
          </span>
          <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-ink-muted">
            <span className="h-1.5 w-3 rounded-full bg-accent-short" />
            Short γ
          </span>
          <Pill tone={snapshot.net_gex < 0 ? "down" : "up"}>
            Net {fmtUsd(snapshot.net_gex / 1e9, true)}B
          </Pill>
        </div>
      }
      contentClassName="p-0"
    >
      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-auto"
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            <linearGradient id="gexNeg" x1="1" y1="0" x2="0" y2="0">
              <stop offset="0%" stopColor="#ef4444" stopOpacity="0.95" />
              <stop offset="100%" stopColor="#ef4444" stopOpacity="0.15" />
            </linearGradient>
            <linearGradient id="gexPos" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#22c55e" stopOpacity="0.15" />
              <stop offset="100%" stopColor="#22c55e" stopOpacity="0.95" />
            </linearGradient>
            <filter id="barGlow" x="-30%" y="-50%" width="160%" height="200%">
              <feGaussianBlur stdDeviation="3" />
            </filter>
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
            y={H - PAD.b + 18}
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
                  y={H - PAD.b + 18}
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

          {/* spot crosshair */}
          <line
            x1={PAD.l}
            y1={spotY}
            x2={W - PAD.r}
            y2={spotY}
            stroke="#ff2a5b"
            strokeWidth="1.2"
            strokeDasharray="3 4"
            opacity="0.85"
          />
          <rect
            x={W - PAD.r + 6}
            y={spotY - 9}
            width={70}
            height={18}
            rx={4}
            fill="#ff2a5b"
            opacity="0.9"
          />
          <text
            x={W - PAD.r + 41}
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
            const isCallWall = r.strike === snapshot.call_wall;
            const isPutWall = r.strike === snapshot.put_wall;

            const barX = r.gexM < 0 ? xOf(r.gexM) : center;
            const barW = Math.abs(xOf(r.gexM) - center);
            const fill = r.gexM < 0 ? "url(#gexNeg)" : "url(#gexPos)";
            const glowColor = r.gexM < 0 ? "#ef4444" : "#22c55e";

            return (
              <g
                key={r.strike}
                onMouseEnter={() => setHover(r.strike)}
                onMouseLeave={() => setHover(null)}
                className="cursor-default"
              >
                {/* row hover bg */}
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

                {/* strike label */}
                <text
                  x={PAD.l - 12}
                  y={cy + 3}
                  textAnchor="end"
                  fontSize="11"
                  fill={isHover || isPutWall || isCallWall ? "#f4f4f5" : "#a1a1aa"}
                  fontFamily="var(--font-jb-mono)"
                  fontWeight={isPutWall || isCallWall ? "600" : "400"}
                >
                  {r.strike}
                </text>

                {/* wall tag */}
                {(isCallWall || isPutWall) && (
                  <text
                    x={PAD.l - 56}
                    y={cy + 3}
                    fontSize="8.5"
                    fill={isCallWall ? "#22c55e" : "#ef4444"}
                    fontFamily="var(--font-jb-mono)"
                    letterSpacing="1"
                  >
                    {isCallWall ? "C-WALL" : "P-WALL"}
                  </text>
                )}

                {/* glow underbar (peak strikes) */}
                {Math.abs(r.gexM) > maxAbs * 0.7 && (
                  <rect
                    x={barX - 3}
                    y={cy - 11}
                    width={barW + 6}
                    height={22}
                    fill={glowColor}
                    opacity={0.4}
                    filter="url(#barGlow)"
                  />
                )}

                {/* bar */}
                <rect
                  x={barX}
                  y={cy - 7}
                  width={Math.max(2, barW)}
                  height={14}
                  rx={2}
                  fill={fill}
                  stroke={isHover ? glowColor : "transparent"}
                  strokeWidth="1"
                  opacity={isHover ? 1 : 0.92}
                />

                {/* value label */}
                <text
                  x={r.gexM < 0 ? barX - 6 : barX + barW + 6}
                  y={cy + 3}
                  textAnchor={r.gexM < 0 ? "end" : "start"}
                  fontSize="10"
                  fill={isHover ? glowColor : "#71717a"}
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

        {/* footer caption */}
        <div className="flex items-center justify-between px-5 pb-4 pt-1 text-[10.5px] text-ink-faint">
          <span>
            Pin candidate{" "}
            <span className="tabnum text-signal-pin">@{snapshot.pin.top_strike}</span>{" "}
            · prob{" "}
            <span className="tabnum text-ink-base">
              {(snapshot.pin.top_probability * 100).toFixed(0)}%
            </span>
          </span>
          <span>
            Walls — call{" "}
            <span className="tabnum text-accent-long">{snapshot.call_wall}</span>
            {" · "}put{" "}
            <span className="tabnum text-accent-short">{snapshot.put_wall}</span>
          </span>
        </div>
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
    <div className="flex h-64 items-center justify-center">
      {isError || empty ? (
        <span className="text-[11px] uppercase tracking-[0.18em] text-accent-warn">
          {message ?? "no live state"}
        </span>
      ) : (
        <div className="h-full w-full mx-5 rounded-md bg-bg-subtle/40 animate-pulse" />
      )}
    </div>
  );
}
