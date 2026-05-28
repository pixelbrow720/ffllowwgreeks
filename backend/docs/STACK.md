# Tech Stack

> Stack decisions for FlowGreeks, with rationale. Update only when a decision changes (not when adding tools).

## Backend language: Go 1.22+

**Why Go:**
- Solo-dev velocity: simple syntax, fast compile, excellent stdlib
- Concurrency primitives (goroutines + channels) match our problem perfectly: many independent tick streams
- Performance: 10x slower than Rust hot-path, but more than enough for sub-100ms targets
- Lower context-window cost when pairing with Claude (smaller surface area than Rust)
- Production-proven for low-latency systems (NATS, etcd, CockroachDB all in Go)

**What Go gives up vs Rust:**
- ~2-3x raw compute speed for tight numerical loops
- GC pauses (mitigated: GOGC tuning, pool allocators on hot path)
- No zero-cost abstractions (rarely matters here)

**When we'd reach for Rust:** specific bottleneck functions only (e.g. SBE decoder, IV solver). Use cgo or a separate sidecar service. Not before profiler points there.

**Style:**
- `go fmt` mandatory, `golangci-lint` in CI (M3+)
- Errors: `fmt.Errorf("compute dpi: %w", err)` — wrap with context
- No panic in hot path, ever
- Prefer concrete types over interfaces unless 2+ implementations exist
- Hot path: zero-alloc steady state, pre-sized slices, `sync.Pool` for transient buffers

## Storage: TimescaleDB + Redis

### TimescaleDB (Postgres 15 + TimescaleDB extension)

**Why:**
- Hypertables = time-partitioned Postgres = native time-series performance
- SQL = no DSL learning, can use any Postgres client
- Compression on cold chunks = 10-20x storage reduction
- Continuous aggregates = pre-computed bars (1s, 1m) materialized automatically
- Single binary to ops vs Kafka+Druid+ClickHouse stack

**Alternatives rejected:**
- ClickHouse: faster for analytics but worse for live writes + smaller ecosystem
- InfluxDB: query language friction, not SQL
- Plain Postgres: no native partitioning for tick data scale

**Sizing estimate:**
- ~2M ticks/day SPX+NDX after filter
- ~80 bytes/row compressed
- ~160 MB/day raw → ~10-20 MB/day compressed
- **~7 GB/year**: trivial

### Redis 7

**Why:**
- Sub-ms read/write — perfect for live state
- Pub/sub built-in (though we use NATS for service-to-service)
- Hash data structure maps naturally to per-strike state
- Lua scripts for atomic state transitions
- Single binary, trivial ops

**Use:**
- DPI / charm / vanna current value (per symbol)
- Strike-level gamma matrix (sliding 5min window)
- WS connection metadata
- Rate limit counters

## Internal message bus: NATS JetStream

**Why over alternatives:**
- vs Kafka: 10x simpler ops, 5x lower latency, sufficient durability
- vs RabbitMQ: subject-based routing fits our domain better
- vs Redis pub/sub: actual durability + replay
- vs custom (channels): need cross-process boundaries

**Subjects naming:**
```
ticks.<symbol>.<expiry>.<strike>.<side>     ← raw normalized
quotes.<symbol>.<expiry>.<strike>           ← bbo updates
trades.<symbol>.<expiry>.<strike>.<side>    ← prints
state.<symbol>.dpi                          ← computed DPI
state.<symbol>.gex                          ← gamma exposure
state.<symbol>.charm                        ← charm velocity
state.<symbol>.flow                         ← flow tape items
narrative.<symbol>                          ← AI narrative items
```

## Frontend: Next.js 14 (App Router)

Lives in [../../web/](../../web/). TypeScript, Tailwind, Radix primitives, Recharts, framer-motion, TanStack Query. Backend's [docs/openapi.yaml](openapi.yaml) is the contract source-of-truth for types.

Decided over SvelteKit because the parent product (flowjob.id) ships on Next.js — sharing a stack reduces context-switching cost and lets the API-key cookie issued by flowjob.id flow directly into FlowGreeks pages.

## Observability

- **Metrics:** Prometheus (pull model)
- **Visualization:** Grafana
- **Logs:** structured JSON via `log/slog`, shipped to Loki (or files for MVP)
- **Tracing:** OpenTelemetry, deferred to M7+ when needed

## Hosting (recommendation, not committed)

**MVP (M0-M4):** Single beefy bare-metal box at Hetzner or OVH.
- AX102 at Hetzner: AMD Ryzen 9 7950X3D, 128GB RAM, 2x NVMe — €120/mo
- Co-locate everything: ingest, compute, Redis, TS, NATS, API
- More than enough horsepower for SPX+NDX 0DTE

**Production (M6+):** Same approach, just upsize. Add a second box for replay/backtest workers.

**When to consider colo (NY4/NY5):**
- If we're competing on sub-10ms latency vs other vendors
- Probably not needed — the 0DTE retail trader cares about insight quality more than 5ms
- Defer indefinitely

**Why NOT cloud (AWS/GCP) for MVP:**
- 3-5x cost for equivalent compute
- Network egress fees (WS streaming = expensive)
- Variable performance (noisy neighbors on tick processing)
- Lock-in

Move to cloud only if we need managed services (RDS, ElastiCache) and revenue justifies it.

## Build & deploy

- **Build:** Multi-stage Dockerfile per binary, scratch base
- **Compose:** docker-compose.yml in `deploy/` for full local stack
- **Migrations:** golang-migrate, SQL files in `scripts/migrations/`
- **CI:** GitHub Actions, run go test + golangci-lint on PR (M3+)
- **Deploy:** SSH + docker-compose pull (M2+). Move to systemd-managed binaries if container overhead matters.

## Authentication & billing

- **Auth:** opaque API keys minted by flowjob.id (the parent product). FlowGreeks does not handle signups, passwords, refresh tokens, or tier gating — those live on flowjob.id. See [`internal/apikey/`](../internal/apikey/) and [docs/reference/02-auth.md](reference/02-auth.md).
- **Billing:** owned by flowjob.id. Subscription state translates to `api_keys.rate_limit_rps` + `rate_burst` on the row, hot-swappable without redeploy.
- **Tier enforcement:** per-key token bucket in [`internal/apikey/ratelimit.go`](../internal/apikey/ratelimit.go).

## What we're explicitly NOT using

- Kafka (overkill)
- Kubernetes (overkill for single-box MVP)
- gRPC (HTTP+JSON is fine for the boundaries we have)
- Microservice frameworks (Go stdlib + chi router is enough)
- ORM (raw SQL with `sqlc` for type safety, no GORM)
- ML frameworks (deterministic models in M0-M5; ML in M9+ if signal warrants)
