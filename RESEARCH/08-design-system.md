# 08 · Design System

> Vendored skills at `.claude/skills/frontend-design`, `.claude/skills/ui-ux-pro-max`, `.claude/skills/ui-styling`, `.claude/skills/design-system`, `.claude/skills/dashboard-screenshot` are the deeper reference. This file captures FlowGreeks-specific decisions only.

## Tokens

### Color

```
--bg-base:    #08080a   # absolute floor
--bg-card:    #0f0f12   # panel surface
--bg-subtle:  #16161a   # bar tracks, dim sub-surfaces
--bg-hover:   #1a1a20   # hover

--ink-high:   #f4f4f5   # primary text + hero numbers
--ink-base:   #d4d4d8   # body text
--ink-muted:  #a1a1aa   # secondary text
--ink-faint:  #71717a   # labels, sub-text
--ink-ghost:  #52525b   # disabled, decoration

--line:        #26262a  # default border
--line-strong: #3a3a40  # emphasized border

--brand:     #ff2a5b    # decorative ambient ONLY
--brand-hi:  #ff5b85    # gradient companion
--brand-dim: rgba(255,42,91,0.10)

--accent-short: #ef4444  # semantic: down, short γ, CRIT
--accent-long:  #10b981  # semantic: up, long γ, healthy
--accent-warn:  #f59e0b  # semantic: pin, FORCED, WARN
```

**Discipline rule:**
> Brand pink + brand-hi + brand-dim are CHROME ONLY: backdrop glows, CTAs, hero number gradient text, decorative dividers, ambient overlays on FORCED state. They MUST NEVER color a value, bar, chart line, axis, data label, or chip representing a measurement.

If you find brand pink on a data element during code review, that's a regression.

## Typography

```
--font-inter:    "Inter Variable", sans-serif   # primary
--font-jb-mono:  "JetBrains Mono Variable", monospace
--font-display:  Inter, weight 500, tracking-tight  (alias for hero numbers)

font-feature-settings: "tnum" "ss01" "cv11"   # tabular numerics, applied via `tabnum` Tailwind utility
```

Type scale:

| Use | Size | Weight | Family |
|---|---|---|---|
| Hero headline ("Read the Dealer.") | text-display-xl (~96px) | 500 | Inter |
| Panel hero number (DPI 75.7) | 48px | 500 | Inter |
| Spot ticker (in RegimeStrip) | 26px | 500 | Inter |
| Section header (panel title) | 10px uppercase tracking-[0.2em] | 500 | JB Mono |
| Section subtitle | 9.5px uppercase tracking-[0.2em] | 400 | JB Mono |
| Body text | 11.5px-13px | 400 | Inter |
| Sub-label | 9-10px uppercase tracking-[0.16em] | 400 | JB Mono |
| Code/value tabular | 10-13px tabnum | 500 | JB Mono |

Letter spacing is part of the look. Section headers use 0.2em+; this is intentional and signals "terminal", not "blog".

## Layout primitives

### Panel

```tsx
<Panel
  title="GEX by Strike"
  subtitle="Dealer gamma per strike ($M notional)"
  actions={<Pill tone="down">net −$54B</Pill>}
  tone="default" // | "glass-brand" | "glass-warn"
  contentClassName="p-3 flex flex-col"
>
  ...
</Panel>
```

- `Panel` primitive at `web/src/components/primitives/Panel.tsx`.
- Self-fills cell height (`h-full min-h-0 flex flex-col`).
- Header row: title (h3 small caps) + subtitle (sub-tracking) + right-aligned actions.
- Content slot: bottom-flex, `min-h-0` so internal charts can `flex-1`.
- `tone` glass variants apply backdrop blur + brand/warn-tinted shadow. Decorative only.

### Pill

```tsx
<Pill tone="up | down | warn | neutral">±$54B</Pill>
```

Small badge, semantic accent border + tinted background.

### LandingBackdrop

`web/src/components/dashboard/LandingBackdrop.tsx` — fixed-positioned ambient layer for the dashboard root. Layered: `bg-grid` + `radial-brand` + `bg-noise mix-blend-overlay`. Use sparingly — it sits BEHIND every panel; panels themselves stay opaque (`bg-bg-card`).

## Density

| Section | Per pixel info budget |
|---|---|
| RegimeStrip topbar | ~14 metrics in 1920×56 = high |
| Panel header | 1 title + 1 sub + 1-3 action chips |
| GEX strike row | strike + side + bar + value = 4 datums per row |
| KeyLevels row | dot + label + price + percent = 4 datums per row |
| Signal Log row | time + sev + kind + message = 4 columns |

Bloomberg / SpotGamma reference: 6-9 datums per row, 60-80px row height. We aim for 4-5 datums per row, 36-44px row height. Slightly less dense than Bloomberg, more dense than typical SaaS.

## Motion / animation

- Throttle live state to 1 update/min on the snapshot store. Force-flush only on regime/zone/pin transitions.
- No Framer Motion. No Lottie. No spring animations on data elements.
- Animations OK on: backdrop spirals, decorative borders, status dot ping, hover transitions.
- Animations NOT OK on: numeric values (re-render in place is fine; do not animate the number itself), chart strokes (use `isAnimationActive={false}` on Recharts).

## Empty / loading / error states

- **Loading**: monochrome skeleton bars (`animate-pulse bg-bg-subtle/40`).
- **Empty**: terse copy + monochrome reference grid SVG. NEVER brand pink. (DPI Timeline empty state was rewritten to remove brand pink in commit `aef4424`.)
- **Error**: small `text-accent-warn` chip + error code + 1-line ink-faint explanation.

## Accessibility (deferred but documented)

- WCAG AA contrast on every text/bg pair. Audit not yet run.
- Keyboard nav: not yet implemented for panel switching. Tab order is DOM order.
- ARIA: Panel header uses `<h3>`. Status indicators don't have `role="status"` yet.

These are real gaps. Defer until M5+.

## What "Bloomberg-grade" means here

Looking at SpotGamma, MenthorQ, and Bloomberg Terminal screenshots, the shared traits:

1. **One focal area** per scene. Not a uniform grid.
2. **Vertical strike-ladder** is THE convention for option dealer state.
3. **Tabular numerics + small caps** are the "this is a terminal" visual signal.
4. **Three-color rule** (red/green/amber for data + monochrome for chrome) is universal.
5. **Density > whitespace.** SaaS uses whitespace; terminals don't.
6. **No drop shadows on panels.** Hairline borders only.
7. **No hover-reveal nav.** Static rail.
8. **No icons-as-decoration.** Icons earn their pixels.

FlowGreeks tries to honor all 8. Where we diverge: brand pink ambient on the backdrop is more "premium product" than terminal — that's a deliberate concession to the marketing surface coherence.

## Skills package

Read these before doing major design work:

- `.claude/skills/frontend-design/SKILL.md` — design tokens, hover states, accessibility, motion guidelines. Anthropics-authored, generic.
- `.claude/skills/ui-ux-pro-max/SKILL.md` — 161 reasoning rules, 67 styles, 161 palettes, 57 font pairings, 99 UX guidelines. Industry-tagged, very dense.
- `.claude/skills/dashboard-screenshot/SKILL.md` — patterns for terminal-grade dashboards.
- `.claude/skills/ui-styling/SKILL.md` — Tailwind patterns + shadcn integration.
- `.claude/skills/design-system/SKILL.md` — generators (Python script bundled).

## 21st.dev components inventory

Browsed at https://21st.dev/community/components. Patterns adopted (recreated inline, no new dependencies):

- Number tickers (RegimeStrip live values)
- Dock toggle (symbol switcher)
- Bento grid (regime panel composition)
- Tooltip popover (strike-tooltip on GEXProfile)
- Empty State (DPI Timeline placeholder, SpotChart placeholder)
- Card glassmorphism (Panel `tone="glass-brand|glass-warn"`)

These are the inspiration; the implementations are FlowGreeks-specific.

## Design decisions to revisit

- Brand pink on FORCED-state strip background — auditor and Brow both flagged it as feeling out of place. Test: same strip with `bg-bg-subtle/60 + ring-1 ring-accent-warn` instead.
- 11-slot RailNav with 10 dimmed — feels promissory. Either remove dimmed slots or finish wiring 4 routes (`/replay`, `/walls`, `/flow-tape`, `/calendar`).
- DPI Timeline as 280h bottom strip — auditor says cramped. Test: full 320h or move to right rail.
- SpotChart Y-axis right-orientation — non-standard. Test: left-orientation or both.
