"use client";

import { ArrowRight, Activity, Zap } from "lucide-react";
import Link from "next/link";
import { CharmSpiral } from "./CharmSpiral";

export function Hero() {
  return (
    <section className="relative isolate flex min-h-screen flex-col justify-center overflow-hidden pt-24">
      <div className="absolute inset-0 -z-10 bg-grid opacity-60" />
      <div className="absolute inset-0 -z-10 radial-brand" />
      <div className="absolute inset-x-0 top-0 -z-10 h-screen bg-noise opacity-[0.35] mix-blend-overlay" />

      <CharmSpiral />

      <div className="mx-auto w-full max-w-[1400px] px-6 lg:px-10">
        <div className="grid grid-cols-12 gap-6 items-center">
          <div className="col-span-12 lg:col-span-7">
            <div className="inline-flex items-center gap-2 rounded-full border border-line bg-bg-card/60 px-3 py-1.5 backdrop-blur">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-brand opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-brand" />
              </span>
              <span className="text-[11px] uppercase tracking-[0.18em] text-ink-base">
                Live · OPRA Pillar + CME MDP 3.0
              </span>
            </div>

            <h1 className="mt-7 font-display text-display-xl text-ink-high">
              Read the
              <br />
              <span className="text-gradient-brand">Dealer.</span>
              <span className="ml-2 italic font-light text-ink-muted text-[0.55em] tracking-tight align-middle">
                in real time.
              </span>
            </h1>

            <p className="mt-7 max-w-xl text-lg text-ink-muted leading-relaxed">
              Predictive 0DTE flow + dealer positioning intelligence for SPX & NDX.
              We don&apos;t guess the price. We tell you what dealers
              <span className="text-ink-high"> must do next </span>
              — in dollar notional, before it hits the tape.
            </p>

            <div className="mt-9 flex flex-wrap items-center gap-3">
              <Link
                href="/dashboard"
                className="group inline-flex items-center gap-2.5 rounded-full bg-brand px-5 py-3 text-sm font-medium text-white shadow-[0_8px_32px_-12px_#ff2a5b] hover:bg-brand-hi"
              >
                Open Live Dashboard
                <ArrowRight className="h-4 w-4 transition-transform group-hover:translate-x-0.5" />
              </Link>
              <button className="inline-flex items-center gap-2 rounded-full border border-line bg-bg-card/60 px-5 py-3 text-sm text-ink-base hover:bg-bg-hover backdrop-blur">
                <Activity className="h-3.5 w-3.5 text-brand-hi" />
                Read the spec · OpenAPI
              </button>
            </div>

            <div className="mt-12 grid max-w-2xl grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-4 border-t border-line pt-7">
              <Metric label="Latency p99" value="< 100ms" hint="wire → WS" />
              <Metric label="Strikes" value="0DTE only" hint="no 3,500-ticker bloat" />
              <Metric label="Tick archive" value="1 year" hint="OPRA + MDP3" />
              <Metric label="Engine" value="Go · NATS" hint="hot-path zero-alloc" />
            </div>
          </div>

          <div className="col-span-12 lg:col-span-5">
            <FloatingPanel />
          </div>
        </div>
      </div>

      <div className="absolute bottom-6 inset-x-0 mx-auto flex max-w-fit items-center gap-2 text-[10px] uppercase tracking-[0.24em] text-ink-faint">
        <span className="h-px w-8 bg-line-strong" />
        <span>scroll · 0DTE forced flow ↓</span>
        <span className="h-px w-8 bg-line-strong" />
      </div>
    </section>
  );
}

function Metric({ label, value, hint }: { label: string; value: string; hint: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">{label}</div>
      <div className="mt-1.5 tabnum text-[20px] font-medium text-ink-high">{value}</div>
      <div className="mt-0.5 text-[11px] text-ink-faint">{hint}</div>
    </div>
  );
}

function FloatingPanel() {
  return (
    <div className="relative">
      <div className="absolute -inset-x-8 -inset-y-12 -z-10 rounded-[40px] bg-gradient-to-br from-brand/20 via-transparent to-transparent blur-3xl" />
      <div className="relative rounded-2xl border border-line bg-bg-card/80 p-5 backdrop-blur-xl shadow-[0_30px_120px_-40px_rgba(255,42,91,0.4)]">
        <div className="flex items-center justify-between border-b border-line pb-3">
          <div className="flex items-center gap-2">
            <Zap className="h-3.5 w-3.5 text-brand-hi" />
            <span className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">
              SPX · 0DTE · 15:30 ET
            </span>
          </div>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-brand/40 bg-brand-dim px-2 py-0.5 text-[9px] uppercase tracking-[0.16em] text-brand-hi">
            ● Live
          </span>
        </div>

        <div className="mt-4 space-y-3.5 text-sm">
          <Row label="DPI composite" value="78.4" tone="brand" mini="FORCED" />
          <Row label="Charm zone" value="PEAK" tone="brand" mini="42m to close" />
          <Row label="Net GEX" value="−$2.14B" tone="down" mini="SHORT γ" />
          <Row label="Zero γ" value="5862.5" tone="default" mini="+0.25% above" />
          <Row label="Pin · 5850" value="47%" tone="pin" mini="γ-strength 0.92" />
        </div>

        <div className="hr-line my-4" />

        <div>
          <div className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">
            Forced flow · next 60m
          </div>
          <div className="mt-2 tabnum text-3xl font-medium text-accent-short">
            −$2.91B
          </div>
          <div className="mt-1 text-[11px] text-ink-faint">
            net of charm aid +$510M · if spot +1.0%
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  tone,
  mini,
}: {
  label: string;
  value: string;
  tone: "default" | "down" | "brand" | "pin";
  mini?: string;
}) {
  const toneCls = {
    default: "text-ink-high",
    down: "text-accent-short",
    brand: "text-brand-hi",
    pin: "text-accent-warn",
  };
  return (
    <div className="flex items-center justify-between">
      <span className="text-[11px] uppercase tracking-[0.14em] text-ink-muted">{label}</span>
      <div className="flex items-center gap-2">
        <span className={`tabnum text-[15px] font-medium ${toneCls[tone]}`}>{value}</span>
        {mini && (
          <span className="font-mono text-[10px] text-ink-faint uppercase tracking-wider">
            {mini}
          </span>
        )}
      </div>
    </div>
  );
}
