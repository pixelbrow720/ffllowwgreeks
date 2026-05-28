"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpRight } from "lucide-react";

const STEPS = [
  {
    n: "01",
    tag: "Ingest",
    title: "OPRA Pillar + CME MDP 3.0",
    body: "Tick-by-tick options + futures over multicast. Parsed in Go with zero-alloc structs. Backpressure to NATS.",
    latency: "5 ms",
  },
  {
    n: "02",
    tag: "Greeks",
    title: "IV solve + Black-Scholes",
    body: "Bisection IV solver, analytic delta/gamma/charm/vanna. Hot loop pre-allocated, sync.Pool for buffers.",
    latency: "2 ms",
  },
  {
    n: "03",
    tag: "Dealer Model",
    title: "Position attribution",
    body: "Net dealer position per strike from public flow heuristics + observed aggressor side. Updated every tick.",
    latency: "8 ms",
  },
  {
    n: "04",
    tag: "DPI · Charm",
    title: "5-signal composite + decay arc",
    body: "Net γ sign, charm velocity, vanna sens, TTC decay, flow conc → composite 0-100. Charm zone state machine.",
    latency: "12 ms",
  },
  {
    n: "05",
    tag: "Forced Flow",
    title: "What-if + scenario engine",
    body: "Simulate spot/vol moves against current state. Returns forced delta + charm aid + net pressure per scenario.",
    latency: "10 ms",
  },
  {
    n: "06",
    tag: "Fanout",
    title: "REST · WS · Webhooks",
    body: "/ws/live broadcasts state.SPX.gex every second. Alert rules evaluated server-side, pushed via webhook + WS.",
    latency: "10 ms",
  },
];

const TRAVEL_VW = 96; // total horizontal travel for the foreground track

export function Pipeline() {
  const sectionRef = useRef<HTMLElement>(null);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    let raf = 0;
    const update = () => {
      const el = sectionRef.current;
      if (!el) return;
      const rect = el.getBoundingClientRect();
      const total = rect.height - window.innerHeight;
      const passed = -rect.top;
      const p = total > 0 ? Math.max(0, Math.min(1, passed / total)) : 0;
      setProgress(p);
    };
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(update);
    };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
      cancelAnimationFrame(raf);
    };
  }, []);

  const trackX = -progress * TRAVEL_VW;
  const bgX = -progress * TRAVEL_VW * 0.55;
  const decorX = -progress * TRAVEL_VW * 1.25;

  return (
    <section
      ref={sectionRef}
      id="pipeline"
      className="relative border-y border-line"
      style={{ height: "280vh" }}
    >
      <div className="sticky top-0 flex h-screen flex-col overflow-hidden">
        {/* fixed header strip */}
        <div className="relative z-20 flex items-end justify-between gap-10 px-[5vw] pt-24 pb-8">
          <div className="max-w-2xl">
            <div className="text-[11px] uppercase tracking-[0.2em] text-brand-hi">
              Pipeline
            </div>
            <h2 className="mt-3 font-display text-display-lg text-ink-high">
              Wire to alert
              <br />
              <span className="text-ink-muted">in under 100ms.</span>
            </h2>
          </div>
          <div className="text-right shrink-0">
            <div className="text-[11px] uppercase tracking-[0.2em] text-ink-faint mb-2">
              p99 budget
            </div>
            <div className="tabnum font-display text-[64px] leading-none text-ink-high">
              47<span className="text-ink-faint">/100</span>
              <span className="text-[20px] text-ink-faint ml-1">ms</span>
            </div>
            <div className="text-[11px] text-ink-faint mt-2">
              measured · last 24h
            </div>
          </div>
        </div>

        {/* horizontal area */}
        <div className="relative flex-1">
          {/* parallax background layer — slower than foreground */}
          <div
            className="absolute inset-y-0 left-0 flex items-center pointer-events-none will-change-transform"
            style={{
              transform: `translate3d(${bgX}vw, 0, 0)`,
              width: `${102 + 100}vw`,
            }}
          >
            {STEPS.map((s, i) => (
              <div
                key={`bg-${s.n}`}
                className="absolute font-display text-[clamp(280px,42vw,640px)] font-bold text-ink-high/[0.018] leading-none select-none"
                style={{
                  left: `calc(4vw + ${i} * (30vw + 2vw))`,
                  top: "50%",
                  transform: "translateY(-50%)",
                }}
              >
                {s.n}
              </div>
            ))}
          </div>

          {/* parallax decor — faster than foreground (subtle dotted grid) */}
          <div
            className="absolute inset-y-0 left-0 pointer-events-none opacity-[0.07] will-change-transform"
            style={{
              transform: `translate3d(${decorX}vw, 0, 0)`,
              width: `${102 + 100}vw`,
              backgroundImage:
                "radial-gradient(circle, rgba(255,255,255,0.6) 1px, transparent 1px)",
              backgroundSize: "32px 32px",
              backgroundPosition: "0 0",
              maskImage:
                "linear-gradient(90deg, transparent 0%, black 12%, black 88%, transparent 100%)",
              WebkitMaskImage:
                "linear-gradient(90deg, transparent 0%, black 12%, black 88%, transparent 100%)",
            }}
          />

          {/* foreground track */}
          <div
            className="absolute inset-y-0 left-0 flex items-center will-change-transform"
            style={{
              transform: `translate3d(${trackX}vw, 0, 0)`,
              width: `${102 + 100}vw`,
              paddingLeft: "4vw",
              paddingRight: "8vw",
            }}
          >
            {STEPS.map((s, i) => (
              <PipelineCard
                key={s.n}
                step={s}
                index={i}
                active={progress >= i / (STEPS.length - 1) - 0.05}
              />
            ))}
          </div>
        </div>

        {/* progress rail (bottom) */}
        <div className="relative z-20 px-[5vw] pb-6">
          <div className="flex items-center gap-4">
            <span className="text-[10px] tabnum uppercase tracking-[0.2em] text-ink-faint">
              {String(Math.min(STEPS.length, Math.floor(progress * STEPS.length) + 1)).padStart(2, "0")}
              <span className="text-ink-ghost">/{STEPS.length}</span>
            </span>
            <div className="relative h-px flex-1 bg-line">
              <div
                className="absolute inset-y-0 left-0 bg-brand"
                style={{ width: `${progress * 100}%` }}
              />
            </div>
            <span className="hidden md:inline text-[10px] uppercase tracking-[0.2em] text-ink-faint">
              scroll · horizontal
            </span>
          </div>
        </div>
      </div>
    </section>
  );
}

function PipelineCard({
  step,
  index,
  active,
}: {
  step: (typeof STEPS)[number];
  index: number;
  active: boolean;
}) {
  return (
    <div
      className="group relative shrink-0"
      style={{
        width: "30vw",
        height: "62vh",
        marginRight: index < STEPS.length - 1 ? "2vw" : 0,
      }}
    >
      {/* left red bar — appears on hover */}
      <div className="absolute inset-y-0 left-0 w-px bg-line transition-colors duration-300 group-hover:bg-brand" />
      <div className="absolute left-0 top-1/2 h-0 w-px -translate-y-1/2 bg-brand transition-all duration-500 ease-out group-hover:h-full group-hover:top-0 group-hover:translate-y-0 shadow-[0_0_24px_rgba(255,42,91,0.7)]" />

      {/* corner glow on hover */}
      <div className="absolute -bottom-10 -left-10 w-64 h-64 rounded-full bg-brand/20 blur-3xl opacity-0 transition-opacity duration-700 group-hover:opacity-100 pointer-events-none" />

      {/* card body */}
      <div className="relative h-full flex flex-col justify-between pl-8 pr-6 py-10">
        {/* top: number + tag */}
        <div className="flex items-start justify-between">
          <div
            className={`tabnum font-display text-[120px] font-bold leading-none transition-colors duration-500 ${
              active ? "text-ink-ghost" : "text-ink-ghost/40"
            } group-hover:text-ink-muted`}
          >
            {step.n}
          </div>
          <div className="text-right shrink-0 mt-3">
            <div className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">
              latency
            </div>
            <div className="tabnum mt-1 font-display text-[26px] text-ink-high font-medium">
              {step.latency}
            </div>
          </div>
        </div>

        {/* bottom: title + body + cta */}
        <div>
          {/* hairline rule above heading — gray → brand on hover */}
          <div className="h-px w-12 bg-line transition-all duration-500 group-hover:w-20 group-hover:bg-brand" />

          <div className="mt-5 inline-flex items-center rounded-full border border-line bg-bg-card/40 px-2.5 py-0.5 text-[10px] uppercase tracking-[0.18em] text-brand-hi backdrop-blur transition-colors group-hover:border-brand/40">
            {step.tag}
          </div>

          <h3 className="mt-4 font-display text-[40px] leading-[1.05] tracking-tight text-ink-high">
            {step.title}
          </h3>

          <p className="mt-4 max-w-[26vw] text-[14px] leading-relaxed text-ink-muted">
            {step.body}
          </p>

          <div className="mt-6 inline-flex items-center gap-1.5 text-[12px] uppercase tracking-[0.18em] text-ink-faint transition-all duration-300 group-hover:text-brand-hi group-hover:translate-x-1">
            Explore
            <ArrowUpRight className="h-3.5 w-3.5" />
          </div>
        </div>
      </div>
    </div>
  );
}
