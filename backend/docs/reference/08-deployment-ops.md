# 08 — Deployment + ops

> Validated against commit `3e5b0ec`.
> Source: [`deploy/`](../../deploy/), [`scripts/`](../../scripts/), `cmd/api/main.go` (graceful shutdown), `.github/workflows/`.

## Compose topology

[`deploy/docker-compose.yml`](../../deploy/docker-compose.yml) — three profiles, one stack:

```
                 ┌──────────────────────────────────────────────────┐
                 │                   compose stack                   │
                 │                                                   │
                 │   ┌────────┐     ┌────────┐     ┌────────┐        │
                 │   │postgres│     │ redis  │     │  nats  │        │
                 │   │ (TS)   │     │  7     │     │ JS+M   │        │
                 │   └───┬────┘     └────────┘     └────┬───┘        │
                 │       │                              │            │
                 │       │ migration sidecar gates      │            │
                 │       │ startup via                  │            │
                 │       │ depends_on:                  │            │
                 │       │  service_completed_           │            │
                 │       │  successfully                │            │
                 │       │                              │            │
                 │       ▼                              │            │
                 │   ┌──────────┐                       │            │
                 │   │ migrate  │ (one-shot)            │            │
                 │   └──────────┘                       │            │
                 │                                      │            │
                 │   ┌──────────┐  ┌──────────┐  ┌──────────┐        │
                 │   │   api    │  │ ingest   │  │ compute  │        │
                 │   └──────────┘  └──────────┘  └──────────┘        │
                 │                                                   │
                 │   ┌──────────┐                                    │
                 │   │  replay  │  (or  synth_state in demo profile) │
                 │   └──────────┘                                    │
                 │                                                   │
                 │   ┌──────────┐  ┌──────────┐                      │
                 │   │ promethus│  │ grafana  │  ← obs profile       │
                 │   └──────────┘  └──────────┘                      │
                 └──────────────────────────────────────────────────┘
```

Profiles:

| `--profile` | Brings up | Use case |
|---|---|---|
| (default) | postgres + redis + nats + migrate | Local dev: run `go run ./cmd/api` from host |
| `app` | infra + 4 binaries (api, ingest, compute, replay) + obs | Full stack with real Databento (set `DATABENTO_API_KEY`) |
| `demo` | infra + api + synth_state + obs | Frontend dev / CI smoke — no Databento needed |
| `obs` | prometheus + grafana | Auto-on for `app` and `demo` |

## Dockerfile shape

Single `deploy/Dockerfile` shared by all four binaries:

```
ARG GO_VERSION=1.25

FROM golang:${GO_VERSION}-alpine AS build
ARG BINARY
RUN apk add --no-cache ca-certificates git
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/${BINARY} ./cmd/${BINARY}

FROM scratch
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=build /out/${BINARY} /app/${BINARY}
ENTRYPOINT ["/app/${BINARY}"]
```

Distroless `scratch` base — only the static binary + CA certs ship. No shell, no package manager, no attack surface beyond the Go runtime.

## Migration sidecar

```yaml
migrate:
  image: migrate/migrate:4
  depends_on:
    postgres:
      condition: service_healthy
  volumes:
    - ../scripts/migrations:/migrations:ro
  entrypoint: >
    migrate -path /migrations
            -database postgres://.../?sslmode=disable
            up
```

The api / ingest / compute / replay services declare `depends_on: { migrate: { condition: service_completed_successfully } }`, which holds them off until the schema is current. No application code ever runs against an old schema.

Migrations in [`scripts/migrations/`](../../scripts/migrations/):

| File | What it does |
|---|---|
| `0001_init` | `schema_version` table |
| `0002_ticks_hypertable` | `ticks` hypertable + retention |
| `0003_users` | accounts |
| `0004_dealer_state` | `dealer_state_1s` hypertable + 7d compression + 14mo retention |
| `0005_refresh_tokens` | refresh token persistence |
| `0006_refresh_token_family` | `family_id` column for reuse detection |
| `0007_account_lockout` | `failed_login_count` + `locked_until` |

## Health probes (k8s)

```
liveness:   GET /health         200, body {"status":"ok","service":"api"}
            GET /health/live    same handler — k8s canonical name
readiness:  GET /health/ready   200 when NATS+Postgres OK; 503 otherwise
                                503 with status="draining" during shutdown
```

Recommended k8s probe config:

```yaml
livenessProbe:
  httpGet: { path: /health/live, port: 8080 }
  periodSeconds: 30
  failureThreshold: 3

readinessProbe:
  httpGet: { path: /health/ready, port: 8080 }
  periodSeconds: 5
  failureThreshold: 2
  initialDelaySeconds: 5
```

Why two endpoints: liveness only signals "process up"; readiness signals "deps up". Mixing them causes restart loops when Postgres has a transient blip — readiness should fail without taking the pod down.

## Two-phase graceful shutdown

[`cmd/api/main.go`](../../cmd/api/main.go) orchestrates a deliberate shutdown sequence on SIGTERM:

```
                                 SIGTERM
                                    │
                                    ▼
              ┌─────────────────────────────────────────────┐
              │  draining.Store(true)                        │
              │  → /health/ready now returns 503             │
              │     status="draining"                        │
              └─────────────────────────────────────────────┘
                                    │
                                    │ time.Sleep(SHUTDOWN_DRAIN_DELAY)
                                    │ default 5s
                                    ▼
              ┌─────────────────────────────────────────────┐
              │  load balancer pulls instance from rotation  │
              │  (k8s readinessProbe failed twice)           │
              │  in-flight requests still served             │
              │  new requests stop arriving                  │
              └─────────────────────────────────────────────┘
                                    │
                                    ▼
              ┌─────────────────────────────────────────────┐
              │  srv.Shutdown(ctx with 15s deadline)         │
              │  - stops accepting new conns                 │
              │  - waits for in-flight to finish             │
              │  - closes idle conns                         │
              └─────────────────────────────────────────────┘
                                    │
                                    ▼
              ┌─────────────────────────────────────────────┐
              │  cleanup: close limiters, close pool,        │
              │  drain alerts engine, close NATS subscribers │
              └─────────────────────────────────────────────┘
                                    │
                                    ▼
                                  exit 0
```

The 5s drain window is critical for k8s rolling restarts — without it, the pod takes traffic until the moment `srv.Shutdown` runs, cutting requests mid-flight. `SHUTDOWN_DRAIN_DELAY=0` disables for local dev.

## Production config refusals

`cmd/api` boot refuses (`os.Exit(2)`) under `APP_ENV=production` if:

- `JWT_SECRET` empty / dev placeholder / < 32 chars
- `POSTGRES_PASSWORD` is the dev default
- `LOG_LEVEL=debug`
- `API_CORS_ORIGINS` empty

Check is in [`internal/config/config.go`](../../internal/config/config.go) `Validate()`. Dev / staging / test bypass.

To satisfy `JWT_SECRET`:

```
go run ./scripts/jwt_secret -raw >> .env
```

## CI / CD shape

Two workflows live under [`.github/workflows/`](../../.github/workflows/):

### `test.yml` — every PR + push to main

```
build:
  go mod tidy (verify clean)
  go build ./...
  go vet ./...
  go test -race -count=1 -timeout 5m ./...

lint:
  golangci-lint (gosec / gocritic / bodyclose / etc)

security:
  staticcheck ./...
  govulncheck ./...

smoke:
  needs: build
  docker compose --profile demo up -d --build
  wait /health/ready (60s budget)
  go run ./scripts/smoke/e2e
  on failure: dump compose ps + logs
  always: tear down -v
```

Race detector runs in CI because the local Windows host has no gcc. Smoke runs the full demo stack so Dockerfile drift, compose service wiring, and migration ordering are caught — failures `go test` can't see.

### `nightly.yml` — 03:00 UTC daily + on-demand

```
ws-stress:
  bring up demo stack
  wait /health/ready
  ws_stress -clients 100  -duration 30s   ← smoke
  ws_stress -clients 500  -duration 60s   ← soak
  ws_stress -clients 1000 -duration 60s   ← target
  on failure: dump /metrics + logs
  tear down
```

Three escalating tiers; any tier exiting non-zero fails the workflow. 1000-concurrent-client target matches the launch-readiness goal recorded in `docs/PROGRESS.md`.

## Smoke + scripts

| Script | Purpose |
|---|---|
| `scripts/smoke/e2e` | Walks `/health`, `/health/ready`, `/metrics`, snapshot, levels, simulate, `/ws/live`. Exits non-zero on any failure. |
| `scripts/smoke/publish` | Pumps a synthetic state into NATS for ad-hoc local testing |
| `scripts/smoke/ws` | Single-client WS connect probe |
| `scripts/synth_state` | Continuous synthetic state publisher (used by `demo` profile) |
| `scripts/ws_stress` | N-concurrent-client WS load generator |
| `scripts/jwt_secret` | crypto/rand 32-byte hex output |
| `scripts/jetstream_setup` | Idempotently creates / updates TICKS, STATE, FLOW streams |
| `scripts/backfill` | Skeleton — historical Databento fetch (waits on OPRA unlock) |

## Makefile helpers

[`Makefile`](../../Makefile):

```
make demo-up         docker compose --profile demo up -d --build
make demo-down       docker compose --profile demo down -v
make synth-state     run synth_state against running stack
make ws-stress       run ws_stress against running stack
make backfill-plan   dry-run the backfill skeleton
make jetstream-setup run jetstream_setup
make smoke-e2e       run scripts/smoke/e2e
```

## Observability ports

| Port | Service | Path |
|---|---|---|
| 8080 | api | `/metrics`, `/health`, `/health/ready`, `/health/live`, REST + WS |
| 9090 | prometheus | scrape ui (when `obs` profile) |
| 3001 | grafana | dashboards (auto-provisions Prometheus DS + flowgreeks-pipeline.json) |
| 4222 | nats | client port |
| 8222 | nats | management |
| 5432 | postgres | exposed for local dev only — do NOT expose in prod |
| 6379 | redis | same |

## What this section does **not** cover

- Metric / log / alert detail → see [`09-observability.md`](09-observability.md).
- Defense-in-depth posture → see [`07-defense-in-depth.md`](07-defense-in-depth.md).
- TLS termination — out of scope for this binary; reverse proxy / ingress concern.
