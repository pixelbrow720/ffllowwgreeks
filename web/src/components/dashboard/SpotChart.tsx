"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { SPOT_HISTORY, SNAPSHOT } from "@/lib/mock";
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

export function SpotChart() {
  const last = SPOT_HISTORY[SPOT_HISTORY.length - 1];
  const first = SPOT_HISTORY[0];
  const delta = last.spot - first.spot;
  const pct = (delta / first.spot) * 100;
  const trendUp = delta >= 0;

  return (
    <Panel
      title={`${SNAPSHOT.symbol} Spot · ${SNAPSHOT.fut_front_sym} basis ${SNAPSHOT.basis_smooth.toFixed(2)}`}
      subtitle="1-min · with dealer key levels"
      actions={
        <>
          <Pill tone={trendUp ? "up" : "down"}>
            {trendUp ? "▲" : "▼"} {pct.toFixed(2)}%
          </Pill>
          <Pill tone="brand">SHORT γ</Pill>
        </>
      }
    >
      <div className="h-full min-h-0 flex flex-col">
        <div className="flex items-baseline gap-3 mb-3 shrink-0">
          <span className="tabnum text-3xl font-medium text-ink-high">
            {fmtNum(last.spot, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </span>
          <span className={`tabnum text-sm ${trendUp ? "text-signal-up" : "text-signal-down"}`}>
            {trendUp ? "+" : ""}
            {delta.toFixed(2)}
          </span>
          <span className="text-xs text-ink-faint">vs prev close 5840.21</span>
        </div>

        <div className="flex-1 min-h-0">
          <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={SPOT_HISTORY} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
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
              interval={2}
            />
            <YAxis
              domain={[5790, 5910]}
              stroke="#52525b"
              fontSize={10}
              tickLine={false}
              axisLine={false}
              orientation="right"
              tickFormatter={(v) => v.toFixed(0)}
              width={48}
              ticks={[5800, 5825, 5850, 5862, 5875, 5900]}
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
              y={SNAPSHOT.call_wall}
              stroke="#22c55e"
              strokeDasharray="4 4"
              strokeOpacity={0.7}
              label={{ value: `Call Wall ${SNAPSHOT.call_wall}`, fill: "#22c55e", fontSize: 9, position: "insideTopRight" }}
            />
            <ReferenceLine
              y={SNAPSHOT.zero_gamma}
              stroke="#a855f7"
              strokeDasharray="2 4"
              strokeOpacity={0.7}
              label={{ value: `Zero γ ${SNAPSHOT.zero_gamma}`, fill: "#a855f7", fontSize: 9, position: "insideTopRight" }}
            />
            <ReferenceLine
              y={SNAPSHOT.put_wall}
              stroke="#ef4444"
              strokeDasharray="4 4"
              strokeOpacity={0.7}
              label={{ value: `Put Wall ${SNAPSHOT.put_wall}`, fill: "#ef4444", fontSize: 9, position: "insideBottomRight" }}
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
