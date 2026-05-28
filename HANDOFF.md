# HANDOFF — FlowGreeks workspace consolidation

> Read this before doing anything in a new Claude Code session.
> Source-of-truth ranking: this file > [CLAUDE.md](CLAUDE.md) > [backend/HANDOFF.md](backend/HANDOFF.md) > [backend/docs/PROGRESS.md](backend/docs/PROGRESS.md) > git log.

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
├── web/                 ← Next.js (was C:\dev\flowgreeks-web)
├── docs/                ← workspace-level cross-cutting docs
├── design-reference/
│   ├── mockup3/         ← HTML reference (was Documents\!!!!!\flowgreeks-mockup3)
│   └── academy/         ← HTML curriculum (was Documents\!!!!!\flowgreeks-academy)
└── archive/
    └── python-legacy/   ← deprecated Python backend (was Documents\FLOWGREEKS)
```

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

User has not committed to a single direction yet. Likely options:

**A.** Run `web/` dev server, screenshot dashboard, write UX critique + redesign proposal for the Pulse scene (the most-watched scene). ~1h work, immediate visual artifact.

**B.** Set up math validation framework: property tests + py_vollib cross-check + synthetic scenario assertions. ~2h work, solid offline foundation, defensible numbers.

**C.** Write `docs/integration/flowjob-api-keys.md` — full spec + TypeScript reference implementation for the Node.js/Next.js side. ~1h, unblocks kawan.

**D.** Write `docs/methodology/competitor-crosscheck.md` — teardown SpotGamma + GEXBot + Squeeze Metrics methodology vs FlowGreeks, identify defensible differentiators with citations. ~1h, addresses user's "research feels weak" concern head-on.

Ask the user which one — don't pick autonomously.

## Quick-start checklist for the next Claude

1. Read [CLAUDE.md](CLAUDE.md)
2. Read this file
3. Verify workspace state: `cd C:\FLOWGREEKS` then `git log --oneline -5`
4. If user asks about a backend file: `cd backend` first, then read its own CLAUDE.md / HANDOFF.md
5. Ask user which of A/B/C/D above to work on — don't start without direction
