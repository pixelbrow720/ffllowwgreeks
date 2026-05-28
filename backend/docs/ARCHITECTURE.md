# Architecture

> System design for FlowGreeks. Read after [CLAUDE.md](../CLAUDE.md). Update sections that change as the system evolves.

## High-level diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          DATA SOURCES (vendor)                              │
│   OPRA Pillar (options ticks, all strikes)    CME Globex MDP 3.0 (futures) │
│              ↓ Databento Live API (DBN binary protocol)                     │
└──────────────────┬──────────────────────────────────────────────────────────┘
                   │ via dbn-go (NimbleMarkets, Apache-2.0, community Go SDK)
                   │ — wraps Databento Live + DBN parsing. We use it through
                   │   our internal/feed.Feed interface so it's swappable.
                   ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       INGEST LAYER  (cmd/ingest)                            │
│   - Databento Live client (dbn-go LiveClient wrapper)                       │
│   - Subscribe: OPRA.PILLAR (SPX/SPXW/NDX/NDXP via parent symbology)         │
│              + GLBX.MDP3 (ES/NQ front month for spot proxy + basis)         │
│   - DBN messages already typed (Mbp1Msg, TradeMsg, etc.) — no SBE work      │
│   - Normalizer: DBN msg → internal Tick struct                              │
│   - Symbol filter at dbn-go subscription layer (no extra filter needed)     │
│   - Publishes to NATS subjects: ticks.spx, ticks.ndx, quotes.spx, etc.      │
└──────────────────┬──────────────────────────────────────┬───────────────────┘
                   │                                       │
                   ▼                                       ▼
┌─────────────────────────────────────────┐   ┌──────────────────────────────┐
│       COMPUTE LAYER (cmd/compute)       │   │   ARCHIVE WRITER             │
│   subscribed to NATS, runs in parallel  │   │   batch-flush ticks every    │
│                                         │   │   1s to TimescaleDB          │
│   Pipelines:                            │   │   (separate worker, NEVER on │
│   - Greeks engine (BS + IV solver)      │   │    hot path)                 │
│   - Dealer positioning (per strike GEX) │   └──────────────────────────────┘
│   - Charm Clock (intraday velocity)     │                  │
│   - Vanna sensitivity                   │                  ▼
│   - Pin probability (EOD)               │   ┌──────────────────────────────┐
│   - Forced-flow simulator (precompute)  │   │   TimescaleDB                │
│                                         │   │   - ticks (hypertable, 7d hot)│
│   Writes computed state to Redis (TTL). │   │   - bars_1s, bars_1m         │
│   Publishes deltas to NATS state.*      │   │   - snapshots_1s             │
└──────────────────┬──────────────────────┘   │   - dealer_state_1s          │
                   │                          └──────────────────────────────┘
                   ▼                                          │
┌─────────────────────────────────────────┐                   │
│       LIVE STATE CACHE (Redis)          │                   │
│   - Per-symbol DPI, GEX, charm, vanna   │                   │
│   - Strike-level gamma matrix           │                   │
│   - Last 5 min sliding window           │                   │
│   - Pub/Sub for fanout                  │                   │
└──────────────────┬──────────────────────┘                   │
                   │                                          │
                   ▼                                          │
┌─────────────────────────────────────────┐                   │
│       API LAYER  (cmd/api)              │                   │
│   - REST: /snapshot, /history, /levels  │◀──────────────────┘
│   - WebSocket: /ws/live                 │      (historical reads
│   - Subscribes to NATS state.* fanout   │       go straight to TS)
│   - API-key auth, per-key rate limit    │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│       BROWSER (frontend, M5+)           │
│   SvelteKit / Next.js, WebSocket client │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│           REPLAY WORKER (cmd/replay) — separate, async                      │
│   - Reads tick range from TS                                                │
│   - Replays through compute pipeline at variable speed                      │
│   - Streams to dedicated WS topic /ws/replay/<session_id>                   │
│   - Backtest jobs use same machinery, batch mode (no WS)                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Key design decisions

### 1. Microservices via process boundaries, not network boundaries

Four binaries (`ingest`, `compute`, `api`, `replay`) communicate via **NATS on a single host**. Same machine for MVP. This gives:

- Process isolation (crash in compute doesn't take down ingest)
- Independent restarts and rolling updates
- Horizontal scale path later (move processes to separate hosts when needed)
- No network serialization tax for MVP (NATS over loopback is ~10µs)

### 2. NATS over Kafka

Kafka is overkill. NATS JetStream gives:
- Sub-ms publish latency (vs ~1-5ms Kafka)
- Built-in replay (acks + persistence) — sufficient for our durability needs
- Single binary, trivial ops
- Subject-based routing matches our domain (`ticks.spx.5810C.20260525`)

Trade-off: smaller ecosystem. Acceptable.

### 3. Hot path vs cold path strict separation

**Hot path (must be < 100ms p99):**
ingest → normalize → compute → Redis → WS fanout

**Cold path (best-effort, may be seconds):**
- Tick archive write (batched 1s flushes)
- Backtest runs
- Replay sessions (start latency 1-2s acceptable)
- Snapshot history queries

The hot path **never blocks on database writes**. Archive writer is a separate worker reading from NATS.

### 4. Greeks compute on every tick (not on every quote)

OPRA volume is enormous (~50M msg/sec at peak across all options). Even after SPX/NDX filter, we still see ~500k-2M msg/sec.

Strategy:
- **Quotes** → update IV per strike, recalc Greeks at quote frequency, cap at 10Hz per strike (decimate if needed)
- **Trades** → update gamma exposure, dealer positioning estimate (every trade matters)
- **Mid-snapshot every 1s** → write to TS, recompute full DPI/charm

This keeps compute load bounded while preserving the dealer-impact signal we care about.

### 5. Dealer positioning estimation strategy

We don't have direct dealer position data — we **infer it** from open interest + volume + trade aggressor side. Approach:

- Per strike, classify trades using BVal/Lee-Ready or "at-bid vs at-ask" rules
- Accumulate signed volume → estimate net dealer inventory delta
- Combine with prior-day OI to get absolute positioning estimate
- Apply Greeks to get per-strike gamma/charm/vanna exposure
- Sum across strikes for net DPI

This is the **proprietary part** of the product. See [COMPUTE_MODEL.md](COMPUTE_MODEL.md) for math details.

### 6. Storage hierarchy

| Layer | Retention | Use case | Tech |
|---|---|---|---|
| Process memory | seconds | live compute window | Go structs |
| Redis | 5-60 minutes | live state cache, WS state | Redis 7 |
| TimescaleDB hot | 7 days | replay, intraday queries | TS hypertable |
| TimescaleDB cold | 1+ year | backtest, historical research | TS compressed chunks |
| S3 / object store | forever | tick archive raw, backups | optional, M9 |

### 7. WebSocket fanout pattern

```
NATS state.spx.dpi → api server → maintains map[clientID]chan State → write loop per conn
```

- Each connection gets its own goroutine + bounded send channel (drop oldest if full)
- Heartbeat every 15s
- Subscriptions are per-symbol-per-stream (`spx.dpi`, `spx.gamma`, `ndx.flow`)
- Server handles backpressure: slow clients get dropped, never block the fanout loop

## Latency budget (target p99)

| Stage | Budget | Realistic |
|---|---|---|
| OPRA wire → ingest decoded | 1-3ms | 2ms |
| Symbol filter + normalize | 1ms | 0.5ms |
| NATS publish → compute receive | 0.5ms | 0.2ms |
| Greeks + dealer compute | 30ms | 15-25ms |
| Redis write | 1ms | 0.5ms |
| NATS state fanout → API | 0.5ms | 0.2ms |
| API → WS frame to client | 5-30ms | 10-20ms (network-bound) |
| **Total wire → screen** | **< 100ms** | **30-60ms** typical |

## Failure modes & resilience

- **Ingest dies:** systemd restarts. NATS retains messages briefly. 1-5s gap acceptable.
- **Compute dies:** state cache stale, but readable. New computes resume from latest snapshot in TS. Replay missing minutes.
- **Redis dies:** API serves stale data with `degraded` flag. Compute writes queued in memory until reconnect.
- **TimescaleDB dies:** archive writes buffer in memory + spool to disk. Live path unaffected.
- **NATS dies:** worst case. Everything stops. Single point of failure for MVP — acceptable for solo dev. Multi-node JetStream cluster in M8.

## What's explicitly OUT of scope (for now)

- ❌ Multi-region / DR
- ❌ HA database (single TS instance with backups suffices)
- ❌ Kubernetes (docker-compose is plenty)
- ❌ Streaming analytics framework (Flink/Spark) — Go workers are fine
- ❌ ML models — start with deterministic heuristic models
- ❌ Mobile apps — desktop only by user mandate
