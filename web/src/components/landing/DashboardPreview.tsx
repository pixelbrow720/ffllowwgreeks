"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

export function DashboardPreview() {
  return (
    <section className="relative pt-32 pb-20 overflow-hidden">
      <div className="absolute inset-0 -z-10 bg-grid opacity-40" />
      <div className="absolute inset-x-0 top-0 -z-10 h-[300px] bg-gradient-to-b from-brand/5 to-transparent" />
      <div className="absolute inset-x-0 bottom-0 -z-10 h-[120px] bg-gradient-to-t from-bg-base to-transparent" />

      <div className="mx-auto w-full max-w-[1400px] px-6 lg:px-10">
        <div className="grid grid-cols-12 gap-8 items-end mb-12">
          <div className="col-span-12 lg:col-span-8">
            <div className="text-[11px] uppercase tracking-[0.2em] text-brand-hi">
              Live console
            </div>
            <h2 className="mt-3 font-display text-display-lg text-ink-high">
              Terminal-grade. <br />
              <span className="text-ink-muted">Built for one screen.</span>
            </h2>
          </div>
          <div className="col-span-12 lg:col-span-4 lg:text-right">
            <Link
              href="/dashboard"
              className="inline-flex items-center gap-2 rounded-full bg-brand px-5 py-3 text-sm font-medium text-white shadow-[0_8px_32px_-12px_#ff2a5b] hover:bg-brand-hi"
            >
              Open the live mockup
              <ArrowUpRight className="h-4 w-4" />
            </Link>
          </div>
        </div>

        <div className="relative">
          <div className="absolute -inset-1 -z-10 rounded-3xl bg-gradient-to-br from-brand/20 via-transparent to-transparent blur-3xl" />
          <div className="rounded-2xl border border-line bg-bg-card overflow-hidden shadow-[0_60px_180px_-60px_rgba(255,42,91,0.45)]">
            {/* Mock window chrome */}
            <div className="flex items-center justify-between border-b border-line bg-bg-subtle/40 px-4 py-2.5">
              <div className="flex items-center gap-1.5">
                <span className="h-2.5 w-2.5 rounded-full bg-accent-short/70" />
                <span className="h-2.5 w-2.5 rounded-full bg-accent-warn/70" />
                <span className="h-2.5 w-2.5 rounded-full bg-accent-long/70" />
              </div>
              <div className="font-mono text-[11px] text-ink-faint">
                flowgreeks.app/dashboard — SPX · 0DTE · live
              </div>
              <span className="text-[10px] text-ink-faint">42ms ws lag</span>
            </div>

            <div className="relative grid grid-cols-12 gap-px bg-line/40 p-px">
              <FakePanel
                cls="col-span-7 h-[200px]"
                title="SPX Spot · 1m · with walls"
                sparklinePath="M 0 70 Q 50 60 100 65 T 200 45 T 300 35 T 400 38 T 500 28"
                badges={[{ label: "▲ +0.28%", tone: "up" }, { label: "SHORT γ", tone: "brand" }]}
                big="5,847.62"
              />
              <FakePanel
                cls="col-span-5 h-[200px]"
                title="DPI — Composite"
                ring={78.4}
                badges={[{ label: "Live", tone: "brand" }, { label: "Charm: Peak", tone: "brand" }]}
              />
              <FakePanel
                cls="col-span-7 h-[220px]"
                title="Charm Clock · 24h dial"
                clock
                badges={[{ label: "42m to close", tone: "brand" }]}
              />
              <FakePanel
                cls="col-span-5 h-[220px]"
                title="Key Levels"
                levels
                badges={[{ label: "Expected MV ±28", tone: "brand" }]}
              />
              <FakePanel
                cls="col-span-12 h-[140px]"
                title="Options Flow Tape · live aggressor"
                tape
                badges={[{ label: "10 last", tone: "brand" }]}
              />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

type Badge = { label: string; tone: "up" | "down" | "brand" };

function FakePanel({
  cls,
  title,
  big,
  sparklinePath,
  ring,
  clock,
  levels,
  tape,
  badges,
}: {
  cls: string;
  title: string;
  big?: string;
  sparklinePath?: string;
  ring?: number;
  clock?: boolean;
  levels?: boolean;
  tape?: boolean;
  badges?: Badge[];
}) {
  const toneCls: Record<Badge["tone"], string> = {
    up: "border-accent-long/30 text-accent-long bg-accent-long/10",
    down: "border-accent-short/30 text-accent-short bg-accent-short/10",
    brand: "border-brand/40 text-brand-hi bg-brand-dim",
  };
  return (
    <div className={`${cls} flex flex-col bg-bg-card`}>
      <div className="flex items-center justify-between border-b border-line/40 px-4 py-2">
        <span className="text-[10px] uppercase tracking-[0.16em] text-ink-faint">{title}</span>
        <div className="flex items-center gap-1">
          {badges?.map((b, i) => (
            <span
              key={i}
              className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[9px] uppercase tracking-wider ${toneCls[b.tone]}`}
            >
              {b.label}
            </span>
          ))}
        </div>
      </div>
      <div className="flex-1 p-4 relative overflow-hidden">
        {big && (
          <>
            <div className="tabnum text-3xl font-medium text-ink-high">{big}</div>
            <svg className="absolute inset-x-0 bottom-0 w-full h-24" viewBox="0 0 500 100" preserveAspectRatio="none">
              <defs>
                <linearGradient id="spkFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ff2a5b" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#ff2a5b" stopOpacity="0" />
                </linearGradient>
              </defs>
              <path d={`${sparklinePath} L 500 100 L 0 100 Z`} fill="url(#spkFill)" />
              <path d={sparklinePath} stroke="#ff2a5b" strokeWidth="1.5" fill="none" />
              <line x1="0" y1="40" x2="500" y2="40" stroke="#22c55e" strokeOpacity="0.5" strokeDasharray="3 4" />
              <line x1="0" y1="75" x2="500" y2="75" stroke="#ef4444" strokeOpacity="0.5" strokeDasharray="3 4" />
            </svg>
          </>
        )}
        {ring !== undefined && (
          <div className="flex items-center gap-4">
            <svg width="120" height="120" viewBox="0 0 120 120" className="dpi-glow">
              <circle cx="60" cy="60" r="48" fill="none" stroke="#1c1c20" strokeWidth="9" />
              <circle
                cx="60"
                cy="60"
                r="48"
                fill="none"
                stroke="#ff2a5b"
                strokeWidth="9"
                strokeLinecap="round"
                strokeDasharray={2 * Math.PI * 48}
                strokeDashoffset={2 * Math.PI * 48 * (1 - ring / 100)}
                transform="rotate(-90 60 60)"
              />
              <text x="60" y="62" textAnchor="middle" dominantBaseline="middle" fontSize="22" fill="#f4f4f5" fontWeight="600">
                {ring.toFixed(0)}
              </text>
              <text x="60" y="80" textAnchor="middle" fontSize="8" fill="#ff8aa5" letterSpacing="2">FORCED</text>
            </svg>
            <div className="flex-1 space-y-1.5 text-[10px]">
              {["Net γ sign", "Charm velocity", "Vanna sens.", "TTC decay", "Flow conc."].map((l, i) => (
                <div key={l} className="flex items-center gap-2">
                  <span className="w-20 text-ink-faint">{l}</span>
                  <div className="relative flex-1 h-1 rounded-full bg-bg-subtle overflow-hidden">
                    <div className="absolute inset-y-0 left-0 bg-brand" style={{ width: `${60 + i * 8}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
        {clock && (
          <div className="flex items-center justify-center h-full">
            <svg width="160" height="160" viewBox="0 0 160 160">
              {Array.from({ length: 24 }).map((_, h) => {
                const a1 = (h / 24) * 360;
                const a2 = ((h + 1) / 24) * 360;
                const r1 = 48, r2 = 70;
                const toXY = (deg: number, r: number) => {
                  const rad = ((deg - 90) * Math.PI) / 180;
                  return [80 + Math.cos(rad) * r, 80 + Math.sin(rad) * r];
                };
                const [x1, y1] = toXY(a1, r2);
                const [x2, y2] = toXY(a2, r2);
                const [x3, y3] = toXY(a2, r1);
                const [x4, y4] = toXY(a1, r1);
                const isTrade = h >= 9 && h <= 16;
                const t = isTrade ? (h - 9) / 7 : 0;
                const inten = isTrade ? 0.2 + Math.sin(t * Math.PI) * 0.7 : 0.06;
                return (
                  <path
                    key={h}
                    d={`M ${x1} ${y1} A ${r2} ${r2} 0 0 1 ${x2} ${y2} L ${x3} ${y3} A ${r1} ${r1} 0 0 0 ${x4} ${y4} Z`}
                    fill={`rgba(255,42,91,${inten})`}
                    stroke="#0f0f12"
                  />
                );
              })}
              <circle cx="80" cy="80" r="46" fill="none" stroke="#26262a" strokeWidth="1" />
              <text x="80" y="75" textAnchor="middle" fontSize="8" fill="#71717a" letterSpacing="2">CHARM</text>
              <text x="80" y="92" textAnchor="middle" fontSize="18" fill="#f4f4f5" fontWeight="600">15:30</text>
            </svg>
          </div>
        )}
        {levels && (
          <div className="space-y-1 text-[11px]">
            {[
              ["Call Wall", "5900", "+0.90%", "up"],
              ["Zero γ", "5862.5", "+0.25%", "pin"],
              ["Pin", "5850", "+0.04%", "brand"],
              ["Spot", "5847.62", "—", "now"],
              ["Put Wall", "5800", "−0.81%", "down"],
            ].map(([l, p, d, t]) => (
              <div key={l as string} className={`flex items-center gap-2 rounded px-2 py-1 ${t === "now" ? "bg-brand-dim" : ""}`}>
                <span className="text-[9px] uppercase tracking-wider text-ink-faint w-16">{l}</span>
                <span className="tabnum text-ink-high flex-1">{p}</span>
                <span className={`tabnum text-[10px] ${t === "up" ? "text-accent-long" : t === "down" ? "text-accent-short" : "text-ink-faint"}`}>{d}</span>
              </div>
            ))}
          </div>
        )}
        {tape && (
          <div className="font-mono text-[10px] space-y-1">
            {[
              ["15:31:42", "P", "5825", "1840", "$0.77M", "BUY", "SWEEP"],
              ["15:31:38", "C", "5850", "2210", "$1.72M", "SELL", "BLOCK"],
              ["15:31:35", "P", "5850", "920", "$0.57M", "BUY", "OPEN"],
              ["15:31:31", "C", "5875", "540", "$0.21M", "BUY", "REPEAT"],
              ["15:31:28", "P", "5800", "3120", "$0.76M", "BUY", "SWEEP"],
            ].map((r, i) => (
              <div key={i} className="grid grid-cols-[64px_36px_56px_56px_64px_44px_60px] gap-x-3 text-[11px]">
                <span className="text-ink-faint tabnum">{r[0]}</span>
                <span className={r[1] === "C" ? "text-accent-long" : "text-accent-short"}>{r[1]}</span>
                <span className="tabnum text-ink-high">{r[2]}</span>
                <span className="tabnum text-ink-base">{r[3]}</span>
                <span className="tabnum text-ink-high">{r[4]}</span>
                <span className={r[5] === "BUY" ? "text-accent-long" : "text-accent-short"}>{r[5]}</span>
                <span className="text-brand-hi">{r[6]}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
