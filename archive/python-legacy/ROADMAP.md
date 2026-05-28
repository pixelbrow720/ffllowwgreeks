# Roadmap — Deferred Production-Readiness Items

These are real multi-day projects identified during the Rev 10 review pass
that are out of scope for the closure passes (Rev 11 / Rev 12). Documented
here for explicit tracking — every entry has scope, rationale, acceptance
criteria, and a rough effort estimate so a future planner can size them
accurately.

The Rev 11 pass closed all 12 CRITICAL and 18 HIGH findings. The Rev 12
pass (this one) closes the small actionable MEDIUM/LOW items —
multi-stage Dockerfile (SRE-14), Python patch pin (SRE-15), lockfile
documentation (SRE-16), pipeline pause/resume + per-calculator kill
switch (SRE-19), env-configurable lifespan loop intervals (SRE-24),
half-day calendar operator override (SRE-25 / DR-26).

What remains here are the items where a half-implementation would be
worse than no implementation at all (observability without spans is
worse than admitting we have no tracing; partial replay tooling is
worse than no replay tooling).

---

## SRE-17 — OpenTelemetry tracing

**Scope.** Instrument the FastAPI app, the asyncpg/SQLAlchemy engine,
the OPRA + GLBX ingestion entrypoints, and the per-symbol pipeline
tick with OpenTelemetry. Export via OTLP (gRPC or HTTP) to a collector.
Span graph: `pipeline_tick(symbol)` parent → `loader.load_chain` →
`spot.resolve` → per-calculator child spans → `streaming.fanout` (with
WS-publish span links so the receiving end of the fan-out is reachable
from the originating tick).

**Rationale.** Today the only end-to-end visibility is structlog +
Prometheus gauges from `/admin/metrics`. The pipeline emits run-level
audit but a partial tick can't be traced to which calculator stalled
or which DB session pool-saturated. A trace backend would close that
gap without operators trawling logs.

**Acceptance criteria.**
- Every pipeline tick emits exactly one root span per symbol.
- Calculator spans are children of the pipeline span; calculator name
  is on the span as `flowgreeks.calculator`.
- WS publish fan-out spans link to the originating pipeline span.
- A span context is carried through asyncio task boundaries (`Span.set_attribute("asyncio.task", ...)`).
- The OTLP endpoint is configurable via `OTEL_EXPORTER_OTLP_ENDPOINT`;
  no hardcoded collector URL.
- Disabled-by-default behind `ENABLE_TRACING=false`.

**Estimated effort.** 3–5 days.

---

## SRE-18 — SLO/SLI definition and alerting wiring

**Scope.** Author `SLO.md` (next to `OPS.md`) defining:
- SLI: snapshot p99 latency (REST `/v1/{symbol}/snapshot`)
- SLI: pipeline freshness lag (max age of latest `pipeline_runs.finished_at` per symbol during RTH)
- SLI: auth success rate (`200|201` over `200|201|401|403` for `/v1/*` REST)
- SLI: WS connection availability (sessions that closed cleanly vs server-side errored)

For each SLI, an SLO target (e.g. p99 < 100ms over rolling 30 days,
freshness lag < 60s during RTH) and an alert query (Prometheus PromQL
or equivalent) plus a runbook entry pointing to the operator action.

**Rationale.** Rev 10 SRE-12 documented the `FlowGreeksDBPoolSaturated`
alert template but nothing else has a target. Without targets, the
"is the system healthy" question reduces to log-tail grep, which
doesn't scale.

**Acceptance criteria.**
- `SLO.md` lists every SLI with definition, target, and PromQL query.
- Each alert has a runbook subsection in `OPS.md` documenting the
  expected operator response.
- The `/admin/metrics` exposition includes every gauge / counter
  referenced by the SLO queries.
- `OPS.md` cross-links to `SLO.md`.

**Estimated effort.** 1–2 days.

---

## SRE-20 — Replay/backfill admin tooling

**Scope.** Add `POST /admin/pipeline/replay` taking
`{symbol, from_ts, to_ts}`. The handler walks `options_chain`
snapshots in 60s buckets between the bounds, calls
`run_pipeline_for_symbol(force_ts=bucket_ts)` (the pipeline needs a
new `force_ts` kwarg) and UPSERTs the resulting `computed_metrics`
rows. Idempotent — re-running over the same window leaves the table
in the same state.

**Rationale.** Today, when a calculator bug ships to production the
only recovery is "wait for natural recomputation" (which never
recomputes historical rows) or run a one-off SQL surgery. Neither is
acceptable in a quant-validation context.

**Acceptance criteria.**
- The endpoint is JWT-protected, bounded to one window per symbol per
  call.
- A long-running replay is async-friendly: returns 202 + a job id, and
  job state is queryable via `GET /admin/pipeline/replay/{job_id}`.
- The replay path UPSERTs (does not delete-and-insert) `computed_metrics`
  rows so concurrent live ticks don't lose rows.
- Logs progress every N buckets via `RAISE NOTICE`-style pipeline log.
- Documented in `OPS.md` with the operator workflow.

**Estimated effort.** 2–3 days.

---

## SRE-21 — Secret manager integration

**Scope.** Replace `.env`-only secret resolution with a pluggable
`SecretSource` abstraction. Adapters: env (current behavior), AWS
Systems Manager Parameter Store, HashiCorp Vault KV v2, Docker secrets
(`/run/secrets/<name>`). Config picks the source via
`SECRET_SOURCE=env|ssm|vault|docker`. Each adapter resolves a fixed set
of keys (`JWT_SECRET`, `DB_ENCRYPTION_KEY`, `DATABENTO_API_KEY_*`,
`ADMIN_PASSWORD`).

**Rationale.** Today rotating any of these secrets means a
docker-compose redeploy with a new `.env` — fine for single-node, not
fine for any production-grade multi-tenant deployment. The `.env`
file also has to live somewhere; checked-in `.env.example` works for
dev but production needs an out-of-band path.

**Acceptance criteria.**
- `app.config.Settings` reads from `SecretSource.resolve(name)` rather
  than directly from env when `SECRET_SOURCE != env`.
- Each adapter is unit-tested with a mocked client.
- The Docker secrets adapter handles missing files gracefully (falls
  back to env).
- `OPS.md` documents the per-environment recommendation
  (single-node → env, k8s → docker secrets or projected SA token,
  AWS → SSM, hybrid → Vault).

**Estimated effort.** 2–3 days.

---

## SRE-22 — Automated backup + restore drill

**Scope.** Two pieces:
1. Nightly `pg_dump --format=custom` against the production database,
   uploaded to S3-compatible object storage with a configurable
   retention (default 30 days). Driven by a cron container in the
   compose stack or a k8s `CronJob`. Encrypted at rest via the
   storage backend.
2. A documented quarterly **restore drill** playbook: spin up a fresh
   Postgres, restore the latest dump, run a smoke test against the
   restored backend, document the elapsed time. The playbook is the
   acceptance gate — a backup that hasn't been restored is not a
   backup.

**Rationale.** Today the backup story is "the operator runs `pg_dump`
when they remember to". That's a Recovery Point Objective of
"whenever the operator last remembered" — for a quant pipeline that
emits 1.4M `computed_metrics` rows per day per symbol, that's
unworkable.

**Acceptance criteria.**
- The cron is wired in `docker-compose.yml` and `OPS.md` §X documents
  the env vars required (`BACKUP_S3_BUCKET`, `BACKUP_S3_PREFIX`,
  `BACKUP_RETENTION_DAYS`).
- The backup script verifies the dump (`pg_restore --list` against the
  freshly-uploaded artifact) before reporting success.
- `OPS.md` includes the restore-drill playbook with a checklist:
  prerequisites, restore command, smoke-test command, success
  criteria, rollback plan.
- The first quarterly drill is logged as a successful exercise.

**Estimated effort.** 1–2 days for the backup; ongoing for the drill
rhythm.

---

## Note on SRE-23

Folded into Lane B2 (structlog allow-list redaction work) — not
deferred further.
