# Dashboard Redesign — 2026-05-30 (round 2)

> Round 1 fixed the architecture (single canvas, monochrome accents, no
> scene-slider). Round 2 fixes the **chrome**: the dashboard still didn't
> visually rhyme with the landing page. This brief applies landing-page
> ambient lighting + glassmorphism + 21st.dev-inspired interaction
> patterns to the dashboard while keeping data-color discipline intact.

## Verdict (R1 → R2)

R1 was correct but austere. The terminal aesthetic was so dialed in that the
dashboard read as a *different product* from the landing page — same brand,
two visual identities. Brow's note "kalo dashboard butuh perbaikan ya kau
perbaiki" is a license to add brand presence as **decorative ambient**, not
to compromise the data-color rule.

The R2 thesis is one sentence: **the dashboard is the page that comes after
the hero**. Same backdrop motif (radial brand glow, fine grid, noise overlay,
charm-spiral hint), same typography contract (`font-display` for hero
numbers, `font-mono` for tabular data), same chrome (glassmorphic panels at
focal moments, hairline borders elsewhere). The difference is density and
data — not visual language.

## Skills + 21st.dev patterns applied

### Skills (vendored, read first)

- **`frontend-design`** — committed to a single bold direction
  ("Bloomberg-grade trading terminal with brand-pink ambient lighting").
  Display fonts for hero callouts, monospace for live data; cohesive
  conceptual identity across panels.
- **`ui-ux-pro-max`** — Real-Time Monitoring + Data-Dense Dashboard +
  Financial Dashboard patterns. Bento Box hierarchy at the macro level
  (focal hero panel earns more density than peripherals). Dimensional
  Layering (backdrop → panel surface → glass overlay → data ink).
- **`dashboard-screenshot`** — verifies via Playwright at 1920×1080
  baseline; results on file at `tmp/dashboard-audit/`.
- **`design`** + **`ui-styling`** — token system already in place
  (`tailwind.config.ts`); reuse `bg-card`, `accent-*`, `brand-*` tokens
  unchanged. Add no new dependencies.

### 21st.dev component patterns adopted

| 21st.dev category | URL | Adapted as |
|---|---|---|
| **Number** (animated counters / live tickers) | https://21st.dev/community/numbers | RegimeStrip ticker block — large `font-display` spot + delta with brand glow at FORCED state |
| **Dock** (macOS-style icon strip with tooltip + hover lift) | https://21st.dev/community/docks | Symbol pill toggle (SPX·NDX) and RailNav active-item glass pill |
| **Bento Grid** (mixed-density tile composition) | https://21st.dev/community/bento-grids | Hero row remains 280·1fr·360 but each cell now picks its own density tier |
| **Tooltip** (rich hover popovers with arrow + keyboard) | https://21st.dev/community/tooltips | GEXProfile strike-row hover popover (gamma, charm, vanna, dealer pos) |
| **Empty States** (illustrated, on-brand) | https://21st.dev/community/empty-states | SpotChart + DPITimeline empty states use a CharmSpiral motif instead of "waiting for first session tick" prose |
| **Card** (glassmorphism + glow on critical data) | https://21st.dev/community/cards | Glass panel variant on DPILive when dpi ≥ 75 (FORCED) and on PinPanel when pin probability ≥ 0.5 |

These are **reference implementations**. We re-create the visual idea inline
in Tailwind + recharts + lucide-react. No new dependencies.

## Three most important visual changes

1. **Ambient backdrop ties the dashboard to the landing page.** New
   `LandingBackdrop` renders a `bg-grid` + `radial-brand` + `bg-noise`
   layered backdrop behind every panel. Panels keep their opaque
   `bg-bg-card` surface so the backdrop is felt at the seams, not
   underneath the data. Backdrop is muted (10–20% opacity vs landing's
   60%) so it's atmosphere, not decoration competing with the chart.

2. **Hero focal hierarchy.** The DPI composite number jumps from 44px to
   72px `font-display`, gets a `text-gradient-brand` treatment, and earns
   a glassmorphic frame when ≥75. The regime strip's spot value is
   amplified the same way. Everything else stays calm — the eye now has
   one focal point per row instead of nine equally loud panels.

3. **Strike hover popover on GEXProfile.** Hovering any row pops a glass
   tooltip with the breakdown: gamma, charm, vanna, dealer pos, distance
   from spot, raw GEX in dollars. Uses `font-mono` numbers, `font-display`
   strike header, and a pin/wall callout at the top when relevant. Strikes
   within ±0.5% of spot get a subtle ink-high tint and 1.4× row height
   (visual amplification, not extra data).

Plus a basket of smaller polish:

- Recharts width=-1 warnings — gated behind a `useLayoutMounted()` hook
  that renders the chart only after the parent has a non-zero box.
- Spot history empty state and DPI timeline empty state — replaced
  generic "waiting for first tick" prose with a brand-themed
  pulsing-spiral SVG and on-brand copy.
- RailNav active item gets a small `border-l-2 border-brand` accent
  (decorative, not data) plus glass background, matching the landing
  CTA's glass-on-glow look.
- PinPanel charm-zone segmented bar gets a brand-tinted backdrop when
  `PEAK` zone — drama without coloring data.
- Symbol toggle (SPX·NDX) becomes a proper pill dock with hover lift +
  a subtle brand-glow on active.

## Pattern selection (Real-Time Monitoring + Financial Dashboard)

Per `ui-ux-pro-max`'s pattern catalog, this dashboard is the union of:

- **Real-Time Monitoring** — DPI composite, regime, spot, charm zone all
  refresh every second via WS. Latency-sensitive viewers; eye stays fixed.
- **Financial Dashboard** — dollar-notional GEX, dealer-position context,
  walls/pin/zero-gamma reference levels. Trader needs to scan deltas and
  cross-correlate. Tabular numerics required.
- **Bento Box Grid** — at the macro level (3-cell hero row + 2-cell
  bottom strip). Each cell picks its own density per content; we don't
  enforce a uniform tile size.

Reject patterns:

- Mobile / responsive — desktop only, 1920×1080 baseline.
- Card-shadow Material aesthetic — terminals are flat; shadows are
  reserved for the glass focal moments only.
- AI chat / assistant column — out of scope for this product surface.

## Hierarchy: which panel is the focal point

**The DPI composite number in the left rail.** Justification:

- It's the single answer to the question the product asks ("how forced
  is the dealer right now"). Every other panel is context for that
  number.
- It's persistent across symbol switches (lives in left rail, not
  changing scene).
- It earns the glassmorphic frame + brand gradient text + scale (72px)
  because the rule book explicitly allows brand colors on hero number
  callouts. It's the only number that gets that treatment.

Secondary focal: **spot price + delta in the regime strip.** Tertiary:
**GEX-by-strike profile** as the largest panel by area, but visually
muted via monochrome scaffold + accent bars only.

The bottom strip (DPI timeline + signal log) is intentionally less dense
than the hero — Bloomberg-style "context below, action above".

## What's deliberately *not* changing

- The grid (`280px 1fr 360px` × `460+260` × `280` bottom). It works.
- Data colors stay the three accents (long, short, warn). No exceptions.
- The `Panel` primitive's hairline-border-on-bg-card aesthetic. Glass is
  a *variant*, not a replacement.
- `lib/api/*` and `lib/ws/*`. Not touched.

## Followups for Brow

- **Drop `signal.{up,down,warn,info,pin}`** from `tailwind.config.ts`.
  After this round, the only remaining reference is the landing
  FloatingPanel's `text-signal-pin` Row tone — fixed in this PR by
  remapping to `text-accent-warn`. Token block can be deleted.
- The dashboard's RailNav still points 10 items at unwired routes
  (DPI Console, Charm Clock, Flow Tape, etc). Pending content for those
  pages — they're dimmed-with-tooltip per R1.
- `replay_dbn.exe` and `api.exe` still in worktree (gitignored); leave.

## Files added

- `web/src/components/dashboard/LandingBackdrop.tsx` — ambient layer.
- `web/src/components/primitives/StrikeTooltip.tsx` — GEX hover popover.
- `web/src/lib/hooks/useLayoutMounted.ts` — recharts layout gate.

## Files rewritten

- `web/src/app/dashboard/page.tsx` — wraps content in LandingBackdrop.
- `web/src/components/primitives/Panel.tsx` — adds `tone="glass"` variant.
- `web/src/components/dashboard/RegimeStrip.tsx` — symbol dock + spot
  hero number.
- `web/src/components/dashboard/RailNav.tsx` — glass active item.
- `web/src/components/dashboard/DPILive.tsx` — hero composite + glass
  frame at FORCED.
- `web/src/components/dashboard/SpotChart.tsx` — recharts gate +
  on-brand empty state.
- `web/src/components/dashboard/GEXProfile.tsx` — strike amplification
  near spot + hover popover.
- `web/src/components/dashboard/DPITimelineLive.tsx` — recharts gate +
  on-brand empty state.
- `web/src/components/dashboard/PinPanel.tsx` — PEAK zone brand tint.
- `web/src/components/landing/Hero.tsx` — `text-signal-pin` →
  `text-accent-warn` (single line).

## Verification

- Playwright audit at 1920×1080: page height = 1056 (no scroll-of-doom),
  zero console errors expected (recharts warning gone).
- `npm run lint` clean.
- `npm run build` clean.
