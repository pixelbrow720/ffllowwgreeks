# Design system — mockup3 · v3.1

> Premium tier. Tailwind-modern palette. Restraint motion. Terminal aesthetic.

## Files

| File | State | Purpose |
|---|---|---|
| `_v3.css` | v3.1 | Single source of truth: tokens, type scale, primitives, components |
| `_v3.js`  | v3.1 | Progressive enhancements: spotlight, reveal, ticker, hyper-text, char-split, tracing-beam, Cmd+K, live-flicker |
| `index.html` | v3.1 | Mockup catalog · journey · design choices |
| `landing.html` | v3.1 | Marketing — lamp hero · char-split title · KPI tickers · glare cards · tracing beam · CTA |
| `activate.html` | v3.1 | Onboarding — lamp art column · step list · API-key reveal · quick-start tabs |
| `dashboard.html` | v3.1 | Live terminal — sidebar · 3-col grid · KPI rail with beam-red · GEX dual-panel · DPI · Charm/Flow · Pin · tape · statusbar · Cmd+K |
| `simulator.html` | v3.1 | What-if — sliders · presets · glare result with ticker · contributors · curl preview |

User journey: **Landing → Activate → Dashboard → Simulator.**

No build step. Open the html files in any browser. Desktop only (1280–1440).

---

## Color discipline

Three earned accents. Two decorative-only glows. Everything else is grayscale on near-black.

### Earned accents — only when data carries semantic

| Token | Hex | Use only when |
|---|---|---|
| `--accent-short` | `#ef4444` (red-500) | Data carries short-gamma / dealer-pressure / alarm semantic |
| `--accent-long`  | `#10b981` (emerald-500) | Data carries long-gamma / forced-buy / calm semantic |
| `--accent-warn`  | `#f59e0b` (amber-500) | Charm peak / watch / attention — never decorative |

Each comes with `*-bg` (10% alpha fill) and `*-glow` tokens (`var(--accent-long-glow)` etc.) for soft shadows on numerics and bars.

### Decorative-only glows — hero ambient lighting only

| Token | Hex | Use |
|---|---|---|
| `--glow-indigo` | `#6366f1` | Lamp tint, ambient hero gradients, palette accents |
| `--glow-violet` | `#a855f7` | Secondary lamp tint, deeper ambient |

**Rule:** indigo and violet must never carry data semantic. They live in the lamp cone, page-card hovers, and the journey strip step indicator. If a *number* turns indigo, that's a bug.

---

## Surfaces

Five levels of near-black. Warmer slate base than v3.

```
--bg-0: #08080c   page
--bg-1: #0d0d12   card resting
--bg-2: #131319   card hover, subtle button
--bg-3: #1a1a22   button hover, slider track
--bg-4: #22222b   reserved, rare
```

### Hairlines — four step ladder

```
--line-faint:  rgba(255,255,255,0.05)   decorative dividers, statusbar segs
--line:        rgba(255,255,255,0.09)   default card border
--line-strong: rgba(255,255,255,0.16)   button hover, active slider
--line-bright: rgba(255,255,255,0.28)   hand-picked emphasis only
```

### Foreground scale (zinc)

```
--fg-0: #fafafa    headings, primary numerics
--fg-1: #d4d4d8    body, secondary numerics
--fg-2: #a1a1aa    muted body
--fg-3: #71717a    eyebrows, captions
--fg-4: #3f3f46    almost-disabled, slider marks
```

---

## Typography

Inter for prose. JetBrains Mono for numerics. Both via Google Fonts.

Globally-on font features: `tnum` (tabular numerals), `ss01` (alt single-storey g), `cv11` (Inter variant). Set on `body` so all numerics align in tables.

### Type scale

| Token | Size | Use |
|---|---|---|
| `--t-display` | clamp(56px, 7vw, 108px) | Hero only — one per page |
| `--t-h1` | clamp(36px, 4vw, 56px) | Section headers |
| `--t-h2` | 28px | Sub-section |
| `--t-h3` | 18px | Card titles in dense layouts |
| `--t-body` | 15px | Paragraph default |
| `--t-muted` | 13.5px | Secondary copy in `--fg-2` |
| `--t-tiny` | 11px | Uppercase eyebrow labels |

Display sizes use `letter-spacing: -0.04em`. Headings drop to `-0.025em` and `-0.02em`. Body and below stay near-zero for readability.

### Inline classes

```
.t-mono     JetBrains Mono with tabular nums
.t-num      Mono + tabular for digits in tables
.fg-long    color: var(--accent-long)
.fg-short   color: var(--accent-short)
.fg-warn    color: var(--accent-warn)
```

---

## Spacing

4px-base scale. `--s-1` through `--s-32`.

```
--s-1: 4px    --s-2: 8px    --s-3: 12px   --s-4: 16px
--s-5: 20px   --s-6: 24px   --s-8: 32px   --s-10: 40px
--s-12: 48px  --s-14: 56px  --s-16: 64px  --s-20: 80px
--s-24: 96px  --s-32: 128px
```

---

## Radii

Square-leaning. Terminal aesthetic — corners are subtle, never pill-rounded.

```
--r-1: 2px    pills, tiny chips, slider tracks
--r-2: 4px    nav items, small buttons
--r-3: 6px    standard buttons, sliders
--r-4: 8px    cards
--r-5: 12px   featured panels (CTA, hero card, big result)
--r-6: 16px   reserved
```

---

## Motion

Restraint. Trading software wants linear-ish ease — no bouncy springs.

### Easing

```
--ease-out:    cubic-bezier(0.22, 1, 0.36, 1)    default
--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1)    loops
--ease-emph:   cubic-bezier(0.16, 1, 0.3, 1)     premium emphasis
```

Use `--ease-emph` for the char-split title reveal, lamp drift, and CTA hover lifts.

### Durations

```
--dur-1: 120ms    micro hover
--dur-2: 200ms    button states
--dur-3: 360ms    card transitions
--dur-4: 600ms    reveals
--dur-5: 900ms    long ticker animations
```

---

## Components

### Layout

#### `.card`

Base card. Modifiers: `.card-pad` / `.card-pad-lg` (interior padding), `.card-hd` (header row), `.card-bd` (body padding).

Compose with `.spotlight` (cursor highlight on hover) and `.glare` (diagonal sheen on hover).

#### `.btn`

`.btn` resting → `.btn:hover` lifts to `--bg-3`. Modifiers:
- `.btn-primary` — white-on-black, accent CTA
- `.btn-ghost` — transparent until hover
- `.btn-sm` / `.btn-lg` — size variants

#### `.pill`

Small inline chip. Modifiers:
- `.pill-short` / `.pill-long` / `.pill-warn` — earned color
- `.pill-mono` — monospace contents (tickers, timestamps)

#### `.dot`

6×6 colored dot. Modifiers `.dot-short`, `.dot-long`, `.dot-warn`. `.dot-pulse` adds a 1.8s scale+opacity loop — use only on connection liveness indicators.

#### `.kbd`

Keyboard-shortcut chip. Use as `<span class="kbd">⌘</span><span class="kbd">K</span>`. Pairs with the `data-open-palette` button on the topbar.

---

### Premium components (new in v3.1)

#### `.lamp`

Linear-style cone of light at hero top. Place inside a relative section with `position: relative; isolation: isolate;`. Renders behind content via z-index 0.

```html
<section class="hero">
  <div class="lamp"></div>
  <div class="container">…content…</div>
</section>
```

Indigo + violet conic gradient, 4s breathe loop. Place under content; do not stack with `.aurora`.

#### `.glare`

Diagonal sheen sweeps across card on hover. Compose with `.card`:

```html
<div class="card glare">…</div>
```

Pure CSS — `::after` overlay with `transform: skewX(-25deg) translateX(…)` on hover.

#### `.beam` + variants

Animated conic-gradient border. Variants:
- `.beam` — neutral white
- `.beam-emerald` — long-γ green
- `.beam-amber` — warn yellow
- `.beam-red` — short-γ red, used on Net GEX KPI in the dashboard

Wrap any panel:

```html
<div class="kpi beam beam-red">…</div>
```

**Restraint:** only one beam-bordered widget per dashboard view. Reserve for the most critical signal.

#### `.tracing-beam`

Left-rail vertical beam that fills as user scrolls. JS auto-wires `--beam-h` from scroll progress:

```html
<aside class="tracing-beam"></aside>
<div class="thesis-content">…sticky scroll sections…</div>
```

Used on the landing page's Pipeline / Thesis section.

#### `.statusbar` + `.statusbar .seg`

Bottom strip for the dashboard. Persistent at `bottom: 0`, full-width. Each `.seg` is a labeled segment separated by `--line-faint`.

```html
<div class="statusbar">
  <span class="seg"><span class="dot dot-long dot-pulse"></span>NATS · OK</span>
  <span class="seg">PG · 12/16 conn</span>
  …
</div>
```

#### `.gradient-text` / `.gradient-text-emerald`

Hero title accent words — `background-clip: text` with linear gradient.

#### `.split-char`

Char-by-char reveal token. JS wraps each character of `[data-split]` in this span and staggers `transitionDelay`. See JS hook below.

#### `.flicker-up` / `.flicker-down`

Two-frame keyframe (140ms). JS adds these to `[data-live]` elements every ~2.4s for the data-jitter effect.

---

### Bespoke (page-local)

These live in `<style>` blocks inside individual pages — single-use, not promoted to `_v3.css` yet:

- `.gex-bar` — vertical bar in landing-page hero teaser
- `.spine-r` / `.spine-row` — horizontal label + bar row for GEX spine viz
- `.dpi-row` — 3-col grid row for DPI component bars
- `.zone` — segmented bar cell for Charm Clock zones
- `.tape-row` — 3-col grid row for narrative tape
- `.pin-row` — 3-col grid row for Pin candidates
- `.latency-cell` — pipeline-stage card on landing
- `.contrib-row` — 5-col grid row for simulator's contributor table
- `.journey-cell` — segment of the journey strip on index
- `.scenario-tile` — small projection tile on simulator

If any of these get reused in a third page, promote into `_v3.css`.

---

## JS hooks

All in `_v3.js`. Progressive enhancement only — every page works with JS off; this just adds the polish layer.

| Selector | Behavior |
|---|---|
| `.spotlight` | Cursor-anchored radial highlight on hover via `--mx`/`--my` |
| `.reveal`, `.reveal-blur`, `.reveal-stagger` | IntersectionObserver adds `.in` when entering viewport |
| `[data-split]` | Wraps each char in `.split-char`, staggers reveal 14ms when in viewport |
| `[data-ticker]` | Animates from 0 to value over 900ms ease-out (configurable via `data-dur`) |
| `[data-hyper]` | Cycles random chars on hover, settles to original text |
| `.tracing-beam` | Sets `--beam-h` to scroll progress percentage |
| `#palette` | Cmd+K toggles `.open`. Esc closes. Backdrop-click closes. `[data-open-palette]` opens |
| `[data-live]` | Random one element flickers every 2.4s — adds `.flicker-up` or `.flicker-down` |
| `.gex-bar[data-h]` | Sets `height` from `data-h` attribute |

### Data-attribute reference

```html
<!-- ticker -->
<span data-ticker="-3.84"
      data-prefix="$"
      data-suffix="B"
      data-decimals="2"
      data-dur="900">−$3.84B</span>

<!-- hyper-text scramble on hover -->
<span data-hyper>SPX</span>

<!-- char-split reveal in viewport -->
<h1 data-split>Move the dealer.</h1>

<!-- live flicker target -->
<span data-live>5810.42</span>

<!-- Cmd+K palette container -->
<div id="palette">
  <div class="palette-box">
    <div class="palette-input">…</div>
    <div class="palette-list">…</div>
  </div>
</div>

<!-- button that opens palette -->
<button data-open-palette>
  <span class="kbd">⌘</span><span class="kbd">K</span>
</button>
```

---

## Page composition recipes

### Landing hero — lamp + grid + char-split + ticker

```html
<section class="hero">
  <div class="lamp"></div>
  <div class="grid-bg"></div>
  <div class="container">
    <span class="eyebrow"><span class="dot"></span>Live · 0DTE</span>
    <h1 class="t-display" data-split>Read the dealer
      <span class="gradient-text-emerald">before the move.</span></h1>
    <div class="hero-card glare">
      <div class="kpi-grid">
        <div class="kpi">
          <div class="lbl">Net GEX</div>
          <div class="num"><span data-ticker="-3.84" data-prefix="$" data-suffix="B" data-decimals="2"></span></div>
        </div>
      </div>
    </div>
  </div>
</section>
```

### Dashboard — sidebar + main + statusbar via grid-template-areas

```css
.shell {
  display: grid;
  grid-template-columns: 224px 1fr;
  grid-template-rows: 1fr auto;
  grid-template-areas:
    "side main"
    "status status";
  min-height: 100vh;
}
```

The Net GEX KPI gets `.beam.beam-red` — the only beam-bordered widget. Restraint principle: one critical signal earns the animated border.

### Simulator — glare result with ticker

```html
<div class="result-headline glare spotlight">
  <div class="meta"><span class="dot dot-long dot-pulse"></span>Forced dealer hedge · 15m</div>
  <div class="num long">
    <span data-ticker="1.24" data-prefix="+$" data-suffix="B" data-decimals="2" data-dur="1100"></span>
  </div>
</div>
```

### Activate — lamp on left art column

The left column of `activate.html` mirrors the landing hero recipe — `.lamp` + `.grid-bg` + char-split title — but tighter (column not full-width). The right column stays clean for the secret reveal and code samples.

---

## What this doesn't have (and why)

- **Light mode.** Trading terminal. Light backgrounds wash out red/green semantic on numerics. Skip.
- **Mobile responsive.** Per durable user constraint: desktop-only, 1280–1440px target.
- **GSAP / Framer-Motion.** All motion implemented in CSS keyframes + 190 lines of vanilla JS. GSAP-CDN remains an option (`<script src="https://cdnjs.cloudflare.com/ajax/libs/gsap/3.13.0/gsap.min.js">`) for ScrollTrigger + SplitText if the IntersectionObserver-based reveals ever feel too snappy — but `--ease-emph` already covers premium emphasis without the dep.
- **Icon library.** Inline SVG strokes per call-site. Production frontend will adopt Lucide.
- **shadcn components.** This is HTML/CSS preview only. Production frontend (TS + React + shadcn/ui) will rebuild these primitives properly using the v3.1 tokens via `tailwind.config.ts`.

---

## Notes for the next session (production port)

When stepping up to TypeScript + React + shadcn/ui:

1. **Tokens → Tailwind.** Port `--bg-N`, `--fg-N`, `--accent-*`, `--line-*`, `--glow-*` into `tailwind.config.ts` `theme.extend.colors`. Spacing and radii transfer 1-1.
2. **Display fonts.** Inter + JetBrains Mono via `next/font` (or equivalent). Set `tnum + ss01 + cv11` globally in `app/layout.tsx`.
3. **shadcn primitives.** Button / Card / Badge / Input map cleanly. Override radii to the square-leaning values.
4. **Bespoke charts.** GEX dual-panel (spine + ladder), Charm Clock, DPI breakdown bars, Pin candidates, narrative tape — all hand-rolled React + SVG. Won't come from any chart lib.
5. **Live numerics.** Always set `font-feature-settings: "tnum"` on numeric containers so digits don't jitter when WebSocket pushes updates.
6. **Lamp hero in production** should respect `prefers-reduced-motion` (skip the drift animation when user opts out). Same for char-split reveal.
7. **Cmd+K palette.** Replace the static demo `#palette` with `cmdk` (the Vercel library shadcn ships).
8. **Tracing beam.** Will need `useScroll` from Framer Motion (or vanilla `IntersectionObserver` + `requestAnimationFrame`) to maintain the same scroll-coupling.

---

**Last updated:** 2026-05-27 — v3.1 complete. All four pages + index ported. Ready for production stack port.
