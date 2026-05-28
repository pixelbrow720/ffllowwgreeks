"use client";

import { Panel, Pill } from "@/components/primitives/Panel";
import { DPI_HISTORY } from "@/lib/mock";
import {
  ResponsiveContainer,
  Area,
  Line,
  XAxis,
  YAxis,
  ReferenceLine,
  Tooltip,
  CartesianGrid,
  ComposedChart,
} from "recharts";

const SERIES = [
  { key: "composite", label: "Composite", color: "#ff2a5b", width: 2.5, fill: true },
  { key: "charm", label: "Charm", color: "#a855f7", width: 1.5, dash: "3 3" },
  { key: "vanna", label: "Vanna", color: "#3b82f6", width: 1.5, dash: "3 3" },
  { key: "gamma", label: "Gamma", color: "#22c55e", width: 1.5, dash: "3 3" },
];

export function DPITimeline() {
  return (
    <Panel
      title="DPI Timeline · Session"
      subtitle="Composite + components, 30-min bucket"
      actions={
        <div className="flex items-center gap-2">
          {SERIES.map((s) => (
            <span
              key={s.key}
              className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.14em] text-ink-muted"
            >
              <span
                className="h-1.5 w-3 rounded-full"
                style={{ background: s.color }}
              />
              {s.label}
            </span>
          ))}
          <Pill tone="brand">Composite 78.4</Pill>
        </div>
      }
      contentClassName="p-4 pt-2 flex flex-col"
    >
      <div className="flex-1 min-h-0 -ml-2">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={DPI_HISTORY}
            margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
          >
            <defs>
              <linearGradient id="compositeFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#ff2a5b" stopOpacity="0.35" />
                <stop offset="60%" stopColor="#ff2a5b" stopOpacity="0.08" />
                <stop offset="100%" stopColor="#ff2a5b" stopOpacity="0" />
              </linearGradient>
            </defs>

            <CartesianGrid
              stroke="#26262a"
              strokeOpacity={0.4}
              strokeDasharray="2 4"
              vertical={false}
            />

            <XAxis
              dataKey="t"
              stroke="#52525b"
              fontSize={10}
              tickLine={false}
              axisLine={false}
              tick={{ fill: "#71717a" }}
              dy={6}
            />
            <YAxis
              stroke="#52525b"
              fontSize={10}
              tickLine={false}
              axisLine={false}
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tick={{ fill: "#71717a" }}
              width={32}
            />

            <Tooltip
              contentStyle={{
                background: "rgba(15,15,18,0.95)",
                border: "1px solid #26262a",
                borderRadius: 10,
                fontSize: 11,
                padding: "8px 10px",
                boxShadow: "0 12px 32px -12px rgba(0,0,0,0.6)",
              }}
              labelStyle={{
                color: "#a1a1aa",
                marginBottom: 4,
                fontWeight: 500,
              }}
              cursor={{ stroke: "#3a3a40", strokeDasharray: "2 4" }}
            />

            <ReferenceLine
              y={75}
              stroke="#ff2a5b"
              strokeDasharray="3 4"
              strokeOpacity={0.5}
              label={{
                value: "FORCED",
                fill: "#ff8aa5",
                fontSize: 9,
                position: "right",
              }}
            />
            <ReferenceLine
              y={50}
              stroke="#f59e0b"
              strokeDasharray="3 4"
              strokeOpacity={0.35}
            />

            {/* Composite as gradient area + line */}
            <Area
              type="monotone"
              dataKey="composite"
              stroke="#ff2a5b"
              strokeWidth={2.5}
              fill="url(#compositeFill)"
              dot={false}
              activeDot={{
                r: 4,
                fill: "#ff2a5b",
                stroke: "#fff",
                strokeWidth: 1.5,
              }}
              name="Composite"
            />

            {/* Component lines (dashed, behind area not possible — drawn over) */}
            <Line
              type="monotone"
              dataKey="charm"
              stroke="#a855f7"
              strokeWidth={1.5}
              dot={false}
              strokeDasharray="3 3"
              name="Charm"
            />
            <Line
              type="monotone"
              dataKey="vanna"
              stroke="#3b82f6"
              strokeWidth={1.5}
              dot={false}
              strokeDasharray="3 3"
              name="Vanna"
            />
            <Line
              type="monotone"
              dataKey="gamma"
              stroke="#22c55e"
              strokeWidth={1.5}
              dot={false}
              strokeDasharray="3 3"
              name="Gamma"
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </Panel>
  );
}
