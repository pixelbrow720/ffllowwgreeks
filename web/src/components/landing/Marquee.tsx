"use client";

const ITEMS = [
  "0DTE-ONLY",
  "FORCED-FLOW SIMULATOR",
  "CHARM CLOCK",
  "DEALER POSITIONING INDEX",
  "WHAT-IF DEALER MODEL",
  "REPLAY ANY SESSION",
  "BACKTEST ANY RULE",
  "NO 3,500-TICKER BLOAT",
  "OPRA + CME LIVE",
  "SUB-100MS WIRE-TO-WS",
];

export function Marquee() {
  const duped = [...ITEMS, ...ITEMS];
  return (
    <section className="relative border-y border-line bg-bg-base/60 py-7 overflow-hidden">
      <div className="flex animate-marquee whitespace-nowrap will-change-transform">
        {duped.map((label, i) => (
          <span key={i} className="flex items-center gap-6 px-6">
            <span className="font-display text-[clamp(2rem,5vw,4.5rem)] font-medium uppercase tracking-tight text-ink-high">
              {label}
            </span>
            <span className="h-2.5 w-2.5 rotate-45 bg-brand shadow-[0_0_12px_#ff2a5b]" />
          </span>
        ))}
      </div>
    </section>
  );
}
