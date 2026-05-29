# 07 · Landing Page

> **Snapshot of `web/src/components/landing/*` as of commit `aef4424`.**
> Use this as the starting point if you want the dashboard to harmonize with the marketing surface, OR as a reference if you scrap and rebuild the landing too.

## Files

- `web/src/components/landing/Hero.tsx` — section 1, hero with FloatingPanel.
- `web/src/components/landing/Manifesto.tsx` — section 2.
- `web/src/components/landing/Pipeline.tsx` — section 3 (latency story).
- `web/src/components/landing/Modules.tsx` — section 4 (the 11 dashboard modules).
- `web/src/components/landing/Marquee.tsx` — section 5 (logo strip / quotes).
- `web/src/components/landing/Pricing.tsx` — section 6.
- `web/src/components/landing/DashboardPreview.tsx` — section 7 (image-style preview).
- `web/src/components/landing/Footer.tsx`
- `web/src/components/landing/Nav.tsx` — top fixed nav.
- `web/src/components/landing/CharmSpiral.tsx` — animated decorative element.
- `web/src/components/landing/SmoothScroll.tsx` — Lenis smooth scroll wrapper.

## Hero (the surface that sets the tone)

Reference: [`web/src/components/landing/Hero.tsx`](../web/src/components/landing/Hero.tsx).

**Layout:**

```
┌─────────────────────────────────────────────────────────────────────┐
│  [bg-grid + radial-brand + bg-noise — three layered backdrops]      │
│  [CharmSpiral — animated SVG, decorative]                            │
│                                                                       │
│   ● Live · OPRA Pillar + CME MDP 3.0   <pill>                       │
│                                                                       │
│   Read the                                                            │
│   Dealer.   in real time.                                             │
│   ────────                                                            │
│   Predictive 0DTE flow + dealer positioning intelligence...           │
│                                                                       │
│   [Open Live Dashboard →]   [Activity Read the spec · OpenAPI]       │
│                                                                       │
│   Latency p99  Strikes      Tick archive   Engine                     │
│   < 100ms      0DTE only    1 year         Go · NATS                  │
│                                                                       │
│                              ┌──────────────── FloatingPanel ────┐    │
│                              │ ⚡ SPX · 0DTE · 15:30 ET   ● Live │    │
│                              │                                   │    │
│                              │ DPI composite     78.4   FORCED   │    │
│                              │ Charm zone        PEAK   42m...   │    │
│                              │ Net GEX          −$2.14B SHORT γ  │    │
│                              │ Zero γ            5862.5  +0.25%  │    │
│                              │ Pin · 5850        47%    γ-str... │    │
│                              │                                   │    │
│                              │ Forced flow · next 60m            │    │
│                              │ −$2.91B                           │    │
│                              │ net of charm aid +$510M · ...     │    │
│                              └───────────────────────────────────┘    │
│                                                                       │
│  ─── scroll · 0DTE forced flow ↓ ───                                  │
└─────────────────────────────────────────────────────────────────────┘
```

**Key chrome:**
- Backdrop: `bg-grid opacity-60` + `radial-brand` + `bg-noise opacity-[0.35] mix-blend-overlay`. Three layered effects.
- Hero number: `font-display text-display-xl` with `text-gradient-brand` on "Dealer." word.
- CTA pill: `bg-brand` with brand glow shadow `shadow-[0_8px_32px_-12px_#ff2a5b]`.
- FloatingPanel: glassmorphic card `border-line bg-bg-card/80 backdrop-blur-xl shadow-[0_30px_120px_-40px_rgba(255,42,91,0.4)]`.
- Metric grid: 4 stats with label / value / hint stack, JB Mono for hints, font-display for value.

**Color usage on landing:**
- Brand pink decorative: backdrop glows, CTA, "Dealer." gradient, hero ambient.
- Brand pink as TEXT on FloatingPanel: `text-brand-hi` for tag labels (FORCED, PEAK).
- Data values: monochrome ink-high, accent-short on negative numbers.
- ⚡ icon: `text-brand-hi`.

This is the "decorative ambient only" rule in practice — every brand-pink usage on the landing is chrome, never a data value.

## Brand tokens (in `web/src/app/globals.css` or `tailwind.config.ts`)

```
--brand:    #ff2a5b   (primary brand)
--brand-hi: #ff5b85   (highlight)
--brand-dim: rgba(255,42,91,0.10)  (background ambient)

--bg-base: #08080a       (deepest dark)
--bg-card: #0f0f12       (panel surface)
--bg-subtle: #16161a     (sub-surface, e.g. bar tracks)
--bg-hover: #1a1a20      (hover state)

--ink-high:   #f4f4f5    (primary text)
--ink-base:   #d4d4d8
--ink-muted:  #a1a1aa
--ink-faint:  #71717a
--ink-ghost:  #52525b

--line:        #26262a   (default border)
--line-strong: #3a3a40   (emphasized border)

accent-short: #ef4444   (semantic red — declining, short γ, CRIT)
accent-long:  #10b981   (semantic green — rising, long γ, healthy)
accent-warn:  #f59e0b   (semantic amber — pin, FORCED, WARN)
```

## Typography

- Display font: Inter, weight 500, used for hero numbers + section headers.
- Body font: Inter, weight 400.
- Mono font: JetBrains Mono, used for labels, tabnum values, code-like hints.
- Tabular numerics: `font-feature-settings: "tnum", "ss01", "cv11"` — applied via `tabnum` Tailwind utility.
- Tracking: section labels use `tracking-[0.18em]` to `tracking-[0.24em]` for the small-caps feel.

## Sections

1. **Hero** — `Hero.tsx`. Above the fold. Pull quote, CTAs, FloatingPanel.
2. **Manifesto** — `Manifesto.tsx`. "Why this exists" essay block. Long-form prose, single column. Brand-tint pull-quotes.
3. **Pipeline** — `Pipeline.tsx`. Latency story. Stage-by-stage diagram with budget per stage.
4. **Modules** — `Modules.tsx`. 11 dashboard module cards in a grid. Each card: title, sub, mini preview SVG.
5. **Marquee** — `Marquee.tsx`. Logo / quote strip. Auto-scrolling.
6. **Pricing** — `Pricing.tsx`. Tier cards (Free / Pro / Studio?). Pricing currently placeholder per parent-product billing.
7. **DashboardPreview** — `DashboardPreview.tsx`. SVG-style mockup of the actual dashboard. NOT live data, hand-coded.
8. **Footer** — `Footer.tsx`.

## Decisions baked in

- Long-form prose for manifesto. We're not a SaaS; we're a tool for a niche audience. Long copy is fine.
- `CharmSpiral` is the signature decorative element. It's live in the Hero. Kept across sessions.
- Smooth scroll via Lenis is intentional — feels more "premium product" than instant scroll.
- No testimonials in the marquee yet; placeholder logos.

## Coherence rules for dashboard

If you want the dashboard to feel like a sibling page:

1. Reuse `bg-grid + radial-brand + bg-noise` as a backdrop layer (`LandingBackdrop` component already does this).
2. Use `text-gradient-brand` on the DPI hero number when FORCED — landing already uses it on "Dealer.".
3. Glassmorphic panels (`bg-bg-card/70 backdrop-blur-xl`) for the highest-importance metrics.
4. Brand pink ambient on FORCED-state strip background.
5. Same JB Mono on label tracking + same `tabnum` on numbers.
6. CTA shape (rounded-full pill) on the symbol toggle in RegimeStrip — matches the landing CTA.

## What's broken on landing right now

Per `web/src/components/landing/Hero.tsx::Row` toneCls — `text-signal-pin` reference still exists but `signal-pin` token was retired. The Pin row in FloatingPanel is mock; real impl should use `text-accent-warn`. (Cleanup deferred per audit.)
