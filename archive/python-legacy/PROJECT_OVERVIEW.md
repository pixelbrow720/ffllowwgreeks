# Project Overview — flowgreeks-engine

Options flow analytics backend for index options (SPXW, NDXP). Live + historical
ingestion from Databento, TimescaleDB time-series storage, a derivatives metrics
engine, REST + WebSocket + SSE API.

This repository is **backend-only**. Consumers connect over the documented
`/v1/*` API surface; the stability contract is in [API_POLICY.md](API_POLICY.md)
and the consumer-visible changelog is in [CHANGELOG.md](CHANGELOG.md).

This document is a structural map of what is in the repo, derived from reading
the code. For a tutorial-style getting-started, see [README.md](README.md).

---

## Repo layout

```
flowgreeks-engine/
├── backend/                FastAPI app + processing engine + ingestion
│   ├── app/
│   │   ├── api/            HTTP / WS / SSE routers, schemas, deps, notifiers
│   │   ├── core/           crypto, security, logging
│   │   ├── db/             SQLAlchemy models, async session, alembic migrations
│   │   ├── ingestion/      Databento live + historical + EOD-OI + bulk writers + DLQ + key pool
│   │   ├── processing/     Pipeline + every metric calculator
│   │   ├── config.py       Pydantic Settings (env-driven)
│   │   └── main.py         App factory + lifespan + middleware
│   ├── tests/              Unit + DB-backed test modules
│   ├── requirements.txt    Pinned deps (FastAPI 0.115, SQLAlchemy 2, databento 0.47…)
│   ├── pyproject.toml      Ruff lint config
│   └── Dockerfile
│
├── docs/api_reference.md   Endpoint reference
├── docker-compose.yml      db + backend
├── README.md               Consumer-facing tour of the surface
├── API_POLICY.md           /v1/* stability contract
├── CHANGELOG.md            Consumer-visible changelog
├── CHANGELOG.json          Machine-readable companion
├── OPS.md                  Operational runbook
└── ROADMAP.md              Deferred production-readiness items
```

---

## Stack

| Layer            | Choice                                                  |
|------------------|---------------------------------------------------------|
| Runtime          | Python 3.11                                             |
| Framework        | FastAPI 0.115 + Uvicorn                                 |
| ORM              | SQLAlchemy 2 async + asyncpg + psycopg                  |
| Migrations       | Alembic (async)                                         |
| Database         | PostgreSQL 15 + TimescaleDB 2.16 (hypertables)          |
| Scheduling       | APScheduler `AsyncIOScheduler`                          |
| Math             | numpy, pandas, scipy (Black-Scholes inversion)          |
| Ingestion        | databento 0.47 (Live + Historical)                      |
| Auth             | bcrypt API keys, PyJWT for admin JWT, Fernet for at-rest secrets |
| Rate limiting    | slowapi (per-key / per-IP) + custom sliding-window      |
| Logging          | structlog (JSON)                                        |
| Holiday calendar | `holidays` package (NYSE preferred, US federal fallback) |
| HTTP client      | httpx                                                   |

### Infra
Docker Compose with two services:
- `db` (TimescaleDB, internal only)
- `backend` (FastAPI on port 8000)

---

## Database schema

### Hypertables (time-series, 7-day retention default)
| Table                  | Grain                                        |
|------------------------|----------------------------------------------|
| `options_chain`        | OPRA snapshots: (ts, symbol, expiration, strike, option_type) → oi, volume, iv, delta, gamma, bid, ask, last_price, underlying_price |
| `computed_metrics`     | (ts, symbol, metric_type, strike, expiration) → value + JSONB extra |
| `futures_ticks`        | GLBX MDP 3.0 trade tape (~5M rows/day on ES) |
| `options_trades`       | OPRA trade tape with Lee-Ready side + signed_premium pre-computed |
| `liquidity_snapshots`  | MBP-10 top-of-book JSONB snapshots (1Hz)     |

### Regular tables
| Table                   | Purpose                                     |
|-------------------------|---------------------------------------------|
| `api_keys`              | bcrypt-hashed, key_prefix, allowed_symbols, expiry, usage_count |
| `admin_users`           | future multi-admin (today bootstrap admin from env) |
| `flow_events`           | SWEEP / BLOCK / UOA detections              |
| `alert_rules`           | JSONB predicate-tree user alerts            |
| `alert_events`          | Alert firings                               |
| `eod_open_interest`     | Daily EOD OI fallback when intraday OI null |
| `pipeline_runs`         | Audit row per scheduler tick per symbol     |
| `session_events`        | session_open / session_close / reset audit  |
| `metric_type_registry`  | Reference catalogue of every emitted metric |
| `databento_api_keys`    | Encrypted failover pool (Fernet)            |
| `dead_letter_queue`     | Unparsable ingestion payloads               |
| `backfill_checkpoints`  | (dataset, symbol) → last_completed_at       |
| `contract_adv`          | N-day ADV per contract (UOA threshold)      |

Migrations 0001 → 0013. Migration 0008 drops the public-auth tables
(`users`, `access_requests`, `user_sessions`) introduced in migration 0006;
0009 drops a redundant `computed_metrics` index; 0010 adds
`api_keys.key_lookup` for O(1) auth; 0011 swaps the `flow_events` index
direction; 0012 ships SEC-1/SEC-2/SEC-10 (legacy-key deactivation,
`jwt_revocations`, `admin_audit_events`); 0013 (Rev 9 DT-6) constrains
`computed_metrics.metric_type` against `metric_type_registry` via FK.

---

## Processing engine — `backend/app/processing/`

The pipeline runs every `COMPUTE_INTERVAL_SECONDS` (default 60s) per supported
symbol. Each tick is logged in `pipeline_runs` with status: `running` → `ok` |
`partial` | `failed` | `session_open` | `session_close`.

### Modules
| File                | Output                                                  |
|---------------------|---------------------------------------------------------|
| `pipeline.py`       | Orchestrator. Loads chain, resolves spot, runs every metric, persists, audits, fans out to streaming subscribers |
| `loader.py`         | Latest options-chain snapshot loader                    |
| `session.py`        | RTH gate (09:30–16:15 ET, NYSE holiday-aware), τ for 0DTE, expiration-day check, session_snapshot dict |
| `spot.py`           | Futures-basis EMA → put-call parity → stale-cache spot resolver |
| `bsm.py`            | Black-Scholes pricing + Newton/Brent IV inversion       |
| `iv.py`             | ATM IV, IV skew per expiry, full IV surface             |
| `gex.py`            | GEX per strike (`γ·W·100·S²·0.01`), call/put, net, top ±5, zero-gamma. Fallback chain: oi → volume → premium → uniform |
| `vanna_charm.py`    | Vanna + Charm net total + per-strike curve              |
| `max_pain.py`       | Per-expiry max pain + 5-expiry aggregate                |
| `walls.py`          | Top-3 call/put walls by OI and by volume                |
| `regime.py`         | Bullish/neutral/bearish from net GEX, with hysteresis (`GEX_REGIME_THRESHOLD`) |
| `zero_gamma.py`     | Zero-gamma flip level                                   |
| `term_structure.py` | IV term structure + 25-delta risk reversal per expiry   |
| `move_tracker.py`   | Realized-vs-implied move ratio                          |
| `pin_probability.py`| Per-strike 0DTE pin probability heatmap                 |
| `zero_dte.py`       | 0DTE / back-month split (GEX_OI/VOL, Charm, decay rate, flip speed Δ/Δt) |
| `futures_levels.py` | Translates cash levels → futures coordinates via EMA basis |
| `lee_ready.py`      | Trade-side classifier (quote-rule + tick fallback)      |
| `hiro.py`           | Cumulative signed premium per bucket                    |
| `flow_events.py`    | SWEEP / BLOCK / UOA detection                           |
| `flow_pipeline.py`  | Window-based flow + HIRO + basis + volume_profile + persists |
| `alert_pipeline.py` | Evaluates alert_rules predicates → alert_events         |
| `basis.py`          | SPX–ES basis snapshot                                   |
| `volume_profile.py` | Daily ES volume profile (price bins)                    |
| `scheduler.py`      | APScheduler config: 60s compute (RTH-gated, bounded concurrency 4), 22:30 UTC EOD OI, 09:29 ET session_open, 16:16 ET session_close |

### Metric type discriminator (pipeline contract)
Single chain pipeline tick is expected to emit ~36 distinct `metric_type`
values. Set lives in `EXPECTED_METRIC_TYPES` in `pipeline.py`. Any shortfall
downgrades run status from `ok` to `partial` and is recorded in
`pipeline_runs.missing_metric_types`. Highlights:

```
GEX_NET_TOTAL / _VOL                CHARM_NET_TOTAL / _LEVEL
GEX_LEVEL / _LEVEL_VOL              VANNA_NET_TOTAL / _LEVEL
MAX_PAIN / MAX_PAIN_AGG             IV_TERM_STRUCTURE / RISK_REVERSAL_25D
CALL_WALL_OI / _VOL                 MOVE_TRACKER / PIN_PROBABILITY
PUT_WALL_OI / _VOL                  REGIME_OI / REGIME_VOL
ATM_IV / IV_SKEW / IV_SURFACE       SPOT
GEX_0DTE_NET_TOTAL / _LEVEL / _VOL / _LEVEL_VOL
GEX_BACK_NET_TOTAL / _LEVEL / _VOL / _LEVEL_VOL
CHARM_0DTE_NET_TOTAL / _LEVEL / _DECAY_RATE
GEX_0DTE_FLIP_SPEED
HIRO / BASIS_SPX_ES / VOLUME_PROFILE_ES   (flow pipeline cadence)
```

0DTE rows are persisted **every tick** even on non-0DTE days: `value=0` with
`extra_json.reason="no_0dte_today"`.

---

## Ingestion — `backend/app/ingestion/`

| File                     | Role                                                |
|--------------------------|-----------------------------------------------------|
| `databento_live.py`      | OPRA Pillar live: definition + cmbp-1 + trades + statistics. In-memory contract registry by `instrument_id`. Reconnect with exponential backoff. Drops unsupported schemas at gateway-error. Emits `options_chain` + `options_trades` rows. Diagnostics for `/admin/inspector`. |
| `databento_globex.py`    | GLBX.MDP3 live (ES + NQ). Same machinery, writes `futures_ticks`. |
| `databento_historical.py`| Phase 1 contract definitions + Phase 2 cmbp-1 NBBO backfill |
| `databento_eod_oi.py`    | Daily EOD Open Interest pull → `eod_open_interest`  |
| `key_pool.py`            | Resolution + failover for Databento keys: env first, then DB pool sorted by priority ASC. ≥5 errors → 30 min cooldown. Reset on success. |
| `writer.py`              | `OptionsChainWriter` — buffered upserts to `options_chain` |
| `bulk_writers.py`        | `BulkUpsertWriter` for futures_ticks / options_trades / flow_events / liquidity_snapshots, plus periodic flush loop and backpressure |
| `dlq.py`                 | Dead-letter queue ring buffer                       |

Writer policy: `UPSERT_BATCH_SIZE=1000`, hard cap `INGESTION_MAX_PENDING_ROWS=10000`
with synchronous flush on overflow. DLQ cap `INGESTION_DLQ_MAX_SIZE=1000`.
Live registry refresh every `INGESTION_REGISTRY_REFRESH_SECONDS=14400` (4h).

---

## API surface — `backend/app/api/endpoints/`

### Public
- `GET /health` — DB ping, OPRA/GLBX feed freshness, pipeline_running, last_compute per symbol. Always 200.

### End-user data (require `X-API-Key` header, rate-limited 120/min/key)
All responses wrapped in `{symbol, computed_at, next_update_in_seconds, data}`.
- `GET /v1/{symbol}/gex` — full GEX curve, top ±5, net total. `mode=oi|volume`
- `GET /v1/{symbol}/max-pain` — per-expiry + aggregate. `expiry=nearest|all|YYYY-MM-DD`
- `GET /v1/{symbol}/walls` — top 3 call+put walls. `mode=oi|volume|both`
- `GET /v1/{symbol}/iv` — ATM IV, skew per expiry, full surface
- `GET /v1/{symbol}/snapshot` — comprehensive: GEX (both modes) + max_pain + walls + IV + regime + vanna/charm + zero_gamma + pin_probability + move_tracker + iv_term_structure + risk_reversal_25d + hiro_cumulative + flow_events_last_hour + session_state + spot + zero_dte + back_month
- `GET /v1/{symbol}/0dte` — curated subset for the 0DTE-focused page
- `GET /v1/{symbol}/spot` — standalone spot resolution + provenance
- `GET /v1/{symbol}/futures-levels` — cash levels translated into ES/NQ via EMA basis
- `GET /v1/{symbol}/flow` — SWEEP/BLOCK/UOA feed
- `GET /v1/{symbol}/hiro` — cumulative signed premium series

### Streaming
- `WS  /v1/{symbol}/stream` — pushes snapshot frames + 25s heartbeat. Auth via `X-API-Key` header or `?key=`. Per-key cap `MAX_WS_CONNECTIONS_PER_KEY=5`. Initial REST snapshot primes the connection
- `GET /v1/{symbol}/stream/sse` — SSE fallback for proxy environments that strip WS upgrades

### Admin (require `Authorization: Bearer <jwt>`)
- `POST   /admin/login` — `{username, password}` → `{access_token, expires_in_seconds}`. 5 req/min/IP
- `GET|POST|PATCH|DELETE /admin/api-keys[/...]` — CRUD, plaintext shown ONCE on create
- `GET    /admin/api-keys/{id}/usage`
- `GET    /admin/system/status` — Pipeline + ingestion + DB row counts + Rev 3 telemetry (futures_lag_ms, opra_lag_ms, dlq_pending, flow_events_last_hour, last_pipeline_runs[], live_ingester diagnostics)
- `GET|POST|PATCH|DELETE /admin/databento-keys[/...]` — Failover pool CRUD
- `POST   /admin/databento-keys/{id}/test` — Decryption sanity check
- `GET    /admin/inspector` — Data Inspector: per-table row counts, metric breakdown, latest metrics, term structure, pin probability, flow events, alert events, chain-quality coverage, ingester diagnostics

---

## Auth model

### Two layers
1. **Admin JWT** — HS256 signed with `JWT_SECRET`, `JWT_EXPIRE_MINUTES=480` (8h). Bootstrap admin from `ADMIN_USERNAME` / `ADMIN_PASSWORD` (plaintext OR bcrypt hash starting with `$2`).
2. **API key** (machine-to-machine) — generated as `ak_<urlsafe-token>`, bcrypt-hashed, only 11-char `key_prefix` stored in plaintext for display. Per-key `allowed_symbols` ACL. Plaintext shown ONCE at creation.

### Databento key pool (Fernet at rest)
`databento_api_keys.api_key_encrypted` is Fernet-encrypted. The Fernet key is
derived deterministically from `JWT_SECRET` via HKDF-SHA256 with fixed salt
`pantek-waang.crypto.v1` and info `databento-api-key-encryption`. **Rotating
`JWT_SECRET` invalidates every encrypted key in the pool** — operators must
re-register them through the admin UI.

Resolution order on every connect: env (`DATABENTO_API_KEY_OPRA` /
`DATABENTO_API_KEY_GLOBEX` / legacy `DATABENTO_API_KEY`) first, then DB rows
matching dataset (`OPRA.PILLAR` / `GLBX.MDP3` / `BOTH`) by priority ASC. Keys
with `error_count >= 5` are skipped for 30 min after `last_error_at`.

---

## Configuration (env vars)

Loaded via `pydantic-settings` from `.env`. See `backend/app/config.py` for
defaults. Keys grouped by area:

```
# Databento
DATABENTO_API_KEY_OPRA        OPRA.PILLAR (options)
DATABENTO_API_KEY_GLOBEX      GLBX.MDP3 (CME futures)
DATABENTO_API_KEY             legacy single-key fallback

# Database
DATABASE_URL                  postgresql+asyncpg://...
DB_POOL_SIZE=20  DB_MAX_OVERFLOW=10  DB_POOL_RECYCLE_SECONDS=3600  DB_POOL_PRE_PING=true

# Admin auth
ADMIN_USERNAME=admin  ADMIN_PASSWORD=changeme
JWT_SECRET                    HS256 secret (also used to derive Fernet key for key pool)
JWT_EXPIRE_MINUTES=480

# Symbols / cadence
SUPPORTED_SYMBOLS=SPXW,NDXP
RISK_FREE_RATE=0.05
DATA_RETENTION_DAYS=7
COMPUTE_INTERVAL_SECONDS=60
HISTORICAL_BACKFILL_DAYS=7

# Ingestion behavior
DISABLE_LIVE_INGESTION=false
DISABLE_HISTORICAL_BACKFILL=false
UPSERT_BATCH_SIZE=1000
INGESTION_MAX_PENDING_ROWS=10000  INGESTION_DLQ_MAX_SIZE=1000
INGESTION_REGISTRY_REFRESH_SECONDS=14400
FUTURES_FEED_LAG_WARN_MS=5000
MAX_WS_CONNECTIONS_PER_KEY=5

# Processing thresholds
GEX_REGIME_THRESHOLD=0.2
FLOW_SWEEP_MIN_PREMIUM=50000  FLOW_BLOCK_MIN_SIZE=100  FLOW_UOA_VOL_OI_RATIO=2.0

# RTH / 0DTE / spot resolver (Rev 4)
RTH_OPEN_TIME=09:30  RTH_CLOSE_TIME=16:15
SPOT_PARITY_DEVIATION_WARN_PCT=0.5
SPOT_STALE_CACHE_MAX_AGE_SECONDS=300
SPOT_BASIS_EMA_ALPHA=0.1
ATM_BAND_PCT_0DTE=0.005
OVERRIDE_RTH_GATE=false        DEV ONLY — bypass RTH gate

# CORS
ADMIN_CORS_ORIGINS=http://localhost:3000   # consumer origin — adjust for prod

# Misc
RATE_LIMIT_PER_MINUTE=120
LOG_LEVEL=INFO
ENABLE_OPENAPI_DOCS=true       set false in prod to hide /docs /redoc /openapi.json
```

---

## Operational hooks

### App lifespan (`main.py`)
On startup (non-test mode):
1. Configure structlog + install uvicorn log redaction (scrubs `token=`, `key=`, `code=`, `state=`, `Authorization` from query strings/log records)
2. Refuse to boot if `ADMIN_PASSWORD` / `JWT_SECRET` are at known defaults
3. Start periodic flush loops for all bulk writers
4. Run historical backfill (definitions + cmbp-1 NBBO)
5. Run EOD OI ingestion
6. Force one pipeline tick per supported symbol (so dashboard has data immediately, RTH-gate-bypassed)
7. Start OPRA + GLBX live ingesters
8. Start scheduler

On shutdown: scheduler.shutdown → ingester.stop → cancel background tasks → final writer flush → dispose DB engine.

### Middleware
- `_SecurityHeadersMiddleware` (pure ASGI) — HSTS, x-content-type-options, referrer-policy, x-frame-options DENY, permissions-policy, CSP (HTML-tight for `/docs` `/redoc`, JSON-strict otherwise)
- `CORSMiddleware` — `ADMIN_CORS_ORIGINS`. `allow_credentials` flips to false if any origin is `*`
- `GZipMiddleware` — minimum 1KB

### Scheduler jobs
- `compute_pipeline` — every `COMPUTE_INTERVAL_SECONDS`, RTH-gated, fan-out across symbols with `Semaphore(4)`
- `eod_oi_daily` — cron 22:30 UTC (~17:30 ET, after market close)
- `eod_oi_startup` — once on startup
- `session_open` — cron mon-fri 09:29 ET → reset basis cache + flip-speed cache + write `session_open` audit
- `session_close` — cron mon-fri 16:16 ET → write `session_close` audit

---

## Tests

`backend/tests/` — pure-function tests run without DB; DB-backed
tests need `TEST_DATABASE_URL` or auto-spin a Postgres testcontainer.

Coverage areas:
- `test_processing_*` — every metric calculator (gex, max_pain, walls, iv, vanna_charm, regime, lee_ready, hiro, flow_events, basis, futures_levels, move_tracker, pin_probability, term_structure, volume_profile, zero_gamma, spot)
- `test_zero_dte.py`, `test_session.py`, `test_spot_resolver.py` — Rev 4
- `test_bsm_correctness.py`, `test_property_iv.py` — Hypothesis property tests
- `test_metrics_correctness.py`, `test_flow_correctness.py`
- `test_pipeline_hardening.py`, `test_dlq_and_backpressure.py`
- `test_api_admin.py`, `test_api_auth.py`, `test_api_hardening.py`
- `test_streaming_api.py`
- `test_security.py`, `test_crypto.py`, `test_key_pool.py`

Lint with `ruff check app tests`.

---

## Quick commands

```bash
# Full stack (db + backend)
docker compose up --build

# Backend dev (requires running Postgres+TimescaleDB)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export DATABASE_URL=postgresql+asyncpg://options:options@localhost:5432/options_db
alembic upgrade head
uvicorn app.main:app --reload

# Tests + lint
APP_TESTING=1 python -m pytest -q
python -m ruff check app tests
```
