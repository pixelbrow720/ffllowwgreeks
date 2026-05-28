"use client";

import { useState, useMemo } from "react";
import { Panel, Pill } from "@/components/primitives/Panel";
import { SNAPSHOT } from "@/lib/mock";

type Resolution = "1m" | "5m";

interface Sample {
  t: number;        // minutes since 09:30
  spot: number;
  charm: number;    // 0..1 normalized
}

function buildSession(resMin: number): Sample[] {
  const totalMin = 6.5 * 60; // 09:30 → 16:00 = 390min
  const out: Sample[] = [];
  for (let m = 0; m <= totalMin; m += resMin) {
    const t = m / totalMin;
    // Charm intensity: peaks ~14:30-15:30 (75% to 92% of session)
    const peak = 0.85;
    const sigma = 0.18;
    const gauss = Math.exp(-Math.pow(t - peak, 2) / (2 * sigma * sigma));
    const charm = Math.max(0.04, gauss + (Math.random() - 0.5) * 0.06);
    // Spot meanders 5810-5855
    const trend = 5825 + Math.sin(t * Math.PI * 2.4) * 12 + (t - 0.5) * 18;
    const spot = trend + (Math.random() - 0.5) * 3;
    out.push({ t: m, spot, charm: Math.min(1, charm) });
  }
  return out;
}

function charmColor(c: number): string {
  // cool → warm gradient: indigo → magenta → brand-pink
  if (c < 0.25) return "#3b82f6";          // info blue (low charm)
  if (c < 0.45) return "#8b5cf6";          // violet
  if (c < 0.65) return "#d946ef";          // fuchsia
  if (c < 0.85) return "#ff2a5b";          // brand pink
  return "#ff8aa5";                         // brand-hi (peak)
}

export function CharmClock() {
  const [res, setRes] = useState<Resolution>("5m");
  const samples = useMemo(() => buildSession(res === "1m" ? 1 : 5), [res]);

  // SVG plot dimensions
  const W = 720;
  const H = 280;
  const PAD = { l: 44, r: 16, t: 18, b: 26 };
  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;

  const totalMin = 390;
  const yMin = Math.min(...samples.map((s) => s.spot)) - 4;
  const yMax = Math.max(...samples.map((s) => s.spot)) + 4;

  const xOf = (t: number) => PAD.l + (t / totalMin) * plotW;
  const yOf = (v: number) => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * plotH;

  const nowMin = 360; // 15:30
  const nowSample = samples[Math.floor(nowMin / (res === "1m" ? 1 : 5))];

  const xTicks = [0, 60, 120, 180, 240, 300, 360];
  const xTickLabel = (m: number) => {
    const total = 9 * 60 + 30 + m;
    const h = Math.floor(total / 60);
    const mm = total % 60;
    return `${String(h).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
  };

  const yTicks = [
    Math.ceil(yMin / 10) * 10,
    Math.round((yMin + yMax) / 2 / 10) * 10,
    Math.floor(yMax / 10) * 10,
  ];

  return (
    <Panel
      title="Charm Clock · Session"
      subtitle={`Spot path · dot color = charm intensity · ${samples.length} samples`}
      actions={
        <div className="flex items-center gap-2">
          <Pill tone="brand">PEAK · 42m to close</Pill>
          <div className="flex items-center gap-0.5 rounded-full border border-line/70 bg-bg-base/40 p-0.5">
            {(["1m", "5m"] as Resolution[]).map((r) => (
              <button
                key={r}
                onClick={() => setRes(r)}
                className={`rounded-full px-2.5 py-0.5 text-[10px] font-medium uppercase tracking-[0.12em] transition-colors ${
                  res === r
                    ? "bg-bg-card text-ink-high"
                    : "text-ink-faint hover:text-ink-base"
                }`}
              >
                {r}
              </button>
            ))}
          </div>
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
            <linearGradient id="charmBgFade" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#ff2a5b" stopOpacity="0.06" />
              <stop offset="100%" stopColor="#ff2a5b" stopOpacity="0" />
            </linearGradient>
            <filter id="dotGlow" x="-50%" y="-50%" width="200%" height="200%">
              <feGaussianBlur stdDeviation="2" />
            </filter>
          </defs>

          {/* plot bg */}
          <rect
            x={PAD.l}
            y={PAD.t}
            width={plotW}
            height={plotH}
            fill="url(#charmBgFade)"
          />

          {/* y gridlines */}
          {yTicks.map((v) => (
            <g key={`y-${v}`}>
              <line
                x1={PAD.l}
                y1={yOf(v)}
                x2={W - PAD.r}
                y2={yOf(v)}
                stroke="#26262a"
                strokeOpacity="0.5"
                strokeDasharray="2 4"
              />
              <text
                x={PAD.l - 8}
                y={yOf(v)}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize="9.5"
                fill="#71717a"
                fontFamily="var(--font-jb-mono)"
              >
                {v}
              </text>
            </g>
          ))}

          {/* x ticks */}
          {xTicks.map((m) => (
            <g key={`x-${m}`}>
              <line
                x1={xOf(m)}
                y1={H - PAD.b}
                x2={xOf(m)}
                y2={H - PAD.b + 4}
                stroke="#3a3a40"
              />
              <text
                x={xOf(m)}
                y={H - PAD.b + 14}
                textAnchor="middle"
                fontSize="9.5"
                fill="#71717a"
                fontFamily="var(--font-jb-mono)"
              >
                {xTickLabel(m)}
              </text>
            </g>
          ))}

          {/* trading session band (subtle) */}
          <line
            x1={xOf(nowMin)}
            y1={PAD.t}
            x2={xOf(nowMin)}
            y2={H - PAD.b}
            stroke="#ff2a5b"
            strokeOpacity="0.4"
            strokeDasharray="2 3"
          />

          {/* dots */}
          {samples.map((s, i) => {
            const isLast = i === samples.length - 1;
            const isPeak = s.charm > 0.85;
            const r = isLast ? 5 : 2.5 + s.charm * 1.5;
            const color = charmColor(s.charm);
            return (
              <g key={i}>
                {(isPeak || isLast) && (
                  <circle
                    cx={xOf(s.t)}
                    cy={yOf(s.spot)}
                    r={r * 2.4}
                    fill={color}
                    opacity={0.35}
                    filter="url(#dotGlow)"
                  />
                )}
                <circle
                  cx={xOf(s.t)}
                  cy={yOf(s.spot)}
                  r={r}
                  fill={color}
                  opacity={isLast ? 1 : 0.85}
                />
                {isLast && (
                  <circle
                    cx={xOf(s.t)}
                    cy={yOf(s.spot)}
                    r={r + 4}
                    fill="none"
                    stroke="#fff"
                    strokeWidth="1.5"
                    opacity="0.9"
                  />
                )}
              </g>
            );
          })}

          {/* 'NOW' annotation */}
          {nowSample && (
            <g>
              <text
                x={xOf(nowMin) + 8}
                y={PAD.t + 14}
                fontSize="9"
                fill="#ff8aa5"
                fontFamily="var(--font-jb-mono)"
                letterSpacing="2"
              >
                NOW · 15:30
              </text>
            </g>
          )}
        </svg>

        {/* legend bar */}
        <div className="px-4 pb-4">
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.16em] text-ink-faint">
              <span>Charm</span>
              <div
                className="h-2 w-32 rounded-full"
                style={{
                  background:
                    "linear-gradient(90deg, #3b82f6, #8b5cf6, #d946ef, #ff2a5b, #ff8aa5)",
                }}
              />
              <span className="tabnum text-ink-base">peak</span>
            </div>

            <div className="flex items-center gap-3 text-[10.5px] text-ink-muted">
              <span>
                Spot{" "}
                <span className="tabnum text-ink-high">
                  {nowSample?.spot.toFixed(2)}
                </span>
              </span>
              <span className="text-ink-ghost">·</span>
              <span>
                Charm{" "}
                <span className="tabnum text-brand-hi">
                  {(nowSample?.charm ?? 0).toFixed(2)}
                </span>
              </span>
              <span className="text-ink-ghost">·</span>
              <span>
                Velocity{" "}
                <span className="tabnum text-ink-high">
                  {SNAPSHOT.charm_velocity_raw.toFixed(4)}/min
                </span>
              </span>
            </div>
          </div>

          <div className="mt-3 rounded-xl border border-line/60 bg-bg-subtle/40 px-3.5 py-2.5 text-[11.5px] text-ink-muted leading-relaxed">
            Charm velocity peaks ≈ 14:30–15:30 ET. Dealers must
            <span className="text-brand-hi"> re-hedge </span>
            as 0DTE δ drops toward 0. Forced flow expected next 42m:
            <span className="tabnum text-ink-high"> −$1.84B</span>.
          </div>
        </div>
      </div>
    </Panel>
  );
}
