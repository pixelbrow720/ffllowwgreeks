# FlowGreeks — Read the Dealer

> **For Claude Code in any future session:** Read this file FIRST. Then read [HANDOFF.md](HANDOFF.md) and [docs/reference/README.md](docs/reference/README.md). Everything else is reference material.

## What is FlowGreeks?

FlowGreeks is a real-time options flow + dealer positioning intelligence platform, **laser-focused on 0DTE SPX & NDX**. Tagline: **"Read the Dealer."**

Differentiator vs SpotGamma / GEXBot / MenthorQ:
- **Predictive forced-flow** — translates dealer state into "what dealers MUST do next" in dollar notional
- **Charm Clock** — visualizes intraday charm decay window with directional bias
- **0DTE-only** — no 3,500-ticker bloat; deliberately narrow surface
- **Replay + Backtest** — time machine for any historical session, plus signal backtest engine
- **Desktop-only** — terminal-grade aesthetic, no mobile compromise

See competitor breakdown in personal memory: `flowgreeks_competitor_landscape.md`.

## Where this fits

FlowGreeks is one of three products that merge into [**flowjob.id**](https://flowjob.id) — alongside a quant-macro fund track and the main site (curriculum: order flow + options flow). The parent site owns user accounts, billing, and add-on activation. **This binary does not handle user signups, passwords, refresh tokens, or tier gating** — it authenticates inbound traffic against opaque API keys provisioned by flowjob.id.

## Project status

Backend complete (M0–M9 + post-M9 hardening + observability + production-readiness + deep review + auth pivot to API keys). Production frontend lives at [../web/](../web/) (Next.js 14, ~35% complete — landing + dashboard skeleton on mock data). Static visual references in [../design-reference/mockup3/](../design-reference/mockup3/).

Hard-blocked on Databento OPRA unlock for live verification + DPI/Charm/Pin calibration.

## Solo dev context

- Solo dev pairing with Claude (Code + chat). No team.
- Data feeds owned: **OPRA Pillar** (US options, all-strikes) + **CME Globex MDP 3.0** (futures incl. ES/NQ for hedging proxy + RTH/ETH session marks). 1 year of historical archive available.
- Hosting not yet decided. See [docs/STACK.md](docs/STACK.md) for recommendation.
- Latency target: best-effort sub-100ms wire-to-WebSocket, acceptable up to 1-2s for MVP.

## Tech stack (committed)

- **Backend language:** Go 1.22+ (rationale in [docs/STACK.md](docs/STACK.md))
- **Tick archive:** TimescaleDB (Postgres extension) — hypertables on tick + minute-bar tables
- **Live state cache:** Redis 7+ — sliding window state for DPI / charm / walls
- **Frontend:** Next.js 14 (App Router) — lives in `../web/`
- **Message bus (internal):** NATS JetStream for fanout between ingest → compute → distribute
- **Observability:** Prometheus + Grafana (self-hosted)
- **Auth:** opaque API keys provisioned by flowjob.id — see `internal/apikey/`

## Directory layout

```
flowgreeks/
├── CLAUDE.md               ← you are here
├── HANDOFF.md              ← last-session summary + next-step menu
├── SECURITY.md             ← reporting channel + 5-layer posture
├── README.md               ← human-facing readme
├── docs/
│   ├── reference/          ← deep validated docs, one file per subsystem
│   ├── ARCHITECTURE.md     ← high-level system design
│   ├── STACK.md            ← tech choices + why
│   ├── DATA_MODEL.md       ← schemas: ticks, bars, snapshots
│   ├── COMPUTE_MODEL.md    ← math: DPI, charm clock, simulator, pin
│   ├── ROADMAP.md          ← M0 → M9 phased milestones
│   ├── PROGRESS.md         ← live state of build (UPDATE OFTEN)
│   ├── REVIEW.md           ← review findings + fix status
│   └── openapi.yaml        ← REST contract
├── cmd/                    ← binary entrypoints
│   ├── ingest/             ← OPRA + MDP3 tick consumer
│   ├── compute/            ← greeks + DPI + charm engine
│   ├── api/                ← REST + WebSocket server
│   └── replay/             ← historical replay worker
├── internal/               ← private packages
│   ├── feed/               ← OPRA Pillar + MDP3 protocol parsers
│   ├── greeks/             ← Black-Scholes, IV solver, charm/vanna analytic
│   ├── dealer/             ← dealer positioning models, DPI, simulator
│   ├── apikey/             ← API-key auth (replaces internal/auth/)
│   ├── store/              ← Timescale + Redis adapters
│   ├── bus/                ← NATS publish/subscribe wrappers
│   └── api/                ← chi router, /ws/live, alerts handlers, etc
├── scripts/                ← migrations, backfill, ops scripts
└── deploy/                 ← Dockerfiles, compose, prometheus rules, grafana
```

## Working agreements (for Claude Code sessions)

- **Always update [docs/PROGRESS.md](docs/PROGRESS.md)** after meaningful work — that's the cross-session source of truth
- **Read before write** — verify file/code state before editing; never assume from memory
- **Match Go conventions** — `go fmt`, `go vet`, idiomatic error handling, no panic in hot path
- **Test the hot path** — Greeks computation, dealer model, WebSocket fanout: must have unit + benchmark
- **Hot path = no allocations in steady state** — pre-allocate buffers, reuse structs, `sync.Pool` where applicable
- **Latency budget per stage:** ingest 5ms, normalize 2ms, compute 30ms, fanout 10ms — total p99 < 100ms wire to WS
- **No premature abstractions** — write concrete first, extract interface only when the second concrete consumer exists
- **Comment discipline** — only when WHY is non-obvious. The user explicitly hates diff-narrating comments

## How to resume in a fresh session

1. Read `CLAUDE.md` (this file) end-to-end
2. Read `HANDOFF.md` — most recent state + next-step menu
3. Read `docs/reference/README.md` if you need a subsystem deep-dive
