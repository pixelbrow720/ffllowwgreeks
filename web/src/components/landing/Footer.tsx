"use client";

import { Github, Twitter } from "lucide-react";

export function Footer() {
  return (
    <footer className="relative overflow-hidden">
      <div className="absolute inset-0 -z-10 bg-grid opacity-30" />
      <div className="absolute inset-x-0 top-0 -z-10 h-px bg-gradient-to-r from-transparent via-line-strong to-transparent" />

      <div className="mx-auto w-full max-w-[1400px] px-6 lg:px-10 py-24">
        <div className="grid grid-cols-12 gap-10 items-end">
          <div className="col-span-12 lg:col-span-8">
            <h2 className="font-display text-[clamp(3rem,8vw,7.5rem)] leading-[0.9] tracking-[-0.04em] text-ink-high">
              Read
              <br />
              the <span className="text-gradient-brand">dealer.</span>
            </h2>
          </div>
          <div className="col-span-12 lg:col-span-4">
            <p className="text-ink-muted leading-relaxed mb-6">
              Get the weekly desk note — what dealer state told us this week, what backtests
              say about it, and what we shipped.
            </p>
            <form className="flex items-center gap-2 rounded-full border border-line bg-bg-card p-1 focus-within:border-brand/40">
              <input
                type="email"
                placeholder="your@email.com"
                className="flex-1 bg-transparent px-3 py-2 text-sm text-ink-base placeholder:text-ink-faint focus:outline-none"
              />
              <button className="rounded-full bg-brand px-4 py-2 text-[12px] font-medium uppercase tracking-[0.14em] text-white hover:bg-brand-hi">
                Subscribe
              </button>
            </form>
          </div>
        </div>

        <div className="hr-line my-12" />

        <div className="grid grid-cols-2 md:grid-cols-5 gap-8">
          <div className="col-span-2">
            <div className="flex items-center gap-2.5">
              <div className="relative h-6 w-6">
                <div className="absolute inset-0 rounded-md bg-gradient-to-br from-brand to-brand-lo" />
                <div className="absolute inset-[2.5px] rounded-[5px] bg-bg-base" />
                <div className="absolute inset-[5px] rounded-[3px] bg-gradient-to-br from-brand-hi to-brand" />
              </div>
              <span className="text-[14px] font-semibold tracking-tight text-ink-high">
                flow<span className="text-brand">greeks</span>
              </span>
            </div>
            <p className="mt-4 text-[12px] text-ink-faint max-w-xs leading-relaxed">
              An add-on to flowjob.id · 0DTE dealer positioning intelligence.
              Built solo in Go. Not investment advice.
            </p>
          </div>
          {[
            { h: "Product", l: ["Live Dashboard", "Charm Clock", "Forced Flow", "Backtest"] },
            { h: "Developers", l: ["OpenAPI 3.1", "WebSocket /ws/live", "Webhooks", "Status"] },
            { h: "Company", l: ["About", "Changelog", "Pricing", "Contact"] },
          ].map((c) => (
            <div key={c.h}>
              <div className="text-[10px] uppercase tracking-[0.2em] text-ink-faint mb-4">{c.h}</div>
              <ul className="space-y-2 text-[13px] text-ink-base">
                {c.l.map((i) => (
                  <li key={i}>
                    <a href="#" className="hover:text-brand-hi transition-colors">{i}</a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-16 flex flex-col md:flex-row items-center justify-between gap-4 text-[11px] text-ink-faint">
          <span>© 2026 flowgreeks · part of flowjob.id · v0.2.0 beta</span>
          <div className="flex items-center gap-4">
            <a href="#" className="hover:text-ink-base"><Github className="h-4 w-4" /></a>
            <a href="#" className="hover:text-ink-base"><Twitter className="h-4 w-4" /></a>
            <a href="#" className="hover:text-ink-base">privacy</a>
            <a href="#" className="hover:text-ink-base">terms</a>
            <a href="#" className="hover:text-ink-base">security.txt</a>
          </div>
        </div>
      </div>
    </footer>
  );
}
