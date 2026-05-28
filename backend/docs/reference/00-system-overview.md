# 00 — System overview

> **Validated against:** commit `3e5b0ec` (2026-05-27).
> Every diagram in this folder is sourced from the actual code. File:line citations point at the line that proves the claim.

## What FlowGreeks is

A real-time options flow + dealer positioning intelligence platform, scope-locked to **0DTE SPX & NDX**. Tagline: "Read the Dealer."

The product surface (REST + WebSocket) is documented in [`docs/openapi.yaml`](../openapi.yaml). This folder documents *how the system actually works inside*.

## Binary topology

Four binaries communicate through one NATS bus and one TimescaleDB cluster.

```
                    ┌──────────────────────────────────────┐
                    │        Databento WebSocket           │
                    │   (OPRA.PILLAR + GLBX.MDP3)          │
                    └─────────────────┬────────────────────┘
                                      │ wire
                                      ▼
                    ┌──────────────────────────────────────┐
                    │  cmd/ingest                          │
                    │  - normalises ticks                  │
                    │  - publishes ticks.<sym>.<type>...   │
                    │  - archives to TimescaleDB (ticks)   │
                    └─────────────┬───────────┬────────────┘
                                  │           │
                          NATS ◄──┘           └──► Postgres (ticks)
                          STREAM
                          TICKS
                                  │
                                  ▼
                    ┌──────────────────────────────────────┐
                    │  cmd/compute                         │
                    │  - subscribes ticks.>                │
                    │  - solves IV per quote               │
                    │  - aggregates dealer state @ 1Hz     │
                    │  - publishes state.<sym>.gex         │
                    │  - publishes narrative.<sym>         │
                    │  - archives to dealer_state_1s       │
                    └─────────────┬───────────┬────────────┘
                                  │           │
                          NATS ◄──┘           └──► Postgres (dealer_state_1s)
                          STREAM
                          STATE / FLOW
                                  │
                ┌─────────────────┴─────────────────┐
                ▼                                   ▼
    ┌───────────────────────┐         ┌───────────────────────┐
    │  cmd/api              │         │  cmd/replay           │
    │  - REST endpoints     │         │  - reads dealer_state │
    │  - /ws/live (broker)  │         │    archive            │
    │  - /ws/replay (mgr)   │         │  - paces playback     │
    │  - alerts engine      │         │  - re-publishes ticks │
    │  - simulate / backtest│         │    onto NATS subjects │
    └─────────────┬─────────┘         └───────────┬───────────┘
                  │                               │
                  └────► clients (browser, ws_stress, smoke/e2e)
```

| Binary | Entry | Role |
|---|---|---|
| `cmd/ingest` | [`main.go:36`](../../cmd/ingest/main.go#L36) | Vendor → normalised → NATS + tick archive |
| `cmd/compute` | [`main.go:107`](../../cmd/compute/main.go#L107) | Tick → Greeks → dealer state → NATS + state archive |
| `cmd/api` | [`main.go:42`](../../cmd/api/main.go#L42) | REST + WS + auth + alerts + simulate + backtest |
| `cmd/replay` | [`main.go:36`](../../cmd/replay/main.go#L36) | Historical session → tick stream replayer |

## NATS subject hierarchy

Subjects are typed strings centralised in [`internal/bus/subjects.go`](../../internal/bus/subjects.go).

```
Streams:                                Subjects:

TICKS  (memory, short retention)        ticks.<sym>.quote.<expiry>.<strike>.<C|P>
                                        ticks.<sym>.trade.<expiry>.<strike>.<C|P>
                                        ticks.<sym>.future.<contract>

STATE  (memory, short retention)        state.<sym>.dpi
                                        state.<sym>.gex
                                        state.<sym>.charm
                                        state.<sym>.flow_pulse
                                        state.<sym>.basis
                                        state.<sym>.regime
                                        state.<sym>.pin

FLOW   (file storage, 7d retention)     narrative.<sym>
                                        state.<sym>.flow      (flow tape)

—                                       control.replay.<session_id>
```

| Subject | Producer | Consumer | Source |
|---|---|---|---|
| `ticks.>` | `cmd/ingest` | `cmd/compute` (via JS subscribe) | [`subjects.go:28`](../../internal/bus/subjects.go#L28) |
| `state.<sym>.gex` | `cmd/compute` aggregator | `cmd/api` cache + alerts engine | [`subjects.go:64`](../../internal/bus/subjects.go#L64) |
| `narrative.<sym>` | `cmd/compute` | `cmd/api` broker (kind=alert) | [`subjects.go:72`](../../internal/bus/subjects.go#L72) |

`StateKind` enum (gex / dpi / charm / vanna / flow / flow_pulse / basis / regime / pin) is at [`subjects.go:79`](../../internal/bus/subjects.go#L79).

## Storage topology

```
┌────────────────────────────────────────────────────────────┐
│ TimescaleDB (Postgres + hypertables)                       │
│                                                            │
│  ticks               (hypertable, 1d chunks, 14mo retain)  │
│  dealer_state_1s     (hypertable, 1d chunks, 14mo retain)  │
│                       7d compression                       │
│  users               (regular table)                       │
│  refresh_tokens      (regular table, family_id indexed)    │
│  schema_version      (migration tracking)                  │
└────────────────────────────────────────────────────────────┘
```

Migrations live in [`scripts/migrations/`](../../scripts/migrations/) — `0001_init` through `0007_account_lockout`.

## Hot-path latency budget

Stage targets recorded in [`CLAUDE.md`](../../CLAUDE.md):

| Stage | Budget | Achieved (bench) |
|---|---|---|
| Ingest tick → normalised | 5ms | well under |
| BS pricing | 200ns | **105ns** |
| All Greeks | 500ns | **259ns** |
| IV solver | 5µs | **1.03µs** |
| GEX aggregator (200 strikes) | 50µs | **5.2µs** |
| Lee-Ready classifier | 100ns | **71ns** |
| Position Apply | 100ns | **49ns** |
| Basis Update | 200ns | **156ns** |
| End-to-end wire → WS | <100ms | best-effort |

All hot-path components verified zero-alloc on steady state.

## Concurrency model

| Component | Pattern | Source |
|---|---|---|
| Tick fanout | NATS JetStream | `internal/bus/publisher.go` |
| WS broker | `sync.RWMutex` + per-subscriber buffered chan | [`api/state.go`](../../internal/api/state.go) |
| WS subscriber filter | `sync.RWMutex` + `snapshotFilter()` copy-on-iter | [`api/state.go:192`](../../internal/api/state.go#L192) |
| Subscriber `dropped` counter | `atomic.Uint64` | [`api/state.go:194`](../../internal/api/state.go#L194) |
| ArchiveWriter | `closeCh + done + closeOnce + atomic.Bool running` | `internal/store/archive.go` |
| StateWriter | same lifecycle redesign as ArchiveWriter | `internal/store/state_writer.go` |
| Synthetic generator | `wg.Wait(producers) → close(out)` | `internal/feed/synthetic/generator.go` |
| Replay session | `cancelMu + cancel + doneOnce sync.Once` | `internal/replay/session.go` |
| Postgres pool | one `*pgxpool.Pool` shared by auth+replay+backtest | [`cmd/api/main.go:90`](../../cmd/api/main.go#L90) ish |

## Reading order

For first-time readers, follow this order:

1. [`01-data-pipeline.md`](01-data-pipeline.md) — how a tick gets from Databento to a websocket client
2. [`03-math-pipeline.md`](03-math-pipeline.md) — Black-Scholes → IV → Greeks
3. [`04-dealer-model.md`](04-dealer-model.md) — DPI, Charm Clock, GEX, Pin, Simulator
4. [`02-auth.md`](02-auth.md) — signup/login/refresh/lockout flows
5. [`05-time-machine.md`](05-time-machine.md) — replay + backtest
6. [`06-alerts-engine.md`](06-alerts-engine.md) — rules → triggers → delivery
7. [`07-defense-in-depth.md`](07-defense-in-depth.md) — 7-layer security model
8. [`08-deployment-ops.md`](08-deployment-ops.md) — compose, k8s, shutdown
9. [`09-observability.md`](09-observability.md) — traces, audit log, metrics, alerts

## How to validate this folder

Every doc cites file paths and line numbers. To verify a claim:

```bash
# Check the cited line still says what the doc claims
sed -n '147,159p' internal/api/state.go
```

If a citation is stale, the contract is to update the doc in the same commit that moved the code. CI does not enforce this — humans do.
