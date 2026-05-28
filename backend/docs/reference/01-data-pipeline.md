# 01 — Data pipeline

> Validated against commit `3e5b0ec`.

## End-to-end: tick → websocket client

```
Databento WS                                                                  Browser / ws_stress
     │                                                                                ▲
     │ wire bytes                                                                     │
     ▼                                                                                │
┌─────────────────────────┐                                                  ┌────────┴──────────┐
│ databento.Client        │                                                  │ /ws/live          │
│ - ws session            │                                                  │ LiveHandler       │
│ - bootstrap registry    │                                                  │ - 4KB read limit  │
│ - decoder routes:       │                                                  │ - origin allowlist│
│   Mbp1Msg → Tick (quote)│                                                  │ - heartbeat 15s   │
│   Cmbp1Msg → Tick       │                                                  └─────────▲─────────┘
│   Mbp0Msg → Tick (trade)│                                                            │
│   Stat → OI Tick        │                                                            │ JSON event
└──────────┬──────────────┘                                                            │
           │ feed.Tick (90 bytes, fixed layout)                                        │
           ▼                                                                           │
┌─────────────────────────┐                                                  ┌─────────┴─────────┐
│ cmd/ingest runDispatch  │                                                  │ api.Broker        │
│ - publishes to NATS     │   ticks.<sym>.{quote,trade,future}.<...>         │ - Publish()       │
│ - archives to TimescaleDB                                                  │ - matchesFilter() │
│ - drops on archive backpressure (counter)                                  │ - drop-on-slow    │
└─────┬───────────────┬───┘                                                  └─────────▲─────────┘
      │ NATS          │                                                                │
      │ JS publish    │ ArchiveWriter.Write                                            │
      ▼               ▼                                                                │
NATS TICKS stream    Postgres                                              ┌───────────┴────────┐
      │              ticks (hypertable)                                    │ api.Cache.Put      │
      │                                                                    │ + Broker.Publish   │
      │                                                                    └───────────▲────────┘
      │ subscribe ticks.>                                                              │
      ▼                                                                                │
┌─────────────────────────┐                                                            │
│ cmd/compute             │                                                            │
│ pipelinePerSymbol       │                                                            │
│  (one Pipeline per      │                                                            │
│   sym = SPX | NDX)      │                                                            │
│                         │                                                            │
│ handleTick switches on  │                                                            │
│   TickType:             │                                                            │
│   - OI   → positions    │                                                            │
│   - Quote→ quotes+IV    │                                                            │
│   - Trade→ classifier+  │                                                            │
│            position     │                                                            │
│   - Future → basis      │                                                            │
│                         │                                                            │
│ runAggregator @ 1Hz:    │                                                            │
│   - aggregate dealer    │                                                            │
│     state               │                                                            │
│   - StateWriter.Write   │ ─────► Postgres dealer_state_1s                            │
│   - publish state.<sym> ├────────────────────────────────────────────────────────────┘
│     .gex on NATS        │   state.<sym>.gex
│   - publish narrative   │   narrative.<sym>
└─────────────────────────┘
```

## Stage details

### 1. Vendor → `feed.Tick`

`internal/feed/databento/client.go` runs the WS session.
`internal/feed/databento/convert.go` does the decode:

| Source message | Branch | Becomes | Citation |
|---|---|---|---|
| `dbn.Mbp1Msg` (top-of-book) | `convertMbp1` | `feed.Tick` quote | [`convert.go:118`](../../internal/feed/databento/convert.go#L118) |
| `dbn.Cmbp1Msg` (consolidated) | `convertCmbp1` | `feed.Tick` quote | [`convert.go:141`](../../internal/feed/databento/convert.go#L141) |
| `dbn.Mbp0Msg` (trade) | `convertTrade` | `feed.Tick` trade | [`convert.go:163`](../../internal/feed/databento/convert.go#L163) |
| Stat (open_interest) | bootstrap fanout | `feed.Tick` OI | `bootstrap.go` |

OPRA does not broadcast `SymbolMappingMsg`, so `bootstrapDataset` pre-fetches definition records to build the `instrument_id → instrumentMeta` map ([`bootstrap.go:36`](../../internal/feed/databento/bootstrap.go#L36)). Without this every live record is dropped because the visitor's meta cache is empty.

### 2. `runDispatch` — publish + archive

The hot loop in [`cmd/ingest/main.go:186`](../../cmd/ingest/main.go#L186):

```go
for tick := range client.Ticks() {
    if err := pub.Publish(ctx, t); err != nil {
        publishErrorsTotal.Inc()      // metric: flowgreeks_ingest_publish_errors_total
    } else {
        publishedTotal.WithLabelValues(ttLabel).Inc()
    }
    if err := archive.Write(t); err != nil {
        // dropped — buffer full or writer closing.
    }
}
```

Publish path is **synchronous** (waits for JS ack); archive path is **async** (channel send → batched COPY). If Postgres can't keep up, archive drops; NATS publish stays alive.

### 3. NATS subjects

`pub.subjectFor(t)` ([`bus/publisher.go:184`](../../internal/bus/publisher.go#L184)) routes per `AssetClass`:

```
AssetClassOption  + TickTypeQuote → ticks.<sym>.quote.<expiry>.<strike>.<side>
AssetClassOption  + TickTypeTrade → ticks.<sym>.trade.<expiry>.<strike>.<side>
AssetClassFuture                  → ticks.<sym>.future.<contract>
```

Stream `TICKS` is created idempotently with subjects `["ticks.>"]`, memory storage, short retention ([`publisher.go:131`](../../internal/bus/publisher.go#L131)). Run `scripts/jetstream_setup` to apply.

### 4. Archive — `ArchiveWriter`

Background goroutine drains a buffered channel and bulk-COPYs into the `ticks` hypertable. Lifecycle is `closeCh + done + closeOnce + atomic.Bool running` (mirrored by `StateWriter`).

Critical fix from the deep review: **final flush uses a fresh 10s `Background` deadline** when `ctx.Err() != nil` ([`archive.go:179` reference](../../internal/store/archive.go)). Without this the last batch was lost on graceful shutdown.

### 5. Compute — per-symbol `Pipeline`

[`cmd/compute/main.go:107`](../../cmd/compute/main.go#L107) `main` boots one goroutine per symbol. Each owns a `Pipeline`:

```go
type Pipeline struct {
    symbol    feed.Symbol
    quotes    *dealer.QuoteCache
    positions *dealer.Positions
    classifier *dealer.LeeReadyClassifier
    flow5min  map[strikeKey]flowAccum
    pinFlow   map[strikeKey]pinAccum
    ivCache   map[ivKey]float64
    ivMu      sync.RWMutex
    ...
}
```

`handleTick` ([`main.go:236`](../../cmd/compute/main.go#L236)) dispatches on TickType:

| TickType | Action |
|---|---|
| `TickTypeOI` | `positions.SeedFromOI(t)` |
| `TickTypeQuote` | `quotes.Update(t)`; if mid > 0, solve IV (warm-start from `ivCache`); store IV |
| `TickTypeTrade` | classifier with cached NBBO; update positions; bump flow5min + pinFlow |
| Futures | `basis.UpdateFuture(t)` |

IV warm-start (`cfg.InitGuess = last`) cuts the average solver iteration count dramatically — see [`cmd/compute/main.go:277-289`](../../cmd/compute/main.go#L277).

### 6. `runAggregator` — 1Hz aggregation + publish

Same file, [`main.go:388`](../../cmd/compute/main.go#L388). Once per second:

1. Snapshot `positions` + `flow5min` + `pinFlow`
2. `dealer.Aggregate(...)` produces `StrikeRow[]` + summary (NetGEX, walls, regime, charm, pin)
3. `aggregateCharmVelocity` derives the velocity scalar
4. `publishState` emits `state.<sym>.gex` to NATS
5. `publishNarrative` emits `narrative.<sym>` if rules trip
6. `StateWriter.Write` enqueues a `dealer_state_1s` row

Backpressure: `StateWriter.Write` is non-blocking; on full buffer it bumps `flowgreeks_state_rows_dropped_total` and keeps going.

### 7. API ingestion — `Cache` + `Broker`

`cmd/api/main.go` subscribes to `state.>` and `narrative.<sym>`. Each event hits two sinks:

```go
cache.Put(snap)      // last-known per (symbol, kind)
broker.Publish(snap) // fanout to live subscribers
```

`Cache` is what `/api/snapshot/{symbol}` reads; it's also seeded onto a freshly subscribed WS client as `type=snapshot.replay` (the WS resume path, [`api/ws.go:129-138`](../../internal/api/ws.go#L129)).

### 8. WebSocket fanout — `LiveHandler`

[`internal/api/ws.go:58`](../../internal/api/ws.go#L58):

```go
acceptOpts := &websocket.AcceptOptions{
    OriginPatterns: h.Origins,         // empty = same-origin only
}
c, err := websocket.Accept(w, r, acceptOpts)
c.SetReadLimit(maxInboundMessageBytes) // 4 KiB cap
```

Per-connection channel is bounded (`Subscribe(256, SubFilter{})`). On a slow client the broker drops, increments `flowgreeks_ws_drops_total{symbol,kind}`, keeps the rest of the fanout healthy.

Heartbeat ticks 15s; clients can rely on it as a liveness signal.

## Failure modes

| What breaks | Symptom | Recovery |
|---|---|---|
| Vendor disconnect | `databento.Client` `Errors()` channel | client reconnects with backoff; ingest dispatch resumes |
| NATS down | `pub.Publish` returns error | `publishErrorsTotal` ticks; ticks still archive to Postgres |
| Postgres slow | archive buffer full | `flowgreeks_archive_ticks_dropped_total` ticks; NATS still flowing |
| Compute slow | aggregator < 1Hz | alert `AggregatorStuck` (Prometheus rule) |
| Slow WS client | broker drops to that subscriber | drop counter only on that subscriber, not fleet-wide |

See [`deploy/prometheus/flowgreeks.rules.yml`](../../deploy/prometheus/flowgreeks.rules.yml) for the full alert taxonomy.

## What this section does **not** cover

- Math inside the IV solver / Greeks → see `03-math-pipeline.md`
- DPI / Charm Clock / GEX / Pin / Simulator math → see `04-dealer-model.md`
- WS resume + replay → see `05-time-machine.md`
