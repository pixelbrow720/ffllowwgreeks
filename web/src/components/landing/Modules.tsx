"use client";

import { ArrowUpRight, Compass, Gauge, History, LineChart, Workflow, Bell } from "lucide-react";

const MODULES = [
  {
    n: "01",
    tag: "DPI",
    title: "Dealer\nPositioning Index",
    body:
      "5-signal composite (net gamma, charm velocity, vanna, time-to-close, flow concentration) that quantifies how forced dealer hedging is — 0 stable, 100 forced.",
    icon: Gauge,
  },
  {
    n: "02",
    tag: "Charm Clock",
    title: "Intraday\nDecay Window",
    body:
      "Radial view of charm decay over the trading session. See when gamma-flip dealers will be forced to re-hedge — and which direction it pushes spot.",
    icon: Compass,
  },
  {
    n: "03",
    tag: "Forced Flow",
    title: "What dealers\nMUST do next",
    body:
      "Predictive simulator translating dealer state into dollar-notional hedge requirements per scenario. The number that hits the tape ten minutes later.",
    icon: LineChart,
  },
  {
    n: "04",
    tag: "What-If",
    title: "Dealer\nSimulator",
    body:
      "Move spot ±N%, shift vol ±N pts, advance time N minutes. Returns forced delta, charm aid, net pressure, and top contributing strikes — sub-50ms.",
    icon: Workflow,
  },
  {
    n: "05",
    tag: "Replay",
    title: "Time machine\nfor any session",
    body:
      "Stream any historical session at 1×, 4×, 16×. Identical wire shape to live WS. Pause, scrub, branch — and overlay alerts as they would have fired.",
    icon: History,
  },
  {
    n: "06",
    tag: "Signal Studio",
    title: "Rules → alerts\n→ backtest",
    body:
      "Compose alert rules from the same primitives (DPI thresholds, charm zones, pin probability, regime). Backtest them against 1 year of state archive in 30s.",
    icon: Bell,
  },
];

export function Modules() {
  return (
    <section id="modules" className="relative border-b border-line py-32">
      <div className="mx-auto w-full max-w-[1400px] px-6 lg:px-10">
        <div className="mb-16 flex flex-col lg:flex-row gap-10 lg:items-end justify-between">
          <div className="max-w-2xl">
            <div className="text-[11px] uppercase tracking-[0.2em] text-brand-hi">
              Core modules
            </div>
            <h2 className="mt-3 font-display text-display-lg text-ink-high">
              Six engines.
              <br />
              <span className="text-ink-muted">One dealer model.</span>
            </h2>
          </div>
          <p className="max-w-md text-ink-muted leading-relaxed">
            Each module is its own Go service speaking NATS JetStream. They share a single
            in-memory dealer state — so a What-If query, a Replay scrub, and a live alert
            all read the same source of truth.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {MODULES.map((m, i) => {
            const Icon = m.icon;
            return (
              <div
                key={m.n}
                className="group relative flex flex-col rounded-xl border border-line bg-bg-card p-7 glow-hover min-h-[320px] overflow-hidden"
                style={{ animationDelay: `${i * 50}ms` }}
              >
                <div className="flex items-start justify-between">
                  <span className="font-mono text-[11px] tracking-[0.18em] text-ink-faint">
                    {m.n}
                  </span>
                  <Icon className="h-4 w-4 text-ink-faint group-hover:text-brand-hi transition-colors" />
                </div>

                <div className="mt-1 inline-flex w-fit items-center rounded-full border border-line bg-bg-subtle/40 px-2 py-0.5 text-[10px] uppercase tracking-[0.18em] text-ink-muted">
                  {m.tag}
                </div>

                <h3 className="mt-6 font-display text-[28px] leading-[1.05] tracking-tight text-ink-high whitespace-pre-line">
                  {m.title}
                </h3>

                <p className="mt-auto pt-8 text-[13px] leading-relaxed text-ink-muted">{m.body}</p>

                <a
                  href="#"
                  className="mt-5 inline-flex w-fit items-center gap-1.5 text-[12px] uppercase tracking-[0.16em] text-brand-hi opacity-0 -translate-y-1 transition-all duration-300 group-hover:opacity-100 group-hover:translate-y-0"
                >
                  Explore <ArrowUpRight className="h-3 w-3" />
                </a>

                {/* corner accent */}
                <div className="pointer-events-none absolute -right-12 -top-12 h-32 w-32 rounded-full bg-brand/0 blur-3xl transition-all duration-500 group-hover:bg-brand/25" />
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
