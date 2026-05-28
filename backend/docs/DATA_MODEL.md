# Data Model

> Schemas for FlowGreeks. Tick formats, bar tables, snapshot tables, Redis structures. Update when schema changes.

## Internal Go types (in-memory)

These are the canonical types passed through NATS and used in compute. Keep them small and fixed-size where possible (cache-friendly, no pointer chasing on hot path).

### Tick (raw event)

```go
// internal/feed/types.go
type Tick struct {
    Timestamp time.Time   // exchange ts (nanosecond)
    Recv      time.Time   // ingest receive ts
    Symbol    Symbol      // SPX, NDX
    Expiry    uint32      // YYYYMMDD as int (cache-friendly)
    Strike    uint32      // strike * 1000 (e.g. 5810500 = 5810.5)
    Side      Side        // CALL, PUT
    Type      TickType    // QUOTE, TRADE, NBBO, OI_UPDATE
    Price     float64     // for trades
    Size      uint32      // for trades
    Bid       float64     // for quotes
    Ask       float64     // for quotes
    BidSize   uint32
    AskSize   uint32
    Aggressor Aggressor   // BUY, SELL, UNKNOWN (Lee-Ready inferred)
    Exchange  Exchange    // OPRA participant code
}

type Symbol uint8     // SPX=1, NDX=2 (extensible)
type Side uint8       // CALL=1, PUT=2
type TickType uint8
type Aggressor uint8  // UNKNOWN=0, BUY=1, SELL=2
type Exchange uint8
```

**Why fixed-int strike:** options strikes have at most 3 decimal places in OPRA. Multiply by 1000 → uint32 → no float comparison weirdness.

**Why uint32 expiry as YYYYMMDD:** sortable as int, cache-friendly, no time.Time alloc on hot path.

### ComputedState (per symbol, per second)

```go
// internal/dealer/types.go
type ComputedState struct {
    Timestamp     time.Time
    Symbol        Symbol
    Spot          float64       // index value
    DPI           float64       // 0-100
    DPIBreakdown  DPIBreakdown
    NetGEX        float64       // notional
    ZeroGamma     float64       // strike level
    CallWall      float64
    PutWall       float64
    CharmVelocity float64       // delta/min current
    CharmZone     CharmZone     // WEAK, RISING, PEAK, FADING, PIN
    VannaSens     float64       // delta-per-vol-pt
    Regime        Regime        // SHORT_GAMMA, LONG_GAMMA, NEUTRAL
    StrikeMatrix  []StrikeRow   // per strike Greeks (capped at active strikes)
}

type DPIBreakdown struct {
    NetGammaSign      float64 // 0-100
    CharmVelocity     float64
    VannaSensitivity  float64
    TimeToCloseDecay  float64
    FlowConcentration float64
}

type StrikeRow struct {
    Expiry      uint32
    Strike      uint32
    Side        Side
    OI          uint32
    Volume      uint32
    NetSignedVol int32
    IV          float64
    Gamma       float64  // per-contract
    Charm       float64  // per-contract per minute
    Vanna       float64
    GEXNotional float64  // signed, dealer-side
}

type CharmZone uint8  // WEAK, RISING, PEAK, FADING, PIN
type Regime uint8     // SHORT_GAMMA, LONG_GAMMA, NEUTRAL
```

### BasisState (per symbol, live)

```go
// internal/dealer/basis.go
type BasisState struct {
    Timestamp     time.Time
    Symbol        Symbol      // SPX → tracks ES, NDX → tracks NQ
    Spot          float64     // index value
    FutFrontSym   string      // e.g. "ESM6" (front month)
    FutFrontMid   float64
    Basis         float64     // raw: fut - spot
    BasisSmooth   float64     // EWMA α=0.1
    FutBackSym    string      // populated only during rollover window
    FutBackMid    float64
    BasisBack     float64
    InRollover    bool        // true when front contract < 8d to expiry
}
```

### FlowTapeItem

```go
type FlowTapeItem struct {
    Timestamp    time.Time
    Symbol       Symbol
    Expiry       uint32
    Strike       uint32
    Side         Side
    Size         uint32
    Premium      float64
    PrintType    PrintType    // SWEEP, BLOCK, SPLIT, NORMAL
    Aggressor    Aggressor
    DealerImpact int8         // -2, -1, 0, +1, +2 (dealer-positive scale)
}

type PrintType uint8
```

## TimescaleDB schema

All timestamps stored as `TIMESTAMPTZ` (microsecond resolution sufficient).

### `ticks` (hypertable)

```sql
CREATE TABLE ticks (
    ts             TIMESTAMPTZ NOT NULL,
    recv_ts        TIMESTAMPTZ NOT NULL,
    symbol         SMALLINT NOT NULL,           -- SPX=1, NDX=2
    expiry         DATE,                        -- NULL for futures
    strike         INTEGER,                     -- strike * 1000, NULL for futures
    side           SMALLINT,                    -- CALL=1, PUT=2, NULL for futures
    tick_type      SMALLINT NOT NULL,
    price          DOUBLE PRECISION,
    size           INTEGER,
    bid            DOUBLE PRECISION,
    ask            DOUBLE PRECISION,
    bid_size       INTEGER,
    ask_size       INTEGER,
    open_interest  INTEGER,
    aggressor      SMALLINT,
    exchange       SMALLINT,
    instrument_id  BIGINT                       -- raw vendor id, identifies futures contract
);

SELECT create_hypertable('ticks', 'ts', chunk_time_interval => INTERVAL '6 hours');

CREATE INDEX idx_ticks_symbol_ts ON ticks (symbol, ts DESC);
CREATE INDEX idx_ticks_strike ON ticks (symbol, expiry, strike, side, ts DESC);

-- compress chunks older than 2 days
ALTER TABLE ticks SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, expiry, strike, side',
    timescaledb.compress_orderby = 'ts DESC'
);
SELECT add_compression_policy('ticks', INTERVAL '2 days');

-- drop chunks older than 1 year (or move to S3)
SELECT add_retention_policy('ticks', INTERVAL '14 months');
```

**Futures rows.** The same hypertable holds futures ticks (ES/NQ for spot
proxy / basis). For those rows `expiry`, `strike`, and `side` are `NULL`, and
`instrument_id` carries the vendor's raw contract id used to disambiguate
the front/back month.

### `bars_1s` (continuous aggregate)

```sql
CREATE MATERIALIZED VIEW bars_1s
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 second', ts) AS bucket,
    symbol, expiry, strike, side,
    first(price, ts) AS open,
    max(price) AS high,
    min(price) AS low,
    last(price, ts) AS close,
    sum(size) AS volume,
    sum(CASE WHEN aggressor = 1 THEN size ELSE 0 END) AS buy_volume,
    sum(CASE WHEN aggressor = 2 THEN size ELSE 0 END) AS sell_volume,
    last(bid, ts) AS bid,
    last(ask, ts) AS ask
FROM ticks
WHERE tick_type IN (1, 2)  -- QUOTE, TRADE
GROUP BY bucket, symbol, expiry, strike, side;

SELECT add_continuous_aggregate_policy('bars_1s',
    start_offset => INTERVAL '10 minutes',
    end_offset   => INTERVAL '10 seconds',
    schedule_interval => INTERVAL '10 seconds');
```

### `dealer_state_1s` (derived state, written by compute)

```sql
CREATE TABLE dealer_state_1s (
    ts                TIMESTAMPTZ NOT NULL,
    symbol            SMALLINT NOT NULL,
    spot              DOUBLE PRECISION,
    dpi               REAL,
    dpi_net_gamma     REAL,
    dpi_charm_vel     REAL,
    dpi_vanna         REAL,
    dpi_ttc           REAL,
    dpi_flow_conc     REAL,
    net_gex_notional  DOUBLE PRECISION,
    zero_gamma        DOUBLE PRECISION,
    call_wall         DOUBLE PRECISION,
    put_wall          DOUBLE PRECISION,
    charm_velocity    DOUBLE PRECISION,
    charm_zone        SMALLINT,
    vanna_sens        DOUBLE PRECISION,
    regime            SMALLINT
);
SELECT create_hypertable('dealer_state_1s', 'ts', chunk_time_interval => INTERVAL '1 day');
CREATE INDEX idx_dealer_state_sym_ts ON dealer_state_1s (symbol, ts DESC);
```

### `flow_tape` (derived, append-only)

```sql
CREATE TABLE flow_tape (
    ts            TIMESTAMPTZ NOT NULL,
    symbol        SMALLINT NOT NULL,
    expiry        DATE NOT NULL,
    strike        INTEGER NOT NULL,
    side          SMALLINT NOT NULL,
    size          INTEGER NOT NULL,
    premium       DOUBLE PRECISION,
    print_type    SMALLINT,
    aggressor     SMALLINT,
    dealer_impact SMALLINT
);
SELECT create_hypertable('flow_tape', 'ts', chunk_time_interval => INTERVAL '1 day');
```

### `narrative_log` (AI commentary, M4+)

```sql
CREATE TABLE narrative_log (
    ts        TIMESTAMPTZ NOT NULL,
    symbol    SMALLINT NOT NULL,
    tag       TEXT NOT NULL,         -- REGIME, CHARM, PIN, FLOW, VOL
    text      TEXT NOT NULL,
    ref_state JSONB                  -- snapshot of state used to generate
);
```

## Redis structures

### Live state per symbol

Hash `state:<symbol>` → fields = current values. Updated every 1s by compute.

```
HSET state:spx
  spot 5811.42
  fut_front_sym ESM6
  fut_front_mid 5817.28
  basis 5.86
  basis_smooth 5.78
  in_rollover 0
  dpi 78
  dpi_net_gamma 88
  dpi_charm 72
  dpi_vanna 58
  dpi_ttc 81
  dpi_flow 64
  net_gex -2800000000
  zero_gamma 5805
  call_wall 5840
  put_wall 5775
  charm_vel -3.1
  charm_zone 3       # PEAK
  regime 1           # SHORT_GAMMA
  ts 1716640328123
EXPIRE state:spx 30
```

All level fields (`zero_gamma`, `call_wall`, `put_wall`, strike matrix) are stored in **spot space**. Frontend applies `+ basis_smooth` shift when user is in FUTURES view.

### Strike matrix (sorted set per symbol+expiry)

```
ZADD gex:spx:20260525:call <strike> <gex_notional>
ZADD gex:spx:20260525:put  <strike> <gex_notional>
```

Score = strike (so range queries by price work). Value = packed JSON or msgpack of full strike row.

### Sliding window for charm clock

List `charm:<symbol>` with last 600 entries (10 min @ 1s) of charm velocity samples. RPUSH on each compute, LTRIM to 600.

### WS subscription registry

```
SET ws:conn:<conn_id> '{"user_id":"...","tier":"edge","subs":["spx.dpi","spx.gamma"]}'
EXPIRE ws:conn:<conn_id> 60   # heartbeat refreshes
```

## NATS subjects

```
ticks.<symbol>                    # raw ticks fanout
quotes.<symbol>.<expiry>.<strike> # quote updates (high freq, decimated)
trades.<symbol>.<expiry>.<strike> # trade prints
state.<symbol>.dpi                # computed DPI deltas
state.<symbol>.gex                # gex matrix updates
state.<symbol>.charm              # charm velocity updates
state.<symbol>.flow               # flow tape items
state.<symbol>.basis              # spot/futures basis updates (~10Hz)
narrative.<symbol>                # AI narrative events
control.replay.<session_id>       # replay control
prefs.updated.<user_id>           # user preference change broadcast
```

Stream config:
- `TICKS` stream → 24h retention, 10GB max, file storage
- `STATE` stream → 1h retention, memory storage (high freq)
- `FLOW` stream → 7d retention, file storage

## Symbol/Expiry/Strike encoding rules

- **SPX symbol IDs:** 1=SPX, 2=NDX (extensible to 3=RUT, 4=DJX later, never)
- **Strike** stored as `int = strike * 1000`. SPX strikes are integer points but NDX has 5-pt and decimal increments. Multiplier handles both.
- **Expiry** stored as YYYYMMDD `uint32` in memory, `DATE` in TS. Conversion via tiny helpers in `internal/feed/symbol.go`.

## Versioning

Schema version table:
```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ DEFAULT NOW(),
    description TEXT
);
```

Migrations live in `scripts/migrations/NNN_*.sql`, applied via `golang-migrate`.
