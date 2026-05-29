# 09 · Known Bugs

> Snapshot at commit `aef4424`. Updated after every audit / Brow complaint.

## Severity legend

- **P0** — broken / unreadable / wrong data. Blocks shipping.
- **P1** — ugly, confusing, or sub-optimal. Blocks polish review.
- **P2** — nice to have. Park.

---

## Active

### P0-1 · Spot value flickering rapidly during replay
**Symptom:** Brow reports spot jumping `6884 → 6944` in a single second, repeating.
**Root cause hypothesis:**
1. Replay event-time advances faster than wall-clock (unpaced `Speed=0`), so 1 wall-second can carry many event-time minutes.
2. The 1-min throttle on snapshot store force-flushes on regime/charm/pin transitions; if those flip rapidly during replay, throttle effectively disables.
3. Spot history dedupe is by `HH:MM` — adjacent event-time minutes alternating produce visible flicker.

**Mitigation candidates (not yet implemented):**
- Pace replay at `Speed=10x` (default for "feels live"). Stops the flicker entirely. `tmp/run-replay.ps1 -Speed 10`.
- Drop force-flush exceptions during replay (detect via `is_replay` flag in snapshot).
- Throttle `useSpotHistory` accumulator at 1 update / 5 sec instead of dedupe-by-minute.

**Path forward:** test pacing first. If pacing is acceptable, no code change.

---

### P0-2 · Dashboard layout still feels wrong subjectively
**Symptom:** Brow has flagged the dashboard 3+ times. Audit confirms layout = 1080px exact, panels filled, no console errors. Issue is aesthetic / hierarchy.
**Path forward:** rebuild from `06-dashboard-spec.md` with a different layout than current. Candidate approaches:
- "Bookmap-style" — central spot area with depth-of-book sidebars.
- "SpotGamma-style" — strike ladder dominates, everything else compact.
- "Bloomberg-style" — top-bar + 4-quadrant grid + bottom blotter.

The data contracts are stable. Pick a layout, port the panels.

---

### P1-3 · DPI Timeline backfill works but charm zone always shows FADING
**Symptom:** charm_zone column shows FADING in most rows.
**Root cause hypothesis:** charm clock `weak_ceiling` and `peak_floor` thresholds were defaulted from spec (`1e6` / `5e6`) but the realized Feb-2026 charm velocity distribution is on a different scale. Once the session-max climbs past `peak_floor`, FADING triggers as soon as velocity dips < 75% of max. With unpaced replay, "session" is short and max stays high → mostly FADING.

**Path forward:** run `cmd/calibrate` against the archive, get fitted `charm_zone_boundaries`, feed back via `--calibration-config`. Empirically refit.

---

### P1-4 · "FADING" zone label may render brand-pink-tinted on screen
**Symptom:** Claude audit suggested FADING text appears brand pink. Code says `text-ink-muted`.
**Root cause hypothesis:** browser rendering of `text-ink-muted` (#a1a1aa) over a backdrop with brand-pink ambient produces a muted pink-tint by additive blending.
**Path forward:** add `mix-blend-normal` or solid bg behind charm zone label. Test in browser; not yet investigated.

---

### P1-5 · "Pipeline LIVE" misaligned with "LOCAL" time
**Symptom:** ~40px gap between LOCAL label and time value (per Claude audit).
**Path forward:** explicit gap-based layout in RegimeStrip clock+pipeline section. Not yet fixed.

---

### P1-6 · NDX dashboard empty
**Symptom:** Switching to NDX shows mostly empty state.
**Root cause:** Replay only feeds SPX archive currently. NDX OI archive is partial (vendor blocker on 02-12).
**Path forward:** wait for OPRA unlock. Until then, NDX is a known-empty mode. Optionally hide NDX toggle or label it `(coming soon)`.

---

### P1-7 · Pin candidate panel mostly `—`
**Symptom:** Pin section shows em-dash placeholders, not strike + probability.
**Root cause:** Pin engine triggers `pin.active=true` only when prob > 40% threshold. Real Feb-2026 sessions rarely activate pin until last 30 min.
**Path forward:** by design, but consider showing the TOP candidate even when inactive (with a ghost-tone "—" near probability) so the panel reads as "watching strikes" instead of "no data".

---

### P1-8 · Signal Log heavy on PEAK + DPI repeats
**Symptom:** 47 events, mostly CHARM_ZONE PEAK and DPI > 70 (warn).
**Root cause:** Default rules + 60-120s cooldowns + active session = these are the rules that fire constantly.
**Path forward:** either tune cooldowns up, or add a "deduplicate consecutive same-rule triggers" step in the engine. Not yet decided.

---

### P2-9 · GEX bar widths may not use full rail
**Symptom:** Audit notes bars max out around 60-80px but rail is 280px.
**Root cause:** new flex-row implementation gives strike+labels 60% of rail and bars + value 40%. The bar within that 40% is sized by `Math.abs(gex) / maxAbs * 100%`. So small-magnitude strikes have small bars. Working as designed, but feels under-utilized at far-OTM.
**Path forward:** consider log-scale bar widths so even small gex values get a visible bar.

---

### P2-10 · Walls overlap label when walls equal
**Symptom:** Both call_wall and put_wall at 7000 cause two labels at the same Y.
**Status:** Fixed in commit `aef4424`. Render single neutral "Call/Put Wall 7000" label.

---

### P2-11 · Right rail percent column clipping
**Symptom:** `+1.49%` text clipped at right edge of Key Levels.
**Status:** Fixed in commit `aef4424`. Padding adjusted.

---

### P2-12 · Charm velocity rate format
**Symptom:** `25653464.2857/min` raw number.
**Status:** Fixed in commit `aef4424`. Now `25.65M/min` via `fmtRate` util.

---

### P2-13 · Signal Log scientific notation
**Symptom:** `Net GEX -6.3e+10`.
**Status:** Fixed in commit `aef4424`. Now `Net GEX -63.0B` via `formatAlertMessage`.

---

### P2-14 · `signal-info` / `signal-pin` legacy tokens in Hero.tsx
**Symptom:** `text-signal-pin` referenced in Hero.tsx::Row toneCls; the token was retired.
**Path forward:** swap to `text-accent-warn`. 1-line cleanup. Deferred per audit.

---

## Closed (commit `aef4424`)

- ✅ DPI Timeline rendering single dot — backfill from `/api/history` on mount.
- ✅ DPI Live TTC decay row clipping — gap-3.5 → gap-2, hero 58px → 48px.
- ✅ KeyLevels RES/SUP dot color inversion — flipped per data semantics.
- ✅ KeyLevels right rail clipping — padding adjusted.
- ✅ Charm velocity raw number — `fmtRate` utility.
- ✅ Signal Log scientific notation — `formatAlertMessage` utility.
- ✅ SpotChart walls overlap — single neutral label when equal.
- ✅ RegimeStrip pin "0 · 0%" — em-dash when inactive.
- ✅ DPI Timeline empty state brand pink — replaced with monochrome scaffold.

## Closed (commit `b5336a5`)

- ✅ GEX SVG `preserveAspectRatio="meet"` crushing vertical span — rewritten to HTML flex rows.
- ✅ snapshot store update at 1Hz — throttled to 1/min with regime/zone force-flush.
- ✅ RTH cutoff 22:30 WIB → 20:30 WIB (13:30 UTC).
- ✅ Garbage spot values < 1000 leaking into chart — backend + frontend filter.

## Backend bugs (resolved)

- ✅ Replay reader can't reconstruct `Tick.FuturesContract` — added `FrontMonthContract` reconstructor in `internal/replay/futures.go`.
- ✅ `cmd/compute` aggregator wall-clock vs event-time — pipeline now uses `Pipeline.lastEventNs` atomic.
- ✅ `state.<sym>.gex` JSON > 1 MiB — top-64 strikes by `|dealer_pos|`.
- ✅ Bus rejected OI ticks — added `SubjectTickOI`.
- ✅ PositionTracker concurrent map race — added `sync.RWMutex`.
- ✅ Middleware order panic in `cmd/api` — moved IPMiddleware before any route registration.
- ✅ getServerSnapshot infinite loop — frozen empty constants.
- ✅ Default alert rules never seeded — seed 6 rules per symbol on startup.

## Hard blockers (not bugs, vendor)

- ⏳ Databento OPRA account locked. Live verification + DPI/charm/pin calibration vs ground truth blocked.
- ⏳ NDX OI archive partial on 02-12. Until OPRA unlocks, can't backfill missing days.
