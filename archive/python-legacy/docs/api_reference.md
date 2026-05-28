# API Reference

Base URL: `http://<host>:8000`

All `/v1/*` endpoints require an `X-API-Key` header and are rate-limited per
API key (default 120 req/min, configurable via `RATE_LIMIT_PER_MINUTE`).
Administrative endpoints under `/admin/*` require a JWT bearer token obtained
via `POST /admin/login`.

The data envelope is always:

```json
{
  "symbol": "SPXW",
  "computed_at": "2026-06-01T13:30:00+00:00",
  "next_update_in_seconds": 42,
  "data": { /* endpoint-specific payload */ }
}
```

`computed_at` is `null` when no metrics have been written yet — typically
right after `docker compose up` before the first scheduler tick completes.

---

## Public

### `GET /health`

Liveness + feed-health snapshot. **No auth.** Always returns `200`; consumers
infer health from the body.

```json
{
  "status": "ok",
  "db": "ok",
  "pipeline_running": true,
  "last_compute_per_symbol": { "SPXW": "2026-06-01T13:30:00+00:00", "NDXP": null },
  "opra_lag_ms": 412,
  "futures_lag_ms": 88
}
```

---

## End-user data (X-API-Key)

### `GET /v1/{symbol}/gex`

Per-strike Gamma Exposure curve plus aggregate top-positive / top-negative
strikes.

| Query | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `oi` \| `volume` | `oi` | OI-weighted or volume-weighted GEX. |
| `expiry` | `all` \| `YYYY-MM-DD` | `all` | Filter to a single expiry. |

`data`:

```json
{
  "net_total": -1.23e9,
  "curve": [{"strike": 4500, "call_gex": 1.2e8, "put_gex": -8.5e7, "net_gex": 3.5e7}],
  "top_positive": [{"strike": 4500, "net_gex": 3.5e7}],
  "top_negative": [{"strike": 4400, "net_gex": -1.1e8}],
  "zero_gamma": 4485.2,
  "weight_col": "oi",
  "weight_source": "oi"
}
```

`weight_source` ∈ `{oi, volume, volume_fallback, premium_fallback, uniform_fallback}` —
records which weight the calculator actually used. Fallback fires when the
requested weight is fully zero on the chain (off-hours, definitions-only feeds).

### `GET /v1/{symbol}/max-pain`

Max-pain strike per expiry plus a chain-wide aggregate.

| Query | Type | Default |
|-------|------|---------|
| `expiry` | `nearest` \| `all` \| `YYYY-MM-DD` | `nearest` |

```json
{
  "per_expiry": [{"expiration": "2026-06-13", "strike": 4495, "pain": 1.2e7}],
  "aggregate": {"strike": 4500, "value": 8.4e7}
}
```

### `GET /v1/{symbol}/walls`

Top OI / volume walls per side.

| Query | Type | Default |
|-------|------|---------|
| `mode` | `oi` \| `volume` \| `both` | `both` |

`data` keys: `call_wall_oi`, `put_wall_oi`, `call_wall_volume`, `put_wall_volume`
— each a list of `{rank, strike, value}`.

### `GET /v1/{symbol}/iv`

ATM IV, IV skew per expiry, and a sampled volatility surface.

```json
{
  "atm_iv": 0.187,
  "skew": {"2026-06-13": 0.024, "2026-06-20": 0.031},
  "surface": [{"expiration": "2026-06-13", "strike": 4500, "option_type": "C", "iv": 0.182, "delta": 0.49}]
}
```

### `GET /v1/{symbol}/snapshot`

Single-call aggregate that returns every metric type the pipeline produces.
This is what the WS / SSE streams push as well, so a fresh client can prime
its full UI with one REST call before subscribing.

`data` keys (Rev 6):

```json
{
  "gex":          { /* same shape as /gex with mode=oi */ },
  "gex_volume":   { /* same shape as /gex with mode=volume */ },
  "max_pain":     { "per_expiry": [...], "aggregate": {...} },
  "walls_oi":     { "call_wall_oi": [...], "put_wall_oi": [...] },
  "walls_volume": { "call_wall_volume": [...], "put_wall_volume": [...] },
  "walls":        { /* legacy: walls_oi + walls_volume merged */ },
  "iv":           { "atm_iv": ..., "skew_per_expiry": {...}, "surface": [...] },
  "vanna_total":  { "net_total": ..., "curve": [...], "top_positive": [...], "top_negative": [...] },
  "charm_total":  { "net_total": ..., "curve": [...], "top_positive": [...], "top_negative": [...] },
  "vanna_level":  [ { "strike": 4500, "value": ..., ... } ],
  "charm_level":  [ ... ],
  "regime":       { "oi": {...}, "vol": {...}, "label": "neutral", "score": 0.12 },
  "zero_gamma":   4485.2,
  "pin_probability": [ { "strike": 4500, "prob": 0.18 } ],
  "move_tracker": { "realized_move": 0.42, "implied_move": 0.55, "ratio": 0.76 },
  "risk_reversal_25d": [ { "expiration": "2026-06-13", "value": -0.024 } ],
  "iv_term_structure": [ { "expiration": "2026-06-13", "atm_iv": 0.18 } ],
  "hiro_cumulative": 1.2e6,
  "hiro": { /* full HIRO payload — see /hiro */ },
  "flow_events_last_hour": 14,
  "flow": [ /* the most-recent 50 flow events — same shape as /flow */ ],
  "session_state": { "is_rth": true, "tau_0dte_years": 0.0008, "minutes_to_close": 27.0, "is_expiration_day": true },
  "spot": { "price": 5234.10, "source": "futures_basis", "basis": -0.7, "futures_price": 5234.80, "parity_price": 5234.20, "parity_deviation_pct": 0.002 },
  "zero_dte": { "gex_oi": {...}, "gex_volume": {...}, "charm_total": {...}, "charm_decay_rate": 0.012, "flip_speed": 4.2e5 },
  "back_month": { "gex_oi": {...}, "gex_volume": {...} }
}
```

**Rev 6 additions:** `flow` (last 50 flow events embedded), `hiro` (full
payload — consumers can render the HIRO chart from snapshot alone, no second
fetch needed). `hiro_cumulative` retained as a scalar shortcut for legacy
consumers.

**Rev 6 in-process cache.** The snapshot response is cached per-symbol with a
10-second TTL, refreshed on every successful pipeline tick. Reconnect storms
prime from cache; a single connecting client repopulates after TTL expiry.

### `GET /v1/{symbol}/0dte`

Curated 0DTE-only envelope — same data as `/snapshot` filtered to the 0DTE-
relevant blocks. Use when the consumer only needs `session_state`, `spot`,
`zero_dte`, `back_month`, `pin_probability`, `move_tracker`.

### `GET /v1/{symbol}/spot`

Lightweight spot-resolution endpoint. `session_state` + `spot` block only.
`spot.source` ∈ `{futures_basis, parity, stale_cache}`.

### `GET /v1/{symbol}/futures-levels`

Cash-space levels (Zero Gamma, Call Wall, Put Wall, Max Pain, top GEX
strikes) translated into futures coordinates (ES / NQ) using the EMA basis.
When the futures feed is offline, `futures_level` is `null` — consumers
should render an "offline" badge.

### `GET /v1/{symbol}/flow`

Flow events (SWEEP / BLOCK / UOA) filtered by event type and time range.

| Query | Type | Default |
|-------|------|---------|
| `event_type` | `SWEEP` \| `BLOCK` \| `UOA` \| `all` | `all` |
| `since` | ISO-8601 (must be within last 24h) | last 1h |
| `limit` | int (1–1000) | 100 |

```json
{
  "symbol": "SPXW",
  "event_type": "all",
  "since": "...",
  "limit": 100,
  "events": [
    {
      "id": "uuid",
      "ts": "2026-06-01T13:29:00+00:00",
      "symbol": "SPXW",
      "expiration": "2026-06-13",
      "strike": 4500.0,
      "option_type": "C",
      "event_type": "SWEEP",
      "side": 1,
      "size": 850,
      "price": 12.40,
      "legs": 4,
      "venues": ["CBOE", "ISE", "NYSE", "PHLX"],
      "meta": { "premium_usd": 1.05e6, "execution_time_ms": 120 }
    }
  ]
}
```

### `GET /v1/{symbol}/hiro`

HIRO time-series feed. **Aligned with the SpotGamma definition** — canonical
output is delta-notional shares-equivalent that a dealer must hedge. When
the upstream chain delta is unavailable the calculator falls back to
signed-premium (records the path in `weight_source`).

| Query | Type | Default |
|-------|------|---------|
| `bucket` | `1m` \| `5m` \| `15m` | `1m` |
| `since` | ISO-8601 (must be within last 24h) | last 1h |

`data`:

```json
{
  "symbol": "SPXW",
  "bucket": "1m",
  "since": "...",
  "cumulative": 1.2e6,
  "series": [
    {
      "ts": "2026-06-01T13:29:00+00:00",
      "call_premium": 8.4e5,
      "put_premium": 4.2e5,
      "net_premium": 1.26e6,
      "cumulative": 1.26e6,
      "call_delta_notional": 84000.0,
      "put_delta_notional": -32000.0,
      "net_delta_notional": 52000.0,
      "next_expiry_delta_notional": 18000.0,
      "next_expiry_premium": 220000.0,
      "weight_source": "delta_notional"
    }
  ],
  "weight_source": "delta_notional"
}
```

**Sign convention** (per SpotGamma, do not invert):

| Customer flow | Dealer hedge | HIRO sign |
|---------------|--------------|-----------|
| Buy CALL | Buy underlying | + |
| Sell CALL | Sell underlying | − |
| Buy PUT | Sell underlying | − |
| Sell PUT | Buy underlying | + |

`weight_source` per bucket and overall:

* `delta_notional` — canonical SpotGamma path (chain delta available)
* `signed_premium` — fallback (delta missing for this row/bucket)
* `mixed` — both paths exercised in the window

The 0DTE green line in the SpotGamma chart maps to
`next_expiry_delta_notional`; calls/puts breakdown to `call_delta_notional`
and `put_delta_notional`. Total/Purple = `net_delta_notional` (or
`net_premium` when fallback fired).

---

## Streaming

All streaming endpoints accept the API key via `X-API-Key` header **or**
`?key=<api-key>` query parameter (browsers cannot set custom headers on
WS / EventSource upgrades). Per-key cap: `MAX_WS_CONNECTIONS_PER_KEY`
(default 5).

### `WS /v1/{symbol}/stream`

Pipeline-snapshot push channel. On connect, the server primes with the
current `/snapshot` body (served from the in-process cache to absorb
reconnect storms — cache TTL 10s, refreshed on every pipeline tick).
Subsequent frames arrive within milliseconds of `run_pipeline_for_symbol`
publishing — typically every `COMPUTE_INTERVAL_SECONDS` (default 60s).

A `{"type": "heartbeat", "ts": "..."}` frame is sent every 25s so corporate
proxies don't drop the idle connection.

Frame shape:

```json
{
  "symbol": "SPXW",
  "computed_at": "2026-06-01T13:30:00+00:00",
  "data": { /* same as /snapshot data */ }
}
```

**Close codes:**

* `1000` — normal client close
* `1008` — policy violation (missing/invalid auth, symbol ACL miss, per-key cap exceeded)
* `4401` — *application code (Rev 5)* — auth was valid at connect but the API key was deactivated or expired mid-stream. Independent revocation watcher polls `api_keys` every 30s and closes proactively, even on busy streams. Clients should re-authenticate before reconnecting.

### `WS /v1/{symbol}/stream/ticks` *(Rev 5)*

High-frequency raw spot/futures tick channel. Each ES/NQ trade fans out a
frame the moment the GLBX feed emits. Backed by `TickNotifier`; per-subscriber
queue depth is 500 (vs. 32 on the snapshot stream) so a brief network stall
doesn't lose a window of ticks. Slow subscribers have their oldest frame
dropped — freshness beats completeness.

Frame shape:

```json
{
  "type": "tick",
  "symbol": "SPXW",
  "data": {
    "instrument_id": 12345,
    "contract_symbol": "ESM6",
    "price": 5234.75,
    "size": 12,
    "ts_event": "2026-06-01T13:30:01.234567+00:00",
    "cash_spot": 5234.05,
    "basis": -0.7
  }
}
```

Same auth, same heartbeat cadence (25s), same close codes as `/stream`.

### `GET /v1/{symbol}/stream/sse`

Server-Sent Events fallback for clients (browsers behind corporate proxies
that strip `Upgrade`) that cannot connect via WebSocket. Same payload as
`/stream`. SSE has no equivalent of close code 4401 — the connection
terminates on revocation; the client should re-auth before reconnecting.

---

## Admin (Authorization: Bearer <jwt>)

### `POST /admin/login`

Exchange username/password for a JWT.

**Rate-limited to 5 req/min per IP.** Login is **constant-time**: bcrypt
runs even on a bad username so timing cannot enumerate valid usernames.
Field caps: `username` ≤ 128 chars, `password` ≤ 256 chars.

```json
{ "username": "admin", "password": "..." }
```

Response: `{ "access_token": "...", "token_type": "bearer", "expires_in_seconds": 28800 }`.

### `GET /admin/system/status`

Combined health snapshot:
* `pipeline_running`, `last_databento_event`, `last_compute_per_symbol`
* Per-symbol row counts (`rows_per_symbol`, `metric_rows_per_symbol`)
* `futures_lag_ms`, `opra_lag_ms`, `dlq_pending`, `flow_events_last_hour`
* `last_pipeline_runs[]` — most recent `pipeline_runs` row per symbol with `status` ∈ `{ok, partial, failed, session_open, session_close}`
* `live_ingester` — diagnostics from the OPRA ingester (registry size, schemas active/dropped, sample record attrs, error counters)

### `GET /admin/inspector` *(Rev 3)*

Feed-level diagnostics: per-table row counts, metric breakdown, latest
metrics, term structure, pin probability, flow events, alert events,
chain-quality coverage, ingester sample records.

### `GET /admin/inspector/dlq` *(Rev 3)*

Paginated dead-letter queue.

| Query | Default |
|-------|---------|
| `source` | (all) |
| `limit` | 50 |
| `offset` | 0 |

### `/admin/api-keys` CRUD

* `GET /admin/api-keys` — list (no plaintext)
* `POST /admin/api-keys` — `{label, allowed_symbols, expires_at?}` → returns plaintext **once**
* `PATCH /admin/api-keys/{id}` — update label / symbols / expiry / `is_active`
* `DELETE /admin/api-keys/{id}` — revoke
* `GET /admin/api-keys/{id}/usage` — usage stats

**Rev 6:** newly issued keys are populated in `api_keys.key_lookup` (keyed
BLAKE2b digest, unique index) so the auth path resolves the row in O(1) via
that column. Bcrypt remains the verifier — `key_lookup` is only an index.
Pre-Rev 6 rows have `key_lookup = NULL`; the auth path falls back to a
prefix scan and lazily backfills on first successful verify.

### `/admin/databento-keys` CRUD *(Rev 4)*

Failover pool of encrypted Databento API keys per dataset (`OPRA.PILLAR` |
`GLBX.MDP3` | `BOTH`). Plaintext keys are encrypted with Fernet (HKDF-SHA256
of `DB_ENCRYPTION_KEY`, falling back to `JWT_SECRET` for legacy deployments)
before storage; the listing only returns the first ~8 chars of the key for
identification.

* `POST /admin/databento-keys/{id}/test` — decryption sanity check (does **not** contact Databento; the ingester records auth errors on next connect attempt)

**Rotating `DB_ENCRYPTION_KEY` invalidates every encrypted blob.** Operators
must re-register every key through this endpoint after a rotation. The
recommended rollout for an existing deployment is to set `DB_ENCRYPTION_KEY`
to the current value of `JWT_SECRET` first, so existing rows decrypt
cleanly, *then* rotate `JWT_SECRET` independently. See `OPS.md`.

---

## Errors

* `401 Unauthorized` — bad/missing API key or JWT.
* `403 Forbidden` — API key inactive, expired, or not authorised for the requested symbol.
* `404 Not Found` — symbol is unknown or no data has been computed yet.
* `422 Unprocessable Entity` — invalid query parameter (e.g. malformed `expiry` date, `since` outside the 24h window, `username`/`password` exceeding length cap).
* `429 Too Many Requests` — rate limit exceeded.
* `503 Service Unavailable` — backend not ready (DB unreachable).

All error responses include a JSON body `{"detail": "..."}` per FastAPI
conventions.
