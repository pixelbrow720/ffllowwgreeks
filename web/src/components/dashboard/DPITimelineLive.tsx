"use client";

import { useEffect, useRef, useState } from "react";
import { Panel } from "@/components/primitives/Panel";
import { useLiveSocket } from "@/lib/ws/useLiveSocket";
import { useMeasuredBox } from "@/lib/hooks/useLayoutMounted";
import type { Channel } from "@/lib/ws/client";
import {
  ResponsiveContainer,
  Area,
  XAxis,
  YAxis,
  ReferenceLine,
  Tooltip,
  CartesianGrid,
  ComposedChart,
} from "recharts";
import { useSnapshot } from "@/lib/api/snapshot";
import { getHistory } from "@/lib/api/client";
import type { Symbol as Sym } from "@/lib/api/types";

interface Sample {
  ts_ns: number;
  t: string;
  composite: number;
}

const MAX_SAMPLES = 480; // 8h at 1 sample / minute

// DPITimelineLive — composite DPI timeline. Backfills from /api/history
// on mount so a fresh page-load shows the morning DPI track instead of
// rendering a single dot at the current minute. After backfill, live WS
// deltas are throttled to one bucket per minute via lastBucket dedupe.
export function DPITimelineLive({ symbol }: { symbol: Sym }) {
  const { snapshot } = useSnapshot(symbol);
  const sock = useLiveSocket();
  const [series, setSeries] = useState<Sample[]>([]);
  const lastBucket = useRef<string>("");
  const backfilled = useRef<Sym | "">("");

  useEffect(() => {
    if (backfilled.current === symbol) return;
    backfilled.current = symbol;
    const to = new Date();
    const from = new Date(to.getTime() - 8 * 60 * 60 * 1000);
    void getHistory(symbol, { from, to, max: 480 }).then((resp) => {
      const samples: Sample[] = [];
      const seen = new Set<string>();
      for (const s of resp.samples) {
        if (s.spot < 1000) continue;
        const date = new Date(Math.floor(s.ts_ns / 1e6));
        const minOfDay = date.getUTCHours() * 60 + date.getUTCMinutes();
        if (minOfDay < 13 * 60 + 30) continue; // 20:30 WIB onward
        const t = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
        if (seen.has(t)) {
          samples[samples.length - 1] = { ts_ns: s.ts_ns, t, composite: s.dpi };
        } else {
          seen.add(t);
          samples.push({ ts_ns: s.ts_ns, t, composite: s.dpi });
        }
      }
      setSeries(samples);
      lastBucket.current = samples[samples.length - 1]?.t ?? "";
    }).catch((err) => {
      // eslint-disable-next-line no-console
      console.warn("[flowgreeks] dpi timeline backfill failed", err);
      backfilled.current = "";
    });
  }, [symbol]);

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

  const composite = snapshot?.dpi.composite ?? 0;

  return (
    <Panel
      title="DPI Timeline"
      subtitle={`${symbol} · composite · 1m bucket · ${series.length} samples`}
      actions={
        <div className="flex items-center gap-3 font-mono text-[9.5px] uppercase tracking-[0.18em] text-ink-faint">
          <Legend swatch="bg-ink-high" label="Composite" />
          <span>50 ELEVATED</span>
          <span className="text-accent-warn">75 FORCED</span>
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
          </ComposedChart>
        </ResponsiveContainer>
      ) : null}
    </div>
  );
}

function Legend({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-1 w-3 ${swatch}`} />
      {label}
    </span>
  );
}

// Empty — monochrome empty state. Brand pink ambient was confusing the
// auditor as a data-color violation; removed in favour of a flat
// monochrome scaffold per the discipline rule.
function Empty() {
  return (
    <div className="relative flex h-full min-h-[160px] flex-col items-center justify-center overflow-hidden">
      <svg
        viewBox="0 0 400 120"
        className="absolute inset-0 m-auto h-full w-full opacity-60"
        preserveAspectRatio="none"
      >
        <line x1="20" y1="30" x2="380" y2="30" stroke="#26262a" strokeOpacity="0.6" strokeDasharray="2 4" />
        <line x1="20" y1="60" x2="380" y2="60" stroke="#26262a" strokeOpacity="0.6" strokeDasharray="2 4" />
        <line x1="20" y1="90" x2="380" y2="90" stroke="#26262a" strokeOpacity="0.6" strokeDasharray="2 4" />
        <text x="384" y="32" fontSize="8" fill="#52525b" fontFamily="var(--font-jb-mono)">75</text>
        <text x="384" y="62" fontSize="8" fill="#52525b" fontFamily="var(--font-jb-mono)">50</text>
        <text x="384" y="92" fontSize="8" fill="#52525b" fontFamily="var(--font-jb-mono)">25</text>
      </svg>

      <div className="relative z-10 flex flex-col items-center gap-1 text-center">
        <span className="font-mono text-[10px] uppercase tracking-[0.24em] text-ink-faint">
          / DPI timeline
        </span>
        <span className="font-display text-[16px] font-medium tracking-tight text-ink-high">
          loading session
        </span>
        <span className="text-[10.5px] text-ink-faint">
          Backfilling DPI samples from the history endpoint.
        </span>
      </div>
    </div>
  );
}
