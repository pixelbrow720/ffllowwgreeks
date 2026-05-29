"use client";

import { useRef } from "react";
import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { useSpotHistory } from "@/lib/api/history";
import { useMeasuredBox } from "@/lib/hooks/useLayoutMounted";
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  ReferenceLine,
  YAxis,
  XAxis,
  Tooltip,
} from "recharts";
import { fmtNum } from "@/lib/utils";

export function SpotChart({ symbol }: { symbol: "SPX" | "NDX" }) {
  const { snapshot, status, error } = useSnapshot(symbol);
  const series = useSpotHistory(symbol);

  if (!snapshot) {
    return (
      <Panel title={`${symbol} Spot`} subtitle="1-min · with dealer key levels">
        <SpotPlaceholder status={status} message={error?.message} />
      </Panel>
    );
  }

  const last = series[series.length - 1] ?? { spot: snapshot.spot, t: "—" };
  const first = series[0] ?? last;
  const delta = last.spot - first.spot;
  const pct = first.spot > 0 ? (delta / first.spot) * 100 : 0;
  const trendUp = delta >= 0;

  const hasSeries = series.length > 0;

  const seriesSpots = series.map((p) => p.spot);
  const minSpot = seriesSpots.length > 0 ? Math.min(...seriesSpots) : snapshot.spot;
  const maxSpot = seriesSpots.length > 0 ? Math.max(...seriesSpots) : snapshot.spot;
  const lo = Math.min(snapshot.put_wall || snapshot.spot, minSpot, snapshot.spot);
  const hi = Math.max(snapshot.call_wall || snapshot.spot, maxSpot, snapshot.spot);
  const pad = Math.max(2, (hi - lo) * 0.05);
  const yDomain: [number, number] = [Math.floor(lo - pad), Math.ceil(hi + pad)];

  const regimeLabel =
    snapshot.regime === "SHORT_GAMMA"
      ? "SHORT \u03B3"
      : snapshot.regime === "LONG_GAMMA"
        ? "LONG \u03B3"
        : snapshot.regime === "NEUTRAL"
          ? "NEUTRAL"
          : "—";

  return (
    <Panel
      title={`${symbol} Spot`}
      subtitle={`${snapshot.fut_front_sym || "fut"} basis ${snapshot.basis_smooth.toFixed(2)} · ${series.length} samples`}
      actions={
        <>
          <Pill tone={trendUp ? "up" : "down"}>
            {trendUp ? "\u25B2" : "\u25BC"} {Math.abs(pct).toFixed(2)}%
          </Pill>
          <Pill tone="neutral">{regimeLabel}</Pill>
        </>
      }
      contentClassName="p-3 flex flex-col"
    >
      <div className="mb-3 flex items-baseline gap-3 shrink-0">
        <span className="font-display tabnum text-[34px] font-medium leading-none tracking-[-0.02em] text-ink-high">
          {fmtNum(last.spot, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </span>
        <span
          className={`tabnum text-sm ${trendUp ? "text-accent-long" : "text-accent-short"}`}
        >
          {trendUp ? "+" : "\u2212"}
          {Math.abs(delta).toFixed(2)}
        </span>
        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          {series.length > 1
            ? `vs session open ${first.spot.toFixed(2)}`
            : "session warming up"}
        </span>
      </div>

      <div className="min-h-0 flex-1">
        {hasSeries ? (
          <ChartBody snapshot={snapshot} series={series} yDomain={yDomain} />
        ) : (
          <SpotEmptyState />
        )}
      </div>
    </Panel>
  );
}

function ChartBody({
  snapshot,
  series,
  yDomain,
}: {
  snapshot: ReturnType<typeof useSnapshot>["snapshot"];
  series: ReturnType<typeof useSpotHistory>;
  yDomain: [number, number];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const box = useMeasuredBox(ref);

  if (!snapshot) return null;

  return (
    <div ref={ref} className="h-full w-full">
      {box.ready ? (
        <ResponsiveContainer width={box.width} height={box.height}>
          <AreaChart data={series} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="spotFillMono" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#f4f4f5" stopOpacity={0.16} />
                <stop offset="100%" stopColor="#f4f4f5" stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="t"
              stroke="#52525b"
              fontSize={10}
              tickLine={false}
              axisLine={false}
              interval="preserveStartEnd"
              minTickGap={32}
            />
            <YAxis
              domain={yDomain}
              stroke="#52525b"
              fontSize={10}
              tickLine={false}
              axisLine={false}
              orientation="right"
              tickFormatter={(v) => v.toFixed(0)}
              width={48}
            />
            <Tooltip
              contentStyle={{
                background: "#0f0f12",
                border: "1px solid #26262a",
                borderRadius: 0,
                fontSize: 11,
              }}
              labelStyle={{ color: "#a1a1aa" }}
              formatter={(v) => (v as number).toFixed(2)}
            />
            <ReferenceLine
              y={snapshot.zero_gamma}
              stroke="#71717a"
              strokeDasharray="2 4"
              strokeOpacity={0.85}
              label={{
                value: `Zero \u03B3 ${snapshot.zero_gamma.toFixed(1)}`,
                fill: "#a1a1aa",
                fontSize: 9,
                position: "insideTopRight",
              }}
            />
            {/* Walls. When call_wall === put_wall (gamma concentrated at a
                single round-number strike — common on OPEX days), render a
                single neutral merged label so the two ink-only labels
                don't sit on top of each other unreadable. */}
            {snapshot.call_wall > 0 && snapshot.call_wall === snapshot.put_wall ? (
              <ReferenceLine
                y={snapshot.call_wall}
                stroke="#a1a1aa"
                strokeDasharray="4 4"
                strokeOpacity={0.85}
                label={{
                  value: `Call/Put Wall ${snapshot.call_wall}`,
                  fill: "#e4e4e7",
                  fontSize: 9,
                  position: "insideTopRight",
                }}
              />
            ) : (
              <>
                <ReferenceLine
                  y={snapshot.call_wall}
                  stroke="#10b981"
                  strokeDasharray="4 4"
                  strokeOpacity={0.7}
                  label={{
                    value: `Call Wall ${snapshot.call_wall}`,
                    fill: "#10b981",
                    fontSize: 9,
                    position: "insideTopRight",
                  }}
                />
                <ReferenceLine
                  y={snapshot.put_wall}
                  stroke="#ef4444"
                  strokeDasharray="4 4"
                  strokeOpacity={0.7}
                  label={{
                    value: `Put Wall ${snapshot.put_wall}`,
                    fill: "#ef4444",
                    fontSize: 9,
                    position: "insideBottomRight",
                  }}
                />
              </>
            )}
            {snapshot.pin.active && snapshot.pin.top_strike > 0 && (
              <ReferenceLine
                y={snapshot.pin.top_strike}
                stroke="#f59e0b"
                strokeDasharray="1 3"
                strokeOpacity={0.8}
                label={{
                  value: `Pin ${snapshot.pin.top_strike} \u00B7 ${(snapshot.pin.top_probability * 100).toFixed(0)}%`,
                  fill: "#f59e0b",
                  fontSize: 9,
                  position: "insideBottomRight",
                }}
              />
            )}
            <Area
              type="monotone"
              dataKey="spot"
              stroke="#f4f4f5"
              strokeWidth={1.75}
              fill="url(#spotFillMono)"
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
      ) : null}
    </div>
  );
}

// SpotEmptyState — replaces "waiting for first session tick" prose with
// a brand-themed pulsing spiral motif. Decorative only; copy clarifies
// the why. Per CLAUDE.md, brand is decorative ambient.
function SpotEmptyState() {
  return (
    <div className="relative flex h-full min-h-[200px] flex-col items-center justify-center overflow-hidden">
      <svg
        viewBox="0 0 200 200"
        className="absolute inset-0 m-auto h-[160%] w-[160%] -translate-y-2 opacity-60"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <radialGradient id="spotEmptyGlow" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#ff2a5b" stopOpacity="0.18" />
            <stop offset="55%" stopColor="#ff2a5b" stopOpacity="0.06" />
            <stop offset="100%" stopColor="#ff2a5b" stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx="100" cy="100" r="80" fill="url(#spotEmptyGlow)" />
        {[24, 36, 48, 60, 72, 84].map((r, i) => (
          <circle
            key={r}
            cx="100"
            cy="100"
            r={r}
            stroke="#ff2a5b"
            strokeOpacity={0.06 + i * 0.01}
            strokeWidth="0.6"
            strokeDasharray="2 6"
            fill="none"
            className="animate-spin-slow"
            style={{
              transformOrigin: "100px 100px",
              animationDuration: `${20 + i * 6}s`,
              animationDirection: i % 2 === 0 ? "normal" : "reverse",
            }}
          />
        ))}
        {/* axis crosshair */}
        <line x1="20" y1="100" x2="180" y2="100" stroke="#26262a" strokeOpacity="0.6" strokeDasharray="2 4" />
        <line x1="100" y1="20" x2="100" y2="180" stroke="#26262a" strokeOpacity="0.6" strokeDasharray="2 4" />
      </svg>

      <div className="relative z-10 flex flex-col items-center gap-1 px-4 text-center">
        <span className="font-mono text-[10px] uppercase tracking-[0.24em] text-brand-hi">
          / live spot
        </span>
        <span className="font-display text-[18px] font-medium tracking-tight text-ink-high">
          warming up the tape
        </span>
        <span className="text-[10.5px] text-ink-faint">
          The chart unfolds once the WS stream produces 1 minute of session data.
        </span>
      </div>
    </div>
  );
}

function SpotPlaceholder({ status, message }: { status: string; message?: string }) {
  const isError = status === "error";
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="mb-3 flex items-baseline gap-3 shrink-0">
        <span className="font-display tabnum text-[34px] font-medium leading-none text-ink-faint">
          —
        </span>
        <span className="tabnum text-sm text-ink-faint">…</span>
        <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          {isError ? message ?? "backend unreachable" : "loading"}
        </span>
      </div>
      <div className="flex min-h-0 flex-1 items-center justify-center">
        {isError ? (
          <span className="font-mono text-[10.5px] uppercase tracking-[0.18em] text-accent-warn">
            no live state
          </span>
        ) : (
          <SpotEmptyState />
        )}
      </div>
    </div>
  );
}
