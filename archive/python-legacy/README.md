# FlowGreeks Engine

Options analytics backend for index options (SPXW, NDXP). Live + historical
OPRA Pillar ingestion via [Databento](https://databento.com), TimescaleDB
time-series storage, a derivatives-metrics processing engine
(GEX, max pain, walls, IV, vanna/charm, HIRO, flow events), and a secured
REST + WebSocket + SSE API.

This repository is **backend-only**. Consumers (trader UIs, partner
integrations, codegen clients) connect over the documented `/v1/*` API
surface. The stability contract is in [API_POLICY.md](API_POLICY.md);
the consumer-visible changelog is in [CHANGELOG.md](CHANGELOG.md).

---

## Architecture at a glance

```
                         ┌──────────────────────┐
       OPRA Pillar       │     Databento        │
       (live + hist.)──▶ │   ingestion clients  │
       GLBX MDP3         └────────┬─────────────┘
                                  │ batches
                                  ▼
                         ┌──────────────────────┐        ┌─────────────────┐
                         │   options_chain      │        │  computed_      │
                         │   futures_ticks      │◀──────▶│  metrics        │
                         │   options_trades     │  60s   │  (TimescaleDB)  │
                         │   (TimescaleDB)      │        │                 │
                         └────────┬─────────────┘        └────────┬────────┘
                                  │                               │
                         ┌────────▼─────────────┐        ┌────────▼─────────┐
                         │  Processing engine   │        │   FastAPI        │
                         │  GEX / MaxPain /     │───────▶│   /v1/* + admin  │
                         │  Walls / IV / Vanna /│        │   X-API-Key, JWT │
                         │  Charm / HIRO / Flow │        │                  │
                         └──────────────────────┘        └────────┬─────────┘
                                                                  │
                                                            REST / WS / SSE
                                                                  ▼
                                                          API consumers
```

---

## Tech stack

| Layer            | Choice                                               |
|------------------|------------------------------------------------------|
| Runtime          | Python 3.11                                          |
| Framework        | FastAPI + SQLAlchemy 2 (async) + asyncpg             |
| Data store       | PostgreSQL 15 + **TimescaleDB** extension            |
| Migrations       | Alembic (async)                                      |
| Scheduling       | APScheduler `AsyncIOScheduler`                       |
| Ingestion        | `databento` Python client (Live + Historical)        |
| Math             | `numpy`, `pandas`, `scipy` (Black-Scholes inversion) |
| Auth             | bcrypt-hashed API keys + JWT for admin endpoints     |
| Rate limiting    | `slowapi` (per-API-key minute window)                |
| Logging          | Structured JSON via `structlog`                      |
| Containerization | Docker + Docker Compose                              |

---

## Running with Docker Compose

```bash
cp .env.example .env
# edit .env — at minimum set the Databento keys, ADMIN_PASSWORD, JWT_SECRET,
# and DB_ENCRYPTION_KEY:
#   DATABENTO_API_KEY_OPRA   → OPRA.PILLAR (options)
#   DATABENTO_API_KEY_GLOBEX → GLBX.MDP3   (CME futures)
# (the legacy single ``DATABENTO_API_KEY`` is still honoured as a fallback)
docker compose up --build
```

Services:

- **db** – TimescaleDB (PostgreSQL 15) – internal only
- **backend** – FastAPI on `http://localhost:8000`

The backend container automatically runs `alembic upgrade head` on startup,
runs the historical backfill, starts the live Databento stream, and starts
the 60s compute scheduler.

If you don't have a Databento key yet, set `DISABLE_LIVE_INGESTION=true`
and `DISABLE_HISTORICAL_BACKFILL=true` in `.env`. The API will still come
up cleanly; the data endpoints will return empty payloads until data is
ingested.

---

## Configuration (`.env`)

| Variable                       | Default                                | Notes                                                       |
|--------------------------------|----------------------------------------|-------------------------------------------------------------|
| `DATABENTO_API_KEY_OPRA`       | —                                      | API key for OPRA.PILLAR (live + historical options).        |
| `DATABENTO_API_KEY_GLOBEX`     | —                                      | API key for GLBX.MDP3 (CME ES/NQ futures live tape).        |
| `DATABENTO_API_KEY`            | —                                      | Legacy single-key fallback used if the two above are empty. |
| `DATABASE_URL`                 | `postgresql+asyncpg://options:options@db:5432/options_db` | Async SQLAlchemy URL.       |
| `ADMIN_USERNAME`               | `admin`                                | Admin login.                                                |
| `ADMIN_PASSWORD`               | `changeme`                             | Plain text **or** bcrypt hash starting with `$2`.           |
| `JWT_SECRET`                   | —                                      | HMAC secret for admin JWT tokens.                           |
| `JWT_EXPIRE_MINUTES`           | `60`                                   | Admin token lifetime.                                       |
| `DB_ENCRYPTION_KEY`            | —                                      | Operator-managed Fernet root for encrypted Databento keys.  |
| `SUPPORTED_SYMBOLS`            | `SPXW,NDXP`                            | Comma-separated underlyings.                                |
| `RISK_FREE_RATE`               | `0.05`                                 | Used as `r` in Black-Scholes IV inversion.                  |
| `DATA_RETENTION_DAYS`          | `7`                                    | TimescaleDB drops data older than this.                     |
| `COMPUTE_INTERVAL_SECONDS`     | `60`                                   | Pipeline cadence.                                           |
| `HISTORICAL_BACKFILL_DAYS`     | `7`                                    | Window pulled on first startup.                             |
| `DISABLE_LIVE_INGESTION`       | `false`                                | Set `true` to skip the live stream (dev/testing).           |
| `DISABLE_HISTORICAL_BACKFILL`  | `false`                                | Set `true` to skip the historical pull (dev/testing).       |
| `RATE_LIMIT_PER_MINUTE`        | `120`                                  | Per-API-key rate limit on `/v1/*`.                          |
| `GEX_REGIME_THRESHOLD`         | `0.2`                                  | Regime hysteresis deadband.                                 |
| `FLOW_SWEEP_MIN_PREMIUM`       | `50000`                                | Sweep detection floor in USD.                               |
| `FLOW_BLOCK_MIN_SIZE`          | `100`                                  | Block detection floor in contracts.                         |
| `FLOW_UOA_VOL_OI_RATIO`        | `2.0`                                  | UOA volume/OI threshold.                                    |
| `UPSERT_BATCH_SIZE`            | `1000`                                 | Buffered writer batch size.                                 |
| `INGESTION_MAX_PENDING_ROWS`   | `10000`                                | Per-writer backpressure cap.                                |
| `INGESTION_DLQ_MAX_SIZE`       | `1000`                                 | Dead-letter queue ring-buffer cap.                          |
| `INGESTION_REGISTRY_REFRESH_SECONDS` | `14400`                          | Live contract registry refresh interval.                    |
| `FUTURES_FEED_LAG_WARN_MS`     | `5000`                                 | Stale futures feed warn threshold.                          |
| `MAX_WS_CONNECTIONS_PER_KEY`   | `5`                                    | Streaming API per-key cap.                                  |
| `ADMIN_CORS_ORIGINS`           | `http://localhost:3000`                | Allowed origins for `/v1/*` and `/admin/*`. Set to your real consumer origin in prod; never leave as `*`. |

For a full env-var inventory grouped by area see
[PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md). For the consumer-visible API
record see [CHANGELOG.md](CHANGELOG.md). The streaming API and admin
telemetry endpoints are documented in
[docs/api_reference.md](docs/api_reference.md).

---

## Database schema

Hypertables (TimescaleDB) with a 7-day retention policy and compression on
data older than 1 day:

- `options_chain` – partitioned by `ts`. Holds the latest snapshot per
  `(symbol, expiration, strike, option_type)` along with OI, volume, IV,
  greeks, bid/ask, last price, and underlying price.
- `computed_metrics` – partitioned by `ts`. One row per metric per cycle.
  `metric_type` discriminator covers GEX (OI / volume / 0DTE / back-month
  variants), max pain (per-expiry + aggregate), walls (OI / volume),
  IV (ATM / skew / surface / term-structure / 25-delta risk reversal),
  regime (OI / vol), vanna, charm, zero-gamma, move tracker, pin
  probability, HIRO, basis, volume profile, and spot. The full set lives
  in `EXPECTED_METRIC_TYPES` (`backend/app/processing/pipeline.py`)
  and the `metric_type_registry` reference table.
- `futures_ticks` – Globex MDP 3.0 trade tape (one row per tick), 14-day
  retention.
- `options_trades` – OPRA Pillar trade tape with Lee-Ready `side` and
  dealer-signed premium pre-computed at ingest, 14-day retention.
- `liquidity_snapshots` – top-N order-book snapshots from MBP-10 (1Hz),
  bids/asks stored as JSONB.

Regular tables: `api_keys`, `admin_users`, `flow_events`, `alert_rules`,
`alert_events`, `eod_open_interest`, `pipeline_runs`, `session_events`,
`metric_type_registry`, `databento_api_keys`, `dead_letter_queue`,
`backfill_checkpoints`, `contract_adv`, `jwt_revocations`,
`admin_audit_events`.

Apply migrations with `alembic upgrade head` (the backend container does
this automatically on startup).

---

## API reference

### Public

| Method | Path              | Description                                                    |
|--------|-------------------|----------------------------------------------------------------|
| GET    | `/health`         | Liveness only — `{status, ts}`. No auth.                       |
| GET    | `/ready`          | Readiness gate; HTTP 503 when last pipeline tick is stale.     |
| GET    | `/v1/symbols`     | `{symbols: string[]}` — supported underlyings.                 |

### End-user data (require `X-API-Key` header)

All responses are wrapped in
`{ symbol, computed_at, next_update_in_seconds, data }` **except**
`/v1/{symbol}/flow` and `/v1/{symbol}/hiro` (see
[API_POLICY.md](API_POLICY.md) § 1a).

| Method | Path                              | Returns                                                              |
|--------|-----------------------------------|----------------------------------------------------------------------|
| GET    | `/v1/{symbol}/gex`                | Full GEX curve, top ±5 levels, net total. `mode=oi\|volume`, `expiry=YYYY-MM-DD\|all`. |
| GET    | `/v1/{symbol}/max-pain`           | Max-pain strike per expiration + aggregate.                          |
| GET    | `/v1/{symbol}/walls`              | Top 3 call & put wall strikes per mode (`oi\|volume\|both`).         |
| GET    | `/v1/{symbol}/iv`                 | ATM IV, IV skew per expiry, full IV surface, term structure.        |
| GET    | `/v1/{symbol}/snapshot`           | All of the above merged into a single payload.                       |
| GET    | `/v1/{symbol}/0dte`               | Curated 0DTE-focused subset.                                         |
| GET    | `/v1/{symbol}/spot`               | Spot resolution + provenance.                                        |
| GET    | `/v1/{symbol}/futures-levels`     | Cash levels translated into ES/NQ via EMA basis.                     |
| GET    | `/v1/{symbol}/flow`               | SWEEP / BLOCK / UOA event feed (bare envelope).                      |
| GET    | `/v1/{symbol}/hiro`               | Cumulative signed-premium / delta-notional series (bare envelope).   |
| GET    | `/v1/{symbol}/history`            | Bucketed time-series over `computed_metrics`.                        |
| GET    | `/health/detail`                  | Full operational telemetry (DB, ingestion, pipeline, supported_symbols). |

End-user endpoints are rate-limited to **120 req/min per API key**
(configurable via `RATE_LIMIT_PER_MINUTE`, falling back to client IP).

### Streaming

| Method | Path                              | Notes                                                                |
|--------|-----------------------------------|----------------------------------------------------------------------|
| WS     | `/v1/{symbol}/stream`             | Pipeline-snapshot frames + 25s heartbeat. Auth via `X-API-Key` header or `?key=`. Per-key cap `MAX_WS_CONNECTIONS_PER_KEY=5`. Supports `?since_seq=` resume. |
| WS     | `/v1/{symbol}/stream/ticks`       | Raw spot/futures tick fan-out.                                       |
| GET    | `/v1/{symbol}/stream/sse`         | SSE fallback (also supports `?since_seq=`).                          |

Mid-stream key revocation closes the WS with code `4401` — fatal.

### Admin (require `Authorization: Bearer <jwt>`)

| Method | Path                              | Body / params                                                       |
|--------|-----------------------------------|---------------------------------------------------------------------|
| POST   | `/admin/login`                    | `{ username, password }` → `{ access_token, expires_in_seconds }`.  |
| POST   | `/admin/logout`                   | Server-side JWT revocation.                                         |
| POST   | `/admin/refresh-token`            | Mint a new JWT from a still-valid (or recently-expired) one.        |
| GET    | `/admin/api-keys`                 | List all keys (no plaintext).                                       |
| POST   | `/admin/api-keys`                 | `{ label, allowed_symbols, expires_at? }` → returns plaintext once. |
| PATCH  | `/admin/api-keys/{id}`            | Update label / symbols / expiry / `is_active`.                      |
| DELETE | `/admin/api-keys/{id}`            | Revoke.                                                             |
| GET    | `/admin/api-keys/{id}/usage`      | Per-key usage stats.                                                |
| GET    | `/admin/system/status`            | Pipeline + ingestion + DB row counts + telemetry.                   |
| GET    | `/admin/databento-keys[/...]`     | CRUD for the encrypted Databento failover pool.                     |
| POST   | `/admin/databento-keys/{id}/test` | Decryption sanity check.                                            |
| GET    | `/admin/inspector`                | Per-table row counts + chain-quality coverage + ingester diagnostics. |
| GET    | `/admin/metrics`                  | Prometheus exposition.                                              |

See [docs/api_reference.md](docs/api_reference.md) for full payload
shapes and query semantics, and [API_POLICY.md](API_POLICY.md) for the
versioning, deprecation, and discriminator policy.

---

## Local backend development

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# point at a running Postgres (with TimescaleDB) — e.g. via `docker compose up db`
export DATABASE_URL=postgresql+asyncpg://options:options@localhost:5432/options_db
alembic upgrade head
uvicorn app.main:app --reload
```

### Running tests

```bash
cd backend
APP_TESTING=1 pytest                                       # pure-function + security suite
TEST_DATABASE_URL=postgresql+asyncpg://... pytest          # adds DB-backed API/admin tests
pytest tests/test_processing_gex.py::test_name -v          # single test
pytest -m property                                         # Hypothesis property tests
pytest -m integration                                      # integration suite
```

If `TEST_DATABASE_URL` is not set, the conftest tries to spin up a Postgres
testcontainer; if Docker is unreachable, DB-backed tests are skipped and
pure-function tests still run. The conftest sets `APP_TESTING=1` and
disables ingestion/backfill so tests never hit Databento.

### Lint

```bash
cd backend && ruff check .
```

Line length 110. Migrations under `app/db/migrations/versions/` are
excluded from ruff. `pyproject.toml` sets `asyncio_mode = "auto"`.

---

## Security notes

- API keys are generated as `ak_<urlsafe-token>` and stored as a bcrypt
  hash. Only the 11-character `key_prefix` is plaintext for display; a
  keyed-BLAKE2b digest in `key_lookup` powers O(1) auth lookup.
- Admin JWTs are HS256-signed with `JWT_SECRET` and expire after
  `JWT_EXPIRE_MINUTES` (default 60). Logout writes the `jti` to
  `jwt_revocations`; every verify polls the table.
- API key auth checks: existence, `is_active`, expiry, per-key allowed
  symbols.
- Rate limiting is keyed on the `X-API-Key` header (falling back to
  client IP).
- The plaintext API key is shown to the admin **once** at creation time
  and is never stored or logged.
- `databento_api_keys.api_key_encrypted` is Fernet-encrypted with a key
  derived from `DB_ENCRYPTION_KEY` (HKDF-SHA256). Rotating
  `DB_ENCRYPTION_KEY` invalidates every encrypted Databento key in the
  pool — operators must re-register them through the admin API.

---

## Troubleshooting

**Live ingestion is failing with auth errors.** Confirm
`DATABENTO_API_KEY_OPRA` (for options) and `DATABENTO_API_KEY_GLOBEX`
(for futures) are set in `.env` and that the keys have OPRA Pillar live +
historical access (resp. GLBX MDP3 access).

**Compute pipeline reports `pipeline_no_data` for a symbol.** Either no
data has been ingested yet (give the live stream and historical backfill
a minute), or the symbol isn't in `SUPPORTED_SYMBOLS`.

**Admin login returns 401 with the right password.** Ensure the
`ADMIN_PASSWORD` in `.env` matches what you typed. If you store a bcrypt
hash, it must start with `$2`.

**Migrations fail in tests.** The test conftest creates schema via
`Base.metadata.create_all` (skipping TimescaleDB extension calls). For
production deployments, `alembic upgrade head` requires the TimescaleDB
extension.

For deeper architecture / pipeline / migration safety policy see
[CLAUDE.md](CLAUDE.md), [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md), and
[OPS.md](OPS.md).
