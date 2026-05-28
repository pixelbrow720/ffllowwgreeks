"use client";

export function Manifesto() {
  return (
    <section id="product" className="relative border-b border-line py-40 overflow-hidden">
      <div className="absolute inset-0 -z-10 bg-grid-fine opacity-30" />
      <div className="absolute -inset-x-40 top-1/2 -translate-y-1/2 -z-10 h-[600px] bg-[radial-gradient(ellipse_at_center,rgba(255,42,91,0.18),transparent_60%)]" />

      <div className="mx-auto w-full max-w-[1400px] px-6 lg:px-10">
        <div className="text-[11px] uppercase tracking-[0.2em] text-brand-hi mb-10">
          / The thesis
        </div>
        <div className="grid grid-cols-12 gap-10 items-start">
          <div className="col-span-12 lg:col-span-8">
            <h2 className="font-display text-[clamp(2.5rem,5.5vw,5.5rem)] leading-[0.98] tracking-[-0.035em] text-ink-high">
              Everyone watches price.
              <br />
              <span className="text-ink-muted">We watch </span>
              <span className="text-gradient-brand">the position</span>
              <span className="text-ink-muted"> that has to defend it.</span>
            </h2>
          </div>
          <div className="col-span-12 lg:col-span-4 lg:pt-8">
            <p className="text-lg text-ink-muted leading-relaxed">
              0DTE options are the fastest-decaying instrument on the tape. The dealer
              hedging them does not get to choose. We model that dealer minute by minute,
              strike by strike — then publish the hedge they will be forced to make.
            </p>
            <p className="mt-6 text-lg text-ink-muted leading-relaxed">
              You see the move
              <span className="text-ink-high"> before </span>
              the move.
            </p>
          </div>
        </div>

        <div className="hr-line my-16" />

        <div className="grid grid-cols-2 md:grid-cols-4 gap-8 lg:gap-16">
          <Counter k="20M+" v="ticks / day" hint="OPRA + MDP3" />
          <Counter k="0DTE" v="only" hint="SPX & NDX" />
          <Counter k="42ms" v="wire to WS p99" hint="best-effort" />
          <Counter k="1 yr" v="state archive" hint="every snapshot" />
        </div>
      </div>
    </section>
  );
}

function Counter({ k, v, hint }: { k: string; v: string; hint: string }) {
  return (
    <div className="border-l border-line pl-5">
      <div className="font-display text-[clamp(2.5rem,4vw,3.75rem)] leading-none tracking-[-0.035em] text-ink-high tabnum">
        {k}
      </div>
      <div className="mt-2 text-[12px] uppercase tracking-[0.16em] text-ink-base">{v}</div>
      <div className="mt-1 text-[11px] text-ink-faint">{hint}</div>
    </div>
  );
}
