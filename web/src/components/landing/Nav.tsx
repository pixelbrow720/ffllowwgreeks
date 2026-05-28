"use client";

import Link from "next/link";
import { ArrowUpRight } from "lucide-react";

const NAV_OFFSET = -72; // height of the floating nav + breathing room

const LINKS = [
  { label: "Product", href: "#product" },
  { label: "Modules", href: "#modules" },
  { label: "Pipeline", href: "#pipeline" },
  { label: "Activate", href: "#activate" },
  { label: "Docs", href: "/docs" },
];

export function Nav() {
  const onAnchor = (e: React.MouseEvent<HTMLAnchorElement>, href: string) => {
    if (!href.startsWith("#")) return;
    e.preventDefault();
    const target = document.querySelector(href);
    if (!target) return;
    if (typeof window !== "undefined" && window.__lenis) {
      window.__lenis.scrollTo(target as HTMLElement, { offset: NAV_OFFSET });
    } else {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  return (
    <nav className="fixed top-0 inset-x-0 z-50 px-6 py-4">
      <div className="mx-auto flex max-w-[1400px] items-center justify-between rounded-full border border-line/80 bg-bg-base/70 px-4 py-2 backdrop-blur-xl">
        <Link href="/" className="flex items-center gap-2.5">
          <div className="relative h-6 w-6">
            <div className="absolute inset-0 rounded-md bg-gradient-to-br from-brand to-brand-lo" />
            <div className="absolute inset-[2.5px] rounded-[5px] bg-bg-base" />
            <div className="absolute inset-[5px] rounded-[3px] bg-gradient-to-br from-brand-hi to-brand" />
          </div>
          <span className="text-[14px] font-semibold tracking-tight text-ink-high">
            flow<span className="text-brand">greeks</span>
          </span>
          <span className="ml-1 hidden md:inline-flex items-center rounded-full border border-line bg-bg-card px-2 py-0.5 text-[9px] uppercase tracking-[0.2em] text-ink-faint">
            v0.2 · beta
          </span>
        </Link>

        <div className="hidden md:flex items-center gap-1">
          {LINKS.map((l) =>
            l.href.startsWith("#") ? (
              <a
                key={l.label}
                href={l.href}
                onClick={(e) => onAnchor(e, l.href)}
                className="rounded-full px-3 py-1.5 text-[12.5px] text-ink-muted hover:bg-bg-hover hover:text-ink-base transition-colors"
              >
                {l.label}
              </a>
            ) : (
              <Link
                key={l.label}
                href={l.href}
                className="rounded-full px-3 py-1.5 text-[12.5px] text-ink-muted hover:bg-bg-hover hover:text-ink-base transition-colors"
              >
                {l.label}
              </Link>
            )
          )}
        </div>

        <div className="flex items-center gap-2">
          <Link
            href="/dashboard"
            className="hidden sm:inline-flex items-center gap-1.5 rounded-full border border-line bg-bg-card px-3 py-1.5 text-[12px] text-ink-base hover:bg-bg-hover transition-colors"
          >
            Live Dashboard
            <ArrowUpRight className="h-3 w-3" />
          </Link>
          <a
            href="#activate"
            onClick={(e) => onAnchor(e, "#activate")}
            className="inline-flex items-center gap-1.5 rounded-full bg-brand px-3.5 py-1.5 text-[12px] font-medium text-white shadow-[0_0_24px_-6px_#ff2a5b] hover:bg-brand-hi transition-colors"
          >
            Get access
          </a>
        </div>
      </div>
    </nav>
  );
}
