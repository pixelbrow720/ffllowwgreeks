# HANDOFF — FlowGreeks workspace consolidation

> Read this before doing anything in a new Claude Code session.
> Source-of-truth ranking: this file > [CLAUDE.md](CLAUDE.md) > [backend/HANDOFF.md](backend/HANDOFF.md) > [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md) > git log.

## Session 2026-05-29 (current)

### Done (uncommitted — ready to commit)

End-to-end smoke pipeline replay→compute→`dealer_state_1s` proven for the first time on real Databento ticks. Two P0 plumbing bugs surfaced and fixed; three deferred bugs documented for next session.

- **Python bridge load verified.** `backend/scripts/dbn_to_postgres.py` had finished prior to session start. Postgres `ticks` hypertable end state: **211,395,854 rows, 27 chunks, 2.4 GB**, range 2026-02-02 → 2026-02-12 (9 trading days). Per-day SPX+NDX × {Quote, Trade, OI} all populated except day 02-10 NDX quotes partial (3.4M vs ~10M; NQ.FUT mbp-1 ZIP only 72MB), day 02-12 NDX OI absent (statistics file 118KB partial), day 02-13 absent (NQ.FUT auth_account_locked 403). Coverage adequate for smoke; gaps are OPRA-blocked.
- **Bug fix #1 — `bus.Publisher` rejected `TickTypeOI` silently.** `backend/internal/bus/publisher.go::subjectFor` only handled Quote + Trade for options; OI ticks raised "unsupported tick type". With OI rejected, `dealer.PositionTracker.SeedFromOI` never ran → empty snapshot → aggregator short-circuited at `cmd/compute/main.go:420-423` → no state row written. Fix: added `SubjectTickOI` (`backend/internal/bus/subjects.go`) + case in `subjectFor`. Test contract updated in `publisher_test.go::TestSubjectFor/option_oi_unsupported` → renamed to `option_oi` and asserts the new subject.
- **Bug fix #2 — `dealer.PositionTracker` had no concurrency guard.** Doc said "single-threaded by design" but `cmd/compute` runs writers (NATS callback `handleTick`) and readers (aggregator `runAggregator`) on different goroutines. Fatal `concurrent map iteration and map write` panic surfaced 4 rows into smoke #3. Fix: wrapped all `pos` map access in `backend/internal/dealer/position.go` with `sync.RWMutex`. Writers (`SeedFromOI`, `Apply`, `PruneExpired`) take write lock; readers (`Get`, `Snapshot`) take read lock. Concurrency contract comment updated.
- **Smoke run #4 (post-fix) succeeded.** Replay published 57,505 (= 31,267 quote+trade + 26,238 OI) for 2026-02-12 11:29-14:35 UTC SPX. Compute stable, no panic, state writer flushed continuously at 5 rows / 5s = exactly 1 Hz × 1 symbol. **Final `dealer_state_1s`: 250 rows, 1 symbol, 4m09s span at 1 Hz.** Pipeline plumbing proven end-to-end.
- **Build state:** `go vet ./...` clean; `go test ./... -count=1 -timeout 120s` 17/17 packages green post-fix.
- **`.gitignore`:** added `tmp/` (untracked Go binary outputs from this session) + `.kilo/state/`. Removed `tmp/compute.exe` + `tmp/replay.exe` from index (they were checked in by accident).

### Deferred bugs for next session (numeric output is currently zero)

The 250 rows landed in `dealer_state_1s` carry zero values for spot, NetGEX, DPI, Pulse, Pin — plumbing works, math doesn't. Three blockers:

1. **Replay reader can't reconstruct `Tick.FuturesContract[12]`.** `backend/internal/replay/reader.go::scanTick` reads from the `ticks` table, but the schema (`backend/scripts/migrations/000002_ticks_hypertable.up.sql`) has no `futures_contract` column. Every reconstructed futures tick has empty `FuturesContract[12]` → `bus.Publisher.subjectFor` rejects with "future tick missing FuturesContract" (528,913 / 586,418 rejects in smoke #4). Result: basis tracker empty → `pipelineSpot` returns 0 → all dollar-denominated metrics collapse. **Fix shape:** derive contract symbol from `(symbol, ts)` using CME front-month conventions — SPX→ES, NDX→NQ, front month is the next quarterly H/M/U/Z after `ts`. Either (a) compute in `scanTick` and stuff into `t.FuturesContract`, or (b) add a `futures_contract VARCHAR(12)` column to `ticks` schema + backfill from a Python helper before replay.
2. **`cmd/compute` aggregator wall-clock vs event-time mismatch.** `runAggregator` and `fillGreeks` (`backend/cmd/compute/main.go:551, 412`) use `time.Now()` for `TimeToExpiryYears`. Replay ticks Feb 2026 → expiries are months in the past relative to wall clock → every TTE is 0 → all Greeks zero → NetGEX/DPI/Pulse all zero. Live ingest is fine; replay needs a "virtual now" wired through. **Cleanest fix shape:** replay binary publishes a `clock.<sym>` heartbeat at event-time cadence; aggregator subscribes and uses the heartbeat ts for TTE; falls back to `time.Now()` if no heartbeat seen for N seconds. Also persist this `now` into the `dealer_state_1s.ts` field so replays don't all collapse onto the same wall-clock minute (current rows landed at 2026-05-29 ~11:51-11:55 UTC, not at the 2026-02-12 event time).
3. **`state.spx.gex` JSON exceeds NATS 1 MiB max payload.** Once strike cache passes ~600, the per-tick JSON > 1 MiB → spurious `nats: maximum payload exceeded` warns at 1 Hz. **Doesn't block** state writer (it writes a flat row, not the full JSON). Options: (a) tighten state to top-N strikes by |dealer_pos|, (b) raise NATS `max_payload` in `deploy/docker-compose.yml`, (c) split per-strike to a parallel subject + clients merge.

### Next session menu

- **A: Fix deferred bugs #1 + #2 → re-smoke with non-zero math.** Highest signal. Once spot is non-zero and Greeks are non-zero, can finally calibrate DPI/Charm/Pin against historical data without OPRA. ~3-5h.
- **B: Frontend Sprint 1 (`web/`)** — typed fetcher from openapi.yaml, WS client with reconnect, migrate 4-5 dashboard panels off mock data. ~3-4h.
- **C: Token migration in `web/`** — 98 occurrences of `signal-up`/`signal-down`/`signal-warn` → `accent-short`/`accent-long`/`accent-warn` per the new tailwind config. Pure search-and-replace. ~30 min.
- **D: Contact Databento support** to unlock the account; OPRA-dependent verification stays blocked indefinitely without it.

### To start fresh in a new session

```
cd C:\FLOWGREEKS
git status                                # see uncommitted bus + dealer + PROGRESS changes
git log --oneline -10                     # last commits ending at 400b0f6
```

Commit pending; suggested message:
```
fix(bus,dealer): publish OI ticks + guard PositionTracker against races

- bus: add SubjectTickOI + TickTypeOI case in subjectFor (was silently
  unsupported, blocking PositionTracker.SeedFromOI in cmd/compute)
- dealer: wrap PositionTracker.pos with sync.RWMutex (cmd/compute drives
  writers and readers on separate goroutines despite the "single-threaded"
  doc; surfaced as a fatal map race during smoke #3)
- tests: TestSubjectFor updated to assert new oi subject
- docs: PROGRESS.md session log + 3 deferred-bug next-actions
```

## Session 2026-05-28

### Done (committed)
- Wide-range Databento DBN pull script + 9-day historical archive (commit `fbd17aa`). Plan C executed for 78 trading days; got 9 full days + 1 partial before the account auto-locked (twice). Root cause: pull script designed as a 780-call loop instead of one wide call per schema. DBN archive lives under `backend/data/databento/`.
- Math validation extension to 108 parity tests (9 days × 2 roots × 6 snapshots), 321,108 strikes covered, 100% PASS at p99 < 1e-4 vs scipy reference. 11/11 BS invariants under hypothesis n=200. 19 smile gallery PNGs. Docs: [backend/docs/methodology/parity-9day.md](backend/docs/methodology/parity-9day.md), [greeks-parity.md](backend/docs/methodology/greeks-parity.md), [property-tests.md](backend/docs/methodology/property-tests.md), [smile-gallery.md](backend/docs/methodology/smile-gallery.md).
- Multi-agent integration audit landed (commit `a4d545d` types skeleton + later in session). Produced [docs/INTEGRATION_PLAN.md](docs/INTEGRATION_PLAN.md) (15 items, 4 P0), [docs/integration/contract-drift.md](docs/integration/contract-drift.md), [docs/integration/websocket-contract.md](docs/integration/websocket-contract.md), [docs/integration/type-mapping.md](docs/integration/type-mapping.md), [docs/design/dashboard-redesign-proposal.md](docs/design/dashboard-redesign-proposal.md), [docs/methodology/research-paper.md](docs/methodology/research-paper.md) (1036 lines).
- P0 integration fixes applied:
  - **C1**: openapi `DELETE /api/alerts/rules/{id}` declared under apiKeyAuth (was undocumented public).
  - **C2**: WS endpoints (`/ws/live`, `/ws/replay/{id}`) wired behind `apikey.Middleware`.
  - **C4**: tailwind `accent.{short, long, warn}` tokens added in `web/tailwind.config.ts`, mapped to `--accent-short` / `--accent-long` / `--accent-warn` per CLAUDE.md color rule.
- P1 security: per-IP token-bucket rate limit at the root, before auth, via `apikey.IPMiddleware` mounted in `backend/cmd/api/main.go`. Closes credential-stuffing window before the per-key bucket can fire.
- Docs cleanup audit (commit `fbd17aa`): trimmed `docs/README.md`, `docs/ROADMAP.md`, `docs/PROGRESS.md`. Stripped `design-reference/` references from 7 files (folder doesn't exist in this consolidated workspace). 3 file deletions blocked by rm permission denial — listed in [docs/_cleanup-audit.md](docs/_cleanup-audit.md).

### Discovered / blocked (still relevant)
- **Replayer smoke (`cmd/replay_dbn`)**: dbn-go v0.9.1 cannot decode the DBN v1 InstrumentDef format Databento served us. Pipeline blocked until either a v1 fallback decoder is added in the Go side, or definition files are re-pulled with v3 (depends on account unlock). Python bridge `scripts/dbn_to_postgres.py` is the chosen workaround; it has now finished — see Session 2026-05-29.
- **Databento account locked twice** during the wide-range pull. Vendor support contact pending. Same hard blocker as before.
- **3 file deletions blocked by rm permission denial** — see [docs/_cleanup-audit.md](docs/_cleanup-audit.md).

## Workspace just consolidated (2026-05-28)

Previously the project was split across three locations causing cognitive tax:

```
C:\Users\ollama\Documents\!!!!!\flowgreeks\          (Go backend)
C:\Users\ollama\Documents\!!!!!\flowgreeks-mockup3\  (HTML mockup)
C:\Users\ollama\Documents\!!!!!\flowgreeks-academy\  (HTML curriculum)
C:\dev\flowgreeks-web\                                (Next.js frontend)
C:\Users\ollama\Documents\FLOWGREEKS\                 (deprecated Python backend)
```

Now consolidated at `C:\FLOWGREEKS\`:

```
C:\FLOWGREEKS\
├── backend/             ← Go (was Documents\!!!!!\flowgreeks)
├── web/                 ← Next.js (was C:\dev\flowgreeks-web). Design implementation lives here.
├── docs/                ← workspace-level cross-cutting docs
├── tmp/                 ← local Go binary outputs (gitignored)
└── archive/
    └── python-legacy/   ← deprecated Python backend (was Documents\FLOWGREEKS)
```

(The original `flowgreeks-mockup3` and `flowgreeks-academy` HTML references were not consolidated into this workspace; those folders are no longer needed since the production frontend in `web/` owns its own design tokens.)

The original folders are **still on disk** at their old locations as a safety net. Once the user has verified the consolidation works end-to-end (backend builds + frontend runs), they should manually delete the originals.

## What's done in `backend/`

Backend is **production-grade plumbing** — M0–M9 complete + post-M9 hardening tracks A–H + deep review (30 findings, 21 fixed) + production-proven hardening + auth pivot to API keys + 2026-05-29 P0 plumbing fixes (bus OI publish, dealer race).

See [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md) for the full log. Highlights:

- Math core: BS pricing, IV solver Brent, analytical Greeks, Lee-Ready classifier, GEX aggregator, basis tracker, DPI 5-component, Charm Clock zones, Flow Pulse 3-line, Pin engine, What-If simulator, narrative engine
- 5-layer defense-in-depth (API-key middleware, per-key rate limit, body/WS read caps, security response headers, audit log + metrics + alert rules)
- Benchmarks zero-alloc on hot path: BS 105ns, Greeks 259ns, IV 1µs, GEX 5.2µs/200-strike
- CI: test + lint + security (staticcheck, govulncheck) + nightly ws_stress 1000c/60s
- Replay→compute→state plumbing **smoke-verified end-to-end** on 9 days × 2 roots × 211M ticks (2026-05-29). Numeric values currently 0 — see deferred bugs above.

**Hard blocker (vendor side):** Databento OPRA account locked. Without unlock cannot:
- Live verify SPX/NDX option strikes populate end-to-end
- Calibrate DPI / Charm / Pin priors vs ground truth
- Backtest signal validation against real `dealer_state_1s` data

GLBX (futures) verified end-to-end. OPRA bootstrap fix written per Python reference, awaits unlock for verification.

## What's done in `web/`

Frontend is **~35% complete**. See [web/README.md](web/README.md).

Done:
- Next 14 + Tailwind + Radix + Recharts + framer-motion bootstrap
- Landing 9 sections (Nav, Hero, Marquee, Manifesto, Modules, Pipeline, DashboardPreview, Pricing, Footer)
- Dashboard layout with 3 horizontal-slider scenes (Pulse / Levels / Tape)
- 11 dashboard components rendering mock data shaped after `backend/docs/openapi.yaml`

Pending:
- Types codegen from openapi.yaml
- Typed fetcher + zustand stores + TanStack Query
- WebSocket client + reconnect + heartbeat
- 13 deep-dive routes (alerts, webhooks, api-keys, openapi, simulator, replay, backtest, dpi, charm-clock, flow-tape, walls, signals, settings)
- Connect to backend real (everything still mock)
- Auth flow consuming flowjob.id API key
- Error boundaries, skeletons, empty states
- Vitest + Playwright E2E
- Vercel deploy

## Known UX feedback from user

> "aku jujur suka visualiassi landing page nya tapi pas masuk ke dashboard serasa HELL NAHH"

Dashboard needs redesign pass. Likely culprits (unconfirmed until rendered):
- 9 charts at once = no focal point. 0DTE traders need ONE dominant metric (DPI? Forced flow notional?), not democratic info dump
- Color discipline likely violated (CLAUDE.md mandates monochrome with earned accents; dashboard uses brand pink ambient)
- Density too high — `2fr` / `3fr` row split with 4 panels each may feel compressed at 1920×1080

Concrete next step on UX: run `npm run dev`, screenshot every scene, write structured critique with redesign proposals before touching code.

## Known research gaps from user

> "aku merasa kurang dari awal jujur aja dari math/quant validation, dan UX seperti visualisasi"

Math/quant validation can be advanced **offline** (no OPRA needed):
1. Property-based tests for math invariants (gamma symmetry, charm sign, theta < 0, vega > 0, IV solver convergence) — DONE 2026-05-28 (108 parity tests, 11/11 invariants).
2. Cross-validate Greeks vs `py_vollib` — DONE 2026-05-28 (parity p99 < 1e-4 vs scipy).
3. Synthetic chain scenario assertions ("spot drop 1% in short-gamma → DPI must rise, charm zone shifts, forced flow negative") — pending.
4. Competitor methodology cross-check doc — DONE 2026-05-28 (`docs/methodology/research-paper.md`).

Calibration vs ground truth + empirical backtest both **need OPRA unlock**.

## flowjob.id integration (Node.js side, kawan's project)

Backend has migration 0008 with `api_keys` table + `internal/apikey/` package with `Generate`, `HashSecret`, `Middleware`, `RateLimiter`, `AuditSink`. The plaintext secret format and hash spec live in [backend/SECURITY.md](backend/SECURITY.md) and [backend/docs/reference/02-auth.md](backend/docs/reference/02-auth.md).

**Recommended integration pattern:** shared Postgres database. flowjob.id (Node.js) generates secrets and INSERTs hashed rows directly into `api_keys` — no service-to-service auth needed. FlowGreeks Go binary just reads from the same table.

Implementation pending. The TypeScript port of `apikey.Generate` + `HashSecret` needs to be specced and handed to the friend working on flowjob.id. See research gap #6 in [CLAUDE.md](CLAUDE.md).

## What to do next session

See the "Next session menu" under **Session 2026-05-29** above. Recommended priority is **Menu A** (fix deferred bugs #1 and #2) — once spot and Greeks are non-zero, the existing 211M ticks become useful for offline calibration. That unblocks math validation work without waiting for OPRA.

## Quick-start checklist for the next Claude

1. Read [CLAUDE.md](CLAUDE.md)
2. Read this file
3. Verify workspace state: `cd C:\FLOWGREEKS` then `git log --oneline -10`
4. `git status` — review the uncommitted bus + dealer + PROGRESS changes from 2026-05-29
5. If user asks about a backend file: `cd backend` first, then read its own CLAUDE.md / HANDOFF.md
6. Ask user which menu item (A/B/C/D) to work on — don't start without direction
