# HANDOFF — replay→compute math unblocked + state payload trimmed

> Read this before doing anything in a new Claude Code session.
> Source of truth ranking: this file > [`docs/reference/`](docs/reference/) > [`docs/PROGRESS.md`](docs/PROGRESS.md) > [`docs/REVIEW.md`](docs/REVIEW.md) > [`CHANGELOG.md`](CHANGELOG.md) > git log.

## TL;DR (2026-05-29 PM, late)

**All three deferred replay bugs are closed.** Backend math now produces real, plausible output during replay AND the per-second NATS broadcast stays under the 1 MiB cap even with a fully populated 1500+ strike cache. The 211M-tick archive is finally usable for offline calibration without waiting on OPRA.

Smoke run on 2026-02-12 SPX 11:29→14:40 UTC, unpaced replay, post all three fixes: **1,515 rows in `dealer_state_1s` at 1 Hz**, span 11:30→14:39 historical event time, avg spot **6813.01**, **1,203/1,515 rows with non-zero NetGEX** (avg ≈ -54.5B notional, short-gamma regime), DPI composite climbing 60→71, charm zone PEAK by 14:30. Last minute (14:39): 324 snapshots, avg spot 6987.26, avg NetGEX -58.2B, DPI 71.52, zone PEAK. **Zero `nats: maximum payload exceeded` warns** in the entire 17-minute wall-clock run.

`go vet ./...` clean; `go test ./... -count=1` 18/18 packages green post-fix; new `internal/replay/futures_test.go` 9/9 + `cmd/compute/main_test.go` 5/5.

## What was done in this session (2026-05-29 PM)

| Fix | Files | Outcome |
|---|---|---|
| **#1** Replay reader reconstructs futures contract | `internal/replay/futures.go` (new) + `internal/replay/reader.go` `scanTick` | `FrontMonthContract(sym, ts)` derives CME front-month from `(symbol, ts)` per quarterly H/M/U/Z + third-Friday rollover (SPX→ES, NDX→NQ). Schema unchanged. Futures ticks now route through `bus.Publisher.subjectFor`; basis tracker populates; spot ≈ 6987 instead of 0. |
| **#2** Compute aggregator runs on event time | `cmd/compute/main.go`: `Pipeline.lastEventNs atomic`, new event-time threading through `runAggregator` + `fillGreeks`; `internal/dealer/dpi.go::SetSessionBounds`; `internal/dealer/charm_clock.go::SetSessionBounds` | Per-pipeline event-time high-water mark drives TTE, TTC, charm-clock window, persisted ts, published ts_ns. Live ingest unaffected. Replay no longer collapses every TTE to 0 against a 2026-05 wall clock. |
| **#3** State payload trimmed to top-N strikes | `cmd/compute/main.go` (`stateMaxStrikes`=64, `topStrikesByDealerPos` helper, wire format gains `strike_count_total`/`strike_count_returned`) + `cmd/compute/main_test.go` (new, 5 cases) | Per-tick `state.<sym>.gex` JSON stays under NATS 1 MiB cap once strike cache passes ~600. Persisted `dealer_state_1s` is unaffected (writes a flat row). Zero payload warns across 17-minute unpaced replay where the previous version spammed at 1 Hz. |
| **Side a** Eager spot seed from basis | `cmd/compute/main.go::handleTick` futures branch | First futures tick lazy-initialises `p.spot` from `basis.Snapshot` so the IV solver doesn't waste its first thousand attempts against the hardcoded 5800 fallback when replay floods quotes. |
| **Side b** Bump NATS subscriber pending limits | `cmd/compute/main.go::main` (post `nc.Subscribe`) | Default 65k msgs / 64 MiB → 8M msgs / 1 GiB via `sub.SetPendingLimits`. Without this, unpaced replay drops the option-quote flood at the slow-consumer ceiling. Live ingest stays well below. |
| Tests | `internal/replay/futures_test.go` (new, 9 cases) + `cmd/compute/main_test.go` (new, 5 cases) | Covers quarterly H/M/U/Z, expiry-day rollover (Mar 20 → ESM6), year wraparound (2026-12-31 → ESH7), unknown symbol; top-N picker no-trim/trim/zero-n/non-mutating-source/tie-break-determinism. |
| Build | `go vet ./...`, `go test ./... -count=1` | clean, 18/18 packages green. |
| Smoke | unpaced replay 2026-02-12 SPX 11:29→14:40 UTC | 1,515 dealer_state_1s rows, real math, zero payload warns. See TL;DR. |

## What is still blocked

### Deferred bugs from prior sessions

All three closed. None outstanding.

### Hard-blocked on Databento OPRA unlock (vendor-side)

Same as before — manual support recovery pending:

- Live OPRA verification (verify SPX/NDX option strikes populate end-to-end at the wire).
- Backfill execute path for missing days (02-10/12/13 partials + everything before Feb 2026).

But: **DPI / Charm Clock / Pin priors can now be calibrated offline** against the 211M-tick archive. The math is sound; only the empirical normalizer constants need fitting against realised dealer-state behaviour during the 9-day archive.

### Doable but punted

- External pentest (recommended pre-public-launch).
- `dealer.Aggregate` sort.Slice closure alloc / `cmd/compute` map clone per tick (profiler hasn't flagged).
- Race detector locally (no gcc on Windows; CI has `-race` on every PR).

## What to do next session

### Option A — Frontend Sprint 1

User's stated focus track. Now actually meaningful — the dashboard will show real numbers when wired (spot, NetGEX, DPI, charm zone, walls, pin candidates) and the per-tick NATS fan-out is bounded. ~3-4h.

### Option B — Calibrate DPI / Charm / Pin priors offline

Now genuinely unblocked. The 211M-tick archive replays to real `dealer_state_1s` against real Feb-2026 spot levels. Walk the 9 days, snapshot DPI components against realised flow, fit normalizer constants. ~3-5h initial pass; can productively run in parallel with frontend work.

### Option C — Token migration in `web/`

98 occurrences of `signal-up`/`signal-down`/`signal-warn` → `accent-short`/`accent-long`/`accent-warn` per the new tailwind config. Pure search-and-replace, palate cleanser before frontend work. ~30 min.

### Option D — Wait for OPRA, productively

When Databento unlocks: backfill historical data for missing days, run backtest validation, calibrate priors against ground truth.

## Repo orientation

- `CLAUDE.md` — read every session, always.
- **`docs/reference/`** — deep validated documentation, one file per subsystem. Read [`docs/reference/README.md`](docs/reference/README.md) for the recommended order.
- `docs/PROGRESS.md` — current phase + commit log + session-by-session decisions.
- `docs/REVIEW.md` — historical review findings, status per item.
- `CHANGELOG.md` — chronological reference.
- `docs/openapi.yaml` — API contract (post-pivot).
- `SECURITY.md` — reporting channel + 5-layer defense posture.
- `scripts/migrations/` — `0001` schema_version → `0008` api_keys.
- `scripts/dbn_to_postgres.py` — Python bridge (workaround for dbn-go v1 InstrumentDef gap).
- `data/databento/` — 9-day DBN archive (gitignored, 2.4 GB on disk).

## User constraints (durable, restate every session)

- "fully autonomous, jangan minta izin izin lagi" — execute, don't ask.
- "mau apapun itu gas aja sampe kelar" — push through.
- B. Indonesia buat chat casual, English buat code + commit.
- No mobile, no responsive.
- Color discipline applies to ALL future UI work.
- Solo dev; user pushes manually (don't `git push`).
- Tickers locked to SPX + NDX.
- FlowGreeks is an add-on inside flowjob.id; parent site owns billing + user accounts.

## Quick-start checklist for the next Claude

1. Read `CLAUDE.md`.
2. Read this file (`backend/HANDOFF.md`) and `../HANDOFF.md` (workspace level).
3. Read `docs/reference/README.md` — pick the subsystem you need.
4. `git log --oneline -10`.
5. `git status` — review the uncommitted bus + dealer + replay + compute + PROGRESS changes.
6. Ask user which menu item to work on.
7. Don't start work until user picks a direction.

## Auth pivot recap (2026-05-28)

FlowGreeks is now an **add-on inside flowjob.id**. The parent site owns user accounts, billing, and add-on activation. This binary authenticates inbound traffic with **opaque API keys** provisioned by the parent site — no signup, no password, no refresh token, no per-account lockout, no tier gating here.

Defense-in-depth, **five layers deep**:

1. Per-IP rate limit at the root (added 2026-05-28: `apikey.IPMiddleware`)
2. API-key middleware (Bearer / X-API-Key)
3. Per-key rate limit (rate + burst on the `api_keys` row)
4. HTTP body cap + per-WS read limits + security response headers
5. Audit log + Prometheus metrics + alert rules

## Earlier session (2026-05-29 AM) — plumbing-proven

| Step | Outcome |
|---|---|
| Status check on Python bridge load | 211,395,854 rows in `ticks`, 27 chunks, 2.4 GB, 9 trading days. Per-day SPX+NDX × {Quote, Trade, OI} populated except 02-10 NDX qts partial, 02-12 NDX OI absent, 02-13 absent (all OPRA-blocked). |
| **Fix #A**: `internal/bus/{publisher.go,subjects.go}` | Added `SubjectTickOI` + `case feed.TickTypeOI` in `subjectFor`. OI ticks were silently rejected → `PositionTracker.SeedFromOI` never ran → empty state. |
| **Fix #B**: `internal/dealer/position.go` | Wrapped all `pos` map access with `sync.RWMutex`. Doc claimed "single-threaded" but `cmd/compute` drives writers + readers on different goroutines → fatal map-race during smoke #3. |
| Smoke run #4 (post AM fixes) | Plumbing proven end-to-end. 250 rows in `dealer_state_1s` at 1 Hz. **All math fields zero** — closed in PM session above. |
