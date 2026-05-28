# Dashboard redesign proposal

Source: `web/src/components/dashboard/`, `web/src/app/dashboard/page.tsx`
Reference: `design-reference/mockup3/`, `CLAUDE.md` cross-cutting rules
Inspected: 2026-05-28

## Current state inventory

- **Total scenes:** 3 (Pulse / Levels / Tape) — full-page horizontal slider, 300vw track, only 1 visible at a time (`web/src/app/dashboard/page.tsx:50-58`)
- **Panels per scene:**
  - Pulse: SpotChart (8/12), DPIGauge (4/12), CharmClock (7/12), DPITimeline (5/12) — 4 panels, equal visual weight
  - Levels: GEXProfile (7/12), KeyLevels + ForcedFlow stacked (5/12) — 3 panels
  - Tape: FlowTape (8/12), SignalLog (4/12) — 2 panels
- **Grid:** 12-col, `max-w-[1600px]`, `gap-4`, `px-6 pt-20 pb-28` — usable height at 1080p ≈ 888px (`page.tsx:67`)
- **Chrome:** Sidebar mini-rail (left, hover-expand, `Sidebar.tsx:65-90`); Topbar mini-pill (top, mouse < 96px expand, `Topbar.tsx:28-52`); SceneDock (bottom-center, `SceneDock.tsx:24-90`) — all three auto-collapse on mouse-leave
- **Color tokens defined** (`tailwind.config.ts:30-43`): `brand=#ff2a5b` (pink), `signal.up=#22c55e`, `signal.down=#ef4444`, `signal.warn=#f59e0b`, `signal.info=#3b82f6`, `signal.pin=#a855f7`
- **Color tokens defined by mockup3 spec** (`DESIGN_SYSTEM.md:29-44`): `--accent-short=#ef4444`, `--accent-long=#10b981` (emerald, NOT green-500), `--accent-warn=#f59e0b`. Indigo + violet are decorative-only — must never carry data semantic.

## Findings

### F1 [P0]: No persistent focal anchor — the most critical KPIs are hidden

**Evidence:**
- `Topbar.tsx:30-52` — always-visible pill shows `Spot · Net GEX · DPI · Pin` but is small (text-[12.5px]), centered top, easy to overlook
- `Topbar.tsx:55-63` — full topbar with KPI rail auto-hides when cursor leaves the top 96px band
- `page.tsx:65-83` Pulse scene — none of the 4 panels is visually dominant. SpotChart and CharmClock get nearly the same canvas area; DPIGauge (the *titular* signal) is a 4/12 sidebar.

**Why it hurts:**
A 0DTE trader under stress needs ONE anchor that says "the dealer is forced, here is the pressure." Mockup3 dashboard.html:312-315 reserves the *only* `beam-red` border for Net GEX — the framework's restraint principle says exactly one widget earns the animated border. Today's dashboard has no such anchor; eye scans 4 equal panels and bounces.

**Proposed fix:** Persistent top KPI rail (always visible, not hover-revealed). One element gets `beam-red` — Net GEX — because that is the regime indicator. DPI gauge promoted from 4-col sidebar to dominant left-column anchor.

---

### F2 [P0]: Color discipline violation — brand pink (#ff2a5b) carries data semantic

CLAUDE.md rule: "Brand pink, indigo, violet are decorative-only ambient lighting." Pink shows up as a *data* color in:

- `DPIGauge.tsx:48` — DPI ring gradient `#22c55e → #f59e0b → #ff2a5b`. Pink is the "forced" end. Should be `--accent-short` (#ef4444).
- `DPIGauge.tsx:97` — breakdown bars `from-brand-lo to-brand-hi`. Should be neutral fg gradient (`--fg-4 → --fg-1` per mockup3 `_v3.css:145`).
- `DPITimeline.tsx:18, 56-58, 107, 129-138` — composite line, gradient fill, ReferenceLine label "FORCED" all pink.
- `SpotChart.tsx:54-55, 110-111` — spot price area uses brand pink fill+stroke. The price line itself has no semantic — should be `--fg-0` neutral white.
- `CharmClock.tsx:33-40` — `charmColor()` returns `#3b82f6 → #8b5cf6 → #d946ef → #ff2a5b → #ff8aa5` — five branded data colors on one chart.
- `CharmClock.tsx:111, 183, 257, 286` — pink reference line, "NOW" label, charm gradient bar legend, "re-hedge" emphasis.
- `KeyLevels.tsx:12` — `pin: { dot: "bg-brand", label: "PIN" }`. Mockup3 reserves pink for nothing data-related; pin should be `--accent-warn` per `_v3.css:185-188`.
- Topbar/Sidebar/SceneDock — every active state glows pink. Only one element per view should earn an accent.

**Why it hurts:** Brand color saturation kills semantic signal. When the SHORT regime is active, "everything pink" reads as "everything urgent." Trader can't distinguish DPI=78 (forced) from a hover state.

---

### F3 [P0]: Violet/indigo/fuchsia used as data colors

- `SpotChart.tsx:94-99` — Zero Gamma ReferenceLine in violet (`#a855f7`). Mockup3 puts zero-gamma on the *neutral* hairline (`--line-strong`), not a data color.
- `KeyLevels.tsx:11` — `flip: { dot: "bg-signal-pin" }`. FLIP/PIN both use violet — same color, two meanings.
- `CharmClock.tsx:35-37` — three of five charm-intensity colors are decorative palette (`#3b82f6` info-blue, `#8b5cf6` violet, `#d946ef` fuchsia).
- `DPITimeline.tsx:19-21` — Charm/Vanna/Gamma component lines use violet, blue, green. None of these carry semantic meaning that earns those colors per the design system. Should be three shades of `--fg-*` (white→muted) with one earned color only when crossing a threshold.
- `dashboard/page.tsx:43` — fixed lamp glow uses `bg-brand/[0.06]`. Lamp ambient is fine per spec — but the glow tint is brand pink, not the spec'd indigo+violet conic.

---

### F4 [P1]: Tailwind theme doesn't expose the design-system token names

`tailwind.config.ts:30-43` defines `signal.{up,down,warn,info,pin}` and `brand.{...}` but no `accent.{short,long,warn}` matching the design system. This makes the violation invisible at the call site — engineers reach for `text-brand-hi` because it exists, not because it's correct.

**Fix:** Add `accent.short`, `accent.long` (=#10b981 not #22c55e), `accent.warn`. Mark `brand.*` and `signal.pin` as deprecated. Migrate component-by-component.

---

### F5 [P1]: Tabular numerics applied per-element, not globally

`globals.css:11-14` only sets `font-feature-settings` inside the `.tabnum` class. CLAUDE.md rule says it should be on `body`. Today every numeric span needs the class. Audit shows several misses: `Topbar.tsx:77` session-date span has `tabnum`, but `Sidebar.tsx:128` user name area, `FlowTape.tsx:41-55` tape rows are OK (they have `tabnum`), but `DPITimeline.tsx` Recharts tick labels do not — XAxis/YAxis ticks use plain Inter and visibly jitter when data updates.

**Fix:** Set `font-feature-settings: "tnum","ss01","cv11"` on `body` in `globals.css`, and on Recharts `tick={{ ... }}` props.

---

### F6 [P1]: Pulse scene grid has no information hierarchy

`page.tsx:67-80`:
```
grid-cols-12 grid-rows-[minmax(0,3fr)_minmax(0,2fr)]
  SpotChart    8/12  ╗
  DPIGauge     4/12  ╣ row 1 (3fr)
  CharmClock   7/12  ╗
  DPITimeline  5/12  ╣ row 2 (2fr)
```

The trader's task hierarchy for a 0DTE close is:
1. **What is the dealer regime right now?** → DPI + Net GEX (anchor)
2. **What forced flow is queued?** → Forced flow + charm velocity
3. **Where will price pin?** → Pin probability + GEX walls
4. **What is price doing?** → Spot chart (context, not the lead)

Current layout puts spot chart (item 4) as the visual anchor. DPI (item 1) is the smaller sidebar.

---

### F7 [P2]: 3-scene horizontal slider hides 2/3 of data behind keyboard chord

`page.tsx:48-58` — the slider hides Levels and Tape behind ←/→/1/2/3. Mockup3 dashboard.html:96-102 is a single page with 3-col grid (280 / 1fr / 320). Trader sees DPI, GEX walls, flow tape, signal log all at once. Today's design forces 3 mode switches per glance.

**Fix proposal:** collapse to single scene with three columns; keep scene-dock as a saved-view shortcut, not a hard mode switch.

---

### F8 [P2]: signal.up uses tailwind green-500 not emerald-500

`tailwind.config.ts:33` — `up: "#22c55e"` (green-500). Spec says `--accent-long: #10b981` (emerald-500). Visually similar at a glance but desaturated emerald reads less video-game and more terminal.

---

## Redesign proposals

### Proposal A: Single-anchor "DPI is the king" layout

```
┌────────────────────────────────────────────────────────────────────────────┐
│ ◇ SPX · 0DTE · 15:30:42        ⌘K     [share] [bell] [Replay ▶]           │ 56px topbar
├────────────────────────────────────────────────────────────────────────────┤
│ ┌────────┬──────────────┬─────────────┬────────┬───────────┬────────────┐ │
│ │ SPOT   │ NET GEX      │ DPI         │ ZERO γ │ FORCED 15m│ ES BASIS   │ │ 88px KPI rail
│ │5847.62 │ ─$2.14B ⟦red⟧│ 78.4 PEAK   │  5862  │ ─$1.84B   │  +4.18     │ │ ← Net GEX
│ │ +0.31% │ short γ      │ ▮▮▮▮▮▮▮▮░░  │ +14.4  │ SELL ES   │ smoothed   │ │   gets beam-red
│ └────────┴──────────────┴─────────────┴────────┴───────────┴────────────┘ │
├──────────────────┬──────────────────────────────────────┬──────────────────┤
│ DPI breakdown    │  GEX Profile · strikes               │ Forced Flow      │
│ (5 bars)         │  (spot crosshair)                    │ (4 scenarios)    │
│ ─────────        │                                      │                  │
│ Charm Clock      │  ↑ 5900 ▮▮▮ C-WALL  +380M           │ ─────────────    │
│ (peak in 42m)    │    5875 ▮▮         +210M            │ Key Levels       │
│ ─────────        │    5862 ─── ZERO γ ────              │ (8 rows)         │
│ DPI timeline     │    5850 ▮▮▮▮▮ PIN  ─840M  ←── spot   │ ─────────────    │
│ (composite       │    5825 ▮▮▮       ─610M             │ Signal log       │
│  + components)   │    5800 ▮▮▮▮▮ P-WALL ─520M          │ (newest first)   │
│                  │    5775 ▮         ─180M             │                  │
│ 280px            │  flexible center                    │ 320px            │
└──────────────────┴──────────────────────────────────────┴──────────────────┘
                                 [Pulse · Levels · Tape] saved views (chord)
```

**Trade-offs:**
- Pro: Mirrors mockup3 directly. Single visual anchor (Net GEX `beam-red`). All critical signals visible at once at 1920×1080. No mode switching.
- Pro: DPI gauge stays prominent on left rail; spot chart demoted to a small inline sparkline inside the KPI rail (or omitted — spot is in the KPI tile, the chart is decorative).
- Con: GEXProfile and Charm Clock both want chart canvas. At 1280px center column ≈ 1280 − 280 − 320 − 64 = 616px wide × ~720px tall. GEX profile fits 9 strikes × 28px + padding = ~290px tall, leaves room for a Charm Clock band below.
- Con: Loses the "scene" navigation drama. SceneDock becomes a saved-view shortcut, not a hard mode switch.

### Proposal B: Two-pane "Pulse + Tape" — keeps scene model, fixes color + focal

Keep the horizontal slider but reduce to 2 scenes (Pulse, Tape) and merge Levels into Pulse:

```
PULSE scene:
┌─────────────────────────────────────────────────────────────────────────────┐
│ KPI rail (Spot · NetGEX⟦beam⟧ · DPI · ZeroG · Forced · Basis)              │
├──────────────────────────┬──────────────────────────┬───────────────────────┤
│ DPIGauge   (8 col, hero) │ GEXProfile (10 col)      │ ForcedFlow + Levels   │
│ ──────────               │                          │ (6 col, stacked)      │
│ CharmClock + Timeline    │                          │                       │
│ stacked below            │                          │                       │
└──────────────────────────┴──────────────────────────┴───────────────────────┘

TAPE scene:
┌──────────────────────────────────────┬──────────────────────────────────────┐
│ FlowTape (8 col)                     │ SignalLog + DPI summary (4 col)      │
└──────────────────────────────────────┴──────────────────────────────────────┘
```

**Trade-offs:**
- Pro: Less refactor. Topbar + sidebar already exist; just promote them out of hover-hide.
- Pro: Tape gets its own scene (it's a stream — can take the full canvas).
- Con: Still some mode switching. Doesn't fully match mockup3 single-page intent.

### Proposal C: Strict mockup3 port — copy `dashboard.html` 1:1

Drop the scene model entirely. Port `design-reference/mockup3/dashboard.html` structure: top topbar (fixed, not hover-hide), 6-tile KPI rail with `beam-red` on Net GEX, 3-col body (280 / 1fr / 320), bottom statusbar. Replace recharts with hand-rolled SVG (mockup3 uses bespoke `.spine-r`, `.dpi-row`, `.zone`). Cmd+K palette via `cmdk` library.

**Trade-offs:**
- Pro: Maximum design fidelity. Every token, every component already specified.
- Pro: No new surface area. All decisions already made in `_v3.css`.
- Con: Largest refactor. Drops recharts (DPITimeline, SpotChart). Drops scene slider entirely (FlowTape moves into right rail or below GEX as collapsible).
- Con: FlowTape's 8-column wide flow rail doesn't fit a 320px right rail — would need to be re-styled to vertical stream.

---

## Recommendation

**Proposal A** for first pass. It is the smallest delta from current code that (a) restores a focal anchor, (b) fixes color discipline at the most-visible call sites, (c) eliminates the auto-hide chrome that breaks the "always know the regime" trader contract. SceneDock becomes a chord-only shortcut for saved views (still useful, but not the primary navigation).

**Phased order:**
1. Fix color tokens — add `accent.{short,long,warn}` to `tailwind.config.ts`, update `globals.css` to set tabnum on `body`. Migrate brand-pink data uses to `accent.short`. (1 day, mechanical.)
2. Remove auto-hide on Topbar; promote to fixed 56px header. Add fixed 88px KPI rail beneath (6 tiles, `beam-red` on Net GEX only). (1 day.)
3. Restructure body to 280 / 1fr / 320 three-column grid. Move DPI/Charm/DPITimeline into left rail; GEX into center; ForcedFlow/Levels/Signal into right rail. (2 days.)
4. Replace SpotChart with KPI-tile sparkline (or drop — spot is already in KPI rail). (0.5 day.)
5. Promote Proposal A as default; preserve scene-dock as saved-view chord. (0.5 day.)

## Effort estimate

- Refactor scope: 9 components touched (`Panel.tsx`, `Topbar.tsx`, `Sidebar.tsx`, `SceneDock.tsx`, `DPIGauge.tsx`, `CharmClock.tsx`, `DPITimeline.tsx`, `SpotChart.tsx`, `KeyLevels.tsx`), 1 page (`dashboard/page.tsx`), 2 config (`tailwind.config.ts`, `globals.css`)
- Days of work: ~5 dev days for Proposal A. Add ~2 days for Proposal C (full mockup3 port).
- Risk: low — all changes are presentational. Mock data shape (`web/src/lib/mock.ts`) and OpenAPI contract unchanged.
