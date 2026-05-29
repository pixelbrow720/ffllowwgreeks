# HANDOFF — FlowGreeks workspace consolidation

> Read this before doing anything in a new Claude Code session.
> Source-of-truth ranking: this file > [CLAUDE.md](CLAUDE.md) > [backend/HANDOFF.md](backend/HANDOFF.md) > [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md) > git log.

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

### In flight (uncommitted)
- Python bridge `backend/scripts/dbn_to_postgres.py` running in a background shell, loading 9-day DBN archive directly into the `ticks` table via the `databento` Python SDK. As of session end: day 2 of 9 in flight, ETA ~50 minutes remaining. Day 1 may be partial (a previous agent died mid-load) — needs verification once the bridge finishes.

### Discovered / blocked
- **Replayer smoke (`cmd/replay_dbn`)**: dbn-go v0.9.1 cannot decode the DBN v1 InstrumentDef format Databento served us. Pipeline blocked until either a v1 fallback decoder is added in the Go side, or the definition files are re-pulled with v3 (which itself depends on account unlock). Python bridge is the chosen workaround.
- **Databento account locked twice** during the wide-range pull. Vendor support contact pending. Same hard blocker as before, now compounded.
- **3 file deletions blocked by rm permission denial** — see [docs/_cleanup-audit.md](docs/_cleanup-audit.md) for the list.

### Next session menu
- A: Wait for the Python bridge to finish (~50 min), verify day 1 has full futures data, then smoke `cmd/replay` → `cmd/compute` → `dealer_state_1s` end-to-end. Unblocks the backtest API + replay UI surface. ~1–2h.
- B: Frontend Sprint 1 (`web/`) — typed fetcher from openapi.yaml, WS client with reconnect, migrate 4–5 dashboard panels off mock data onto the real REST/WS surface. ~3–4h.
- C: Mechanical token migration in `web/`: 98 occurrences of `signal-up` / `signal-down` / `signal-warn` → `accent-short` / `accent-long` / `accent-warn` per the new tailwind config. Pure search-and-replace. ~30 min.
- D: Contact Databento support to unlock the account; without this, OPRA-dependent verification stays blocked indefinitely.

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
└── archive/
    └── python-legacy/   ← deprecated Python backend (was Documents\FLOWGREEKS)
```

(The original `flowgreeks-mockup3` and `flowgreeks-academy` HTML references were not consolidated into this workspace; those folders are no longer needed since the production frontend in `web/` owns its own design tokens.)

The original folders are **still on disk** at their old locations as a safety net. Once the user has verified the consolidation works end-to-end (backend builds + frontend runs), they should manually delete the originals.

## What's done in `backend/`

Backend is **production-grade** — M0–M9 complete + post-M9 hardening tracks A–H + deep review (30 findings, 21 fixed) + production-proven hardening + auth pivot to API keys.

See [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md) for the full log. Highlights:

- Math core: BS pricing, IV solver Brent, analytical Greeks, Lee-Ready classifier, GEX aggregator, basis tracker, DPI 5-component, Charm Clock zones, Flow Pulse 3-line, Pin engine, What-If simulator, narrative engine
- 5-layer defense-in-depth (API-key middleware, per-key rate limit, body/WS read caps, security response headers, audit log + metrics + alert rules)
- Benchmarks zero-alloc on hot path: BS 105ns, Greeks 259ns, IV 1µs, GEX 5.2µs/200-strike
- CI: test + lint + security (staticcheck, govulncheck) + nightly ws_stress 1000c/60s

**Hard blocker:** Databento OPRA account locked. Without unlock cannot:
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
- ❌ Types codegen from openapi.yaml
- ❌ Typed fetcher + zustand stores + TanStack Query
- ❌ WebSocket client + reconnect + heartbeat
- ❌ 13 deep-dive routes (alerts, webhooks, api-keys, openapi, simulator, replay, backtest, dpi, charm-clock, flow-tape, walls, signals, settings)
- ❌ Connect to backend real (everything still mock)
- ❌ Auth flow consuming flowjob.id API key
- ❌ Error boundaries, skeletons, empty states
- ❌ Vitest + Playwright E2E
- ❌ Vercel deploy

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
1. Property-based tests for math invariants (gamma symmetry, charm sign, theta < 0, vega > 0, IV solver convergence)
2. Cross-validate Greeks vs `py_vollib` — golden tests, match within 1e-6
3. Synthetic chain scenario assertions ("spot drop 1% in short-gamma → DPI must rise, charm zone shifts, forced flow negative")
4. Competitor methodology cross-check doc — map every metric to published reference (SpotGamma, Squeeze Metrics whitepapers)

Calibration vs ground truth + empirical backtest both **need OPRA unlock**.

## flowjob.id integration (Node.js side, kawan's project)

Backend has migration 0008 with `api_keys` table + `internal/apikey/` package with `Generate`, `HashSecret`, `Middleware`, `RateLimiter`, `AuditSink`. The plaintext secret format and hash spec live in [backend/SECURITY.md](backend/SECURITY.md) and [backend/docs/reference/02-auth.md](backend/docs/reference/02-auth.md).

**Recommended integration pattern:** shared Postgres database. flowjob.id (Node.js) generates secrets and INSERTs hashed rows directly into `api_keys` — no service-to-service auth needed. FlowGreeks Go binary just reads from the same table.

Implementation pending. The TypeScript port of `apikey.Generate` + `HashSecret` needs to be specced and handed to the friend working on flowjob.id. See research gap #6 in [CLAUDE.md](CLAUDE.md).

## What to do next session

See the "Next session menu" under **Session 2026-05-28** above. Top priority is verifying the Python bridge load and smoking the replay → compute → dealer_state_1s pipeline end-to-end so the backtest API and replay UI can be unblocked.

## Quick-start checklist for the next Claude

1. Read [CLAUDE.md](CLAUDE.md)
2. Read this file
3. Verify workspace state: `cd C:\FLOWGREEKS` then `git log --oneline -5`
4. If user asks about a backend file: `cd backend` first, then read its own CLAUDE.md / HANDOFF.md
5. Ask user which of A/B/C/D above to work on — don't start without direction
