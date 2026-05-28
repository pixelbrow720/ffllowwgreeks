# Integration audit — consolidated findings

Date: 2026-05-28
Scope: 6 parallel audits to identify everything that blocks frontend/backend integration.

This is a roadmap, not a status report. Every section ends with a concrete next step.

## Sources

| # | Audit | Output | Lines |
|---|---|---|---|
| 1 | OpenAPI contract drift | [integration/contract-drift.md](integration/contract-drift.md) | 135 |
| 2 | WebSocket contract reference | [integration/websocket-contract.md](integration/websocket-contract.md) | 344 |
| 3 | TypeScript type generation | [integration/type-mapping.md](integration/type-mapping.md) | ~95 |
| 4 | Dashboard UX critique | [design/dashboard-redesign-proposal.md](design/dashboard-redesign-proposal.md) | 208 |
| 5 | DBN replayer scaffold | [../backend/cmd/replay_dbn/](../backend/cmd/replay_dbn/) | 488 (Go) |
| 6 | Methodology paper | [methodology/research-paper.md](methodology/research-paper.md) | 975 |

## Status — what's actually verified

| Area | Verified? | Evidence |
|---|---|---|
| BS pricing + IV solver | YES | 36 parity tests, 108,595 strikes, p99 < 1e-6 vs scipy |
| Backend code quality | YES | 4 review passes, 30 findings, race-clean, zero-alloc hot path |
| DBN replayer compiles | YES | `go build ./cmd/replay_dbn/` clean, `go vet ./...` clean |
| Math methodology | DOCUMENTED | research-paper.md §2 |
| DPI weights | NO | intuition-based, calibration plan §3.2 |
| Charm Clock zones | NO | heuristic thresholds, plan §3.3 |
| Pin Probability | NO | uncalibrated, plan §3.4 |
| Frontend ↔ backend wired | NO | mock data only, no real fetcher |
| WS client | NO | doesn't exist yet |
| Dashboard UX | NO | "HELL NAHH" feedback unaddressed |

## Critical findings (do these first)

### C1. Auth gap on `DELETE /api/alerts/rules/{id}` (P0)

OpenAPI spec ([openapi.yaml line 372](../backend/docs/openapi.yaml)) declares this route without `security`. Code mounts it on the protected router. Once frontend codegens types and emits a DELETE without Authorization header, production returns 401 silently.

**Fix**: 4-line edit in spec — add `security: [{apiKeyAuth: []}, {bearerAuth: []}]` and `401`/`429` responses, mirroring the POST operation.

**Where**: `backend/docs/openapi.yaml`

### C2. WebSocket has no auth gate (P0)

Both `/ws/live` and `/ws/replay/{id}` mount on the **public** subrouter. They aren't behind `apikey.Middleware`. ([cmd/api/main.go:160-175](../backend/cmd/api/main.go))

**Decision needed**: do these endpoints need auth? Yes for production. Add `apikey.Middleware` wrap.

**Risk if shipped as-is**: anyone with the WS URL can subscribe to live state without a key. Defeats the entire flowjob.id provisioning model.

### C3. Dashboard hierarchy inverted (P0)

`DPIGauge` is the titular signal but rendered as a 4/12 sidebar in the Pulse scene. Decorative `SpotChart` gets 8/12. ([design/dashboard-redesign-proposal.md F1](design/dashboard-redesign-proposal.md))

**Fix**: 1-week refactor. Three proposals on the table — recommended is "DPI is the king" single-anchor layout.

### C4. Color discipline violations across components (P0)

Brand pink, indigo, violet appearing as data colors instead of decorative. CLAUDE.md mandates monochrome default with three earned accents (short=#ef4444, long=#10b981, warn=#f59e0b).

**Root cause**: `tailwind.config.ts` exposes `brand.*` and `signal.pin` but no `accent.{short,long,warn}` tokens. Engineers reach for the wrong tokens because right tokens don't exist.

**Fix order**: token rename in config first → mechanical migration of components.

## High-priority integration tasks

### H1. Generate API types and import them (1 day)

`web/src/lib/api-types.ts` has been generated (969 lines, openapi-typescript v7.13.0). Nothing imports it yet.

**Action**: replace local `Snapshot` type in `web/src/lib/mock.ts` with imported `paths['/api/snapshot/{symbol}']['get']['responses']['200']['content']['application/json']`. Then add a typed fetcher.

**Cheapest first slice**: `LevelsResponse` — `KEY_LEVELS` mock should derive from it.

### H2. Build the WebSocket client (2 days)

Doesn't exist yet. Reference: [websocket-contract.md](integration/websocket-contract.md) has the exact JSON shapes for subscribe/unsubscribe and 4 server-emit kinds (`gex`, `narrative`, `alert`, `snapshot.replay`).

**Gotchas** (from the contract audit):
- `data.symbol` inside GEX payload is numeric `feed.Symbol` code (SPX=1, NDX=2). Envelope's `symbol` is lowercase string. Use the envelope.
- `narrative.<sym>` is **not cached**. Reconnects lose narrative events that arrived during disconnect. Document this for users.
- Drop-on-slow buffer is 256 events live + 64 events replay status. Frontend should handle missed updates as state-converging, not crash.
- Heartbeat interval is 15s.
- `state.<sym>.<kind>` is wildcarded but only `gex` is currently published. The others (`dpi`, `charm`, `vanna`, etc.) are reserved. Don't subscribe to them.

### H3. Wire dealer_state_1s historical archive (2 days)

DBN replayer (`cmd/replay_dbn`) is built and ready. Now needs:

1. Driver script: bash for-loop over `data/databento/2026-*/` calling `make replay-dbn DIR=...` with `cmd/compute` running in another terminal.
2. Verify dealer_state_1s rows land in Postgres for at least 3 days.
3. Smoke test `POST /api/backtest/run` against those 3 days. Expected: returns trades + Sharpe.

**Once verified**, run on all 78 days (sequentially, ~1 day to chew through).

### H4. Decoder layer for type mismatches (1 day)

Several spec types use int-encoded enums or `strike × 1000` convention. Frontend needs decoder/encoder helpers, not just type swap. ([type-mapping.md](integration/type-mapping.md))

Affected:
- `feed.Symbol`: SPX=1, NDX=2 → string
- `feed.Side`: CALL=1, PUT=2 → "C"/"P"
- `Strike`: int (×1000) → float USD
- `Aggressor`: BUY=1, SELL=2, UNKNOWN=0 → string
- Timestamps: ns since epoch → Date

**Where**: new `web/src/lib/decoder.ts`

## Medium-priority cleanups

### M1. Spec polish (P2 from contract drift)

- `cooldown_min` default value not applied (just declared). Spec should say `default: 5`.
- Missing 404 response on POST alerts.
- `SimulateResponse.symbol` lacks enum `[spx, ndx]`.

These are spec hygiene. Land them in same edit as C1.

### M2. Generated types hygiene

- 9 spec schemas have no corresponding mock yet — frontend will need to write them when implementing each feature.
- 5 frontend mocks have no spec equivalent (`DPI_HISTORY`, `SPOT_HISTORY`, `CHARM_CLOCK_HOURS`, `FLOW_TAPE`, etc.). Two paths:
  - **Option A**: Add backend endpoints for these (proper REST extension).
  - **Option B**: Treat them as client-side WS buffers (correct for time-series).
- `Alert` (UI feed row) vs `AlertRule` (stored definition) name collision — rename one.

### M3. UX P1 findings

- `tabnum` applied per-element instead of body. Move to root.
- Topbar + Sidebar auto-hide on mouse-leave. Bad for 0DTE traders under stress. Make persistent.
- 3-scene horizontal slider hides 2/3 of data. Mockup3 has all panels co-resident.

## Low-priority items

### L1. Calibration work — needs full 78-day archive

After H3 lands and dealer_state_1s is populated:

- DPI weights ridge regression (research-paper.md §3.2)
- Charm Clock empirical PEAK timing study (§3.3)
- Pin Probability logistic regression vs EOD outcomes (§3.4)
- Greeks parity (extend current IV-only parity to Δ, Γ, Θ, vega, charm)

**Status**: pull at ~85/780 calls (78 days × 10 schemas × 2 retries on 504/000). ETA ~12-18h to complete with current retry policy.

### L2. Methodology paper distribution

`docs/methodology/research-paper.md` (975 lines, 6,472 words) is ready for prospect review. After C1-C4 fixes land, this becomes a sales artifact.

## Recommended execution order (week-by-week)

**Week 1** (highest leverage, least scope):
- C1: spec auth fix on DELETE alerts (1h)
- C2: WS auth gate decision + implementation (1d)
- M1: spec hygiene polish bundled with C1 (included)
- H3: dealer_state_1s historical archive (2d)
- H4: decoder helpers (1d)

**Week 2** (frontend foundation):
- H1: import api-types.ts in fetchers (1d)
- H2: WS client + reconnect + heartbeat (2d)
- C4: tailwind token rename + mechanical migration (2d)

**Week 3** (UX surgery):
- C3: dashboard redesign Proposal A (5d)

**Week 4+** (calibration):
- L1: math calibration vs 78-day archive (ongoing)

## What this audit DOESN'T cover

- Production deployment topology (k8s/Hetzner box choice)
- Stripe billing on flowjob.id side (separate kawan's repo)
- Dashboard mobile/responsive — explicitly out of scope per CLAUDE.md
- ML models for signal weight refinement — post-launch optimization

## Honest read

Backend is in better shape than frontend. The integration risks aren't in the Go code — they're at the surface where TypeScript meets Go. Six audits surfaced **15 actionable items**, of which **4 are P0** and could ship integration-broken.

Fix the four P0s (C1-C4) and the path to a working dashboard becomes mechanical rather than discovery-driven.
