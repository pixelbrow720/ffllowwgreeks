# CHANGELOG

All notable, consumer-visible changes to the FlowGreeks engine REST + WS
surface land here. This file is the machine-readable companion to
`API_POLICY.md` and the canonical record of consumer-visible changes
(per-revision engineering notes are not retained — see git history).

Versioning convention: the URL prefix `/v1/` is a stability contract —
additive fields are non-breaking; field removal, rename, or type
narrowing is breaking and requires a new prefix (`/v2/`). Behavioural
changes (auth response codes, header policy, lifetimes, frame shapes)
are also tracked here even when the URL stays at `/v1/`.

The `v1.0 → v1.1` entry is retroactive: the changes shipped before a
contemporaneous changelog existed. They are catalogued here so external
consumers (uptime monitors, codegen clients, partner integrations)
have one document to consult.

A machine-readable companion lives at `CHANGELOG.json` (Rev 12 BC-14).
Codegen consumers and CI guards can parse that file instead of this
markdown — same structure, JSON-typed.

---

## v1.3 — 2026-05-25 (Rev 13)

### Added

- **WS resume tokens / monotonic `seq` (FE-1).** Every published
  snapshot frame now carries a per-symbol monotonic `seq` integer.
  Both `WS /v1/{symbol}/stream` and `GET /v1/{symbol}/stream/sse`
  accept an optional `?since_seq=<int>` query parameter; on
  reconnect the server replays buffered frames whose `seq >
  since_seq` from a per-symbol ring buffer (depth ~60 frames ≈ 1
  minute at 1 Hz). When the buffer fully covers the gap the cache
  prime is skipped to avoid a duplicate frame in front of the
  replay. **Additive**: omitting `since_seq` preserves the v1.2
  connect path; pre-v1.3 servers ignore the parameter and the
  `seq` field. The cache-prime frame on first connect carries the
  most recent live seq (or omits the field if no live publish has
  yet occurred for the symbol).
- **`GET /v1/{symbol}/history` (FE-2).** Bucketed time-series
  endpoint over `computed_metrics`. Last-value-per-bucket
  aggregation. Required `X-API-Key` (same auth as `/snapshot`).
  Query parameters: `metric` (must be in
  `EXPECTED_METRIC_TYPES`; unknown returns HTTP 422 — intentionally
  not 404 so the surface doesn't leak which metric_types exist on
  a given deployment), `since` (ISO datetime, inclusive),
  `until` (ISO datetime, exclusive, defaults to `now()`),
  `interval` (seconds, 1–3600, default 60). Per-request bucket cap
  is 10,000 — windows that would yield more buckets return HTTP
  400 with the cap surfaced in the detail. Empty buckets are
  omitted; consumers should treat absence as "no data published in
  this bucket". Replaces the polling pattern that previously had
  to hit `/snapshot` once per bucket to reconstruct a chart.
- **`POST /admin/refresh-token` (FE-3).** Mints a new admin JWT
  from a still-valid (or recently-expired) one. Accepts the OLD
  bearer token in the `Authorization` header. Acceptance window:
  still within `exp`, OR expired within the last
  `JWT_REFRESH_GRACE_SECONDS` (default 300s = 5 min). The grace
  path absorbs clock drift and lets a consumer that was idle
  across the expiry boundary refresh without a re-login. The OLD
  token's `jti` is inserted into `jwt_revocations` in the same
  transaction so the rotated credential cannot be reused. Rate
  limit 30/min/IP. Returns the same shape as `/admin/login`
  (`access_token`, `expires_in_seconds`).

### Changed

- `Settings.jwt_refresh_grace_seconds` (default 300, env
  `JWT_REFRESH_GRACE_SECONDS`) controls the post-expiry window in
  which `/admin/refresh-token` accepts an expired token. Set to 0
  to disable the grace path entirely (refresh then requires a
  non-expired token).

### Documented (no behaviour change)

- `WsSnapshotFrame.seq` is OPTIONAL on the wire — consumers MUST
  treat absence as "no resume point known yet, ask for everything"
  rather than as an error. This preserves forward compatibility
  with older servers and the cache-prime-before-first-publish
  edge case.

---

## v1.2 — 2026-05-25 (Rev 12)

### Added

- **WS snapshot frame `type` discriminator (BC-9).** The wire frame
  emitted on `WS /v1/{symbol}/stream` now carries an explicit
  `type: "snapshot"` field alongside the existing `data` field. This
  is **additive**: pre-Rev-12 clients that branched on presence of
  `data` continue to work because the field is still emitted; new
  clients can dispatch on `type` uniformly across snapshot / tick /
  heartbeat / error frames. The TS contract previously documented
  the snapshot frame as having NO `type`; that documentation is
  superseded by this version.
- **WS error frame emission (BC-10).** `WsErrorFrame`
  (`{type: "error", code, message}`) was declared in the TS contract
  since v1.1 but never actually emitted. Rev 12 wires the helper
  `_emit_error_frame()` into the existing fatal-ish conditions in
  the WS handlers: initial-snapshot prime failure (code 503) and
  unexpected handler exception (code 500). No new error paths are
  introduced; only pre-existing failures are now surfaced as
  structured frames.
- **`Accept-Version` request-header telemetry (BC-13).** A pure-ASGI
  middleware in `app/main.py` reads any incoming `Accept-Version`
  header, stashes it on `request.state.accept_version`, and emits a
  debug-level structured log line. **No behavioural branching**;
  reserved for v1.2+ behavioural toggles per `API_POLICY.md` § 5.
  Consumers MAY echo the header (e.g. `Accept-Version: v1.2`)
  against pre-Rev-12 servers — those servers ignore unknown headers
  — without breaking forward compatibility.
- **Machine-readable `CHANGELOG.json` (BC-14).** Same structure as
  this markdown file. Codegen consumers and CI guards can parse the
  JSON without lexing markdown. See `API_POLICY.md` § 7 for the
  schema reference.
- **`GET /v1/symbols` anonymous endpoint (BC-17).** Returns
  `{symbols: string[]}` of supported symbols. Restores the
  anonymous discovery channel that Rev 8 SEC-9 closed when it
  thinned `/health` and moved `supported_symbols` to
  `/health/detail` (X-API-Key required). The new endpoint exposes
  ONLY the static symbol list — no operational telemetry, no DB
  connectivity hints, no live-feed booleans.
- **Structlog `drop_sensitive_keys_processor` (SRE-23).** Adds a
  structlog processor that replaces values for well-known sensitive
  keys (`api_key`, `token`, `password`, `secret`,
  `db_encryption_key`, ...) with `"REDACTED"` before render. The
  legacy regex `_redact()` in `app/main.py` still scrubs query
  strings out of free-form access-log lines; the new processor
  handles the orthogonal surface of structured `logger.info(...,
  api_key=...)` calls. Documented in `OPS.md` § 4c.

### Changed

- `/v1/{symbol}/flow` and `/v1/{symbol}/hiro` now carry explicit
  OpenAPI descriptions documenting their validation behaviour
  (BC-18 / BC-19): `flow.limit` outside `[1, 1000]` returns HTTP
  422 (Pydantic), `since` older than 24h or in the future returns
  HTTP 400 (endpoint guard). **Behaviour is unchanged** — only
  documentation. Validation is intentionally eager rather than a
  silent clamp so caller-side bugs surface loudly.

### Documented (no behaviour change)

- **REST envelope inconsistency permanently exempted (BC-15).**
  `/v1/{symbol}/flow` and `/v1/{symbol}/hiro` are NOT wrapped in
  the standard `{symbol, computed_at, next_update_in_seconds,
  data}` envelope. This is now a documented v1 stability exemption
  in `API_POLICY.md` § 1. **`/v2/` MUST unify all response
  envelopes.** Source-level `# REV12-V2: wrap envelope` comments
  in `flow.py` and `hiro.py` mark the wrap site for the future
  implementer.
- **NaN / Infinity serialisation contract (BC-16).** All numeric
  values that are NaN, +Infinity, or -Infinity are serialised as
  JSON `null` in BOTH REST and WebSocket transports. Consumers
  should treat `null` numeric values as "not computable" rather
  than as zero. See `API_POLICY.md` § 8.

---

## v1.1 — 2026-04-XX (Rev 8 + Rev 9)

### Breaking

- **`GET /health` response thinned to `{status, ts}`.** Previous fields
  (`db_connected`, `live_opra_connected`, `live_globex_connected`,
  `supported_symbols`, `pipeline_running`, `now`,
  `last_compute_per_symbol`, `compute_interval_seconds`) moved to
  the authenticated `GET /health/detail` endpoint (requires
  `X-API-Key`). External uptime monitors that scraped `/health` for
  these fields must either point at `/health/detail` with a key, or
  fall back to status-code-only probing.
- **Auth failure responses unified to `401`.** The previous split of
  `401` (invalid credential) vs `403` (revoked / expired / ACL-deny)
  is gone — every authentication or authorisation failure on a
  protected route returns `401` with a generic body. Consumers that
  branched on `403` to distinguish "key revoked" from "key wrong"
  must collapse the branches; the distinction is no longer surfaced.
- **Admin JWT lifetime shortened from 480 minutes to 60 minutes.**
  `JWT_EXPIRE_MINUTES` default is now `60`. Long-lived admin clients
  (BI dashboards polling on a cached token) will see expired-token
  rejections faster and must refresh on `401`.
- **WebSocket snapshot frame shape re-aligned to emitter.** The
  prior TS shape must update — distinguish snapshot vs tick /
  heartbeat / error by presence of `data` (snapshots) vs presence
  of `type` (everything else).
- **Pin probability field renamed from `probability` to `prob`** in
  the `/v1/{symbol}/snapshot` payload (`pin_probability[].prob`).
- **Migration 0012 deactivates legacy API keys with `key_lookup IS
  NULL`.** Operators must regenerate any pre-migration-0010 keys
  post-deploy; bcrypt is one-way and the keyed-BLAKE2b digest cannot
  be backfilled. The auth fast-path's NULL-fallback prefix-scan was
  removed in the same migration.

### Added

- `GET /health/detail` — full operational telemetry, requires
  `X-API-Key`. Carries the fields removed from `/health`.
- `POST /admin/logout` — server-side admin JWT denylist
  (`jwt_revocations` table, polled by every JWT verify).
- `GET /admin/metrics` — Prometheus exposition of pipeline counters
  (`flowgreeks_pipeline_partial_total`,
  `pipeline_run_finalize_errors_total`,
  `streaming_publish_errors_total`,
  `flowgreeks_dlq_evictions_total`).
- `WS /v1/{symbol}/stream/ticks` — raw spot/futures tick fan-out
  (independent of the per-tick pipeline snapshot stream).
- 13+ snapshot envelope fields added to the typed schemas:
  `health`, `walls_oi`,
  `walls_volume`, `weight_source_oi`, `weight_source_volume`,
  `vanna_total`, `charm_total`, `vanna_level`, `charm_level`,
  `risk_reversal_25d`, `iv_term_structure`, `hiro_cumulative`,
  `flow_events_last_hour`, `session_state.session_open_price_set`.

### Changed

- WebSocket revocation watcher poll interval shortened from 30s to
  5s. A revoked key now closes mid-stream with WS code 4401 within
  ~5s of the admin revoking the row.
- Body size cap of 64 KiB enforced on JSON request bodies; oversize
  requests return `413 Payload Too Large` (was previously parsed
  fully). Default applies to all `/v1/*` and `/admin/*` endpoints
  that accept a JSON body.
- HIRO bucket vocabulary on `/v1/{symbol}/hiro` accepts both `1m` /
  `5m` / `15m` (legacy) and `1min` / `5min` / `15min` (canonical) on
  the `bucket` query parameter. The response field always returns
  the canonical `1min|5min|15min` form. The snapshot endpoint's
  `data.hiro.bucket_size` was already canonical; this aligns the
  standalone endpoint with it. Pre-existing clients sending `1m`
  continue to work.
- `IvResponse.skew` on `/v1/{symbol}/iv` is deprecated in favour of
  `skew_per_expiry` (same dict, identical content, clearer name).
  Both fields remain populated for v1; `skew` will be removed in
  `/v2/`. The endpoint emits `Deprecation:` and `Sunset:` headers
  whenever the response includes the deprecated field.

---

## v1.0 — initial release

Initial public surface — `/health`, `/v1/{symbol}/{gex,max-pain,walls,
iv,snapshot,0dte,spot,futures-levels,flow,hiro}`, `/v1/{symbol}/stream`
(WS), `/v1/{symbol}/stream/sse` (SSE), full `/admin/*` surface (JWT
auth). Initial fields and shapes are documented in
`docs/api_reference.md` (current revision) and recoverable from git
history at the v1.0 tag.
