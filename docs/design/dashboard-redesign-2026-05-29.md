# Dashboard Redesign — 2026-05-29

> Brief from user: "aku jujur suka visualiassi landing page nya tapi pas masuk ke
> dashboard serasa HELL NAHH. AKU INTINYA INGIN DASHBOARD NYA BAGUS TERSERAH MAU
> GIMANA MODELANNYA AKU SERAHKAN KE KAU."

## Verdict

The current dashboard reads like a SaaS marketing surface, not a trading
terminal. Three structural problems sit on top of a dozen smaller violations:

1. **Three-scene horizontal slider is hostile to the use case.** A 0DTE trader
   stares at this for 6+ hours. Forcing them to swipe between Pulse, Levels and
   Tape means *every* answer requires a second action. Bloomberg, SpotGamma,
   Bookmap all pick the opposite tradeoff: one dense canvas with everything in
   peripheral view, gaze moves not the layout. The slider also wastes 200vw of
   render every frame for content not on screen.
2. **No focal hierarchy.** Every panel is a `rounded-2xl` card on a soft glow,
   each with its own header pill that says `Live` / `Brand`. Eye has nothing to
   land on. The dealer-positioning headline (regime + zero gamma + DPI score)
   should be 2-3× larger than everything else and live above the chart, not
   buried inside a `<Panel>` header.
3. **Color discipline is broken.** Brand pink is the primary color of the spot
   line, the DPI gauge fill, the pin-strike crosshair, the "FORCED" label, the
   regime pill, the active sidebar item, every "Live" indicator, the DPI
   timeline area, the scene dock, the dot in the topbar Bell, the alert badge.
   Per CLAUDE.md brand pink is *decorative ambient lighting only* — the rule is
   monochrome 90% with three earned accents (`accent-short` red, `accent-long`
   green, `accent-warn` amber). The current dashboard inverts this.

Severity: structural rewrite. Surgical fixes will not get there.

## Audit, panel by panel

### `web/src/app/dashboard/page.tsx`

| Issue | Location | Severity |
|---|---|---|
| Three-scene horizontal slider — 300vw track, hidden content costs render budget every frame | `page.tsx:50-57` | Blocker |
| Brand-pink ambient lamp glow + `bg-grid` overlay in dashboard. Acceptable on landing, distracting at 14:55 ET when DPI hits FORCED | `page.tsx:43-44` | Warn |
| `xl:` columns gate density — at 1920×1080 these render fine but at the same window with devtools open the layout collapses to single-column SaaS-y mode | `page.tsx:68,74,89` | Warn |
| Page is desktop-only per CLAUDE but uses `md:` / `lg:` / `xl:` Tailwind breakpoints throughout (Topbar `hidden md:flex`, Search `hidden lg:flex`, KPI strip `hidden xl:flex`). Should pick 1920 baseline and stop | `Topbar.tsx:108,114,125` | Note |

### `Topbar.tsx`

| Issue | Location | Severity |
|---|---|---|
| Hover-to-reveal full topbar. A 0DTE trader running risk should not be playing peekaboo with their KPI strip. Always-visible, dense, fixed-height | `Topbar.tsx:36-95` | Blocker |
| KPI strip duplicated — once in always-visible compact pill, once in revealed bar. Two truth sources, identical numbers, both mediocre | `Topbar.tsx:46-85`, `125-145` | Warn |
| Brand pink shadow + glow on `Replay` button + Bell dot. Dashboard chrome should be monochrome | `Topbar.tsx:153-155` | Warn |
| "Pin" KPI uses `text-signal-pin` violet which isn't in the 3-accent palette | `Topbar.tsx:204` | Blocker (color rule) |
| Search box at `lg` and up only — useless on a desktop-only product | `Topbar.tsx:114` | Note |

### `Sidebar.tsx`

| Issue | Location | Severity |
|---|---|---|
| Hover-to-reveal sidebar with full nav slide-in. Same peekaboo problem as Topbar | `Sidebar.tsx:60-104` | Blocker |
| Brand-pink active-item gradient + glow, brand logomark, big avatar pill. Reads as a SaaS app shell, not a trading rail | `Sidebar.tsx:152-170` | Warn |
| "Pipeline · live" status block uses `text-accent-long` for everything — fine, but the WS lag value is hardcoded "42 ms" not derived from `useSocketStatus` | `Sidebar.tsx:191-200` | Warn |
| 13 nav items half of which point at unbuilt routes (Replay, Backtest, Signal Studio, Webhooks, OpenAPI...). Dashboard rail should expose only what's wired | `Sidebar.tsx:23-52` | Warn |

### `SceneDock.tsx`

| Issue | Location | Severity |
|---|---|---|
| The whole scene-dock concept is the wrong primitive — see Verdict §1. Removing the dock removes 91 LOC and a keyboard handler that fights the OS browser left/right | `SceneDock.tsx`, `page.tsx:26-38` | Blocker |
| Brand-pink active pill + brand-pink "Next" button shadow | `SceneDock.tsx:42-77` | Blocker (color rule) |

### `DPIGauge.tsx`

| Issue | Location | Severity |
|---|---|---|
| Reads `SNAPSHOT` mock — never wired to live API. Will show 78.4 forever even when backend is down | `DPIGauge.tsx:4` | Blocker (data) |
| Gradient ring `green → amber → brand-pink` — uses brand pink as the danger color. Should be `accent-long → accent-warn → accent-short` to match the rest of the system | `DPIGauge.tsx:46-48` | Blocker (color rule) |
| Brand-pink "FORCED" label below the number is the only word in the whole panel that is not data; reads as marketing copy | `DPIGauge.tsx:87` | Warn |
| Component bars are gradient brand-pink; should be monochrome with one accent for the dominant component | `DPIGauge.tsx:97` | Warn |
| Pill-tone uses `info` and `pin` tones for charm-zone state — these aren't in the palette | `DPIGauge.tsx:6-12` | Blocker (color rule) |
| `Math.abs(s.dpi.net_gamma_sign)` always `1` so the bar for "Net γ sign" is always full — the visual carries no signal | `DPIGauge.tsx:23` | Warn |

### `DPITimeline.tsx`

| Issue | Location | Severity |
|---|---|---|
| Reads `DPI_HISTORY` mock | `DPITimeline.tsx:4` | Blocker (data) |
| Composite line is brand pink, charm is violet (`#a855f7`), vanna is signal blue (`#3b82f6`), gamma is signal green (`#22c55e`). Four colors that don't exist in the palette | `DPITimeline.tsx:18-22` | Blocker (color rule) |
| `FORCED` reference label at 75 in brand pink — same marketing tone as DPI gauge | `DPITimeline.tsx:106-117` | Warn |
| 4 dashed lines on top of a gradient area = noisy. SpotGamma's DIX is one bold line; everything else is muted. Match that | overall | Warn |

### `ForcedFlow.tsx`

| Issue | Location | Severity |
|---|---|---|
| Reads `FORCED_FLOW_SCENARIOS` mock + `SNAPSHOT.pin.candidates[0]` mock | `ForcedFlow.tsx:4` | Blocker (data) |
| Pin-candidate banner uses `border-signal-pin/20` violet. Pin is the most actionable signal in 0DTE — should earn an accent (`accent-warn`), not invent a fourth color | `ForcedFlow.tsx:72-81` | Blocker (color rule) |
| The "scenarios" are mock-only. We have no `/simulator` endpoint. Either wire to spot×{-1%, 0, +1%} via the live snapshot's gamma+charm or deprecate the panel | conceptual | Warn |

### `CharmClock.tsx`

| Issue | Location | Severity |
|---|---|---|
| **Reads zero live data.** `buildSession()` runs `Math.random()` on every render. The dot color gradient is *intentionally* brand-pink at peak | `CharmClock.tsx:14-31, 33-40` | Blocker (data) |
| 5-color gradient `info-blue → violet → fuchsia → brand-pink → brand-hi` for charm intensity. None of these are palette colors | `CharmClock.tsx:33-40` | Blocker (color rule) |
| 280px tall × 720px wide = takes a full row at 1920. For a panel that displays one scalar (current charm velocity) plus a session-time decoration. Density inverted | overall | Warn |
| Footer copy hardcodes "Forced flow expected next 42m: −$1.84B" — pure mock | `CharmClock.tsx:286-289` | Warn |

### `FlowTape.tsx`

| Issue | Location | Severity |
|---|---|---|
| Reads `FLOW_TAPE` mock; no backend endpoint exists for trade-by-trade tape | `FlowTape.tsx:4` | Blocker (data) |
| `BLOCK` tag uses `text-signal-info`, `SWEEP` uses brand-pink — both off palette | `FlowTape.tsx:8-12` | Blocker (color rule) |

The flow tape is good content for the eventual product but we have no data to
fill it. Deferring the panel is the honest call — see "Judgment calls" §3.

### `KeyLevels.tsx`

| Issue | Location | Severity |
|---|---|---|
| FLIP marker uses `bg-signal-pin` violet | `KeyLevels.tsx:22` | Blocker (color rule) |
| PIN marker uses `bg-brand` brand pink | `KeyLevels.tsx:24` | Blocker (color rule) |
| Distance % column uses `text-accent-long` for above-spot and `text-accent-short` for below-spot. **This is wrong** — distance sign is not a long/short bias. Should be ink-muted | `KeyLevels.tsx:79-86` | Warn |
| Spot row has brand-pink gradient bg + border. Spot is the reference point, not an accent | `KeyLevels.tsx:60` | Warn |

### `SpotChart.tsx`

| Issue | Location | Severity |
|---|---|---|
| Spot line + fill is brand pink (`#ff2a5b`). Should be monochrome ink-high; the accents are reserved for the dealer levels | `SpotChart.tsx:82-83, 139, 141` | Blocker (color rule) |
| Zero-gamma reference line is `#a855f7` violet | `SpotChart.tsx:124` | Blocker (color rule) |
| Pin-strike crosshair is brand pink with brand-pink filled tag | `SpotChart.tsx:189-211` | Blocker (color rule) |

### `GEXProfile.tsx`

| Issue | Location | Severity |
|---|---|---|
| Spot crosshair + pin tag = brand pink | `GEXProfile.tsx:189-213` | Blocker (color rule) |
| Footer caption uses `text-signal-pin` for pin-candidate strike | `GEXProfile.tsx:322` | Blocker (color rule) |

The bar profile itself is good — `accent-long` long-gamma, `accent-short`
short-gamma, monochrome scaffold. Keep the visualization, recolor the chrome.

### `SignalLog.tsx`

| Issue | Location | Severity |
|---|---|---|
| `info` severity badge uses `signal-info` blue (line 48, 58) — not a palette color | `SignalLog.tsx:46-60` | Blocker (color rule) |

Otherwise clean. Tabular nums on, monochrome rows, accent only on severity.

### `Panel.tsx` primitive

| Issue | Location | Severity |
|---|---|---|
| `rounded-2xl` on a data panel. Terminal aesthetic = sharp; web-design-guardian flags `rounded > md` on data panels | `Panel.tsx:26` | Warn |
| `Pill` exposes `info` and `pin` tones (lines 103-104) using `signal-info` / `signal-pin` — these tones leak the off-palette colors throughout the codebase | `Panel.tsx:97-105` | Blocker (color rule) |
| Soft drop shadow `0_30px_60px_-30px` is SaaS-y; terminals are flat | `Panel.tsx:28` | Note |

## The `signal-info` / `signal-pin` decision

**Drop both. Migrate every reference to a palette color.** Justification:

- `signal-info` blue (#3b82f6) is currently used for:
  - "INFO" severity badge in alert log → that's literally the `info` semantic;
    map to `text-ink-muted` on `bg-bg-subtle` border `border-line/60`. Severity
    info is *not* an accent — only `crit`/`warn` should burn an accent.
  - "BLOCK" intent in flow tape → flow tape is being deferred (no backend
    endpoint), so the reference disappears with it.
- `signal-pin` violet (#a855f7) is currently used for:
  - Pin-strike marker in KeyLevels, GEXProfile, ForcedFlow → pin is the most
    actionable 0DTE signal we publish. Map to `accent-warn` amber. That's what
    the rule book says amber is for: "pin proximity, charm flip warning".
  - Zero-gamma line in SpotChart → that's a flip level, not a pin. Drop to
    `text-ink-muted` with a longer dash pattern; the label tag carries the
    semantics. Zero-gamma being violet is a habit from SpotGamma's brand
    palette and we don't have to inherit it.

Once these are gone we can also delete the `signal.*` token block from
`tailwind.config.ts`. **Out of scope for this redesign.** The token block
includes `signal.up` / `signal.down` / `signal.warn` which are the deprecated
predecessors of `accent-long` / `accent-short` / `accent-warn`. Token-block
removal touches landing-page code that's outside this scope; flagged as a
follow-up for Brow.

## Layout candidates considered

### A — Three-scene slider (current)
- Pros: Hides density behind progressive disclosure.
- Cons: Disclosure is the wrong move for a 6h-staring use case. Forces a click
  per question. Two of three scenes are not in peripheral vision.
- **Reject.**

### B — Tabbed scenes (Bloomberg-style F-keys)
- Pros: Familiar metaphor; preserves density per tab.
- Cons: Same root problem as A. We don't have *enough* content to need tabs.
  Bloomberg has 200+ functions; we have one product (regime + dealer +
  pin) with 8 visualizations. Tabs would be over-engineering.
- **Reject.**

### C — Single dense canvas, left rail + main canvas + right rail + bottom log strip
- Pros: All actionable signal in peripheral vision. Fixed gaze, eye scans.
  Direct lift from Bloomberg DES, SpotGamma's Pro Trader Hub, Bookmap. Matches
  the "Read the Dealer" verb — read implies scan, not navigate.
- Cons: Density is real. Needs typography + color discipline to avoid
  overload. (Constraints already enforce both.)
- **Pick.**

## Chosen layout — single dense canvas (1920×1080)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ FLOWGREEKS  SPX | NDX        SPOT 6987.45  −12.3 (−0.18%)        14:32 ET  │  56px topbar
│ pipeline · ws live · snap 0.4s          regime SHORT γ · zero γ 6995.2     │
├──────────┬──────────────────────────────────────────────────┬──────────────┤
│  left    │                                                  │   right      │
│  rail    │       SPOT + LEVELS (main focal chart)           │   rail       │
│  240px   │       ~520px tall                                │   320px      │
│          │                                                  │              │
│  DPI     │   ── call wall ── 7020 ─────────── ─ ─ ─ ─ ─    │  KEY LEVELS  │
│  72.4    │                                                  │  ladder      │
│  FORCED  │   ── spot ─────── 6987 ─━━━━━━━━━━━━━━━━━━━     │  monochrome  │
│  ┌────┐  │                                                  │              │
│  │====│  │   ── put wall ── 6960 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─    │  PIN         │
│  └────┘  │                                                  │  6985 · 64%  │
│          ├──────────────────────────────────────────────────┤              │
│  CHARM   │                                                  │  EXPECTED    │
│  PEAK    │       GEX BY STRIKE (horizontal bars)            │  MOVE        │
│  v 0.42  │       ~360px tall                                │  ±12.4       │
│  /min    │                                                  │              │
│          │   7050 ████████████ +120M  long γ  C-WALL        │  CHARM ZONE  │
│  PIN     │   7030  ████ +28M                                │  PEAK · 42m  │
│  6985    │   7000   ██ +6M                                  │              │
│  64%     │   6970 ────── center ──── −∅                     │  FORCED      │
│          │   6950          ▓▓ −18M                          │  FLOW        │
│  Σ ΔΥ    │   6930      ▓▓▓▓▓▓▓▓ −82M  short γ  P-WALL       │  −$1.8B      │
│  −1.8B   │                                                  │  next 42m    │
├──────────┴──────────────────────────────────────────────────┴──────────────┤
│  DPI TIMELINE — composite + components — 240px tall                        │
├────────────────────────────────────────────────────────────────────────────┤
│  SIGNAL LOG — flat row strip — 152px tall, scroll within                   │
└────────────────────────────────────────────────────────────────────────────┘
```

### Grid

CSS grid, three rows × three columns:

```
grid-template-columns: 240px 1fr 320px
grid-template-rows: minmax(0, 1fr) 240px 152px
```

- Topbar is a `position: fixed` 56px strip; canvas starts at `pt-14`.
- Main canvas at row 1 spans columns 1-3 horizontally only via inner split:
  left rail (col 1), main column with SpotChart + GEXProfile stacked (col 2),
  right rail (col 3). Rows 2 and 3 span the full width (DPITimeline +
  SignalLog).
- No outer padding; 1px `border-line` separators between rail/main/rail and
  between rows. Bloomberg-style cell delineation.

### Rationale (cited references)

- **Bloomberg DES single-canvas + cell delineation:** every quote, level, and
  greek is in peripheral vision. The user moves the eye, never the layout. We
  copy the cell-delineation idea but use a 3-cell rail+canvas+rail because we
  have less content than a Bloomberg function page.
- **SpotGamma Pro Trader Hub left-rail-DPI pattern:** keeps the composite
  score and its components persistent so the trader can correlate them against
  the spot path without context-switching.
- **Bookmap heatmap-style horizontal GEX bars:** their value-by-price ladder
  is the closest analog to dealer GEX-by-strike. Our existing GEXProfile
  already implements this visual; we keep it but recolor the chrome.
- **No SaaS app-shell.** No avatar, no global search, no pricing, no docs link
  in the dashboard chrome. Dashboard is a tool, not a landing page.

## Implementation map

### Files to add

- `web/src/components/dashboard/RegimeStrip.tsx` — new component, fixed
  56px topbar replacement. Always-visible, dense, no hover reveal. Spot,
  delta, regime, zero gamma, session clock.
- `web/src/components/dashboard/DPILive.tsx` — replaces DPIGauge. Wired to
  `useSnapshot`. Compact column layout for left rail.
- `web/src/components/dashboard/DPITimelineLive.tsx` — replaces DPITimeline.
  Reads DPI history via a new in-memory accumulator that piggybacks on
  `lib/api/history.ts`.
- `web/src/components/dashboard/PinPanel.tsx` — new right-rail panel. Pin
  candidate, expected MV, charm zone. Replaces the standalone ForcedFlow + the
  decorative bits of CharmClock.
- `web/src/components/dashboard/RailNav.tsx` — slim static icon rail.
  Replaces hover-reveal Sidebar. Only the routes wired to live data are
  enabled; the rest are dimmed with a tooltip.

### Files to rewrite

- `web/src/app/dashboard/page.tsx` — drop scenes, drop horizontal slider,
  drop ambient lamp glow + grid overlay. Single CSS grid.
- `web/src/components/primitives/Panel.tsx` — drop `rounded-2xl` → `rounded`
  (3px). Drop drop-shadow. Drop `info` and `pin` tones from `Pill`.
- `web/src/components/dashboard/SpotChart.tsx` — recolor: spot line to
  ink-high, fill to ink-high gradient at low opacity, zero-gamma to
  ink-muted, pin-strike crosshair to `accent-warn`.
- `web/src/components/dashboard/GEXProfile.tsx` — recolor: spot crosshair to
  ink-high, pin label to `accent-warn`.
- `web/src/components/dashboard/KeyLevels.tsx` — pin → `accent-warn`, flip
  → ink-muted. Distance % column to ink-muted (not long/short coded).
- `web/src/components/dashboard/SignalLog.tsx` — info severity to
  monochrome.
- `web/src/components/dashboard/Topbar.tsx` — replaced by RegimeStrip.
- `web/src/components/dashboard/Sidebar.tsx` — replaced by RailNav.

### Files to delete

- `web/src/components/dashboard/SceneDock.tsx` — dropping the scene metaphor.
- `web/src/components/dashboard/CharmClock.tsx` — mock-only, charm intensity
  gets a number+sparkline in the left rail (DPILive footer). The decorative
  spot+charm-dot scatter is not actionable; cut it.
- `web/src/components/dashboard/FlowTape.tsx` — mock-only, no backend
  endpoint exists for trade-by-trade tape. Brought back when the data is
  there.
- `web/src/components/dashboard/DPIGauge.tsx`, `DPITimeline.tsx`,
  `ForcedFlow.tsx` — superseded by their `*Live` replacements.

### Edge states

Every panel that subscribes to live data renders three states:

- **Loading** — monochrome skeleton sized to the final layout; no accent
  color, no spinner. `bg-bg-subtle/40 animate-pulse` block.
- **Error** — single line of `text-ink-faint` copy: `backend unreachable ·
  retrying…`. Optional `accent-warn` 8×8 dot if action is needed.
- **Empty (snapshot present, sparse data)** — explanatory copy in
  `text-ink-faint`, e.g. `waiting for first session tick · charm zone updates
  every second`. Never `No data`.

## Open for Brow's review

1. **Removing the three-scene slider is a one-way door** until you re-add the
   alternate views (deep-dive routes will re-introduce them as separate
   pages). Confirm.
2. **Dropping CharmClock + FlowTape components** is the right call given the
   data layer can't fill them. Charm zone state lives in the right rail; flow
   tape returns when there's a backend endpoint.
3. **`signal-info` → ink-muted; `signal-pin` → `accent-warn`.** Pin earns the
   amber accent because the rule book reserves amber for "pin proximity, charm
   flip warning". Confirm or push back.
4. **Killing the brand-pink lamp glow + ambient grid in the dashboard.** Both
   stay on the landing page. The dashboard is a tool, not a vibe.
5. **Topbar collapses from "hover-to-reveal full menu" to a fixed 56px
   regime strip.** Search box, Replay button, alerts bell are dropped from the
   dashboard chrome — they belong in the deep-dive routes (`/replay`,
   `/alerts`) which open in their own pages. Confirm.

Tokens left untouched (deferred to a follow-up):

- `signal.{up,down,warn,info,pin}` block in `tailwind.config.ts` — used by
  landing components per `/web/src/components/landing/Hero.tsx:150`. Removing
  the block requires landing changes which are out of scope.
- `brand.{DEFAULT,hi,lo,dim}` block — still used as decorative ambient on
  the landing page; OK per the rule book.
