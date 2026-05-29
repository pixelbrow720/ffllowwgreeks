"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { useSnapshot } from "@/lib/api/snapshot";
import { useSpotHistory } from "@/lib/api/history";
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

const SYMBOL = "SPX" as const;

export function SpotChart() {
  const { snapshot, status, error } = useSnapshot(SYMBOL);
  const series = useSpotHistory(SYMBOL);

  if (!snapshot) {
    return (
      <Panel title={`${SYMBOL} Spot`} subtitle="1-min · with dealer key levels">
        <SpotPlaceholder status={status} message={error?.message} />
      </Panel>
    );
  }

  const last = series[series.length - 1] ?? { spot: snapshot.spot };
  const first = series[0] ?? last;
  const delta = last.spot - first.spot;
  const pct = first.spot > 0 ? (delta / first.spot) * 100 : 0;
  const trendUp = delta >= 0;

  const lo = Math.min(snapshot.put_wall, ...series.map((p) => p.spot), snapshot.spot);
  const hi = Math.max(snapshot.call_wall, ...series.map((p) => p.spot), snapshot.spot);
  const pad = Math.max(2, (hi - lo) * 0.05);
  const yDomain: [number, number] = [Math.floor(lo - pad), Math.ceil(hi + pad)];

  const regimeLabel = snapshot.regime === "SHORT_GAMMA"
    ? "SHORT γ"
    : snapshot.regime === "LONG_GAMMA"
      ? "LONG γ"
      : snapshot.regime === "NEUTRAL"
        ? "NEUTRAL"
        : "—";

  return (
    <Panel
      title={`${SYMBOL} Spot · ${snapshot.fut_front_sym} basis ${snapshot.basis_smooth.toFixed(2)}`}
      subtitle="1-min · with dealer key levels"
      actions={
        <>
          <Pill tone={trendUp ? "up" : "down"}>
            {trendUp ? "▲" : "▼"} {pct.toFixed(2)}%
          </Pill>
          <Pill tone="brand">{regimeLabel}</Pill>
        </>
      }
    >
      <div className="h-full min-h-0 flex flex-col">
        <div className="flex items-baseline gap-3 mb-3 shrink-0">
          <span className="tabnum text-3xl font-medium text-ink-high">
            {fmtNum(last.spot, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
          <span className={`tabnum text-sm ${trendUp ? "text-accent-long" : "text-accent-short"}`}>
            {trendUp ? "+" : ""}
            {delta.toFixed(2)}
          </span>
          <span className="text-xs text-ink-faint">
            {series.length > 1 ? `vs session open ${first.spot.toFixed(2)}` : "session warming up"}
          </span>
        </div>

        <div className="flex-1 min-h-0">
          <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={series} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="spotFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#ff2a5b" stopOpacity={0.35} />
                <stop offset="100%" stopColor="#ff2a5b" stopOpacity={0} />
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
                borderRadius: 6,
                fontSize: 11,
              }}
              labelStyle={{ color: "#a1a1aa" }}
              formatter={(v) => (v as number).toFixed(2)}
            />
            <ReferenceLine
              y={snapshot.call_wall}
              stroke="#22c55e"
              strokeDasharray="4 4"
              strokeOpacity={0.7}
              label={{ value: `Call Wall ${snapshot.call_wall}`, fill: "#22c55e", fontSize: 9, position: "insideTopRight" }}
            />
            <ReferenceLine
              y={snapshot.zero_gamma}
              stroke="#a855f7"
              strokeDasharray="2 4"
              strokeOpacity={0.7}
              label={{ value: `Zero γ ${snapshot.zero_gamma.toFixed(1)}`, fill: "#a855f7", fontSize: 9, position: "insideTopRight" }}
            />
            <ReferenceLine
              y={snapshot.put_wall}
              stroke="#ef4444"
              strokeDasharray="4 4"
              strokeOpacity={0.7}
              label={{ value: `Put Wall ${snapshot.put_wall}`, fill: "#ef4444", fontSize: 9, position: "insideBottomRight" }}
            />
            <Area
              type="monotone"
              dataKey="spot"
              stroke="#ff2a5b"
              strokeWidth={2}
              fill="url(#spotFill)"
            />
          </AreaChart>
        </ResponsiveContainer>
        </div>
      </div>
    </Panel>
  );
}

function SpotPlaceholder({ status, message }: { status: string; message?: string }) {
  const isError = status === "error";
  return (
    <div className="h-full min-h-0 flex flex-col">
      <div className="flex items-baseline gap-3 mb-3 shrink-0">
        <span className="tabnum text-3xl font-medium text-ink-faint">—</span>
        <span className="tabnum text-sm text-ink-faint">…</span>
        <span className="text-xs text-ink-faint">
          {isError ? (message ?? "backend unreachable") : "loading"}
        </span>
      </div>
      <div className="flex-1 min-h-0 flex items-center justify-center">
        <div
          className={
            isError
              ? "text-[11px] uppercase tracking-[0.18em] text-accent-warn"
              : "h-full w-full rounded-md bg-bg-subtle/40 animate-pulse"
          }
        >
          {isError ? "no live state" : null}
        </div>
      </div>
    </div>
  );
}
