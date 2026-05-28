# OPS.md — Operator Runbook

This document covers operational tasks for **pantek-waang** that go beyond
"docker compose up". Read it before deploying to a real environment.

The audience is the operator who manages secrets, runs migrations, watches
logs, and rotates credentials.

---

## 1. Initial deployment checklist

Before exposing the service publicly:

```bash
cp .env.example .env
```

Then edit `.env` and set, at minimum:

| Var | Why |
|-----|-----|
| `DATABENTO_API_KEY_OPRA` | Required for live + historical OPRA Pillar (options chain). Skip via `DISABLE_LIVE_INGESTION=true` and `DISABLE_HISTORICAL_BACKFILL=true` only for dev. |
| `DATABENTO_API_KEY_GLOBEX` | Required for GLBX.MDP3 (CME futures tape). |
| `ADMIN_PASSWORD` | **Refuses to boot at default.** Must be either a bcrypt hash starting with `$2` OR a plaintext value >= 12 characters (Rev 10 SRE-10). |
| `JWT_SECRET` | **Refuses to boot at default or < 32 chars.** Random. HMAC signing key for admin sessions. The length floor is enforced explicitly at startup so a misconfigured secret fails loudly instead of producing weak tokens (Rev 10 SRE-10). |
| `JWT_EXPIRE_MINUTES` | Idle expiry for admin tokens. Default `60` (Rev 8 SEC-2 trimmed from 480). `.env.example` now matches the runtime default (Rev 10 SRE-9). |
| `DB_ENCRYPTION_KEY` | At-rest encryption for the Databento key pool (`databento_api_keys.api_key_encrypted`). See section 3. |
| `ADMIN_CORS_ORIGINS` | Comma-separated origins allowed to call the API from a browser. Never leave `*` in production. |
| `ENABLE_OPENAPI_DOCS` | `false` in production removes `/docs`, `/redoc`, `/openapi.json` from the public surface. |
| `LOADER_SNAPSHOT_WINDOW_HOURS` | Default `6`. Raise toward `48` only if your feed has multi-hour gaps; tighter = less hypertable scan cost per pipeline tick. |
| `TRUST_PROXY_HEADERS` | Default `false` (Rev 10 SRE-13). Set to `true` only when a stripping reverse proxy (Cloudflare, ingress) is in front of the API. Without that, a remote client can spoof `X-Forwarded-For` and defeat per-IP rate limits / audit logging. |
| `FORWARDED_ALLOW_IPS` | Comma-separated trusted hop list passed to `--forwarded-allow-ips` when `TRUST_PROXY_HEADERS=true`. Default `127.0.0.1`. |
| `SKIP_MIGRATIONS` | Optional. Set to `1` to bypass `alembic upgrade head` on container boot (Rev 10 SRE-2). Use to break a boot loop caused by a failing migration while diagnosing. |
| `ALLOW_MULTI_INSTANCE` | Default `false` (Rev 10 SRE-7). When `true`, the Postgres advisory-lock guard that refuses two simultaneous backends is disabled. In-process state (HIRO incremental, basis EMA, deferred usage buffer, snapshot prime cache) is NOT consistent across replicas — see "Single-instance assertion" below before flipping. |

Then:

```bash
docker compose up --build
```

The backend container runs `alembic upgrade head` automatically on startup.
Verify the boot banner does not WARN about default secrets.

---

## 2. Migrations

Alembic versions live in `backend/app/db/migrations/versions/`. The two
operationally-meaningful upgrades since the original Rev 4 are:

* **0009** — drops the redundant `ix_computed_metrics_symbol_type_ts` index. Halves write amplification on metric upserts. No data change; safe to roll forward and back.
* **0010** — adds `api_keys.key_lookup` (keyed BLAKE2b digest column with a unique constraint) for O(1) API-key lookup. Backward-compatible: existing rows have `NULL` `key_lookup`; the auth path lazily backfills on first successful verify.

Apply manually if Alembic auto-run is disabled:

```bash
docker exec -it ofa-backend alembic upgrade head
```

### 2a. Eager backfill of `key_lookup` (recommended at scale)

The lazy backfill works fine, but if you have many active API keys and want
the unique-index path active for every key immediately (so one
prefix-collision row doesn't pay extra bcrypt verifies), run a one-shot
backfill **after** applying migration 0010.

You only need this if you can present the **plaintext** key — bcrypt is
one-way. In practice that means:

1. **Keep the plaintext temporarily.** When you create a new key via
   `/admin/api-keys`, the response shows the plaintext **once**. If you
   captured it, run:

   ```python
   import hashlib
   _KEY = b"pantek-waang.api-key-lookup.v1"
   digest = hashlib.blake2b(plaintext.encode("utf-8"), key=_KEY, digest_size=32).hexdigest()
   ```

   Then `UPDATE api_keys SET key_lookup = '<digest>' WHERE id = '<id>'`.

2. **Otherwise: rotate.** For keys whose plaintext is lost, the operationally
   sound path is to issue replacement keys through `/admin/api-keys` (which
   populates `key_lookup` on create), distribute them, and revoke the old
   ones. The lazy-backfill behaviour ensures no traffic disruption while
   you do this.

If you don't backfill at all, nothing breaks — the auth path simply scans
the prefix index for `key_lookup IS NULL` rows and writes the digest on
first verify. Throughput goes up automatically as keys are used.

### 2b. Lazy-backfill window: 0010 → 0012 sequencing (Rev 10 MIG-10)

Migrations 0010 and 0012 form a **two-stage credential migration**. They
are correct only if a specific bake window is honoured between them:

| Stage | Migration | Code path expectation |
|-------|-----------|------------------------|
| 0010 (Rev 6) | adds `api_keys.key_lookup`, leaves NULL on legacy rows | App version A: O(1) digest path **with NULL-fallback prefix scan**. Lazy-backfills `key_lookup` on first successful verify. |
| (window)     | application traffic | Every legacy key gets its `key_lookup` populated as it is used. |
| 0012 (Rev 8) | deactivates every `key_lookup IS NULL` row, drops the prefix-scan fallback in app | App version B: O(1) digest path only. NULL-keyed rows are inactive. |

**Required minimum bake time: 30 days** in app version A before deploying
0012. The 30-day window is a heuristic — pick the longer of (a) the
maximum reasonable inactivity period of any legitimate consumer, or (b)
your support SLA for an offline consumer to reconnect. If you have a
weekly-batch consumer that hasn't called the API in 35 days, 30 isn't
enough; bump it.

**Pre-0012 deploy gate (operator query, MUST return 0):**

```sql
SELECT count(*)
  FROM api_keys
 WHERE key_lookup IS NULL
   AND is_active = TRUE;
```

If the count is non-zero, those rows **will be deactivated** the moment
0012 runs and their consumers will start receiving HTTP 401. Treat a
non-zero count as a deploy block and either:

* Issue replacement keys for those consumers via `POST /admin/api-keys`
  ahead of the deploy (lazy-backfill won't help if the consumer hasn't
  called the API in the bake window), or
* Accept the credential loss as a deliberate cleanup of stale keys, then
  proceed.

**Post-0012 visibility (Rev 10 MIG-5):** migration 0012 now emits a
`RAISE NOTICE` line with the count of rows it just deactivated:

```
NOTICE: legacy api_keys with NULL key_lookup deactivated: <N>
```

Capture the alembic stdout during the deploy and store the count in your
release log. If `<N>` is greater than zero, expect 401s from the
corresponding consumers and have replacement keys ready.

**Cross-references:** see `CHANGELOG.md` for the consumer-visible record.
Engineering details for migration 0012 (NULL-key deactivation) and 0013
(FK constraint) live in git history.

### 2c. Rev 10 migration safety policy

Rev 10's MIG-* findings codified three migration safety rules that all
new alembic revisions in this repo must follow:

* **CONCURRENTLY for indexes on hot tables.** Any
  `CREATE INDEX` / `DROP INDEX` on `flow_events`, `dead_letter_queue`,
  `options_chain`, `computed_metrics`, `options_trades`,
  `futures_ticks`, or `liquidity_snapshots` must use
  `CONCURRENTLY` inside an `op.get_context().autocommit_block()`.
  Migrations 0007, 0010, 0011 were retroactively fixed in Rev 10 (MIG-3,
  MIG-4) to follow this rule.

* **NOT VALID for FK adds, separate VALIDATE migration.** Any
  `ADD CONSTRAINT ... FOREIGN KEY` on a populated table must use
  `NOT VALID` to skip the locking validation scan, then a separate
  follow-up migration runs `VALIDATE CONSTRAINT` (only takes
  `ShareUpdateExclusiveLock`). Migration 0013 + 0014 are the canonical
  pattern (Rev 10 MIG-1).

* **Audit destructive UPDATEs / DELETEs with `RAISE NOTICE`.** Any
  migration that mutates production data (deactivation, orphan delete,
  silent UPDATE) must wrap the operation in a `DO $$ ... END $$;` block
  that captures `ROW_COUNT` via `GET DIAGNOSTICS` and emits a
  `RAISE NOTICE` so operators see the impact in the alembic log. 0012
  (deactivation) and 0013 (orphan delete) follow this pattern in Rev 10
  (MIG-2, MIG-5).

### 2d. Filename ordering note (Rev 10 MIG-7)

Migration 0010 lives at `20270115_0000_0010_*.py` while 0011-0013 live
at `20260524_*.py` — the timestamp prefix on 0010 is **chronologically
later** than the prefixes on 0011-0014, even though 0010 runs first.
Rev 10 reviewed this and elected **not to rename**: the filename is
cosmetic, the alembic ordering is governed by `revision` /
`down_revision` strings (not filename), and renaming would invalidate
historical revision references. Future migrations should match the
`YYYYMMDD_HHMM_<rev>_<slug>.py` convention with the timestamp matching
the actual creation date.

### 2e. Migration 0008 downgrade is intentionally NotImplementedError (Rev 10 MIG-8)

`20260101_0000_0008_drop_public_auth_tables.py` raises
`NotImplementedError` from `downgrade()`. This is **deliberate**. The
migration drops `users`, `access_requests`, `user_sessions` — the
public-auth surface — and the underlying data is not recoverable from
remaining tables. A naive downgrade that re-creates empty tables would
silently destroy any retained credentials and audit history. If a
genuine rollback is required, the operator must:

1. Restore from the pre-0008 backup (see §7).
2. Run `alembic downgrade -1` only **after** the restore confirms the
   public-auth tables are present again.

If you reach for `downgrade()` here in production, file a bug — there
is almost certainly a forward fix instead.

### 2g. Operational indexes audit (Rev 12 MIG-11)

Two operator-driven retention indexes live in migration 0007 but were
absent from `app/db/models.py` `__table_args__` until Rev 12:

- `ix_flow_events_ts_only` — `flow_events(ts)`
- `ix_dead_letter_queue_ts_only` — `dead_letter_queue(ts)`

Both back the periodic `DELETE FROM ... WHERE ts < NOW() - INTERVAL ...`
retention queries described in 0007's docstring. Because the model
declarations did not list them, an `alembic revision --autogenerate`
diff would have proposed dropping both indexes — silently regressing
the retention path on the next operator-driven schema sync.

Rev 12 closes the loop: the indexes are now declared on `FlowEvent`
and `DeadLetterEntry` `__table_args__` so the model is the canonical
source of truth and autogenerate-diff is a no-op for them.

**Operator action:** none. The DB already holds these indexes; the
fix is model-side declaration only. If you ever generate a fresh
schema with `Base.metadata.create_all` (test path), both indexes now
materialise automatically.

### 2h. Migration extension-create error handling (Rev 12 MIG-12)

Migration 0001 originally wrapped `CREATE EXTENSION IF NOT EXISTS
timescaledb` in `EXCEPTION WHEN OTHERS THEN NULL`, swallowing every
error class — including syntax errors and unexpected SQLSTATE values
that genuinely warrant aborting the deploy. Rev 12 narrows the catch
to two specific SQLSTATE codes and emits `RAISE NOTICE` on each:

| SQLSTATE | Meaning | Triggered when |
|----------|---------|----------------|
| `42501` (`insufficient_privilege`) | Migration role lacks `CREATE EXTENSION` rights | Managed Postgres without superuser (RDS without `rds_superuser` grant, Aurora before extension is pre-installed by the operator) |
| `0A000` (`feature_not_supported`) | TimescaleDB shared library not loaded | Vanilla Postgres, Postgres-flavoured forks without the extension packaged |

Any other SQLSTATE now propagates and aborts the migration — the
intended behaviour for an actual bug, e.g. a typo in the SQL or a
broken catalog state. The plain-Postgres fallback contract is
preserved.

**Operator action:** none. If you previously relied on the
swallow-everything semantics for a deploy that legitimately failed
extension creation under a third SQLSTATE, file a bug; we'll widen
the catch list with documentation.

### 2i. Hypertable FK runtime monitoring (Rev 12 MIG-14)

Migration 0013 added `FK computed_metrics.metric_type ->
metric_type_registry.metric_type`. Because `computed_metrics` is a
TimescaleDB hypertable, every per-row INSERT now performs an FK
lookup against the (small, indexed) registry on its way to the chunk.

**Expected per-row cost:** single-digit microseconds — a B-tree probe
on a tiny lookup table that lives in `shared_buffers`. At a few
hundred metric rows per pipeline tick, the per-tick FK overhead is
sub-millisecond and well below the existing `_persist_metrics` budget.
This estimate is **not** staging-measured; treat it as a working
ceiling, not a guarantee.

**Recommended monitoring** (closes the loop):
- Track p99 of `_persist_metrics` duration via `pipeline_runs.duration_ms`
  with a per-stage breakdown.
- Alert if the post-0014 p99 of `_persist_metrics` **doubles** relative
  to the 7-day pre-0013 baseline, or eats meaningfully into the 60s
  pipeline budget.
- Bound check: even at 50µs/row × 200 rows = 10ms/tick, the alert
  threshold above will fire long before this becomes the bottleneck.

**Rollback path** (if a measured regression materially exceeds budget):
1. Author a new migration that issues `ALTER TABLE computed_metrics
   DROP CONSTRAINT IF EXISTS fk_computed_metrics_metric_type_registry`.
   The drop takes `AccessExclusiveLock` only briefly to remove the
   catalog row.
2. Either revert to a CHECK constraint against a static enum, or fall
   back to application-layer enforcement via `EXPECTED_METRIC_TYPES`.
3. Keep the registry table — it remains useful for documentation and
   runtime catalog reads even without the FK.

### 2j. Compression policy trade-off (Rev 12 MIG-15)

`options_chain`, `computed_metrics`, `futures_ticks`, `options_trades`,
and `liquidity_snapshots` all run with `add_compression_policy(...,
INTERVAL '1 day')` against a 7-day retention. Concretely:

- The most recent ~24h is hot and uncompressed (write path stays fast).
- Days 1 through 7 are compressed by the TimescaleDB background job.
- Day 8+ is dropped by the retention policy.

So at most ~6 days of the 7-day window is ever compressed — about 70%
of total stored volume.

**Why not a tighter `INTERVAL '1 hour'`?** The 1-day delay protects
the recent-write hot path from compression-related lock contention.
TimescaleDB compression chunks are read-only; aggressive compression
of fresh chunks would push every late-arriving correction (replays,
DLQ drains, EOD adjustments) onto a re-compression workflow that's
much heavier than a normal UPSERT.

**Why not extend retention?** 7 days matches the operational pipeline
contract: operators replay anything older from Databento on demand.
Longer retention without longer compression is mostly storage waste;
longer retention with shorter compression-delay re-introduces the
hot-path lock contention above.

**Decision (Rev 12):** keep the policy as-is. The `~70% compressed`
ratio is a deliberate trade-off, not an oversight. Operators wanting
truly cold archival should:
- bump `add_retention_policy(...)` to a longer interval (e.g. 30 days)
  AND
- accept the storage cost of having more uncompressed days at the
  warm end of the window, OR
- drop the compression policy entirely and reach for an external cold
  store (S3/Glacier) for archival.

**Do not change without measuring** the storage-vs-query-latency
trade-off on a representative production sample. The current setting
is the SpotGamma-cadence default; deviation should be motivated by
data, not aesthetics.

---

## 3. `DB_ENCRYPTION_KEY` rollout

`databento_api_keys.api_key_encrypted` is Fernet-encrypted with a key
derived from `DB_ENCRYPTION_KEY` (HKDF-SHA-256, salt `pantek-waang.crypto.v1`).

**Pre-Rev 6 deployments derived this key from `JWT_SECRET`.** Rev 6
introduced a separate `DB_ENCRYPTION_KEY` so JWT rotation no longer
invalidates the encrypted Databento pool. The fallback chain is:

1. `DB_ENCRYPTION_KEY` if set
2. `JWT_SECRET` if `DB_ENCRYPTION_KEY` is empty (legacy compat)

### 3a. Rolling out on an existing deployment

If you have encrypted Databento keys in your DB (added via
`/admin/databento-keys`), do this **once**, in order:

1. Set `DB_ENCRYPTION_KEY` in `.env` to the **current value of `JWT_SECRET`**. This keeps the derivation identical, so existing encrypted rows still decrypt cleanly.

   ```bash
   # Inside the host shell
   echo "DB_ENCRYPTION_KEY=$(grep '^JWT_SECRET=' .env | cut -d= -f2-)" >> .env
   ```

2. Restart the backend.

3. Verify: open the admin dashboard → Databento Keys → click "Test" on each row. They should all return `ok: true`. If they don't, the secret values diverged — restore from backup before going further.

4. **Now** you can rotate `JWT_SECRET` freely. The Databento pool stays decryptable as long as `DB_ENCRYPTION_KEY` doesn't change.

### 3b. Rotating `DB_ENCRYPTION_KEY` itself

There is **no in-place re-encryption job**. To rotate the at-rest key:

1. Open admin → Databento Keys. Note every label + dataset + plaintext (you'll need to re-paste the plaintexts).
2. Rotate `DB_ENCRYPTION_KEY` in `.env`.
3. Restart the backend.
4. Existing rows now fail to decrypt. The ingester will start failing over through them, mark them with `error_count`, and eventually skip them. **Manually delete each old row** via `DELETE /admin/databento-keys/{id}` — they are dead weight.
5. Re-create each key via `POST /admin/databento-keys` with the same label / dataset / priority / plaintext. They get encrypted with the new key.

Do this during a maintenance window — the ingester degrades to env-var keys
during the rotation if the env keys are still set.

### 3c. Detecting a misconfigured `DB_ENCRYPTION_KEY`

Symptoms:
* Live ingester logs `live_ingestion_record_key_error_failed` or auth errors against rows that used to work.
* Admin → Databento Keys "Test" returns `ok: false` with `Invalid token`.
* `databento_api_keys.error_count` rapidly rising on every row.

Recovery: confirm `DB_ENCRYPTION_KEY` matches the value used at the time
the rows were encrypted. If lost, fall back to environment-only credentials
(`DATABENTO_API_KEY_OPRA`, `DATABENTO_API_KEY_GLOBEX`) and re-create the
DB pool from scratch.

---

## 4. Observability

### 4a. Built-in admin telemetry

Without external metrics tooling, the source of truth is
`GET /admin/system/status`:

* `pipeline_running` — boolean. False ⇒ scheduler stopped.
* `last_compute_per_symbol[<symbol>]` — last successful pipeline tick per symbol. Stale > `2 × COMPUTE_INTERVAL_SECONDS` ⇒ pipeline stuck.
* `opra_lag_ms` / `futures_lag_ms` — `now - max(table.ts)`. Stale > `FUTURES_FEED_LAG_WARN_MS` ⇒ feed gap.
* `dlq_pending` — count in `dead_letter_queue`. Sustained growth ⇒ ingester dropping rows. Drill into `/admin/inspector/dlq` for samples.
* `last_pipeline_runs[]` — most recent run per symbol with `status` ∈ `{ok, partial, failed}`. Persistent `partial` ⇒ check `missing_metric_types`.
* `live_ingester` block — record counters by type, schemas active/dropped, sample record attrs, registry size, terminal-failure flag.

A 30s admin-dashboard polling loop on this endpoint is sufficient for most
operational visibility.

### 4b. `GET /admin/metrics` *(Rev 6)*

Prometheus-style text exposition for the same gauges, suitable for scraping
by an external Prometheus + Alertmanager. Same auth as the rest of `/admin/*`
(JWT bearer).

Available gauges (as of Rev 9):

```
ofa_pipeline_running                 0|1
ofa_last_compute_age_seconds{symbol="SPXW"}    seconds since last successful tick
ofa_db_pool_size                     pool_size config
ofa_db_pool_checked_out              connections in use
ofa_db_pool_overflow                 connections beyond pool_size
ofa_dlq_pending                      dead_letter_queue row count
ofa_opra_lag_ms                      ms since last options_chain row
ofa_futures_lag_ms                   ms since last futures_ticks row
ofa_active_api_keys                  count of active API keys
flowgreeks_dlq_evictions_total       in-memory ring evictions (counter)
flowgreeks_pipeline_partial_total    ticks finishing status=partial (counter, Rev 9)
pipeline_run_finalize_errors_total   finalize attempts that raised pre-commit (counter, Rev 9)
streaming_publish_errors_total       WS / SSE publish failures (counter, Rev 9)
```

Recommended Prometheus alerts:

```yaml
- alert: PipelineStuck
  expr: ofa_last_compute_age_seconds > 180
  for: 2m
  annotations:
    summary: "Pipeline tick > 3min stale for {{ $labels.symbol }}"

- alert: DLQGrowing
  expr: increase(ofa_dlq_pending[5m]) > 100
  annotations:
    summary: "DLQ grew by {{ $value }} rows in 5min"

- alert: DBPoolSaturating
  expr: ofa_db_pool_checked_out / ofa_db_pool_size > 0.9
  for: 1m
  annotations:
    summary: "DB pool {{ $value | humanizePercentage }} utilised"
```

### 4c. Logs

Backend logs are JSON via structlog. Key event names:

* `pipeline_complete` (status, duration_ms, rows_read, missing) — every tick
* `pipeline_partial` / `pipeline_low_coverage` — investigate
* `live_ingestion_*` — connect, schema drop, telemetry, registry refresh
* `live_trade_unmatched_rollup` — registry needs refresh (auto-fires)
* `bulk_writer_flush_failed` — DB issue, batch landed in DLQ
* `stream_ws_error` / `stream_ws_revocation_watcher_error` — WS plumbing
* `accept_version_header` (debug) — Rev 12 BC-13 telemetry; logged once per
  request that supplies the `Accept-Version` header

Avoid grepping `key=` or `token=` — uvicorn access logs have those scrubbed
out by `_install_uvicorn_log_redaction` at startup, but third-party logs
might not.

#### Dual-layer redaction (Rev 12 SRE-23)

Two independent redaction layers cover orthogonal surfaces:

1. **Query-string regex** (`app/main.py::_redact()`) attached to the
   `uvicorn`, `uvicorn.access`, and `uvicorn.error` loggers. Scrubs
   `?token=…`, `?api_key=…`, `?password=…`, `Authorization:` header
   captures from free-form access-log lines. This is the only layer
   that handles raw uvicorn access lines (which arrive at the logger
   pre-formatted).
2. **Structlog `drop_sensitive_keys_processor`** (`app/core/logging.py`)
   wired into the `structlog.configure(processors=...)` chain. Replaces
   values for any structured field whose key matches the well-known
   sensitive-key allow-list (`api_key`, `token`, `password`, `secret`,
   `db_encryption_key`, ...) with `"REDACTED"` BEFORE the JSON
   renderer sees them. This catches structured calls like
   `logger.info("event", api_key=value)` that the regex layer cannot
   reach (structlog records bypass the formatter that the regex hooks
   into).

Don't remove either layer — they cover different code paths. The
allow-list lives in `_SENSITIVE_LOG_KEYS` in `app/core/logging.py`;
add a new key there if you discover a structured log site emitting a
secret-shaped value.

---

## 5. Common operational tasks

### 5a. Add a Databento API key

1. Get the key from your Databento dashboard.
2. Admin → Databento Keys → "Add key".
3. Choose dataset (`OPRA.PILLAR` for options, `GLBX.MDP3` for CME futures, `BOTH` for keys with full entitlements).
4. Set `priority` lower than env keys to make it a fallback, higher to prefer it.
5. Click "Test" after creation — that runs `decrypt_secret` and confirms the at-rest crypto round-trip works.

The ingester picks up new rows on the **next reconnect attempt** without a
restart.

### 5b. Investigate "pipeline_no_data"

```
$ docker exec -it ofa-backend python -c "
from app.db.session import get_engine
import asyncio
async def main():
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.exec_driver_sql(
            \"SELECT symbol, count(*), max(ts) FROM options_chain WHERE ts > NOW() - INTERVAL '1 hour' GROUP BY symbol\"
        )
        for row in result: print(row)
asyncio.run(main())
"
```

If counts are zero → ingestion is failing. Check `/admin/inspector` →
`live_ingester` block for the most-recent error. Common causes:

* All Databento keys exhausted (`error_count >= 5` on every row + cooldown active)
* Schema dropped (`schemas_dropped` non-empty) — key lacks entitlement for OPRA cmbp-1, etc.
* Network: the OPRA gateway is unreachable from your container's egress

### 5c. Force a session reset

Daily at 09:29 ET the scheduler runs `reset_session_state` which clears the
basis cache, flip-speed cache, and HIRO incremental state. To do it manually:

```bash
docker exec -it ofa-backend python -c "
import asyncio
from app.processing.pipeline import reset_session_state
asyncio.run(reset_session_state(['SPXW', 'NDXP']))
"
```

You should rarely need this; the cron job covers it.

### 5d. Revoke an API key

`DELETE /admin/api-keys/{id}` — immediate. Active WS streams from that key
close with code `4401` within 30s (the revocation watcher polls at that
cadence). New requests get `401`.

---

## 6. REV8 — operational changes since Rev 7

### 6a. Live ingester now self-recovers after long outages (OPS-1)

Previously the OPRA + GLBX live ingesters terminated themselves after 5
reconnect attempts (~3 minutes of outage tolerance) and required a manual
`reset_after_terminal()` admin call. Rev 8 ships:

* `INGESTION_MAX_RECONNECTS=30` — reconnect budget (was hardcoded 5).
* `INGESTION_RECONNECT_MAX_BACKOFF_SECONDS=300` — backoff cap (was 60s).
* `INGESTION_TERMINAL_RESET_SECONDS=600` — sleep before auto cold-restart.

When the reconnect budget is exhausted the ingester logs
`live_ingestion_reconnect_budget_exhausted`, sleeps the cold-restart
window, then resumes from a clean state. A routine 30-min OPRA blip is
now self-healing. The manual `reset_after_terminal()` admin path is
preserved for forced cycles initiated by an operator (e.g. after fixing
a Databento key entitlement).

Configuration failures — no API keys at all, or every requested schema
dropped at connect time — still terminate immediately because retrying
without operator intervention would just hit the same wall.

### 6b. Half-day market closes (OPS-2)

`session.py` now exposes a half-day calendar primitive:

* `early_close_at_eastern(today: date) -> time | None` — returns
  `time(13, 0)` on a NYSE half-day session, else `None`.
* `is_half_day(today: date) -> bool` — round-trip flag.
* `effective_rth_close(today: date) -> time` — preferred RTH close
  for `today`; honours half-days.

`is_rth_now`, `session_close_today`, and `minutes_to_close` all consume
`effective_rth_close` so callers see the correct close on Black Friday,
Christmas Eve, and pre-July-4. The pipeline downgrade-to-partial logic
after early close lives in `pipeline.py` (Lane A — see `# REV8-LANE-A`
TODO there).

The scheduler additionally fires a `_on_half_day_close` job at 13:01 ET
on every weekday; the handler short-circuits to a no-op on full-session
days and runs `finalize_session` early only on actual half-days.

The half-day list is hardcoded for 2024-2030 in
`_NYSE_HALF_DAYS_HARDCODED`. **Refresh annually** — when adding 2031
dates, follow the rules:

1. Day after Thanksgiving (4th Friday in November + 1).
2. Christmas Eve (Dec 24) when it falls on a regular trading day.
3. July 3 when July 4 falls on a Tue–Fri trading day.
4. Skip dates that overlap the full-holiday set — full holidays win.

### 6c. EOD OI now business-day gated (OPS-3)

`run_eod_oi_ingestion` now:

* Skips entirely on a non-business day (full holiday or weekend) — the
  cron stamps no rows, leaving yesterday's snapshot intact.
* Derives `oi_date` from each source row's `ts_event` (converted to ET)
  rather than `datetime.now(UTC).date()`, so the stamped date matches
  the actual trading day even on edge timezone boundaries.

Half-day OI snapshots are still valid; only full holidays are skipped.

Operators no longer need to manually clean up post-holiday cron firings
(`DELETE FROM eod_open_interest WHERE oi_date = '<holiday>'`).

### 6d. DLQ retention (OPS-10) *(wired in Rev 9 DT-3)*

`app.ingestion.dlq.cleanup_dlq_older_than(retention_days)` deletes
`dead_letter_queue` rows older than the configured retention window.
Default is `INGESTION_DLQ_RETENTION_DAYS=14`.

Rev 9 wires the cleanup into the lifespan via `_dlq_cleanup_loop` (runs
every 6 hours). The DLQ table no longer grows unbounded with no
operator action. Manual one-shot is still available:

```bash
docker exec -it ofa-backend python -c "
import asyncio
from app.ingestion.dlq import cleanup_dlq_older_than
print(asyncio.run(cleanup_dlq_older_than(14)))
"
```

### 6d-bis. Admin-audit retention *(Rev 9 DT-4)*

`admin_audit_events` is also pruned automatically by
`_admin_audit_prune_loop` (runs daily). Default retention is
`ADMIN_AUDIT_RETENTION_DAYS=365` — generous enough for a full audit
cycle on key rotations and admin actions, while still preventing the
table from growing forever. Set the env to `0` to disable pruning
entirely (the loop short-circuits to a logged no-op).

### 6e. DLQ eviction counter exposed for paging (OPS-11)

`/admin/metrics` now emits `flowgreeks_dlq_evictions_total` (Counter)
incremented every time the in-memory DLQ ring buffer drops the oldest
entry because `INGESTION_DLQ_MAX_SIZE` was reached. Persistent growth
is the signal that ingestion is producing failures faster than the DLQ
can flush them — alert on it.

Recommended Prometheus alert:

```yaml
- alert: DLQRingEvicting
  expr: increase(flowgreeks_dlq_evictions_total[5m]) > 0
  for: 5m
  annotations:
    summary: "DLQ ring buffer evicting entries — failures outpacing flush"
```

### 6f. Backpressure flush no longer head-of-line (OPS-9)

`BulkUpsertWriter.add` and `OptionsChainWriter.add` schedule the soft
`UPSERT_BATCH_SIZE` flush in the background instead of awaiting it
inline. The hard `INGESTION_MAX_PENDING_ROWS=10000` cap remains
synchronous so backpressure correctness is unchanged. The change cuts
record-loop stalls when the DB is slow.

### 6g. New env knobs reference

| Knob | Default | What |
|------|---------|------|
| `INGESTION_MAX_RECONNECTS` | `30` | Reconnect attempts before cold-restart. |
| `INGESTION_RECONNECT_MAX_BACKOFF_SECONDS` | `300` | Cap on exponential reconnect backoff. |
| `INGESTION_TERMINAL_RESET_SECONDS` | `600` | Cold-restart sleep after budget exhausted. |
| `INGESTION_DLQ_RETENTION_DAYS` | `14` | Retention window for `dead_letter_queue` rows. |
| `ADMIN_AUDIT_RETENTION_DAYS` | `365` | Retention window for `admin_audit_events` rows (Rev 9 DT-4). |

---

## 6.5 REV10 — production readiness hardening (SRE lane)

### 6.5a. Container resource limits + log rotation (SRE-1, SRE-11)

`docker-compose.yml` now declares hard ceilings on the `backend`
service:

* `deploy.resources.limits.memory: 2G` — caps RAM. A pathological day
  (Databento backlog flush, OPRA reconnect storm, runaway Pandas
  allocation in a calculator) used to OOM the host and take Postgres
  down with it. The 2 GiB ceiling kills the container instead, which
  systemd / docker `restart: unless-stopped` then resurrects.
* `deploy.resources.limits.cpus: '2.0'` — bounds CPU so a busy
  pipeline tick can't starve the rest of the host (logging, Cloudflare
  agent, etc).
* `deploy.resources.reservations.memory: 512M` — soft floor so the
  scheduler doesn't overcommit the node.
* `ulimits.nofile: 65536/65536` — every WS subscriber is one fd, plus
  writer pools, plus DB connections. The container default of 1024 is
  uncomfortably close to the realistic peak under
  `MAX_WS_CONNECTIONS_PER_KEY × keys`. 65k is generous.
* `logging.driver: json-file` with `max-size: 100m` and `max-file: 5`
  — caps each container's on-disk log footprint at 500 MiB, rotating
  oldest-out. The same block is set on `db` so the Postgres container
  doesn't fill the host fs on a tracebacks-in-a-loop day.

**Tune for your hardware**: the 2 GiB / 2 CPU defaults assume a single
backend replica on a node with 4–8 GiB RAM headroom. Adjust both up
on a larger box; do **not** lower below 1 GiB without profiling — the
pipeline's vectorised numpy passes routinely allocate 50–100 MiB per
SPX tick.

### 6.5b. SKIP_MIGRATIONS escape hatch (SRE-2)

The container `CMD` runs `alembic upgrade head` on every boot by
design — the canonical "build, push image, restart" deploy path
relies on it. When a migration is genuinely broken in production,
that auto-run becomes a boot loop: every restart hits the same
failure and the container never serves traffic.

Set `SKIP_MIGRATIONS=1` in the environment to bypass the alembic
step:

```bash
docker compose run --rm -e SKIP_MIGRATIONS=1 backend
```

The container starts uvicorn directly. Use the time bought to:

1. Inspect the failing migration (`alembic heads`, `alembic current`,
   the migration file itself).
2. Roll back if appropriate (`alembic downgrade -1`).
3. Or fix forward and remove `SKIP_MIGRATIONS` on the next deploy.

This is an **emergency hatch** — leaving it set in steady state means
schema drift accumulates silently and the next clean restart will
need to apply N migrations at once.

### 6.5c. Separate `/ready` endpoint (SRE-3)

`/health` is now liveness only — it returns HTTP 200 while the
process is up regardless of pipeline freshness. `/ready` is the new
readiness probe:

* `GET /ready` — anonymous, returns 200 with
  `{"ready": true, "last_tick_age_seconds": <float>, "ts": <iso>}`
  when every supported symbol has ticked within
  `2 × COMPUTE_INTERVAL_SECONDS`.
* Returns 503 with `{"ready": false, "last_tick_age_seconds":
  <float>}` when any symbol is stale.
* Cached for 5s to absorb aggressive prober traffic without
  hammering `pipeline_runs`.

Wire your load balancer / k8s readinessProbe to `/ready` and your
livenessProbe to `/health`. The split means a stale-pipeline backend
gets removed from rotation while the orchestrator still considers it
alive (so logs / SSH / metrics keep working during the diagnosis).

### 6.5d. Dockerfile HEALTHCHECK (SRE-5)

The image now ships:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1
```

This is what `docker ps` / orchestrators key off. The 60s start
period gives Alembic + the forced startup pipeline tick time to run
before the first probe; `retries=3` tolerates a single transient
failure before flipping the container to unhealthy.

### 6.5e. Graceful WebSocket close on lifespan teardown (SRE-6)

Every accepted WS socket is registered in a process-global set; the
lifespan teardown calls `shutdown_all_websockets()` BEFORE the engine
is disposed. Each socket gets RFC-6455 close code **1012**
("service restart") so clients see a protocol-level signal to back
off and reconnect after the restart instead of an ungraceful TCP
drop. Best-effort — already-disconnected sockets are skipped without
error.

### 6.5f. Single-instance assertion (SRE-7)

The lifespan now acquires a Postgres advisory lock (`pg_try_advisory_lock`)
on a fixed key during startup and holds it for the entire process
lifetime via a parked engine connection. A second backend talking to
the same database fails loudly:

```
RuntimeError: Another backend instance is already running
(pg_advisory_lock contention). Set ALLOW_MULTI_INSTANCE=true to
override (advanced).
```

**Why?** Several state holders are process-local by design and would
silently diverge across replicas:

* `_USAGE_DELTA` (deferred API-key usage buffer).
* `_basis_cache` / `_last_spot_cache` (spot-resolver EMAs).
* `_hiro_state` (HIRO incremental aggregator).
* `_snapshot_cache` (WS prime cache).
* `_KEY_REVOCATION_SUBSCRIBERS` (per-process WS revocation watcher).

Two backends would compute different snapshots, flush divergent
usage counters, and the WS revocation watcher would race on the same
DB rows.

**Override**: `ALLOW_MULTI_INSTANCE=true` skips the lock. Only set
this when you have externalised every relevant state holder and an
L7 proxy that pins each WS connection to a single replica — i.e.
"advanced operator" territory. The lock is also skipped under
`APP_TESTING=1` so the test harness can spin up multiple workers.

The lock key is **5746728934252**, chosen distinct from any alembic
migration lock so a migration in flight on one process doesn't fight
with a running backend on another.

### 6.5g. Final usage-delta drain on graceful shutdown (SRE-8)

`_usage_flush_loop` already drains `_USAGE_DELTA` on its own
`asyncio.CancelledError` branch. Lifespan teardown now adds a
**second** explicit drain after the loop is cancelled, before
`dispose_engine`, to maximise the chance that a graceful shutdown
flushes the last window's usage counters even if the cancellation
handler is interrupted.

`kill -9` still loses the buffer (process state vanishes
immediately). `docker stop` / SIGTERM / `docker compose down` all go
through the lifespan and benefit from the extra drain.

### 6.5h. JWT_SECRET / ADMIN_PASSWORD length validators (SRE-10)

The boot guard already refused known default values. Rev 10 tightens:

* `JWT_SECRET` must be at least 32 characters.
* `ADMIN_PASSWORD` must be either a bcrypt hash starting with `$2`
  OR a plaintext value of at least 12 characters.

Both checks are bypassed under `APP_TESTING=1`. Failing either check
raises `RuntimeError` during `_start_observability` so the container
never serves traffic with a weak secret.

### 6.5i. JWT_EXPIRE_MINUTES sync (SRE-9)

`.env.example` previously shipped `JWT_EXPIRE_MINUTES=480` (the old
8-hour default). Rev 8 SEC-2 trimmed the runtime default to 60; Rev 10
SRE-9 syncs the example so `cp .env.example .env` produces a config
that matches the running app. Existing `.env` files are unaffected —
the value is just an example.

### 6.5j. Conditional --proxy-headers (SRE-13)

The container `CMD` used to pass `--proxy-headers
--forwarded-allow-ips=...` unconditionally. That made `X-Forwarded-For`
trusted whether or not a stripping reverse proxy was in front of the
API — a remote client connecting directly could spoof their IP and
defeat per-IP rate limits / audit logging.

Rev 10 SRE-13 makes it opt-in:

* Default: `TRUST_PROXY_HEADERS=false` — uvicorn ignores
  `X-Forwarded-*`. The rate limiter sees the socket peer.
* `TRUST_PROXY_HEADERS=true` — uvicorn is started with
  `--proxy-headers --forwarded-allow-ips=$FORWARDED_ALLOW_IPS`. Set
  this only when the API is exposed exclusively through a stripping
  edge (Cloudflare, an in-cluster ingress).

The application-level `Settings.trust_proxy_headers` already drove
`_real_client_ip` correctly; SRE-13 closes the gap at the uvicorn
boundary.

### 6.5k. DB pool saturation alert (SRE-12)

Recommended Prometheus alert (paired with the existing
`ofa_db_pool_size` / `ofa_db_pool_checked_out` gauges):

```yaml
- alert: FlowGreeksDBPoolSaturated
  expr: ofa_db_pool_checked_out / ofa_db_pool_size > 0.9
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "DB pool >90% saturated for 5min"
    description: "Backend's SQLAlchemy pool is sustained above 90% utilisation. Symptoms: API tail latency, ingest head-of-line blocking. Investigate slow queries, runaway WS subscribers, or missing indexes before bumping DB_POOL_SIZE."
```

Pair with the shorter-window `DBPoolSaturating` alert in section 4b
to catch transient spikes before they become sustained.

### 6.5l. New env knobs reference (Rev 10)

| Knob | Default | What |
|------|---------|------|
| `SKIP_MIGRATIONS` | unset | Set `1` to bypass `alembic upgrade head` on container boot. Emergency hatch only. |
| `ALLOW_MULTI_INSTANCE` | `false` | Set `true` to bypass the single-instance Postgres advisory-lock guard. Advanced operators only. |
| `TRUST_PROXY_HEADERS` | `false` | Set `true` only behind a stripping reverse proxy. Without one, accepting `X-Forwarded-*` lets clients spoof their IP. |

---

## 7. Backup & disaster recovery

The `db_data` Docker volume holds Postgres data. Take a logical backup:

```bash
docker exec -t ofa-db pg_dump -U options -d options_db -F c > options_db_$(date +%F).pgdump
```

Restore:

```bash
docker exec -i ofa-db pg_restore -U options -d options_db --clean --if-exists < options_db_2026-06-01.pgdump
```

What's safe to lose vs. precious:

| Table | Lose? | Notes |
|-------|-------|-------|
| `options_chain`, `options_trades`, `futures_ticks`, `liquidity_snapshots` | yes | Time-series, 7-day retention. Backfill from Databento on rebuild (`run_historical_backfill`). |
| `computed_metrics` | yes | Re-derived from chain on next pipeline tick. |
| `flow_events`, `pipeline_runs`, `session_events` | yes | Historical audit; re-derives forward. |
| `api_keys` | **no** | Plaintext keys are not stored anywhere. Lose this and you must re-issue every consumer key. |
| `databento_api_keys` | **no** | Plaintext keys are encrypted but the row itself is the only copy. Same caveat as `api_keys` — keep this backed up. |
| `alert_rules` | **no** | Operator-configured. |
| `dead_letter_queue` | yes | Diagnostic only. |
| `eod_open_interest` | yes | Re-fetchable via `run_eod_oi_ingestion`. |

---

## 8. Where to file a bug

If a pipeline run logs `pipeline_error` with a stack trace, capture:
1. The exact log line + traceback
2. `GET /admin/system/status` snapshot
3. `GET /admin/inspector/dlq?limit=20` if DLQ is involved
4. The relevant `pipeline_runs` row (`SELECT * FROM pipeline_runs WHERE symbol=... ORDER BY started_at DESC LIMIT 5`)

That's enough context for a developer to reproduce locally with
`APP_TESTING=1 pytest tests/test_pipeline_hardening.py`.

---

## 7a. Multi-stage Dockerfile rationale (Rev 12 SRE-14)

The Dockerfile is split into a `builder` stage (full toolchain — `gcc`,
`build-essential`, `libpq-dev`) and a `runtime` stage (libpq runtime +
curl only, no compiler). The builder installs every requirement into
`/root/.local`; the runtime stage `COPY --from=builder` pulls the
resolved site-packages over and discards the toolchain.

Why this matters operationally:

| Concern | Single-stage (Rev 11) | Multi-stage (Rev 12) |
|---------|------------------------|------------------------|
| Image size | toolchain-included; ~200-300 MB heavier | toolchain stripped at the layer boundary; ~150 MB smaller in practice |
| Attack surface | `gcc`, `make`, `dpkg-dev` inside the running container | none of those — just `libpq5` + `curl` |
| Build cache | every `apt-get install` invalidates on requirement change | builder cache is independent of runtime stage; small requirement bumps don't churn the runtime layer |
| CVE blast radius | every CVE in the toolchain hits the running container | toolchain CVEs are confined to the builder, never deployed |

All Rev 11 hardening is preserved: non-root `appuser` (uid 1001),
`HEALTHCHECK`, `SKIP_MIGRATIONS=1` boot hatch, and the conditional
`--proxy-headers`/`--forwarded-allow-ips` gate.

The Python base is pinned to `python:3.11.9-slim-bookworm` (Rev 12
SRE-15). Pinning to the patch tag prevents silent CVE-fix drift between
rebuilds and keeps the runtime ABI stable for the wheels we already
cached. Bump deliberately when 3.11.10 / 3.11.11 ship — don't drift to
3.12 without testing the asyncpg/scipy/numpy wheels first.

## 7b. Lockfile + hash-pinning workflow (Rev 12 SRE-16)

The `requirements.txt` file lists the **direct** runtime dependencies
with exact versions. The transitive closure plus per-wheel SHA-256
hashes lives in `requirements.lock.txt`, which is what the Dockerfile
installs with `pip install --require-hashes -r requirements.lock.txt`
when present (falling back to the unpinned file when absent).

To regenerate the lockfile:

```bash
cd backend
python -m venv .lockenv
source .lockenv/bin/activate    # Windows: .lockenv\Scripts\activate
pip install pip-tools           # already in requirements-dev.txt
pip-compile --generate-hashes \
    --resolver=backtracking \
    --output-file requirements.lock.txt \
    requirements.txt
deactivate
rm -rf .lockenv
```

Commit `requirements.lock.txt` alongside the corresponding
`requirements.txt` change. Reviewing a lockfile diff means scanning
two things:

1. The direct version bumps in `requirements.txt` (the change you
   intended).
2. Transitive bumps in `requirements.lock.txt` — confirm none of them
   are unexpected packages (typosquat-grade names, packages your direct
   deps don't actually need).

If `pip-compile` is not available in the build sandbox the Dockerfile
gracefully falls back to the unpinned `requirements.txt`. That is fine
for development; **production builds should always carry a generated
lockfile**.

## 7c. Pipeline pause/resume + per-calculator skip runbook (Rev 12 SRE-19)

When a calculator is misbehaving in production (e.g. a bad chain
snapshot drives `vanna_charm` into an exception loop) you have two
levers without a redeploy:

```bash
# Pause the entire pipeline. Existing tick in flight finishes; the next
# tick logs and skips the calculator phase. Heartbeat persistence
# continues so /admin/system/status keeps reporting freshness lag.
curl -X POST -H "Authorization: Bearer $TOKEN" \
    http://backend:8000/admin/pipeline/pause

# Resume.
curl -X POST -H "Authorization: Bearer $TOKEN" \
    http://backend:8000/admin/pipeline/resume

# Skip a single calculator (here: vanna_charm) — the rest of the tick
# still runs. The skipped calculator's metric_type rows are missing
# from /computed_metrics for that tick, which downgrades pipeline_runs
# status to 'partial'. That's intentional — operators see the skip in
# the audit trail.
curl -X POST -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"calculator": "vanna_charm"}' \
    http://backend:8000/admin/pipeline/skip-calculator

# Re-enable.
curl -X POST -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d '{"calculator": "vanna_charm"}' \
    http://backend:8000/admin/pipeline/unskip-calculator

# Read current state.
curl -H "Authorization: Bearer $TOKEN" \
    http://backend:8000/admin/pipeline/runtime-flags
```

**Persistence note.** The flags are process-local (a coarse lock guards
a module-level bool + a set of strings). A backend restart resets both
the pause flag and the skip set. This is intentional — operators
should not be able to silently leave the pipeline degraded across a
deploy. If you need a longer-lived skip, write a `# DISABLED` note in
the calculator source and ship it.

**Calculator names.** Use the canonical lower-case discriminator the
pipeline knows (e.g. `gex`, `hiro`, `vanna_charm`, `zero_gamma`,
`flow_events`, `walls`, `max_pain`, `regime`, `term_structure`,
`move_tracker`, `pin_probability`, `zero_dte`, `futures_levels`,
`basis`, `volume_profile`, `iv`). Names match exactly — typos
silently no-op.

Every flip is recorded in `admin_audit_events` with the actor's
username and the calculator name (when applicable).

## 7d. `OPERATOR_OVERRIDE_HALF_DAYS` env usage (Rev 12 SRE-25 / DR-26)

The half-day calendar in `app/processing/session.py` is a hardcoded
2024-2030 list. Annual maintenance refreshes it. Between refreshes,
NYSE may announce an unexpected early close (severe weather, exchange
technical issue, presidential proclamation). Set
`OPERATOR_OVERRIDE_HALF_DAYS` to insert dates without a code change:

```bash
# Single date.
export OPERATOR_OVERRIDE_HALF_DAYS=2026-12-29

# Multiple dates — comma-separated, ISO 8601.
export OPERATOR_OVERRIDE_HALF_DAYS="2026-09-15,2026-12-29"
```

Format: comma-separated `YYYY-MM-DD`. Whitespace is trimmed. Malformed
entries are silently dropped so a typo on one date doesn't disable the
others. The env var is read fresh on each `is_half_day(today)` /
`early_close_at_eastern(today)` call — no process restart needed when
the value is updated via a sidecar restart that re-reads `.env`.

The override is **additive** — both the override set and the hardcoded
list are honoured. Removing a hardcoded half-day requires a code
change.

When a half-day is in effect, the scheduler's `effective_rth_close()`
returns 13:00 ET, and the pipeline tick gate respects the early close
just like a regular session.

## 7e. Consumer-readiness endpoints (Rev 13 FE-1 / FE-2 / FE-3)

Three additive surfaces shipped in v1.3 to remove polling pressure
and re-login friction from API consumers.

### WS resume tokens (`?since_seq=`, FE-1)

Every snapshot frame now carries a per-symbol monotonic `seq` integer.
On reconnect, clients should pass `?since_seq=<last_seq>` to replay
buffered frames from the per-symbol ring buffer (in-process, depth
`DEFAULT_FRAME_BUFFER_MAXLEN = 60` ≈ 1 minute at 1Hz tick).

- The buffer is **only populated when there is at least one
  subscriber** for the symbol. Frames published with no subscribers
  are not retained — there is nobody to replay them to.
- Buffer state is **process-local**. A backend restart resets the
  per-symbol counter and empties the buffer; clients seeing `seq`
  reset to a low value should treat it as a fresh server.
- `ALLOW_MULTI_INSTANCE=true` deployments will produce divergent
  `seq` streams across replicas — the in-process buffer cannot
  coordinate with peers. This is consistent with the existing
  caveat for that flag (`Settings.allow_multi_instance` docstring).
- When `since_seq` is older than the buffer can replay, the server
  silently falls back to the standard cache prime. Clients should
  treat that as a cold start.

### Time-series history (`GET /v1/{symbol}/history`, FE-2)

Bucketed query over `computed_metrics`. Last-value-per-bucket
aggregation. Single composite index
`ix_computed_metrics_symbol_type_exp_ts` covers the query — Postgres
scans the `(symbol, metric_type, *, ts)` prefix and the range filter
on `ts` trims the segment.

Operational caveats:

- **No backfill.** If a `metric_type` does not exist in
  `computed_metrics` (e.g. recently added, or not yet computed for
  the symbol), the endpoint returns `points: []`. This is
  intentional — the alternative would mask pipeline gaps.
- **Retention.** `DATA_RETENTION_DAYS` (default 7) caps how far
  back the response can reach. Older windows return empty.
- **Per-request cap.** `(until - since) / interval <= 10000` —
  windows that would yield more buckets return HTTP 400 with a
  hint. The cap is intentionally chosen so a single request cannot
  trigger an unbounded TimescaleDB scan.

### Admin JWT refresh (`POST /admin/refresh-token`, FE-3)

Frontend should call refresh ~5 minutes before the current token's
`expires_in_seconds` elapses to maintain a live admin session
without a re-login. The grace window absorbs clock drift and brief
tab-backgrounded periods.

- Default grace window: **5 minutes** post-expiry
  (`JWT_REFRESH_GRACE_SECONDS=300`). Set to 0 to disable the grace
  path entirely (refresh then requires a non-expired token).
- The OLD token's `jti` is added to `jwt_revocations` in the same
  transaction so the rotated credential cannot be reused. The
  existing `jwt_revocations_prune_loop` (15 min cadence) will
  prune the row once the OLD token's `exp` passes.
- Rate limit: **30/min/IP**. A consumer retrying on transient
  network failure will not trip it; a stolen token cannot be used
  to mint a fleet of refreshed copies before the operator notices.
- Errors:
  - HTTP 401 — token signature/typ invalid, or expired beyond the
    grace window, or already in `jwt_revocations`.
  - HTTP 403 — token's `sub` is not the configured admin
    username. Same posture as `/admin/system/status`.
  - HTTP 429 — rate limit exceeded.

A consumer implementing the refresh loop should:

1. On login, store `token` and compute `expires_at = now() +
   expires_in_seconds`.
2. Schedule a refresh ~5 min before `expires_at`.
3. On refresh, swap the response into the same auth slot and reset
   the timer.
4. On HTTP 401 from refresh, redirect the user to login.


For deferred items beyond Rev 12 (OpenTelemetry, SLOs, replay tooling,
secret manager, automated DR), see `ROADMAP.md` at the repo root.
