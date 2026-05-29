"use client";

import { useEffect, useRef, useState } from "react";
import { Panel } from "@/components/primitives/Panel";
import { useLiveSocket } from "@/lib/ws/useLiveSocket";
import { useMeasuredBox } from "@/lib/hooks/useLayoutMounted";
import type { Channel } from "@/lib/ws/client";
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
import { useSnapshot } from "@/lib/api/snapshot";

interface Sample {
  ts_ns: number;
  t: string;
  composite: number;
  charm: number;
  vanna: number;
  gamma: number;
}

const MAX_SAMPLES = 240; // ~4h at 1Hz, 1 sample/min after dedupe

// DPITimelineLive — composite + 3 components, sampled once per minute
// from the live WS stream. Monochrome scaffold; only the composite line
// uses ink-high. Components are ink-faint dashed lines.
export function DPITimelineLive({ symbol }: { symbol: "SPX" | "NDX" }) {
  const { snapshot } = useSnapshot(symbol);
  const sock = useLiveSocket();
  const [series, setSeries] = useState<Sample[]>([]);
  const lastBucket = useRef<string>("");

  useEffect(() => {
    if (!sock) return;
    const channel = `${symbol.toLowerCase()}:gex` as Channel;
    return sock.subscribe(channel, (ev) => {
      if (!ev.snapshot) return;
      const s = ev.snapshot;
      const date = new Date(Math.floor(s.ts_ns / 1e6));
      const t = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
      const sample: Sample = {
        ts_ns: s.ts_ns,
        t,
        composite: s.dpi.composite,
        charm: s.dpi.charm_velocity * 100,
        vanna: s.dpi.vanna_sensitivity * 100,
        gamma: Math.abs(s.dpi.net_gamma_sign) * 100,
      };
      setSeries((prev) => {
        if (lastBucket.current === t && prev.length > 0) {
          const next = prev.slice(0, -1);
          next.push(sample);
          return next;
        }
        lastBucket.current = t;
        const next = [...prev, sample];
        return next.length > MAX_SAMPLES ? next.slice(-MAX_SAMPLES) : next;
      });
    });
  }, [sock, symbol]);

  useEffect(() => {
    setSeries([]);
    lastBucket.current = "";
  }, [symbol]);

  const composite = snapshot?.dpi.composite ?? 0;

  return (
    <Panel
      title="DPI Timeline"
      subtitle={`${symbol} · composite + components · 1m bucket`}
      actions={
        <div className="flex items-center gap-3 font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-faint">
          <Legend swatch="bg-ink-high" label="Composite" />
          <Legend swatch="bg-ink-muted" label="Charm" />
          <Legend swatch="bg-ink-muted" label="Vanna" label2="dashed" />
          <Legend swatch="bg-ink-muted" label="\u03B3 sign" label2="dotted" />
          <span className="tabnum text-ink-base">
            now {composite.toFixed(1)}
          </span>
        </div>
      }
      contentClassName="p-2 flex flex-col"
    >
      {series.length === 0 ? (
        <Empty />
      ) : (
        <ChartBody series={series} />
      )}
    </Panel>
  );
}

function ChartBody({ series }: { series: Sample[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const box = useMeasuredBox(ref);
  return (
    <div ref={ref} className="flex-1 min-h-0 -ml-1">
      {box.ready ? (
        <ResponsiveContainer width={box.width} height={box.height}>
          <ComposedChart data={series} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id="compositeFillMono" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#f4f4f5" stopOpacity="0.18" />
                <stop offset="100%" stopColor="#f4f4f5" stopOpacity="0" />
              </linearGradient>
            </defs>

            <CartesianGrid
              stroke="#26262a"
              strokeOpacity={0.45}
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
              interval="preserveStartEnd"
              minTickGap={36}
              dy={4}
            />
            <YAxis
              stroke="#52525b"
              fontSize={10}
              tickLine={false}
              axisLine={false}
              domain={[0, 100]}
              ticks={[0, 25, 50, 75, 100]}
              tick={{ fill: "#71717a" }}
              width={28}
            />

            <Tooltip
              contentStyle={{
                background: "#0f0f12",
                border: "1px solid #26262a",
                borderRadius: 0,
                fontSize: 11,
                padding: "6px 8px",
              }}
              labelStyle={{ color: "#a1a1aa", marginBottom: 2, fontWeight: 500 }}
              cursor={{ stroke: "#3a3a40", strokeDasharray: "2 4" }}
            />

            <ReferenceLine
              y={75}
              stroke="#f59e0b"
              strokeDasharray="3 4"
              strokeOpacity={0.6}
              label={{
                value: "FORCED",
                fill: "#f59e0b",
                fontSize: 9,
                position: "right",
                letterSpacing: 2,
              }}
            />
            <ReferenceLine y={50} stroke="#3a3a40" strokeDasharray="2 4" strokeOpacity={0.5} />

            <Area
              type="monotone"
              dataKey="composite"
              stroke="#f4f4f5"
              strokeWidth={2}
              fill="url(#compositeFillMono)"
              dot={false}
              activeDot={{ r: 3, fill: "#f4f4f5", stroke: "#08080a", strokeWidth: 1 }}
              name="Composite"
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="charm"
              stroke="#a1a1aa"
              strokeWidth={1.25}
              dot={false}
              name="Charm \u00D7100"
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="vanna"
              stroke="#71717a"
              strokeWidth={1.25}
              strokeDasharray="3 3"
              dot={false}
              name="Vanna \u00D7100"
              isAnimationActive={false}
            />
            <Line
              type="monotone"
              dataKey="gamma"
              stroke="#52525b"
              strokeWidth={1.25}
              strokeDasharray="1 3"
              dot={false}
              name="\u03B3 sign \u00D7100"
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      ) : null}
    </div>
  );
}

function Legend({
  swatch,
  label,
  label2,
}: {
  swatch: string;
  label: string;
  label2?: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-1 w-3 ${swatch}`} />
      {label}
      {label2 && <span className="text-ink-ghost">·{label2}</span>}
    </span>
  );
}

// Empty — brand-themed empty state. Shows a horizontal trace with
// pulsing brand-pink dots, plus a 50/75 reference grid that previews the
// real timeline. Replaces the prior generic prose.
function Empty() {
  return (
    <div className="relative flex h-full min-h-[160px] flex-col items-center justify-center overflow-hidden">
      <svg
        viewBox="0 0 400 120"
        className="absolute inset-0 m-auto h-full w-full opacity-70"
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <linearGradient id="emptyTimelineFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#ff2a5b" stopOpacity="0.10" />
            <stop offset="100%" stopColor="#ff2a5b" stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* reference grid */}
        <line x1="20" y1="30" x2="380" y2="30" stroke="#26262a" strokeOpacity="0.6" strokeDasharray="2 4" />
        <line x1="20" y1="60" x2="380" y2="60" stroke="#26262a" strokeOpacity="0.6" strokeDasharray="2 4" />
        <line x1="20" y1="90" x2="380" y2="90" stroke="#26262a" strokeOpacity="0.6" strokeDasharray="2 4" />
        <text x="384" y="32" fontSize="8" fill="#52525b" fontFamily="var(--font-jb-mono)">75</text>
        <text x="384" y="62" fontSize="8" fill="#52525b" fontFamily="var(--font-jb-mono)">50</text>
        <text x="384" y="92" fontSize="8" fill="#52525b" fontFamily="var(--font-jb-mono)">25</text>
        {/* dotted preview path */}
        <path
          d="M20 70 Q120 50 200 60 T380 45"
          stroke="#ff2a5b"
          strokeOpacity="0.35"
          strokeWidth="1"
          strokeDasharray="3 6"
          fill="none"
        />
        <path
          d="M20 70 Q120 50 200 60 T380 45 L380 110 L20 110 Z"
          fill="url(#emptyTimelineFill)"
        />
        {/* pulsing dots along the trace */}
        {[60, 140, 220, 300].map((x, i) => (
          <circle
            key={x}
            cx={x}
            cy={68 - i * 4}
            r="2"
            fill="#ff2a5b"
            opacity="0.6"
            className="animate-pulse-slow"
            style={{ animationDelay: `${i * 0.4}s` }}
          />
        ))}
      </svg>

      <div className="relative z-10 flex flex-col items-center gap-1 text-center">
        <span className="font-mono text-[10px] uppercase tracking-[0.24em] text-brand-hi">
          / DPI timeline
        </span>
        <span className="font-display text-[16px] font-medium tracking-tight text-ink-high">
          accumulating buckets
        </span>
        <span className="text-[10.5px] text-ink-faint">
          One sample per minute. The trace appears once the first bucket settles.
        </span>
      </div>
    </div>
  );
}
