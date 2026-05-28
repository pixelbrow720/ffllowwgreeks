# Devin prompt — FlowGreeks dashboard + landing mockup

> Hand this entire file to Devin (or Claude Code, Cursor, v0, any agent that can produce static HTML). It's self-contained — no other reading required.

---

## Mission

Produce a **static HTML/CSS/JS mockup** of FlowGreeks: a desktop-only, terminal-grade, real-time options-flow + dealer-positioning product for 0DTE SPX & NDX traders. Two surfaces:

1. **Landing page** — marketing surface, dark/light theme toggle, animated background, hero → product modules → pricing-style CTA (no real pricing, gated to bootcamp graduates).
2. **Dashboard** — the actual product, three live-state variations.

The mockup is the **visual blueprint** a frontend team uses for production implementation in Next.js + React — every chart, annotation, color, spacing, and interaction state decided here.

**Deliverable:** one folder `flowgreeks-mockup/` containing:

```
flowgreeks-mockup/
├── index.html                landing page (root entry)
├── dashboard.html            full dashboard, default state (mid-session, neutral regime)
├── dashboard-shortgamma.html same dashboard, short-gamma regime, high DPI, charm PEAK
├── dashboard-pinned.html     same dashboard, late session, pin engine active
├── styles/
│   ├── tokens.css            design tokens for BOTH themes (dark default, light variant)
│   ├── components.css        reusable components (panel, card, kbd, gauge, badge, chip)
│   ├── landing.css           landing-specific layout + hero animation
│   └── dashboard.css         dashboard grid + chart-specific tweaks
├── js/
│   ├── data.js               mock data — three dashboard states + landing copy
│   ├── theme.js              dark/light toggle persistence (localStorage)
│   ├── background.js         landing animated background (canvas / WebGL / CSS)
│   ├── charts.js             chart rendering (D3 + lightweight-charts + custom SVG)
│   └── interactions.js       hover, tooltip, scene transitions, command palette
└── README.md                 design rationale + library choices + handoff notes
```

No build step. Open `index.html` in Chrome → landing works. Click CTA → goes to `dashboard.html`. Use CDN for D3, lightweight-charts, and any animation library.

---

## Project context

**FlowGreeks** = real-time options flow + dealer-positioning intelligence platform, **laser-focused on 0DTE SPX & NDX**. Tagline: "Read the Dealer."

**Differentiator vs SpotGamma / GEXBot / MenthorQ:** they show **dealer state**. FlowGreeks shows **dealer action** — the forced flow dealers must execute next, in dollar notional, before the move happens.

**Distribution:** not sold standalone. Access is granted to graduates of the parent product **flowjob.id**'s order-flow + options-flow bootcamp. Users are Indonesian retail-to-prosumer 0DTE traders who already understand the math (DPI, GEX, charm, vanna, dealer positioning) — so the dashboard does **not** need explanatory tooltips for jargon. It needs to surface *signal* fast.

**Constraints (hard — never violate):**
- **Desktop only.** Min viewport 1440×900, target 1920×1080. Banner on tablet/mobile, no responsive layout.
- **Tickers locked to SPX + NDX.** No multi-ticker selectors beyond these two. No equity options, no crypto.
- **Tabular numerics always on**: every number renders with `font-feature-settings: "tnum", "ss01", "cv11"` so columns of digits align column-perfectly.
- **One dominant metric per scene.** Trader's eye should land on the same focal point every time they glance.

---

## Reference sites (study these before writing a single line)

Open each in a browser, study the visual language, screenshot, **copy CSS values** via DevTools, then build. The mockup must visibly draw from these references — especially the Thalex set, which is the closest visual target — without copying any one wholesale.

### Primary visual targets — Thalex (chart aesthetics)

These are the **closest visual reference** for what the user wants. Open in a browser, screenshot every chart, extract the palette, axis style, hover behavior, line weights, annotation treatment. The dashboard's chart aesthetic must match this family.

| URL | What to extract |
|---|---|
| `thalextech.github.io/combo-greeks` | Multi-greek combo chart layout, line-stack treatment, legend positioning, grid weight, tick density, hover crosshair behavior, tooltip card style |
| `thalextech.github.io/greeks` | Per-greek view — clean line treatment, soft grid, axis label discipline, color-by-greek mapping (use this color logic) |
| `thalextech.github.io/break-even` | Break-even surface visualization — how curves are layered, fill opacity, intersection annotations |
| `thalextech.github.io/futures-basis` | Basis chart — how a single-metric time series is presented when it's the focal point. Copy axis treatment, padding, range padding logic |

**Thalex visual signature to replicate:**
- **Background:** very dark (`#0a0a0c` / `#0f1014` family), with subtle grid lines barely visible
- **Lines:** 1.5px stroke weight, slightly desaturated colors (not full saturation), smooth Catmull-Rom interpolation where appropriate
- **Axes:** ticks `--ink-faint`, labels `--ink-muted`, no axis lines (just floating ticks)
- **Hover:** vertical crosshair line spanning full chart height, dot on each series at the cursor x-position, tooltip card fixed top-right or top-left of the chart panel (NOT floating near cursor — fixed corner)
- **Padding:** generous left/right padding so endpoints don't kiss the panel edge
- **No chart titles inside the chart area** — title sits above the chart in the panel header

### Style + animation reference — Orchid

| URL | What to extract |
|---|---|
| `orchid.ai` | Landing page treatment — animated background (likely WebGL particle/flow field or CSS gradient mesh), dark/light theme toggle behavior, hero typography pacing, section transitions, the *feel* of a premium AI-product landing |

**Orchid visual signature to replicate (landing only, NOT dashboard):**
- Animated background — pick ONE of: gradient mesh (CSS `radial-gradient` layered with very slow keyframe motion), WebGL noise field, or canvas particle network. Must run at 60fps and use < 5% CPU.
- Theme toggle — top-right corner, smooth crossfade between dark/light (300ms, ease-out)
- Light mode is **inverted** but still premium — `#fafafa` base, `#08080a` for ink. Brand pink unchanged.
- Generous typography — hero h1 at 80-96px, sans, tight tracking
- Section dividers via subtle gradient lines, not hard borders

### Domain references — Options dashboards

| Site | What to extract |
|---|---|
| **SpotGamma** (`spotgamma.com`) | GEX profile bar layout, key-level annotations (call wall / put wall / zero gamma), HIRO oscillator, intraday seasonality treatment |
| **GEXBot** (`gexbot.com`) | Colored bar GEX with magnitude-driven opacity, dealer-positioning bar, dense numerical readout panels |
| **MenthorQ** (`menthorq.com`) | Charm/vanna heatmap matrix (strike × time), blue-red diverging palette, level-overlay-on-price-chart |
| **Unusual Whales** (`unusualwhales.com/options`) | Flow tape — colored rows by aggressor + premium size, sweep/block/repeat tags, sparkline-in-row patterns |
| **Squeeze Metrics** (`squeezemetrics.com`) | Vanna/charm methodology visual language — they originated DIX/GEX charts |

### Polish + density references

| Site | What to extract |
|---|---|
| **TradingView lightweight-charts** (`tradingview.github.io/lightweight-charts/`) | Use this exact library for the spot chart — not Recharts |
| **Linear app** (`linear.app`) | Dark-theme polish, command palette, keyboard-first interaction, micro-animations |
| **Bloomberg Terminal** (screenshots) | Information density. Every pixel earns its place. |

**The vibe:** Thalex chart aesthetic × Orchid landing polish × Bloomberg density × Linear interaction quality × SpotGamma domain language. NOT a "pretty fintech SaaS" template. NOT a Robinhood retail UI. NOT a generic admin dashboard.

---

## Visual language (lock these tokens — `tokens.css`)

The dashboard is **dark only** (the user constraint hasn't moved). The **landing page supports both themes** with a toggle. Define both palettes — landing reads `[data-theme="light"]` overrides; dashboard ignores theme.

```css
:root {
  /* === DARK (default — applies to dashboard always, landing default) === */

  /* Surfaces — near-black, layered */
  --bg-base:    #08080a;
  --bg-card:    #0f0f12;
  --bg-hover:   #16161a;
  --bg-subtle:  #1c1c20;

  /* Lines */
  --line:        #26262a;
  --line-strong: #3a3a40;

  /* Text — five tiers */
  --ink-high:  #f4f4f5;  /* numeric headlines */
  --ink-base:  #e4e4e7;  /* body */
  --ink-muted: #a1a1aa;  /* labels */
  --ink-faint: #71717a;  /* axis ticks */
  --ink-ghost: #52525b;  /* placeholders */

  /* Earned accents — semantic only, never decorative */
  --signal-up:    #10b981;  /* long gamma, positive flow, green wall */
  --signal-down:  #ef4444;  /* short gamma, negative flow, red wall */
  --signal-warn:  #f59e0b;  /* threshold breach, regime flip */
  --signal-pin:   #a855f7;  /* zero gamma, pin candidate */
  --signal-info:  #3b82f6;  /* live tick highlight, neutral level */

  /* Greek-by-color (Thalex-style) — used in combo charts only */
  --greek-delta: #60a5fa;  /* blue */
  --greek-gamma: #34d399;  /* emerald */
  --greek-theta: #fbbf24;  /* amber */
  --greek-vega:  #a78bfa;  /* violet */
  --greek-charm: #f472b6;  /* pink */
  --greek-vanna: #fb923c;  /* orange */

  /* Brand — decorative-only, ambient lighting only, NEVER on data */
  --brand:       #ff2a5b;
  --brand-dim:   rgba(255, 42, 91, 0.08);

  /* Typography */
  --font-sans: "Inter", system-ui, -apple-system, sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", Consolas, monospace;

  /* Spacing scale (4px grid) */
  --sp-1: 4px; --sp-2: 8px; --sp-3: 12px; --sp-4: 16px;
  --sp-6: 24px; --sp-8: 32px; --sp-12: 48px;

  /* Radii */
  --r-sm: 4px; --r-md: 6px; --r-lg: 8px; --r-xl: 12px;

  /* Motion */
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-smooth: cubic-bezier(0.4, 0, 0.2, 1);
}

/* === LIGHT (landing only — toggle via [data-theme="light"] on <html>) === */
[data-theme="light"] {
  --bg-base:    #fafafa;
  --bg-card:    #ffffff;
  --bg-hover:   #f4f4f5;
  --bg-subtle:  #ececef;

  --line:        #e4e4e7;
  --line-strong: #d4d4d8;

  --ink-high:  #08080a;
  --ink-base:  #18181b;
  --ink-muted: #52525b;
  --ink-faint: #a1a1aa;
  --ink-ghost: #d4d4d8;

  /* Signals + greeks unchanged — they encode meaning, not theme */
  /* Brand unchanged */
}
```

**Typography rules (both themes):**
- Headlines + section titles → Inter, weight 500, tracking -0.01em
- Numbers (every single one) → JetBrains Mono, tabular nums on
- Labels (axis ticks, panel titles) → Inter 11px uppercase, tracking +0.06em, color `--ink-muted`
- Never mix display fonts. Never use Inter for numbers.

**Color discipline:**
- Default surface: `--bg-base`. Cards on `--bg-card`. Hover state: `--bg-hover`.
- Bars / lines / dots representing **direction** use `--signal-up` / `--signal-down`.
- Threshold-crossed alerts use `--signal-warn`. Pin/zero-gamma uses `--signal-pin`.
- **Brand pink is allowed only on:** logo, primary CTA, hero ambient lighting on landing. **Never on a data point.** If a bar is colored `--brand`, you're wrong.
- **Greek-by-color** palette only used in multi-greek combo charts (Thalex-style) — never mix with the signal palette in the same panel.

---

## Landing page (`index.html`)

Reference: Orchid.ai for animation + theme toggle, Linear for typographic pacing, Stripe-grade restraint.

### Sections (top → bottom)

```
[1] Nav (sticky, h:64px)
    └─ logo  ·  product · methodology · pricing-gate  ·  [theme toggle] · "Activate via flowjob.id"

[2] Hero (h:100vh)
    ├─ animated background (see "Background animation" below)
    ├─ headline (h1, 80-96px Inter 500, tight tracking)
    │   "Read the Dealer."
    ├─ subhead (h2, 24px Inter 400, --ink-muted, max-width 720px)
    │   "Dealer-positioning intelligence + forced-flow forecasts for 0DTE SPX & NDX. Built for graduates of the flowjob.id options-flow bootcamp."
    ├─ primary CTA: "Open Dashboard" → /dashboard.html
    ├─ secondary CTA: "Read the methodology" → anchor #methodology
    └─ floating chip pinned bottom-right: "● Live · 5847.62 SPX · DPI 78.4 PEAK"
       (data-driven, ticking every 2s with mock numbers, signals system at work)

[3] Live preview (h:auto)
    └─ Glassmorphic panel showing miniature dashboard preview — 3 charts side-by-side:
       Spot mini, GEX mini, Charm clock mini. NOT iframes. NOT screenshots. Live SVG with mock data.
       Caption: "Below: GEX profile, charm clock, forced-flow scenarios — sampled at 15:31:42 ET."

[4] Methodology (h:auto)
    └─ 3-column grid, each column a methodology pillar:
       ─ "Dealer state" (column 1) — short copy, mini gamma diagram
       ─ "Charm clock" (column 2) — short copy, mini polar diagram
       ─ "Forced flow" (column 3) — short copy, mini bar diagram with sign
       Each diagram is a real D3 SVG, ~120×120px, signal-colored

[5] Pipeline (h:auto)
    └─ Horizontal flow diagram (use SVG):
       OPRA / GLBX  →  Ingest  →  NATS  →  Compute  →  Postgres + Redis  →  /ws/live  →  Dashboard
       Each node is a card with subcaption (latency budget, throughput).
       Animated traveling dots along the path (CSS @keyframes), super subtle.

[6] Differentiator (h:auto)
    └─ Side-by-side comparison table:
       │            │ Other tools  │ FlowGreeks            │
       │ Surface    │ Dealer state │ Dealer ACTION         │
       │ Tickers    │ 3,500+       │ SPX + NDX (0DTE only) │
       │ Forced flow│ —            │ $-notional forecast   │
       │ Charm clock│ —            │ Intraday velocity zones│
       │ Replay     │ —            │ ✓ + backtest engine   │

[7] Access gate (h:auto, NOT pricing)
    └─ Dark card centered, max-width 640px:
       Headline: "Access is gated to flowjob.id bootcamp graduates."
       Subhead: "Order-flow + options-flow bootcamp graduates receive an API key on activation. No standalone purchase. No free tier."
       CTA: "Apply to flowjob.id" → links to flowjob.id

[8] Footer (h:auto)
    └─ minimal: logo · status (●Live · backend health) · methodology · contact · © FlowGreeks
```

### Background animation (landing only — implementations to choose from)

**Pick ONE.** Document choice in README. Constraints: 60fps, < 5% CPU at idle, scales 1440-2560 viewport, looks great in BOTH dark and light theme.

**Option A — Gradient mesh (recommended, lightest):**
- Three blurred radial gradients positioned absolute in hero
- Each gradient: `--brand` / `--signal-pin` / `--signal-info` at very low alpha (0.10-0.15)
- CSS `@keyframes` animating `transform: translate3d()` over 30-60s loops with different timings
- Add `filter: blur(80px)` for the ambient lamp effect
- Light theme: same gradients but lower alpha (0.05-0.08) so it doesn't overwhelm white surface

**Option B — WebGL noise field:**
- Single fullscreen canvas
- Fragment shader = simplex noise + time-driven offset, output mapped to brand-pink → background gradient
- Library: `three.js` or hand-rolled (one shader, ~30 lines)
- Heavier (~3-5% CPU on M-series, more on Intel), but truly premium

**Option C — Particle network (Orchid-style):**
- Canvas with 60-100 floating dots, connected by lines when within 120px
- Dots drift slowly (0.2-0.5 px/frame), wrap at edges
- Color: `--ink-faint` for dots and lines at 0.3 alpha
- Mouse position influences a small radius around it (gentle attraction)

The hero CTA must remain perfectly readable above the animation in both themes. If contrast drops below WCAG AA, soften the animation in that region (mask gradient over the headline).

### Theme toggle (`js/theme.js`)

- Toggle button top-right of nav, sun/moon icon (Lucide)
- On click: `document.documentElement.dataset.theme = next` → CSS variables flip
- Persist in `localStorage.flowgreeks_theme`
- On initial load: read `localStorage`, fallback to `prefers-color-scheme`
- 300ms ease-out transition on `--bg-base`, `--ink-base`, `--line` (NOT on data colors)
- Charts on the landing preview must re-render axis colors on theme change (subscribe to a custom `themechange` event)
- **Dashboard ignores theme** — `dashboard.html` forces `data-theme="dark"` regardless of localStorage

---

## Layout (`dashboard.html`)

Three top-level zones. **No tabs, no sidebars-with-icons-only, no collapsing menus.** Everything visible at all times because traders glance, they don't navigate.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ TOPBAR  (h:56px)                                                            │
│ [logo] [SPX|NDX toggle] [session clock 15:47:22 ET] [regime pill] [user]    │
├──────┬──────────────────────────────────────────────────────────────────────┤
│      │ HERO ROW (h:240px)                                                   │
│      │ ┌─────────────────────────────┬──────────────────┐                   │
│      │ │ SPOT CHART                  │ DPI GAUGE         │                  │
│ SIDE │ │ (lightweight-charts)        │ (radial, 0-100)   │                  │
│ NAV  │ │ + level overlays            │ + 5-component     │                  │
│      │ │ + spot tape                 │   breakdown       │                  │
│ 64px │ └─────────────────────────────┴──────────────────┘                   │
│      ├──────────────────────────────────────────────────────────────────────┤
│      │ INTEL ROW (h:300px) — 3 columns equal                                │
│      │ ┌─────────────┬──────────────┬─────────────────┐                     │
│      │ │ GEX PROFILE │ CHARM CLOCK  │ FORCED FLOW     │                     │
│      │ │ (D3 bars)   │ (polar SVG)  │ (scenario rows) │                     │
│      │ └─────────────┴──────────────┴─────────────────┘                     │
│      ├──────────────────────────────────────────────────────────────────────┤
│      │ TAPE ROW (h:flex, fills remaining)                                   │
│      │ ┌─────────────────────────────────┬──────────────────┐              │
│      │ │ FLOW TAPE (virtualized)         │ SIGNAL LOG       │              │
│      │ │ — 0DTE SPX/NDX, last 200 rows   │ — alerts firing  │              │
│      │ └─────────────────────────────────┴──────────────────┘              │
└──────┴──────────────────────────────────────────────────────────────────────┘
                                            STATUSBAR (h:24px) — ws conn, ts
```

Sidebar is icon-rail only, 64px wide, vertical alignment for: dashboard / replay / backtest / alerts / api-keys / settings. No expand-on-hover. Keyboard `cmd+1..6` jumps between routes.

**Command palette:** `cmd+k` opens dialog overlay, lists actions (toggle SPX/NDX, jump-to-strike, copy-shareable-link, switch-spot-vs-futures-view).

---

## Required visualizations — exact specs per panel

### 1. SPOT CHART (hero, leftmost)

- **Library:** TradingView lightweight-charts (CDN: `unpkg.com/lightweight-charts/dist/lightweight-charts.standalone.production.js`)
- **Series:** 1-second OHLC candles, last 60 minutes visible, full session scrollable
- **Overlays (priceLines + custom layer):**
  - Spot price line — solid `--ink-high` 1px, label "SPOT 5847.62" right-aligned
  - Zero gamma — dashed `--signal-pin` 1px, label "ZG 5862.5"
  - Call wall — dashed `--signal-up` 2px, label "CW 5900"
  - Put wall — dashed `--signal-down` 2px, label "PW 5800"
  - Pin strike (when active) — solid `--signal-pin` 2px with subtle glow
- **No volume pane.** No moving averages. No oscillator subplot.
- **Watermark bottom-right:** "SPX · 0DTE · session 15:47:22 ET"
- Background `--bg-card`, no grid lines, only horizontal price-axis ticks at `--ink-faint`.

### 2. DPI GAUGE (hero, rightmost)

- **Custom SVG**, NOT a library default
- **Geometry:** semicircular gauge, 0 (left) to 100 (right), arc thickness 14px
- **Fill:** linear gradient, transitions through `--signal-up` (low) → `--ink-base` (mid) → `--signal-down` (high). Current value's needle position glows with `drop-shadow(0 0 12px <color-at-position>)`.
- **Reading:** giant tabular number 56px in arc center: `78.4`. Below, label "DPI COMPOSITE" 11px uppercase muted.
- **Below gauge** — 5 mini horizontal bars (5px tall each) for the 5 components, each labeled, each with own value:
  - Net Gamma Sign · -1.00
  - Charm Velocity · 0.82
  - Vanna Sensitivity · 0.61
  - Time-to-Close · 0.73
  - Flow Concentration · 0.88
- Each mini-bar fills from 0 to its value, color-coded by sign.

### 3. GEX PROFILE (intel row, leftmost)

- **D3 horizontal bars**, strikes on Y axis (high → low, descending), centered around spot
- **Bar value:** `gex_notional` in $M. Positive = right of center axis, color `--signal-up` 0.85 alpha. Negative = left of center axis, color `--signal-down` 0.85 alpha.
- **Bar magnitude > $500M** → full alpha + 1px `--ink-high` border
- **Right of each bar:** strike label `5850 C` mono 11px, then notional `$840M` mono 11px color-matched
- **Horizontal lines drawn across full width:**
  - Spot — `--ink-high` solid 1px, label "SPOT" right edge
  - Zero gamma — `--signal-pin` solid 1px, label "ZG"
  - Pin strike — `--signal-pin` dashed 2px (when pin active)
- **Hover state:** bar lifts to full alpha, tooltip shows `strike · side · dealer_pos · gamma · gex_notional`. Tooltip background `--bg-subtle`, 1px `--line` border.
- **No axis labels** (they're redundant given bar labels).
- Background `--bg-card`. Padding `--sp-4` all sides.

### 4. CHARM CLOCK (intel row, middle)

- **Custom SVG polar chart** — clock face metaphor, 24-hour
- **Outer ring:** session hours 09:30 → 16:00 ET, with current time marker (small triangle pointing inward at current position)
- **Inner radial bars:** charm intensity per 15-min bucket through the session, color ramped by zone:
  - WEAK → `--ink-faint`
  - RISING → `--signal-info`
  - PEAK → `--signal-warn`
  - FADING → `--ink-muted`
  - PIN → `--signal-pin`
- **Center label:** current zone in giant text — e.g., `PEAK` 32px, with sub-label "charm velocity 0.82" mono muted
- **Outside the ring at PEAK position:** annotation "expected window 11:45 → 14:30 ET"
- This is a hero/signature chart for FlowGreeks. Make it look earned. No generic donut chart vibe.

### 5. FORCED FLOW (intel row, rightmost)

- **NOT a chart — it's a structured table-like panel**, the differentiator visualization
- 4 rows, each = a scenario:
  ```
  ┌────────────────────────────────────┬──────────────┐
  │ Spot +0.5% in 30m                  │ -$1.56B      │
  │ ▶ forced  -$1.84B   charm aid +$280M  net pressure │
  ├────────────────────────────────────┼──────────────┤
  │ Spot +1.0% in 60m                  │ -$2.91B      │
  │ ▶ forced  -$3.42B   charm aid +$510M  net pressure │
  ├────────────────────────────────────┼──────────────┤
  │ Spot -0.5% in 30m                  │ +$1.56B      │
  ├────────────────────────────────────┼──────────────┤
  │ Vol +1pt in 60m                    │ -$130M       │
  └────────────────────────────────────┴──────────────┘
  ```
- **Net pressure column:** giant mono number, color by sign (`--signal-down` if negative i.e. dealers must SELL, `--signal-up` if positive)
- **Sub-row:** smaller breakdown of forced + charm-aid — showing how net was derived
- This panel must read instantly: "if X happens, dealers must do Y to the tape."

### 6. FLOW TAPE (tape row, leftmost, ~70% width)

- **Library:** plain HTML table inside virtualized scroll (use a lightweight virtualization lib via CDN — or hand-roll, list is bounded to 200 rows)
- **Columns:** `time · symbol · side · strike · qty · price · premium · agg · tag`
- **Row coloring:**
  - Aggressor BUY → left border 2px `--signal-up`
  - Aggressor SELL → left border 2px `--signal-down`
  - Tag SWEEP → mono tag chip with `--signal-warn` background-15% + `--signal-warn` text
  - Tag BLOCK → chip with `--signal-info`
  - Tag OPENING → chip with `--signal-pin`
  - Tag REPEAT → chip with `--ink-muted`
- **Premium column** uses tabular mono, right-aligned, k/M/B suffix (`$1.72M`, `$3.02M`)
- **Latest row at top.** New rows fade-in from top with subtle 200ms `--bg-hover` background flash.
- **No row striping** (unprofessional for terminal aesthetic).

### 7. SIGNAL LOG (tape row, rightmost, ~30% width)

- Reverse-chronological list of fired alerts
- Each row:
  ```
  15:31:40   DPI_ABOVE      DPI composite crossed 75 (now 78.4)        [crit]
  15:30:12   CHARM_ZONE     Charm zone transitioned RISING → PEAK      [warn]
  15:28:55   PIN_PROB       Pin probability 5850 > 45% (47% live)      [warn]
  ```
- **Severity dot** at row start: `--signal-down` (crit) / `--signal-warn` (warn) / `--ink-muted` (info)
- Timestamp mono `--ink-faint`. Kind label mono `--ink-muted`. Message in `--ink-base`.
- Hover row → background `--bg-hover`, click → opens detail dialog with full alert payload (mock the dialog).

---

## Three dashboard states (three HTML files)

**`dashboard.html`** — default, mid-session, neutral regime, DPI ~50, no pin yet. Use this to show the "calm" appearance.

**`dashboard-shortgamma.html`** — short-gamma regime, DPI 78, charm zone PEAK, forced flow heavily negative. The dashboard should *feel* tense — more red bars, the regime pill in topbar pulsing softly, signal log dense.

**`dashboard-pinned.html`** — last 90 minutes, pin engine active, top pin strike 5850 with 47% probability. Pin annotation prominent on spot chart, charm clock center reads "PIN", GEX profile shows pin strike highlighted, flow tape shows repeated activity at 5850.

These three states are mock data variations — not three different dashboards. Same layout, different data. Use a `data.js` with three exports (`STATE_OPEN`, `STATE_SHORTGAMMA`, `STATE_PINNED`) and a query param `?state=pinned` switches between them.

---

## Mock data shape (`data.js`)

Match this shape exactly — it's derived from the production OpenAPI contract:

```javascript
export const STATE_OPEN = {
  ts_ns: 1748464042000000000n,
  symbol: "SPX",
  spot: 5847.62,
  basis_smooth: 4.18,
  fut_front_sym: "ESH6",
  net_gex: -2_140_000_000,
  zero_gamma: 5862.5,
  call_wall: 5900,
  put_wall: 5800,
  expected_mv: 28.4,
  regime: "SHORT_GAMMA",                 // SHORT_GAMMA | LONG_GAMMA | NEUTRAL
  charm_zone: "PEAK",                    // WEAK | RISING | PEAK | FADING | PIN
  charm_velocity_raw: 0.0184,
  dpi: {
    composite: 78.4,
    net_gamma_sign: -1,
    charm_velocity: 0.82,
    vanna_sensitivity: 0.61,
    time_to_close_decay: 0.73,
    flow_concentration: 0.88,
  },
  flow_pulse: { gamma: 0.71, charm: 0.84, vanna: 0.42, total: 0.74 },
  pin: {
    active: false,
    window_mins: 0,
    top_strike: 5850,
    top_probability: 0.0,
    candidates: []
  },
  strikes: [
    { strike: 5750, side: "P", dealer_pos: -1840000, iv: 0.184, gamma: 0.0042, charm: -0.012, vanna: 0.18, gex_notional: -180_000_000 },
    { strike: 5800, side: "P", dealer_pos: -3120000, iv: 0.171, gamma: 0.0098, charm: -0.024, vanna: 0.31, gex_notional: -520_000_000 },
    { strike: 5825, side: "P", dealer_pos: -2680000, iv: 0.162, gamma: 0.0146, charm: -0.041, vanna: 0.39, gex_notional: -610_000_000 },
    { strike: 5850, side: "C", dealer_pos: -4210000, iv: 0.155, gamma: 0.0192, charm: -0.058, vanna: 0.44, gex_notional: -840_000_000 },
    { strike: 5850, side: "P", dealer_pos: -3940000, iv: 0.155, gamma: 0.0188, charm:  0.054, vanna: 0.41, gex_notional: -790_000_000 },
    { strike: 5875, side: "C", dealer_pos: -2110000, iv: 0.149, gamma: 0.0134, charm: -0.039, vanna: 0.36, gex_notional: -440_000_000 },
    { strike: 5900, side: "C", dealer_pos:  1820000, iv: 0.146, gamma: 0.0091, charm: -0.022, vanna: 0.27, gex_notional:  380_000_000 },
    { strike: 5925, side: "C", dealer_pos:   980000, iv: 0.142, gamma: 0.0048, charm: -0.011, vanna: 0.19, gex_notional:  210_000_000 },
    { strike: 5950, side: "C", dealer_pos:   410000, iv: 0.138, gamma: 0.0023, charm: -0.005, vanna: 0.12, gex_notional:   95_000_000 },
  ],
  forced_flow_scenarios: [
    { label: "Spot +0.5% in 30m", forced_notional: -1_840_000_000, charm_aid: +280_000_000, net_pressure: -1_560_000_000 },
    { label: "Spot +1.0% in 60m", forced_notional: -3_420_000_000, charm_aid: +510_000_000, net_pressure: -2_910_000_000 },
    { label: "Spot -0.5% in 30m", forced_notional: +1_280_000_000, charm_aid: +280_000_000, net_pressure: +1_560_000_000 },
    { label: "Vol +1pt in 60m",   forced_notional:   -640_000_000, charm_aid: +510_000_000, net_pressure:   -130_000_000 },
  ],
  flow_tape: [
    { ts: "15:31:42", symbol: "SPX", side: "P", strike: 5825, qty: 1840, price:  4.20, premium:   772800, aggressor: "BUY",  tag: "SWEEP"   },
    { ts: "15:31:38", symbol: "SPX", side: "C", strike: 5850, qty: 2210, price:  7.80, premium:  1723800, aggressor: "SELL", tag: "BLOCK"   },
    { ts: "15:31:35", symbol: "SPX", side: "P", strike: 5850, qty:  920, price:  6.15, premium:   565800, aggressor: "BUY",  tag: "OPENING" },
    // ... add 30-50 more rows for visual density
  ],
  alerts: [
    { ts: "15:31:40", kind: "DPI_ABOVE",  message: "DPI composite crossed 75 (now 78.4)",        severity: "crit" },
    { ts: "15:30:12", kind: "CHARM_ZONE", message: "Charm zone transitioned RISING → PEAK",      severity: "warn" },
    { ts: "15:28:55", kind: "PIN_PROB",   message: "Pin probability 5850 > 45% (47% live)",      severity: "warn" },
    // ... 10-15 more
  ],
};
```

---

## Library choices (use these exact ones)

| Need | Use | CDN |
|---|---|---|
| Spot chart | TradingView lightweight-charts | `unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js` |
| Custom SVG charts (GEX, charm, gauge) | D3.js v7 | `cdn.jsdelivr.net/npm/d3@7` |
| Tooltips | Floating UI | `cdn.jsdelivr.net/npm/@floating-ui/dom@1.6.0/dist/floating-ui.dom.umd.min.js` |
| Iconography | Lucide static SVGs | inline copy from `lucide.dev` |
| Fonts | Google Fonts | Inter + JetBrains Mono variable |

**DO NOT use:** Recharts, Chart.js, ApexCharts, Plotly, Highcharts. They produce generic "business chart" output. We need custom-grade.

---

## Out of scope

- React, Vue, Svelte. **Pure HTML/CSS/JS only.**
- Build tooling (no Vite, Webpack, Rollup).
- Backend wiring. Mock data only.
- Mobile / responsive layout. Banner on viewports < 1280px wide.
- Dashboard theme toggle. **Landing has theme toggle; dashboard is dark-only.**
- Authentication UI. The dashboard is reached *after* auth via flowjob.id; mock the user pill in topbar with `nakim@flowjob.id`.
- Settings / preferences screen. Out of scope for mockup.
- Replay or backtest screens. Landing + dashboard only.
- Real pricing tiers. Access gate section is gated-to-flowjob.id messaging only.

---

## Acceptance criteria

A frontend developer should be able to look at the mockup and answer all of these without asking:

**Landing:**
1. What animation runs in the hero background? (Option A/B/C — document choice in README)
2. How does theme toggle persist across page loads? Across landing → dashboard navigation?
3. What's the hero headline + subhead copy? CTA text?
4. What charts appear in the live preview section? Are they real SVGs or images?

**Dashboard:**
5. What font is used for numbers vs labels? (mono vs sans)
6. What color is a positive GEX bar? Negative? Threshold-crossed?
7. What does the charm clock look like in PEAK vs PIN zones?
8. How is forced flow visualized (it's not a chart)?
9. What animation plays when a new flow tape row arrives?
10. What does hover state look like on a GEX bar? On a flow tape row?
11. What's the keyboard shortcut for command palette? For SPX↔NDX toggle?
12. How does the "shortgamma" state visibly differ from the "open" state?
13. What library renders the spot chart? The GEX profile?

**Cross-cutting:**
14. Where is Thalex's chart aesthetic visible in the dashboard? (axis treatment, line weight, hover crosshair)
15. Where is Orchid's polish visible on the landing? (animation, theme, typography pacing)

If any of these is ambiguous, the mockup is incomplete.

---

## Production assets to deliver alongside

In `README.md` of the mockup folder, include:

1. **Library decision rationale** — why lightweight-charts not Recharts, why D3 not visx, why your background animation choice (A/B/C), etc.
2. **Reference attribution** — for each chart panel, name the reference site you drew from and the specific element you adopted (e.g., "GEX profile axis treatment from thalextech.github.io/greeks; key-level annotations from spotgamma.com")
3. **Token reference** — full `tokens.css` content with one-line comment per token, both dark and light overrides documented.
4. **Screenshots** at 1920×1080:
   - `landing-dark.png` (default theme)
   - `landing-light.png` (toggled)
   - `landing-hero-detail.png` (hero animation captured mid-frame)
   - `dashboard.png` (default state)
   - `dashboard-shortgamma.png` (regime variant)
   - `dashboard-pinned.png` (pin engine active)
5. **Annotated screenshot** — `dashboard-annotated.png` with callouts marking each panel + the visual rule applied + the reference it draws from.
6. **Production handoff notes** — what the React port should know:
   - Which charts are easy to port to React+D3 (GEX, gauge, charm clock)
   - Which charts use a library that already has React bindings (lightweight-charts has `react-lightweight-charts`)
   - Where state lives (page-local vs would-be-global zustand)
   - How theme toggle is wired (CSS variables flip — trivial in React)
   - How to swap mock data → real API: every component reads from a single `useDashboardState()` hook in the React port

---

## Final word

This mockup must look like it was designed by someone who **uses** options-flow tools daily, not someone who watched a tutorial.

For the dashboard: every chart must answer "what does this tell a 0DTE trader they couldn't read in two seconds elsewhere?" If a panel doesn't survive that question, cut it. The Thalex chart family is the closest visual target — match it, then add the FlowGreeks annotations (forced flow, charm zones) on top.

For the landing: it has to feel like Orchid's polish but speak the language of dealer flow. Hero says "Read the Dealer." with confidence — the animation backs it without screaming. Theme toggle works flawlessly. The access gate makes it clear this isn't a SaaS sign-up flow — it's an enclave.

Thalex chart aesthetic × Orchid landing polish × Bloomberg density × Linear interaction quality × SpotGamma domain language. Ship the mockup.

