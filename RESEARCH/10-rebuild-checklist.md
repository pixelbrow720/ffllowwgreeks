# 10 · Rebuild Checklist

> **Use this if you're starting over.** Ordered. Each step has a "definition of done" so you know when to advance.
> Estimates assume one engineer working solo, dropping into the existing codebase. Halve the time if multi-agent.

---

## Phase 0 · Foundations (~2-4h)

### 0.1 · Decide what stays
- [ ] Read [`01-product-vision.md`](01-product-vision.md). Confirm SPX+NDX 0DTE scope.
- [ ] Read [`05-math-model.md`](05-math-model.md). Confirm DPI/Charm/Pin/Forced-flow stay.
- [ ] Read [`02-current-state.md`](02-current-state.md). Decide: keep backend, rewrite frontend? Keep both, redesign? Scrap and rewrite?

### 0.2 · Decide the layout
Pick ONE pattern from these, or invent your own:
- **Bookmap-style** — center spot ladder, side panels.
- **SpotGamma-style** — vertical strike ladder dominates, everything compact.
- **Bloomberg-style** — top bar + 4-quadrant grid + bottom blotter.
- **Single-canvas** — one big interactive canvas with overlays.

Sketch on paper. Don't code until the layout is decided.

### 0.3 · Decide on chrome
Two options:
- **Keep landing-aligned**: brand pink ambient, glassmorphic panels, font-display hero numbers. ([`07-landing-page.md`](07-landing-page.md), [`08-design-system.md`](08-design-system.md)).
- **Pure terminal**: drop brand pink everywhere except CTA, drop glass effects, drop hero gradients. Bloomberg-feel.

The data colors (3 accents + monochrome) stay either way.

---

## Phase 1 · Backend audit (~1-2h)

### 1.1 · Validate compute math
- [ ] Run `go test ./...` — must be 19/19.
- [ ] Run `make demo-up`, then `make calibrate` against the 9-day archive. Record output.
- [ ] Inspect `data/calibration/<date>.json`. Compare percentiles to spec defaults in `internal/dealer/dpi.go` and `internal/dealer/charm_clock.go`. If wildly different, refit.

### 1.2 · Wire calibrated thresholds
- [ ] Edit `cmd/compute` invocation: `--calibration-config data/calibration/<date>.json`.
- [ ] Update `tmp/run-compute.ps1` to pass the flag.
- [ ] Re-smoke. Compare DPI distribution before/after — should now span 0-100 instead of pegging at 70+.

### 1.3 · Wire pin engine `min_probability` gate
- [ ] Currently parsed but unused. Edit `internal/dealer/pin.go` to take a `MinProbability` config field.
- [ ] Apply in `EvaluatePin` to suppress `pin.active=true` when prob < threshold.
- [ ] Add tests.

### 1.4 · Decide on NDX strategy
- [ ] If OPRA unlock pending: hide NDX toggle on dashboard with `(coming soon)` label.
- [ ] If OPRA unlock done: re-ingest NDX 9-day archive, validate `dealer_state_1s` populates.

---

## Phase 2 · Frontend rebuild (~3-6h, depending on layout choice)

### 2.1 · Layout container
- [ ] Edit `web/src/app/dashboard/page.tsx` to your chosen layout.
- [ ] Verify in Playwright audit: `tmp/dashboard-audit/audit.js`.
- [ ] Page height MUST be ≤1080. No body scroll.

### 2.2 · Panel implementations
For each panel listed in [`06-dashboard-spec.md`](06-dashboard-spec.md), copy or rewrite to match your layout:
- [ ] RegimeStrip
- [ ] RailNav (or remove if not in your layout)
- [ ] SpotChart
- [ ] GEXProfile
- [ ] DPILive
- [ ] KeyLevels
- [ ] PinPanel
- [ ] DPITimelineLive
- [ ] SignalLog

Data contracts at [`06-dashboard-spec.md`](06-dashboard-spec.md). DON'T touch `web/src/lib/api/*` or `web/src/lib/ws/*`.

### 2.3 · Replay-vs-live mode toggle
Brow's complaint about flickering spot is rooted in unpaced replay. Add a control:
- [ ] In RegimeStrip or RailNav, add a "Speed" pill: 1x / 10x / unpaced.
- [ ] Pipe to a frontend-only setting that adjusts snapshot store throttle.
- [ ] Live mode (when OPRA unlocks) ignores the setting.

### 2.4 · Empty / loading / error states
- [ ] Every panel must have a non-pink empty state.
- [ ] Loading: monochrome skeletons.
- [ ] Error: small accent-warn chip + ink-faint copy.

### 2.5 · Audit pass
- [ ] Run `tmp/dashboard-audit/audit.js` — verify 1080px exact, 0 console errors.
- [ ] Send screenshots to Claude with the prompt from earlier session. Apply Claude's audit feedback.
- [ ] `npm run lint` zero warnings, `npm run build` clean.

---

## Phase 3 · Operations (~2h)

### 3.1 · Smoke run end-to-end
- [ ] `make demo-up` (postgres + redis + nats + api + synth_state).
- [ ] Verify dashboard loads at http://localhost:3000.
- [ ] Replace synth with replay: stop synth, start `cmd/compute` + `cmd/replay -Speed 10` for canonical day 2026-02-12.
- [ ] Verify dashboard shows real Feb-2026 numbers.

### 3.2 · Calibration walk
- [ ] `make calibrate` against full 9-day archive.
- [ ] Commit calibration JSON to `data/calibration/`.
- [ ] Update `cmd/compute` startup script to use it.

### 3.3 · API key provisioning
- [ ] `cmd/api` admin endpoints at `:9090`. Set `ADMIN_TOKEN` in env.
- [ ] Mint a dev key via `POST /admin/keys`.
- [ ] Verify dashboard auth works with the key.

### 3.4 · Persist across reboots
- [ ] Document `make demo-up` start order.
- [ ] Document `tmp/run-*.ps1` scripts (api, compute, replay) for non-make workflows.

---

## Phase 4 · Vendor unblock (parallel, no engineering)

- [ ] Email Databento support. Reference account, request OPRA unlock.
- [ ] Once unlocked: validate live ingest matches replay numbers within 0.5%.
- [ ] Schedule daily DBN backfill cron.

---

## Phase 5 · Polish (post-launch)

- [ ] Real backtest engine (currently absent).
- [ ] Spot-history granularity tiers (1s / 1m / 1h).
- [ ] Time-machine GEX strike replay (`/api/history-strikes/{symbol}`).
- [ ] 21st.dev Sidebar component for nav (replace static rail).
- [ ] WCAG contrast audit.
- [ ] Pre-launch external pentest.

---

## Don't do these unless asked

- ❌ Mobile / responsive support.
- ❌ Crypto / FX / equity products.
- ❌ Charting indicators (RSI/MACD/etc).
- ❌ Portfolio / position tracking.
- ❌ Order routing / brokerage.
- ❌ Account / billing UI in this repo (parent product owns it).

---

## Rollback plan

If a major rework breaks production:
1. `git reset --hard <last-known-good-commit>`.
2. `git push --force-with-lease` to remote.
3. Re-deploy.

Last known good after this session: `aef4424`.

---

## Multi-agent strategy (if using)

Spawn parallel subagents on independent slices:
- **Agent A:** layout container + 1-2 panels.
- **Agent B:** other 1-2 panels with different chrome.
- **Agent C:** Playwright audit script + screenshot review.

Synthesize output. Pick the best of each. Repeat.
