# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Persona

This is a **backend / quant engineering** workspace. There is no UI in this repo; consumers connect over the documented REST + WebSocket + SSE surface.

When you work here, wear four hats simultaneously:

- **Backend engineer** — FastAPI + SQLAlchemy 2 async + asyncpg + TimescaleDB. Care about latency budgets (1s end-to-end pipeline target), idempotency, backpressure, graceful degradation, observability.
- **Quant engineer** — implement metrics that match published methodology (SpotGamma, Squeeze Metrics) precisely. Validate against canonical references. The numbers must be defensible to a trader who knows the math.
- **Options Greeks specialist** — Black-Scholes-Merton in vectorised numpy. τ conventions matter (calendar vs trading-day, 0DTE session-aware). Numerical stability near expiry is critical (TAU_FLOOR_YEARS). Greeks correctness is non-negotiable.
- **Data engineer** — Databento OPRA + GLBX ingestion, hypertable management, retention, DLQ, key pool rotation. Hot path is vectorised; cold path can be Pythonic.

When in doubt, prioritise correctness over speed, and observability over cleverness. A `partial` pipeline run that emits structured metrics and audits the gap is better than a faster one that silently drops data.

## Repository layout

```
flowgreeks-engine/
├── backend/                FastAPI app, processing engine, ingestion, alembic migrations, tests
├── docs/                   api_reference.md
├── docker-compose.yml
├── README.md               consumer-facing tour of the surface
├── PROJECT_OVERVIEW.md     deeper structural map; read this when in doubt
├── API_POLICY.md           stability contract for /v1/*
├── CHANGELOG.md            consumer-visible changelog
├── CHANGELOG.json          machine-readable companion to CHANGELOG.md
├── OPS.md                  operational runbook
├── ROADMAP.md              deferred production-readiness items
└── CLAUDE.md               this file
```

There is no `.cursor/rules` or `.github/copilot-instructions.md`.

## Common commands

### Stack up
```bash
cp .env.example .env          # set DATABENTO_API_KEY_OPRA, _GLOBEX, ADMIN_PASSWORD, JWT_SECRET, DB_ENCRYPTION_KEY
docker compose up --build     # db (timescale) + backend on :8000
```

If you don't have Databento keys: set `DISABLE_LIVE_INGESTION=true` and `DISABLE_HISTORICAL_BACKFILL=true` — the API still boots cleanly.

### Backend dev (`backend/`)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

export DATABASE_URL=postgresql+asyncpg://options:options@localhost:5432/options_db
alembic upgrade head
uvicorn app.main:app --reload

ruff check .                       # lint (config in pyproject.toml)
APP_TESTING=1 pytest               # pure-function + security tests (no DB needed)
TEST_DATABASE_URL=postgresql+asyncpg://... pytest    # adds DB-backed API/admin/streaming tests
pytest tests/test_processing_gex.py::test_name -v    # single test
pytest -m property                 # hypothesis property tests
pytest -m integration              # integration suite
```

`pyproject.toml` sets `asyncio_mode = "auto"` and excludes `app/db/migrations/versions` from ruff. Line length 110.

If `TEST_DATABASE_URL` is unset, `conftest.py` tries to spin up a Postgres testcontainer; if Docker is unreachable, DB-backed tests are skipped silently and pure-function tests still run. Conftest sets `APP_TESTING=1` and disables ingestion/backfill so tests never hit Databento.

### Migrations
Alembic auto-runs on backend container startup. Manually: `cd backend && alembic upgrade head`. Versions live in `app/db/migrations/versions/` (currently 0001 → 0014). Operationally-meaningful migrations:
- **0008** drops the public-auth tables (`users`, `access_requests`, `user_sessions`) introduced in 0006 — public auth has been retired, only admin remains.
- **0009** drops the redundant `ix_computed_metrics_symbol_type_ts` index (covered by `ix_computed_metrics_symbol_type_exp_ts`). Halves write amplification on metric upserts.
- **0010** adds `api_keys.key_lookup` (keyed BLAKE2b digest) for O(1) auth lookup. Pre-existing rows have `key_lookup = NULL`. Rev 11 MIG-4: unique constraint now built via `CREATE UNIQUE INDEX CONCURRENTLY` + `ADD CONSTRAINT USING INDEX` to avoid blocking writes.
- **0011** swaps `flow_events` ascending `(symbol, ts)` index for descending `(symbol, ts DESC)`. Rev 11 MIG-3: now `CONCURRENTLY` inside `autocommit_block()`. Adds nullable `databento_api_keys.last_test_at`.
- **0012 (Rev 8)** deactivates legacy `key_lookup IS NULL` rows; bcrypt is one-way so the digest cannot be backfilled. Operators must regenerate. Rev 11 MIG-5: deactivation now `RAISE NOTICE` the count. Adds `api_keys.deleted_at`, `jwt_revocations`, `admin_audit_events`.
- **0013 (Rev 9)** adds FK `computed_metrics.metric_type` → `metric_type_registry.metric_type`. Rev 11 MIG-1: FK is now `NOT VALID` (no validation scan); MIG-2: orphan DELETE batched with `RAISE NOTICE` row count.
- **0014 (Rev 11)** runs `VALIDATE CONSTRAINT fk_computed_metrics_metric_type_registry`. Takes `ShareUpdateExclusiveLock` only — run off-hours but doesn't block writes.

### Migration safety policy (Rev 11)
- All `CREATE INDEX` / `CREATE UNIQUE INDEX` MUST use `CONCURRENTLY` inside `op.get_context().autocommit_block()`.
- All `ADD CONSTRAINT FK` MUST use `NOT VALID` followed by a separate `VALIDATE CONSTRAINT` migration.
- All bulk DML MUST be batched (`LIMIT 10000` loop) with `RAISE NOTICE` on row count.
- `env.py` acquires `pg_advisory_lock(5746728934251)` before running migrations to prevent multi-replica races.
- `SKIP_MIGRATIONS=1` env hatch breaks boot loops without code changes.

## Architecture big picture

**Data flow:** Databento (OPRA Pillar + GLBX MDP3) → bulk writers → TimescaleDB hypertables → 60s processing pipeline → `computed_metrics` → REST/WS/SSE API → external API consumers.

### Hypertables (`backend/app/db/models.py`, 7-day retention default)
- `options_chain` — latest snapshots keyed by (ts, symbol, expiration, strike, option_type)
- `computed_metrics` — one row per metric per cycle, `metric_type` string + JSONB `extra_json`
- `futures_ticks` — GLBX trade tape (~5M rows/day on ES)
- `options_trades` — OPRA trade tape with Lee-Ready `side` and signed_premium pre-computed at ingest
- `liquidity_snapshots` — MBP-10 top-of-book JSONB at 1Hz

### Processing engine (`backend/app/processing/`)
`pipeline.run_pipeline_for_symbol()` is the orchestrator. Every 60s per supported symbol it:
1. Loads latest snapshot via `loader.py` — bounded by `LOADER_SNAPSHOT_WINDOW_HOURS` (default 6h, was 2 days; tighter window reduces hypertable scan cost on every tick)
2. Resolves spot via `spot.py` (chain: futures-basis EMA → put-call parity → stale-cache)
3. Runs IV inversion via `iv.fill_missing_iv_async` (CPU work runs in a worker thread so the event loop keeps serving WS/SSE during warm-up)
4. Runs every metric calculator (gex, max_pain, walls, iv, vanna_charm, regime, zero_gamma, term_structure, move_tracker, pin_probability, zero_dte, futures_levels, hiro, flow_events, basis, volume_profile)
5. Persists rows in **one** session, audits the run in `pipeline_runs` (Rev 6 collapse: was 3 sessions per tick), fans out to streaming subscribers + writes through to the snapshot prime cache

**Pipeline contract:** Each tick must emit ~36 distinct `metric_type` values (see `EXPECTED_METRIC_TYPES` in `pipeline.py`). Any shortfall downgrades run status from `ok` to `partial` and is recorded in `pipeline_runs.missing_metric_types`. **0DTE rows are persisted every tick even on non-0DTE days** with `value=0` and `extra_json.reason="no_0dte_today"` — do not omit them.

**GEX weight fallback chain:** OI → volume → premium → uniform. Falling back is recorded in extras so callers know provenance. The hot path uses `gex._gex_vector` (vectorised numpy); per-tick `compute_zero_gamma` runs once on the chain-vol GEX path (Rev 9 PF-2) — `compute_gex(..., compute_zero_gamma=False)` at the other 5 call sites. `compute_max_pain` per-expiry payoff is broadcasting matmul (Rev 7 C4 / Rev 12 NUM2-3 — the latter caught it as re-regressed; closure-claim audit rule applies).

**Clock consistency rule (Rev 12 CORR-1/2):** every `today` derivation in `app/processing/` and `app/api/` MUST go through `_now_eastern()`. Never use `pd.Timestamp.utcnow()` or `datetime.utcnow()` for any path that derives "today" in date-branching logic. Anti-pattern grep: `! grep -RnE "Timestamp\.utcnow|datetime\.utcnow" backend/app/processing backend/app/api`.

**HIRO** (`processing/hiro.py`) follows the SpotGamma definition: **delta-notional** is the canonical signal (`customer_side · size · delta · 100`). When `delta` is unavailable (stale chain, IV not inverted yet) the calculator falls back to signed-premium and records the provenance in `extra_json.weight_source`. The flow pipeline runs `compute_hiro_incremental` after the first warm tick — only re-bucketises new trades and prunes expired buckets, instead of re-aggregating the full 60-minute window every cycle. Reset on session-open via `flow_pipeline.reset_hiro_state()`.

**τ convention:** `processing/session.calendar_tau_years()` is the single source of truth for calendar-day τ. Vanna/charm/zero_gamma/pin_probability all consume it. The 0DTE session-aware τ comes from `session.time_to_expiry_0dte_years()`. Vanna/charm additionally floor τ at `TAU_FLOOR_YEARS = 15 min` to keep Greeks numerically stable in the final minutes before expiry.

### Ingestion (`backend/app/ingestion/`)
- `databento_live.py` (OPRA: definition + cmbp-1 + trades + statistics) and `databento_globex.py` (GLBX: ES + NQ) maintain in-memory contract registries keyed by `instrument_id`. Periodic refresh **rebuilds** the registry rather than merging so expired contracts age out automatically (Rev 5 hardening).
- `key_pool.py` resolves Databento credentials: env vars (`DATABENTO_API_KEY_OPRA`, `_GLOBEX`, legacy `DATABENTO_API_KEY`) first, then DB rows from `databento_api_keys` sorted by `priority ASC`. Keys with `error_count >= 5` are skipped for 30 min after `last_error_at`.
- `writer.py` / `bulk_writers.py` — buffered upserts. `UPSERT_BATCH_SIZE=1000`, hard cap `INGESTION_MAX_PENDING_ROWS=10000` triggers synchronous flush; backpressure is real. Failing batches go to the DLQ with the full serialized payload (Rev 5).
- `dlq.py` — dead-letter ring buffer, cap `INGESTION_DLQ_MAX_SIZE=1000`.

### Auth model (`backend/app/core/security.py`, `crypto.py`)
Two layers:
1. **Admin JWT** — HS256 with `JWT_SECRET`, 60min default. Bootstrap admin from `ADMIN_USERNAME`/`ADMIN_PASSWORD` env (plaintext or bcrypt hash starting with `$2`). Login is **constant-time**: bcrypt runs even on a bad username so timing cannot enumerate valid usernames (Rev 5 fix).
2. **API key** — generated as `ak_<urlsafe-token>`, bcrypt-hashed in `api_keys.key_hash`. Only the 11-char `key_prefix` is stored plaintext for table display. Per-key `allowed_symbols` ACL. Plaintext shown ONCE on creation; never logged.

**Auth fast path (Rev 6):** `api_keys.key_lookup` holds a keyed BLAKE2b digest of the plaintext key. Both REST and WS auth resolve the candidate via `WHERE key_lookup = :digest` (O(1) via unique index) before running bcrypt — eliminates the prefix-collision bcrypt amplification surface. Pre-migration-0010 rows have `key_lookup = NULL`; the auth path falls back to a prefix scan and lazily backfills the digest on first successful verify.

**Critical:** `databento_api_keys.api_key_encrypted` is Fernet-encrypted with a key derived deterministically from **`DB_ENCRYPTION_KEY`** (falls back to `JWT_SECRET` if unset, for legacy deployments) via HKDF-SHA256 (salt `pantek-waang.crypto.v1`, info `databento-api-key-encryption`). **Rotating `DB_ENCRYPTION_KEY` invalidates every encrypted Databento key in the pool** — operators must re-register them through the admin API. The decoupling from `JWT_SECRET` (Rev 5) means the JWT signing key can now be rotated without breaking the encrypted pool.

### App lifecycle (`backend/app/main.py`)
Startup (skipped when `APP_TESTING=1`):
1. Configure structlog + uvicorn log redaction (scrubs `token=`, `key=`, `code=`, `state=`, `Authorization` from query strings/log records)
2. **Refuse to boot if `ADMIN_PASSWORD`/`JWT_SECRET` are at known defaults**
3. Start bulk-writer flush loops, run historical backfill, run EOD OI ingestion
4. Force one pipeline tick per supported symbol (RTH-gate-bypassed, so consumers have data immediately)
5. Start OPRA + GLBX live ingesters, start scheduler

### Scheduler (`backend/app/processing/scheduler.py`)
- `compute_pipeline` every `COMPUTE_INTERVAL_SECONDS`, **RTH-gated** (09:30–16:15 ET, NYSE holidays via `holidays` package), bounded concurrency `Semaphore(4)` across symbols
- `eod_oi_daily` cron 22:30 UTC + once on startup
- `session_open` mon–fri 09:29 ET → reset basis cache + flip-speed cache + write `session_open` audit
- `session_close` mon–fri 16:16 ET → write `session_close` audit
- Set `OVERRIDE_RTH_GATE=true` for dev to bypass RTH gating

### API surface (`backend/app/api/endpoints/`)

See [docs/api_reference.md](docs/api_reference.md) for full payload shapes and query semantics. [API_POLICY.md](API_POLICY.md) governs stability, deprecation, and discriminator rules for `/v1/*`.

- **Public:** `GET /health` (liveness only), `GET /ready`, `GET /v1/symbols`
- **End-user data** (require `X-API-Key`, rate-limited 120/min/key via slowapi keyed on the header, falling back to client IP): `/v1/{symbol}/{gex,max-pain,walls,iv,snapshot,0dte,spot,futures-levels,flow,hiro,history}`, `/health/detail`. The `snapshot` payload includes a `flow` field with the latest 50 events. `/v1/{symbol}/flow` and `/v1/{symbol}/hiro` are NOT wrapped in the standard envelope (frozen v1 exemption — see API_POLICY.md § 1a).
- **Streaming** (X-API-Key header or `?key=` query, cap `MAX_WS_CONNECTIONS_PER_KEY=5`):
  - `WS /v1/{symbol}/stream` — pipeline-snapshot frames + 25s heartbeat. Primed from the in-memory snapshot cache (Rev 6) — pipeline writes through after each tick, prime reads through within the 10s TTL, eliminating reconnect-storm DB hit. Supports `?since_seq=` resume from a per-symbol ring buffer (~60 frames).
  - `WS /v1/{symbol}/stream/ticks` — raw spot/futures ticks via `TickNotifier` (Rev 5).
  - `GET /v1/{symbol}/stream/sse` — SSE fallback (also reads through the snapshot cache, supports `?since_seq=`).
  - **Mid-stream revocation** runs as an independent watcher task that polls `api_keys` every 5s and closes the WS with code 4401 — no longer dependent on queue-timeout, so revocation fires even on busy streams (Rev 5 SEC-CRITICAL fix).
- **Admin** (require `Authorization: Bearer <jwt>`): `/admin/login`, `/admin/logout`, `/admin/refresh-token`, `/admin/api-keys[/...]`, `/admin/api-keys/{id}/usage`, `/admin/system/status`, `/admin/databento-keys[/...]`, `/admin/databento-keys/{id}/test`, `/admin/inspector`, `/admin/metrics` (Prometheus scrape).

**Security headers** middleware (`app/main.py`) ships HSTS, X-Content-Type-Options, Referrer-Policy, X-Frame-Options DENY, Permissions-Policy, and a CSP whose `'unsafe-inline'` relaxation is **scoped to `/docs`, `/redoc`, `/openapi.json`, `/docs/oauth2-redirect`** only (Rev 6). All other routes get the strict JSON CSP regardless of content-type.

All `/v1/*` data responses are wrapped in `{symbol, computed_at, next_update_in_seconds, data}` except the two exemptions noted above.

## Conventions and gotchas

- **Place all imports at the top of files.** Never import inside functions/classes.
- Async-only DB code via SQLAlchemy 2 + asyncpg. Don't introduce sync sessions.
- Migrations are gated for **plain Postgres compatibility** (TimescaleDB extension calls are no-ops without the extension). Tests use `Base.metadata.create_all`, not migrations.
- `metric_type` is a string discriminator. New metrics need an entry in `metric_type_registry` and likely in `EXPECTED_METRIC_TYPES`. Missing one downgrades pipeline runs to `partial`. Also add the metric_type to `_METRIC_TYPES` in `app/api/endpoints/snapshot.py` so it lands in the batch read instead of an N+1 individual lookup.
- **Weight-source provenance rule (Rev 7).** Any metric with a fallback chain MUST persist `weight_source` in `extra_json`. GEX, walls, and HIRO all comply.
- **`TAU_FLOOR_YEARS` is canonical in `app/processing/session.py`** — 15 minutes. Never redefine locally. pin_probability, vanna_charm, zero_dte, zero_gamma all import.
- **0DTE τ rule (Rev 8).** Any Greek-touching code path that takes a chain DataFrame MUST check `expiration == today` and use session-aware τ via `time_to_expiry_0dte_years(now=...)` floored at `TAU_FLOOR_YEARS`. The 1-day calendar floor is wrong for 0DTE all afternoon. iv.py, vanna_charm.py, zero_gamma.py, pin_probability.py all comply. Anti-pattern grep: `! grep -RnE "max\(1,.*\.days\)/365" backend/app/processing`.
- **`is_expiration_day(symbol, *, today=None, available_expirations=None)`** — chain-aware. Resolution: explicit arg → per-symbol cache populated by `set_available_expirations(symbol, expirations)` (pipeline writes the chain's expiry set every tick — verify the call survives any refactor) → static M/W/F fallback. Don't rely on the static set; SPXW lists Tue + Thu weeklies.
- **`bsm.charm` returns delta-change-per-year.** Mixing with count-based weights (OI, volume) requires scaling by τ AND by the contract multiplier (100). pin_probability is the canonical pattern: both terms in share-equivalent units.
- **`bsm` clips `|d1| > 38` to zero** (`D1_CLIP_LIMIT`). `_clipped_count` counter tracks clipped rows. Don't bypass.
- **One revocation watcher per process** (Rev 8 ARCH-6). Never spawn per-WS-connection. Subscribe via `_KEY_REVOCATION_SUBSCRIBERS` event registration.
- **Single-flight snapshot prime** (Rev 8 OPS-4). Use `build_snapshot_payload_single_flight` for cold-cache callers; never re-issue concurrent batch reads.
- **Half-day calendar** (Rev 8 OPS-2). `session.is_half_day(today)` and `effective_rth_close(today)` honour 13:00 ET on Black Friday, Christmas Eve, July 3 (where applicable). Hardcoded 2024-2030; refresh annually every December.
- Read [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) for the deeper structural map; [API_POLICY.md](API_POLICY.md) for the `/v1/*` stability contract; [CHANGELOG.md](CHANGELOG.md) for the consumer-visible record; [OPS.md](OPS.md) for the operational runbook.
- Public end-user auth was removed — migration 0008 drops `users`, `access_requests`, `user_sessions` tables. Don't reintroduce that surface unless asked.
- Holiday calendar uses NYSE preferred → US federal fallback in `session.py`.
- Pipeline `ok` vs `partial` is a real signal in tests and admin telemetry; preserve the distinction.
- DataFrame copy semantics: the loader returns a fresh DataFrame each call; downstream callers can write in place. Avoid speculative `.copy()` on the chain DataFrame — it's a 10–20 MB allocation on SPX.
- New per-row `df.apply(...)` patterns over the chain are a regression — use vectorised numpy/pandas ops. The hot calculators (gex, vanna_charm, zero_gamma, pin_probability) are all vectorised.
- **Consumer-visible API changes ship with a CHANGELOG.md entry.** Anything that touches request validation, response shape, header policy, status codes, WS frames, or close codes is consumer-visible. The stability contract in [API_POLICY.md](API_POLICY.md) governs what counts as additive vs breaking.

## Operational tuning knobs (production-relevant)

- `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` (defaults 20/20). Bumped from 5/5 in Rev 5 — the previous 10-conn ceiling head-of-line-blocked ingest behind API reads.
- `DB_POOL_PRE_PING` (default `false` post-Rev 5). Re-enable behind a flaky connection.
- `LOADER_SNAPSHOT_WINDOW_HOURS` (default 6). Shorter = less data scanned per pipeline tick. Bump back toward 48 if you run extended-hours feeds where the chain can sit idle for hours.
- `DB_ENCRYPTION_KEY` — operator-managed Fernet root. Rotation requires a re-encryption job; falls back to `JWT_SECRET` for backward compat.

## Tests overview

`backend/tests/` mixes pure-function and DB-backed suites:
- `test_processing_*.py` — every metric calculator individually
- `test_bsm_correctness.py`, `test_property_iv.py`, `test_property_hiro_sign.py` — Hypothesis property tests (`-m property`)
- `test_metrics_correctness.py`, `test_flow_correctness.py` — end-to-end correctness
- `test_pipeline_hardening.py`, `test_dlq_and_backpressure.py` — operational invariants
- `test_api_admin.py`, `test_api_auth.py`, `test_api_hardening.py`, `test_streaming_api.py` — API (need DB)
- `test_security.py`, `test_crypto.py`, `test_key_pool.py` — security primitives
- `test_session.py`, `test_spot_resolver.py`, `test_zero_dte.py` — session/spot/0DTE work

Current count: **413+ passed, 34 skipped** (skips are DB-backed, need `TEST_DATABASE_URL`). Always green on `ruff check app`.
