# 05 — Time machine

> Validated against commit `3e5b0ec`.
> Source:
> - [`internal/replay/`](../../internal/replay/) — session.go, manager.go, runner.go, reader.go, ws.go
> - [`internal/backtest/`](../../internal/backtest/) — engine.go, predicates.go
> - [`internal/api/backtest.go`](../../internal/api/backtest.go) — REST handler

## Two surfaces, one archive

```
                          ┌──────────────────────────────┐
                          │   TimescaleDB                 │
                          │   ticks (hypertable)          │
                          │   dealer_state_1s (hypertable)│
                          └──────────────────────────────┘
                                  │              │
                                  │ ticks        │ dealer_state_1s
                                  │              │
                                  ▼              ▼
                       ┌──────────────────┐  ┌──────────────────┐
                       │ replay.Manager    │  │ backtest.Engine   │
                       │ (cmd/api)         │  │                   │
                       │  - reader paces   │  │  - predicate eval │
                       │    historical     │  │    over rows      │
                       │    ticks back     │  │  - synth trades   │
                       │    onto NATS      │  │  - Sharpe/Sortino │
                       │    ticks.<sym>... │  │    /maxDD         │
                       └──────────────────┘  └──────────────────┘
                                  │              │
                       ws clients └────► /ws/replay/{id}
                                                 │
                                                 ▼
                                       POST /api/backtest/run
                                       returns {trades, metrics}
```

## Replay session lifecycle

```
                   ┌──────────────┐
                   │   Idle       │   ← created by Manager.Create
                   └──────┬───────┘
                          │ Start()
                          ▼
                   ┌──────────────┐
                   │   Playing    │   ← runner reads + paces ticks
                   └──┬─────┬─────┘
                      │     │
              Pause() │     │ Stop()
                      │     │
                      ▼     │
                ┌──────────┐│
                │  Paused  ││
                └────┬─────┘│
                     │      │
                Resume      │
                     │      │
                     ▼      ▼
                   ┌──────────────┐         ┌──────────────┐
                   │  Stopped     │ ◄───────│  Finished    │ ← runner consumed range
                   └──────────────┘         └──────────────┘
                          ▲
                          │ on reader/publisher error
                          │
                   ┌──────────────┐
                   │   Failed     │
                   └──────────────┘
```

`SessionState` constants live at [`session.go:17`](../../internal/replay/session.go#L17). The transitions are guarded by a single `sync.RWMutex` plus a `cancelMu + cancel + doneOnce` triple ([`session.go:204`](../../internal/replay/session.go#L204)) — the deep review's race fix replaced the earlier `wg.Add-inside-Run` pattern.

## How a replay session runs

```
WS dial /ws/replay/{id}?symbol=spx&date=2026-05-21&speed=4
   │
   ▼
WSHandler.ServeHTTP                         ([replay/ws.go:75])
   - parse session id
   - origin allowlist (CORSOrigins)
   - SetReadLimit(1024)
   - Manager.Get(id) || Manager.Create(id, range, opts)
   - subscribe status updates
   - send initial SessionStatus
   - heartbeat 15s
   │
   ▼ (server pushes status events for every state change)
   │
   ▼ (client may send {"action":"pause" | "resume" | "set_speed" | "stop"})

Inside the session:
   reader (reader.go) iterates ticks from the archive in event-time order
   runner (runner.go) paces them:
       speed = 1.0  → real-time wall-clock
       speed = N    → tick gap × (1/N)
       speed = 0    → unpaced (as fast as the publisher can keep up)

   each paced tick is published onto the same NATS subject the live
   ingest would have written, so cmd/compute and cmd/api consume the
   replay identically to live.

   Stop() closes a cancel context AND calls doneOnce so close(s.done)
   fires exactly once even if Stop and runner-finished race.
```

The `Speed` value is hot-swappable while playing — [`session.go`](../../internal/replay/session.go) `setSpeed(...)` uses an `atomic.Pointer[float64]` so the runner picks up the change on the next iteration without a restart.

Multiple WS clients can attach to one session — each gets its own status channel, the underlying tick publisher is shared.

## Backtest engine

`POST /api/backtest/run` orchestrates the whole pipeline. Handler at [`internal/api/backtest.go`](../../internal/api/backtest.go); engine at [`internal/backtest/engine.go`](../../internal/backtest/engine.go).

```
POST /api/backtest/run
  body: {
    symbol:  "spx" | "ndx"
    from:    "2026-05-01T13:30:00Z"
    to:      "2026-05-15T20:00:00Z"
    rule_id: "<existing alerts.Rule id>" |
    rule:    { entry, exit, ... } inline,
    holding_period_seconds: int (optional)
  }
       │
       ▼
  validate window:
    duration ≤ 31d (max range)
    handler deadline 30s
       │
       ▼
  store.QueryStates(ctx, pool, sym, from, to)            ← reads dealer_state_1s
  → []store.StateRow                                       ordered by ts ASC
       │
       ▼
  build alerts.Predicate triple (entry, exit, hold)
       │
       ▼
  Engine.Run(strategy, rows)
       │
       ▼
  for each row:
    convert StateRow → alerts.Snapshot
    if no open trade:
        if Entry(snap) → open at snap.Spot, lock direction
    else:
        if Exit(snap) || holding_period_elapsed:
            close at snap.Spot
            ReturnPct = direction × (exit-entry)/entry
       │
       ▼
  on stream end with open trade:
    close at lastSpot (deep review fix — was entrySpot+0%)
       │
       ▼
  metrics:
    Sharpe = mean(returns) / stddev(returns) × annualisationFactor(n, from, to)
    Sortino = mean(returns) / downsideDev(returns)   (sign-independent)
    MaxDrawdown = max peak-trough on cumulative return
       │
       ▼
  Response { trades: [...], metrics: { sharpe, sortino, max_dd, n_trades, win_rate } }
```

### `annualisationFactor`

Old code multiplied Sharpe by `sqrt(252)` blindly. For 0DTE that's meaningless — a strategy can fire 50 entries on Tuesday and zero on Wednesday. The deep review fix derives the factor from the actual window:

```go
trades_per_year = n / (window_seconds / SecondsPerYear)
factor          = sqrt(trades_per_year)
```

When the window is too narrow (n < 2 or duration < 1 day), falls back to 1 (no scaling). [`backtest/engine.go`](../../internal/backtest/engine.go) `annualisationFactor`.

### Sortino sign-independence

Old code short-circuited Sortino to 0 when `mean == 0`. Sortino is defined whenever downside deviation > 0, regardless of mean's sign:

```go
if downsideDev > 0 {
    return mean / downsideDev
}
return 0
```

### Predicate language (`alerts.Predicate`)

The same predicate types power both `internal/alerts` (live) and `internal/backtest` (replay). A predicate is `func(s alerts.Snapshot) bool` — the snapshot has every field of `StateSnapshot`. `internal/backtest/predicates.go` exposes builders:

```
NumericPredicate{Field, Op, Threshold}
   Field ∈ {Spot, NetGEX, DPI, PinProb, CharmVelocity, ...}
   Op    ∈ {GT, LT, GTE, LTE, EQ}

CompositePredicate (AND, OR, NOT)
ZonePredicate(Charm zone in set)
RegimePredicate(regime in set)
```

Every numeric predicate guards against NaN explicitly ([`backtest/predicates.go:18-32`](../../internal/backtest/predicates.go#L18)) — `NaN > x` is false in Go, so a corrupted snapshot would silently miss instead of erroring.

## Concurrency rules

| Surface | Concurrency contract |
|---|---|
| `Manager.Create` | Refuses duplicate id; first writer wins |
| `Session.Pause/Resume/SetSpeed/Stop` | Safe from any goroutine |
| `Session.Status()` | RLock-cheap |
| `Engine.Run` | Sequential — driven by replayed event-time order |
| Backtest handler | 30s deadline, single Postgres query, single goroutine |

The replay error subscriber goroutine ([`session.go`](../../internal/replay/session.go)) has no return signal — flagged as deferred in `docs/REVIEW.md` (#29, tooling-noise only).

## Limits

| Limit | Source |
|---|---|
| Backtest deadline | 30s (handler-imposed) |
| Backtest max range | 31 days (handler-imposed) |
| Replay session count | unbounded by design — k8s replicas + per-IP rate limit are the real cap |
| `/ws/replay` inbound | 1024 byte read limit per [`replay/ws.go`](../../internal/replay/ws.go) |

## Test coverage map

| Test | Covers |
|---|---|
| `TestRunnerUnpacedPreservesOrder` | speed=0 publishes in event-time order |
| `TestRunnerPacedSpeedSlowsAccordingly` | speed=N respects pacing |
| `TestSession_PauseResume` | state transitions |
| `TestSession_StopRaceWithRunner` | regression for the cancel race |
| `TestEngine_LongOnlyEntryExit` | basic strategy round-trip |
| `TestEngine_ShortDirection` | inversion |
| `TestEngine_HoldingPeriodFallback` | exit by timeout |
| `TestEngine_StreamEndStraggler` | open trade at end uses lastSpot |
| `TestEngine_AnnualisationFromWindow` | factor scales with window |
| `TestEngine_SortinoZeroMean` | sortino defined when mean=0 |
| `TestPredicates_NaNGuard` | numeric predicates reject NaN cleanly |
| `TestBacktest_HandlerWindowGuard` | rejects > 31d range |

## What this section does **not** cover

- Live `state.<sym>.gex` publication — see [`01-data-pipeline.md`](01-data-pipeline.md).
- Predicate semantics for live alerts — see [`06-alerts-engine.md`](06-alerts-engine.md).
