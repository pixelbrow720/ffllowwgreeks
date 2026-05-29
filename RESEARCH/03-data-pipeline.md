# 03 · Data Pipeline

```
                ┌──────────────────────────────────────────────────┐
                │                  EXTERNAL                        │
                │  OPRA Pillar (options) ─┐                        │
                │  CME MDP 3.0 (futures) ─┤                        │
                └─────────────────────────┼────────────────────────┘
                                          │ Databento subscription
                                          ▼
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   cmd/ingest     │     │   cmd/replay     │     │ scripts/         │
│ (live, OPRA WS)  │     │ (historical DBN) │     │ dbn_to_postgres.py│
│   parse → norm.  │     │  read DBN files  │     │ python bridge    │
└────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘
         │                        │                        │
         └────────────┬───────────┘                        │
                      ▼                                    ▼
            ┌────────────────────┐          ┌─────────────────────────┐
            │  NATS JetStream    │          │ TimescaleDB (Postgres)  │
            │  ticks.<sym>.>     │          │   ticks  hypertable     │
            │  (pub/sub fabric)  │          │   211M rows, 27 chunks  │
            └─────────┬──────────┘          └─────────────────────────┘
                      │ subscribe
                      ▼
            ┌────────────────────┐
            │   cmd/compute      │
            │  Greeks (B-S+IV)   │
            │  Dealer aggregator │
            │  DPI 5-component   │
            │  Charm clock       │
            │  Pin engine        │
            │  Flow pulse        │
            │  1Hz tick          │
            └─────────┬──────────┘
                      │
                      ├────► state.<sym>.gex (NATS, top-64 strikes JSON, 1Hz)
                      └────► dealer_state_1s (TimescaleDB writer, 1Hz)
                              │
                              ▼
                      ┌────────────────────┐
                      │   cmd/api          │
                      │  REST + WS         │
                      │  /api/snapshot     │
                      │  /api/history      │
                      │  /api/levels       │
                      │  /ws/live          │
                      │  /ws/replay        │
                      │  /admin/keys       │
                      └─────────┬──────────┘
                                │ Authorization: Bearer <key>
                                │ or  ?api_key=<key>  (WS upgrade only)
                                ▼
                      ┌────────────────────┐
                      │   web/             │
                      │  Next.js 14        │
                      │  Typed REST client │
                      │  WS singleton      │
                      │  9 panels          │
                      └────────────────────┘
```

## Stage budgets (hot path live ingest)

| Stage | Budget | Where |
|---|---|---|
| Ingest (parse OPRA frame) | 5 ms | `internal/feed/databento.go` |
| Normalize to `feed.Tick` | 2 ms | `internal/feed/normalize.go` |
| NATS publish | <1 ms | `internal/bus/publisher.go` |
| Compute aggregate (1Hz cycle) | 30 ms | `cmd/compute/main.go::runAggregator` |
| Fanout WS broadcast | 10 ms | `internal/api/ws.go::broker` |
| **Total wire-to-WS p99** | **< 100 ms** | enforced by metrics |

Replay is unpaced — uses the same code path but inputs from Postgres `ticks` instead of OPRA wire. The 100ms budget does NOT apply to replay; replay is offline analytics.

## Tick types

```
TickTypeQuote = 0  // bid/ask update on a strike
TickTypeTrade = 1  // executed trade
TickTypeOI    = 2  // open interest snapshot
TickTypeFuture = ... (futures legs go through TickTypeQuote/Trade with FuturesContract set)
```

OI ticks **must** seed `PositionTracker` before quotes/trades arrive — otherwise the dealer gamma is computed against an empty book. The bus publisher rejected OI ticks until commit `0226631` fixed it.

## Symbol routing

```
SPX cash → Symbol=SPX, ESH6 (Mar 2026 quarterly futures) basis-tracked
NDX cash → Symbol=NDX, NQH6 (Mar 2026 quarterly futures) basis-tracked
```

Front-month rollover: third Friday of Mar/Jun/Sep/Dec via `internal/replay/futures.go::FrontMonthContract`.

## State shapes

### `feed.Tick` (binary wire format on NATS)

```go
type Tick struct {
    Symbol     Symbol      // SPX | NDX
    AssetClass AssetClass  // Option | Future
    Side       Side        // Call | Put | (None for futures)
    Strike     int32       // 1e-3 USD per unit (so 6900.0 = 6_900_000)
    Expiry     uint32      // YYYYMMDD
    TickType   uint8       // Quote | Trade | OI
    TsEvent    uint64      // ns since epoch
    TsRecv     uint64
    Bid, Ask, Last float64
    Size       int64
    OpenInterest int64
    FuturesContract [12]byte // ESH6, NQH6, etc.
}
```

### `state.<sym>.gex` JSON (1Hz NATS broadcast)

See [`reference/snapshot-spx-sample.json`](reference/snapshot-spx-sample.json) for live shape. Top-64 strikes by `|dealer_pos|`, plus the full state header (spot, walls, regime, DPI, pulse, pin, etc).

### `dealer_state_1s` row (Postgres, 1Hz)

See [`reference/db-schemas.txt`](reference/db-schemas.txt) for the exact columns. ~30 columns covering everything the WS broadcast carries except the per-strike strike list.

## Storage budgets

- 1 trading day (RTH only) ≈ 23M ticks × 100 bytes = 2.3 GB raw → ~250 MB after Timescale compression.
- 1 trading day `dealer_state_1s` = 23,400 rows × 200 bytes = 4.7 MB.
- 1 year (252 days) = ~580 GB ticks + 1.2 GB state. Manageable on a single node.

## Why NATS JetStream

- Decouples ingest from compute (compute can lag without dropping ingest).
- Replay swaps in for ingest with zero downstream changes.
- Per-symbol subjects enable horizontal sharding when SPX + NDX scale demands it.
- Subscriber pending limits raised to 8M msgs / 1 GiB so unpaced replay floods don't slow-consumer-drop.
