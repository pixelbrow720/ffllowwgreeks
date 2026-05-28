# FlowGreeks

**Read the Dealer.** Real-time options flow and dealer-positioning intelligence for 0DTE SPX & NDX.

Backend-first solo project. Architecture, milestones, and progress live under [`docs/`](docs/).

## What's different

Other tools show **dealer state**. FlowGreeks shows **dealer action** — the forced flow they must execute next, in dollar notional, before the move happens.

## Status

Backend M0–M9 complete + post-M9 hardening shipped. Awaiting Databento OPRA unlock for end-to-end live verification. See [docs/PROGRESS.md](docs/PROGRESS.md).

## Quick links

- [CLAUDE.md](CLAUDE.md) — entry point for AI-assisted development sessions
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system design
- [docs/STACK.md](docs/STACK.md) — tech choices and rationale
- [docs/DATA_MODEL.md](docs/DATA_MODEL.md) — storage schemas
- [docs/COMPUTE_MODEL.md](docs/COMPUTE_MODEL.md) — math and dealer models
- [docs/ROADMAP.md](docs/ROADMAP.md) — phased milestones M0 → M9
- [docs/PROGRESS.md](docs/PROGRESS.md) — current state (live)
- [docs/openapi.yaml](docs/openapi.yaml) — REST surface (codegen-ready)

## Quickstart — demo stack (no Databento)

The demo profile boots the api binary plus a synthetic state publisher, so frontend and tooling work can happen without an upstream feed.

```bash
cp .env.example .env             # edit POSTGRES_PASSWORD if you like
make demo-up                     # postgres + redis + nats + api + synth_state
curl localhost:8080/health/ready # readiness probe
```

Hits to try:

```bash
# REST
curl localhost:8080/api/snapshot/spx | jq .
curl localhost:8080/api/levels/spx   | jq .

curl -X POST localhost:8080/api/simulate/spx \
  -H 'Content-Type: application/json' \
  -d '{"spot_pct_change":0.005,"duration_minutes":15,"vol_pt_change":0}' | jq .

curl -X POST localhost:8080/api/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol":"spx",
    "from":"2026-05-26T13:30:00Z","to":"2026-05-26T20:00:00Z",
    "direction":"long","cooldown_min":5,"max_hold_min":30,
    "entry":{"kind":"dpi_above","threshold":75},
    "exit":{"kind":"dpi_below","threshold":40}
  }' | jq .

# WebSocket (websocat or similar)
websocat ws://localhost:8080/ws/live <<< '{"action":"subscribe","symbols":["spx","ndx"],"kinds":["gex","narrative"]}'

# Stress
make ws-stress N=1000 D=60s

# Stop
make demo-down
```

## Quickstart — full app stack (needs Databento)

```bash
# .env must have a real DATABENTO_API_KEY
make up                          # infra
docker compose -f deploy/docker-compose.yml --profile app up -d
```

This runs ingest + compute + replay + api + the migration sidecar. Live OPRA / GLBX ingest fans out into NATS; compute publishes per-second state; api serves REST + WS.

## Local dev (no docker)

```bash
make up                          # infra only (postgres, redis, nats)
make migrate-up                  # apply migrations
make run-api                     # in one terminal
make run-compute                 # in another (consumes nats, no databento)
go run ./scripts/synth_state     # in a third (publishes state.<sym>.gex)
```

## Production checklist

`APP_ENV=production` activates [the config guard](internal/config/config.go) which refuses to boot with weak defaults. Before deploying:

- `POSTGRES_PASSWORD` ≠ the dev default
- `API_CORS_ORIGINS` set explicitly (empty = wildcard)
- `LOG_LEVEL` not `debug`
- `APIKEY_ENABLED=true` (gates `/api/simulate`, `/api/alerts/*`, `/api/backtest/*`)

API keys are minted by **flowjob.id** (the parent product surface) and presented to this binary as `Authorization: Bearer <secret>` or `X-API-Key: <secret>`. See [SECURITY.md](SECURITY.md) and [docs/reference/02-auth.md](docs/reference/02-auth.md).

## Repo layout

```
flowgreeks/
├── cmd/                  binary entrypoints (api, ingest, compute, replay)
├── internal/             private packages
│   ├── feed/             OPRA + GLBX adapters (databento + synthetic)
│   ├── greeks/           pricing, IV solver, analytical Greeks
│   ├── dealer/           positioning, GEX, DPI, charm clock, simulator, pin
│   ├── store/            timescale + redis adapters
│   ├── bus/              NATS publish/subscribe
│   ├── api/              REST + WebSocket
│   ├── replay/           historical replay
│   ├── alerts/           rule evaluator + delivery
│   ├── backtest/         strategy validation
│   ├── apikey/           API-key auth (Bearer / X-API-Key)
│   └── narrative/        rule-based event narrator
├── scripts/
│   ├── migrations/       golang-migrate sql files
│   ├── synth_state/      synthetic state publisher (frontend dev)
│   ├── ws_stress/        load generator for /ws/live
│   ├── backfill/         historical backfill (gated until unlock)
│   └── smoke/            one-shot smoke test helpers
├── deploy/               Dockerfile + docker-compose.yml + prometheus rules
└── docs/                 architecture, models, roadmap, progress, openapi
                          + reference/ (deep validated subsystem docs)
```
