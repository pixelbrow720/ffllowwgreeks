# Changelog

All notable changes are recorded here in reverse chronological order. Format inspired by [Keep a Changelog](https://keepachangelog.com/), with semver-shaped headers once the project tags its first release.

## [Unreleased]

### Auth pivot to API keys (3e5b0ec)

FlowGreeks repositioned as an add-on inside flowjob.id. The parent site owns user accounts, billing, and add-on activation; this binary's responsibility shrank to authenticating inbound traffic against opaque API keys provisioned by the parent site.

Removed (~1500 lines):
- `internal/auth/` — signup/login/refresh/lockout/JWT/tier middleware
- migrations 0003 (users), 0005 (refresh_tokens), 0006 (refresh_token_family), 0007 (account_lockout)
- `scripts/jwt_secret/`
- `AuthConfig`, `AUTH_ENABLED`, `JWT_SECRET` env vars

Added:
- `internal/apikey/` — types, store, memory, middleware, audit, ratelimit, metrics + tests. `apikey.Generate()` mints 32-byte hex secrets, `apikey.HashSecret()` SHA-256s them; only the digest persists. Middleware accepts `Authorization: Bearer <secret>` or `X-API-Key`. RateLimiter reads `rate_limit_rps` + `rate_burst` from each row so flowjob.id can hot-swap tier budgets.
- migration 0008 — drops users + refresh_tokens, adds api_keys
- `APIKeyConfig.Enabled` + `APIKEY_ENABLED` env var

Defense-in-depth dropped from 7 layers to 5 — no more account lockout / refresh family revocation / per-user rate limit since there are no user accounts. Keeps API-key middleware → per-key rate limit → HTTP/WS read caps → security response headers → audit + metrics + alert rules.

OpenAPI dropped `/auth/*`, added `apiKeyAuth` security scheme. Prometheus rules: `flowgreeks-apikey` group replaces `flowgreeks-auth`. SECURITY.md, docs/reference/02-auth.md, docs/reference/07-defense-in-depth.md rewritten. CLAUDE.md repositions the project as one of three products in flowjob.id.

> **Note on the entries below:** the historical record of the **old** auth surface (signup/login/refresh/lockout/JWT) is preserved as-is. Those features shipped, then got removed in this pivot. Don't read the entries below as describing the current code — the auth pivot above is the source of truth.

### Pipeline foundation (M0–M9)

- **M0 + M1** — Project foundation, Databento `dbn-go` adapter, OPRA + GLBX live tick ingest, NATS publisher (90-byte fixed-layout encoder, zero-alloc hot path), TimescaleDB archive writer (batched COPY FROM, drop-on-full backpressure)
- **M2** — Black-Scholes pricing, Brent IV solver with warm-start, analytical Greeks, dealer position tracker, Lee-Ready aggressor classifier, GEX aggregator (NetGEX + walls + regime), ES/NQ basis tracker
- **M3** — DPI 5-component composite + EWMA smoothing, Charm Clock zone classifier (WEAK / RISING / PEAK / FADING / PIN), Flow Pulse 3-line oscillator (gamma / charm / vanna decomposed), synthetic SPX chain generator
- **M4** — REST `/api/{snapshot,levels}/{symbol}`, WebSocket `/ws/live` with subscribe / unsubscribe + heartbeat + drop-on-slow-client, in-process Cache + Broker
- **M7 phase 1** — `cmd/replay` binary with reader pacing (1× / N× / unpaced), session manager
- **M8** — What-If Dealer Simulator (forced-flow notional, top contributing strikes), `POST /api/simulate/{symbol}`, Pin Probability Engine
- **M9** — Rule-based narrative engine, compute integration, NATS `narrative.<sym>` fan-out, api WS bridge

### Post-M9 tracks (A–H)

- **A** — WebSocket replay control plane: `/ws/replay/{session_id}`, pause / resume / seek / speed
- **B** — Backtest engine: predicate-driven strategy validation, Sharpe / Sortino / max drawdown
- **C** — Alerts engine: rule evaluator + delivery sinks + REST CRUD + WS bridge
- **D** — End-to-end pipeline integration test (full M2 + M3 wired through synthetic SPX chain)
- **E** — Auth scaffolding: JWT HS256 issuer, bcrypt password store, middleware, `/auth/{signup,login,me}`, `pgxpool`-backed user store
- **F** — `dealer_state_1s` cold-path archive: migration 0004 hypertable + 7d compression + 14mo retention, `store.StateWriter` (batched COPY FROM), compute aggregator wiring
- **G** — `POST /api/backtest/run`: reads `dealer_state_1s` archive over a `[from, to)` window, replays through backtest engine using `alerts.Rule` predicate triple, 30s deadline, 31d max range, returns full trade list + summary metrics
- **H** — GitHub Actions CI workflow: build + vet + test (with race + tidy-drift guard), golangci-lint

### Ops / security follow-ups (post-H)

- **synth_state publisher** (`scripts/synth_state`) — emits realistic `state.<sym>.gex` + `narrative.<sym>` at 1 Hz so frontend can be exercised without Databento
- **Docker compose end-to-end** — multi-stage distroless `Dockerfile` shared by all four binaries, three compose profiles (default infra, `app` full stack, `demo` infra + api + synth_state), migration sidecar gates startup
- **OpenAPI 3.1 spec** (`docs/openapi.yaml`) — every REST route documented, codegen-ready
- **golangci-lint v2 config** — migrated from legacy v1, dropped `gosimple` / `prealloc`, added `gosec` / `gocritic` / `bodyclose`
- **Auth gate wiring** — `Handlers.MountPublic` (snapshot, levels) vs `MountProtected` (simulate, alerts CRUD, backtest); when `AUTH_ENABLED=true` protected group runs behind `auth.Middleware`
- **WebSocket stress test** (`scripts/ws_stress`) — N concurrent clients, p50 / p95 / p99 / max latency report, exits non-zero on failure for CI gating
- **HTTP metrics middleware** — per-route Prometheus histograms + counters keyed by chi route pattern (bounded cardinality)
- **Auth rate limit** — per-IP token bucket on `/auth/{signup,login}`, 5 burst with ~12s refill, `429 Retry-After`
- **`/health/ready` readiness probe** — checks NATS connection state + Postgres reachability with 2s budget; per-dependency JSON; 200 / 503
- **State writer metrics** — `flowgreeks_state_rows_{written,dropped}_total`, flush duration histogram, flush errors counter
- **Historical backfill skeleton** (`scripts/backfill`) — dry-run-by-default; real `dbn_hist.GetRange` glue lands on Databento unlock
- **Production config guard** — refuses to boot under `APP_ENV=production` with weak defaults (dev DB password, short / placeholder JWT secret, empty CORS, debug log level); dev / staging / test bypass
- **WebSocket broker metrics** — subscribers gauge, publish + drop counters by symbol / kind
- **Compute pipeline metrics** — ticks processed by symbol / tick_type, IV solver attempts + failures, aggregator iterations + duration, active-strike gauge
- **Grafana starter dashboard** (`deploy/grafana/flowgreeks-pipeline.json`) — 10 panels covering ingest, compute, api, archives, WS broker
- **JetStream setup helper** (`scripts/jetstream_setup`) — idempotently creates / updates TICKS, STATE, FLOW streams to spec
- **Prometheus alert rules** (`deploy/prometheus/flowgreeks.rules.yml`) — 9 rules across pipeline-liveness, backpressure, http, quote-quality
- **Replay manager + session metrics** — sessions active gauge, created / rejected / finished counters, ticks published, publish errors
- **Alerts engine metrics** — rules gauge, evaluations, fired by kind, cooldown-suppressed by kind, deliveries + delivery errors by sink
- **Webhook async error metric** (`flowgreeks_alerts_webhook_async_errors_total`) — surfaces background POST failures the engine couldn't see (marshal / build / transport / 4xx-5xx)
- **Ingest dispatch metrics** — published total by tick_type, publish errors, feed adapter errors
- **Prometheus + Grafana wiring in compose** — `obs` profile (also auto-on for `app` and `demo`); scrape config covers api/ingest/compute; Grafana auto-provisions Prometheus DS + dashboard JSON; bound on host port 3001
- **End-to-end smoke test** (`scripts/smoke/e2e`) — walks /health, /health/ready, /metrics, snapshot/levels/simulate, /ws/live; exits non-zero on any failure for CI gating
- **OpenAPI spec updates** — `/health/ready` + `/metrics` documented with `ReadinessResponse` schema
- **JWT secret generator** (`scripts/jwt_secret`) — crypto/rand 32-byte hex output to satisfy production guard
- **README quickstart** — three on-ramps (demo / app / local-dev) with curl examples + production checklist + repo layout map
- **Makefile helpers** — `demo-up`, `demo-down`, `synth-state`, `ws-stress`, `backfill-plan`, `jetstream-setup`, `smoke-e2e`

### Production-readiness pass (post-observability)

While waiting on Databento unlock — every gap that would block real frontend integration or live use:

- **Distributed tracing** (`internal/trace/`) — request-scoped trace id propagated via `X-Trace-ID` (HTTP) and `nats.Header` (NATS 2.x). `traceMiddleware` extracts upstream id, falls back to chi request id, otherwise generates a fresh 8-byte hex. `requestLogger` emits `trace_id` alongside `req_id` so slog lines from any binary can be grepped per request. Scope deliberately request-level only — every tick or per-second state publish is too high-volume to justify a header.
- **WebSocket resume on reconnect** — `/ws/live` now pushes the last-known snapshot per `(symbol, kind)` immediately on `subscribe`, marked `type="snapshot.replay"`. Without this, clients had to wait up to 1s for the next compute publish, which surfaced as flicker on reconnect.
- **Auth refresh tokens** — short-lived access JWT (1h) + long-lived refresh (30d). `/auth/{signup,login}` returns `{token, refresh_token, expires_in, user}`; `POST /auth/refresh` rotates both atomically; `POST /auth/logout` revokes the refresh token (idempotent). Migration `0005_refresh_tokens.up.sql` adds the `refresh_tokens` table; secrets stored as SHA-256 hashes only.
- **CI smoke job** — third workflow job that boots the full demo stack via `docker compose --profile demo up -d --build`, waits up to 60s for `/health/ready`, then runs `scripts/smoke/e2e` against the api. Catches Dockerfile drift, compose service wiring, migration ordering — failures `go test ./...` can't see. Captures recent logs from every service on failure.
- **Pagination** on `GET /api/alerts/rules` — accepts `?limit` (1-200, default 50) and `?offset` (default 0). Response shape changed to `{rules, total, offset, limit}`. `engine.ListRulesPage` is the canonical paginated reader; `ListRules` keeps the bare-slice signature plus stable ID-sort for callers that don't want pages.
- **Pgxpool config from env** — `PostgresConfig` carries `MaxConns / MinConns / MaxConnLifetime / MaxConnIdleTime`. Encoded into the DSN as pgxpool query params (`pool_max_conns` etc.) so existing `pgxpool.New(cfg.Postgres.DSN())` call sites pick up tuning without per-call refactor. Empty values fall through to pgx defaults.
- **Two-phase graceful shutdown** in `cmd/api` — on SIGTERM, flips `draining=true` so `/health/ready` returns 503 + `status="draining"`, sleeps `SHUTDOWN_DRAIN_DELAY` (default 5s) so the load balancer pulls this instance from rotation, THEN runs `srv.Shutdown(15s)`. Without the drain delay, k8s rolling restarts cut connections mid-request because the LB was still routing. `SHUTDOWN_DRAIN_DELAY=0` disables for local dev.
- **Pgxpool consolidation** in `cmd/api` — auth, replay, and backtest now share a single pool owned by `main`. Previously each setup function opened its own pool with default `MaxConns=4×CPU`, stacking 3× the connection limit on the same database for zero isolation benefit.

### Deep review pass (post-readiness)

Four reviewer agents in parallel surfaced ~30 findings across feed/bus/store, greeks/dealer, replay/backtest/narrative/cmd, api/auth/alerts. Every finding either fixed or explicitly tracked under "Known gaps" below. See [docs/REVIEW.md](docs/REVIEW.md) for the full report and per-finding rationale.

**Security cluster (P0):**

- **JWT verifier pinned to HS256** — previously accepted any `*SigningMethodHMAC`, allowing alg-confusion downgrade. Also enforces `Issuer="flowgreeks"` so tokens minted by another service sharing the secret can't authenticate here.
- **Refresh rotation atomic CAS** — replaced non-atomic Lookup → IsActive → Revoke with a single `UPDATE … RETURNING` via `ConsumeForRotation`. Two concurrent calls with the same refresh token both pass the lookup but only one wins the CAS — the loser sees `ErrRefreshTokenInactive`. Stops replay-after-leak.
- **WebSocket origin default-deny** — `/ws/live` no longer sets `InsecureSkipVerify=true` when `Origins` is empty. Empty list means same-origin only; cross-origin upgrades from `evil.com` to a logged-in user's session are now blocked unless the origin is explicitly allowlisted.
- **Webhook SSRF guard** — `alerts.NewWebhookSink` validates the URL at construction: `http(s)` scheme only, blocks loopback / private (RFC 1918) / link-local (incl. cloud metadata 169.254.169.254) / CGNAT (100.64/10) / IPv6 ULA (fc00::/7) / site-local. Returns `ErrWebhookBlockedTarget` so a hostile rule fails on save, not on first fire.
- **Alerts authn** — `/api/alerts/*` prefers JWT claims via `callerUserID(r)` over the `X-User-ID` header. Header stays as the dev escape hatch when `AUTH_ENABLED=false`; spoof-via-header dies once the gate flips.

**Data-loss cluster (P0):**

- **ArchiveWriter + StateWriter final-flush context** — when `ctx.Err() != nil`, flush switches to a fresh 10s `context.Background()` deadline so the in-flight batch lands instead of `pgx.CopyFrom` returning `ctx.Err` immediately and the rows being silently logged + dropped.
- **StateWriter lifecycle redesign** — replaced the racey `wg.Add-inside-Run` pattern with `closeCh + done + closeOnce + atomic.Bool running`, mirroring `ArchiveWriter`. `Close` self-drains when `Run` was never invoked. Previous code could pool-close while `Run` was still draining.
- **Synthetic generator close race** — `Stop()` now closes `g.stop`, waits on a `sync.WaitGroup` of the 5 producer goroutines, THEN closes `g.out`. Previous code closed `g.out` immediately while producers were still mid-`select`, racing send-on-closed-channel panics. Added `rngMu` + `randIntn / randNorm / randFloat` helpers since `math/rand.Rand` isn't goroutine-safe.

**Race cluster (P1):**

- **Subscriber.dropped → atomic.Uint64** — was incremented under `Broker.mu.RLock` but read with no synchronization from `Dropped()` callers. `go test -race` would have flagged.
- **Subscriber.filter → RWMutex + snapshotFilter** — `applyFilter` mutated maps on the WS read loop while `Broker.Publish` probed via `matches()` from the publisher goroutine. Concurrent map read/write. Added `filterMu`; `snapshotFilter()` copies under lock so cache-seeding can iterate without holding.
- **Session.cancel race** — `Stop()` reading `s.cancel` while `Run()` was still scheduling `context.WithCancel` was a data race; under `-race` the read could observe `nil`, silently skipping cancellation. Replaced with `cancelMu+cancel` and a `doneOnce sync.Once` gating `close(s.done)`. `Run` re-checks `s.stopped` after publishing the cancel func.

**Math cluster (P1):**

- **TZ caching in `greeks.TimeToExpiryYears`** — `time.LoadLocation("America/New_York")` was called per tick, dominating the per-tick hot path on hosts where tzdata isn't memory-resident. Now cached at package init, falls back to UTC when tzdata is missing.
- **`dealer.Simulate.NetPressure` sign** — was `ForcedNotional + CharmAid`; both share the `-spot×Δ` sign convention so addition inflated magnitude when charm and forced flow moved together. Now subtracts (matching the doc comment).
- **Classifier two-generation reset** — replaced the "wipe everything" cap-hit reset with `curr / prev` two-generation maps. After rotation, hot strikes are still recoverable from the previous generation, so tick-test fallback doesn't regress to `UNKNOWN` for the entire chain after the first 10k unique strikes.
- **Backtest annualisation factor** — `Sharpe = mean/stddev × sqrt(252)` was meaningless for 0DTE strategies that fire many trades intraday or none for days. New `annualisationFactor(n, from, to)` derives `sqrt(trades/year)` from the actual test window; falls back to no scaling when the window is too narrow to estimate.
- **Backtest stream-end straggler** — open trade at end of stream now reports the actual `lastSpot` instead of `entrySpot` with `0%` return. Previous behaviour falsified P&L on truncated streams (replay cancel, ctx timeout): an open winner rendered flat.
- **Backtest sortino** — dropped the `if mean == 0 return 0` short-circuit. Sortino is defined whenever downside deviation > 0.

**Polish cluster (P2):**

- `alerts.sortRulesByID` switched from O(n²) insertion sort to `sort.Slice`.
- `alerts.OnSnapshot` `TsNs == 0 → use wall clock` check fixed: `time.Unix(0, 0).IsZero()` returns FALSE (epoch is 1970-01-01, not Go's zero time). Now checks `TsNs` directly.
- `backtest.alertsRuleMatches` guards every numeric predicate (DPI / NetGEX / PinProb) against `NaN`. `NaN > x` is false in Go, so a corrupted snapshot would silently miss instead of surfacing.
- `auth.isUniqueViolation` now matches via `pgconn.PgError` + `pgerrcode.UniqueViolation` instead of string-match on `"23505" / "duplicate key"`. Locale-translated server builds broke the previous matcher.

### Production-proven hardening (post-deep-review)

User-driven hardening pass closing the gaps the deep review left open. Goal: move from "structurally production-ready" to "production-proven" before frontend integration.

**Auth:**

- **Refresh-token reuse detection** — every refresh token now carries a `family_id` (migration 0006). Login starts a new family; rotation copies the parent's family_id; replaying a *rotated* (revoked) token revokes the entire family via `RevokeFamily`. Legitimate user is forced to re-login on a leak — the canonical OAuth2-rotation defense. `ConsumeForRotation` returns the offending row alongside `ErrRefreshTokenInactive` so handlers have everything they need to act on it. New tests: reuse-revokes-family, distinct-logins-stay-isolated. (`4100e7f`)
- **Audit log** — `auth.AuditEvent` + `auth.AuditSink` with slog backend. Login ok/fail, signup ok/fail, refresh ok/fail, **refresh.reuse_detected (WARN)**, logout — all emitted as structured records with kind, user_id, email, ip, user_agent, detail. Same sink wired into `AlertHandlers` so rule create/delete events live on the same log stream and ship under one SIEM rule. (`4100e7f`, `3b1358a`)

**API surface rate limiting:**

- **Per-user rate limit on protected routes** — `auth.RateLimiter.MiddlewareKeyed` + `auth.UserKeyOrIP`. `/api/simulate`, `/api/alerts/*`, `/api/backtest/run` now gated at 60 req/min, burst 30, keyed by JWT user id (falls through to per-IP for anonymous traffic). Each request can carry a 30s server-side deadline, so without this a single account fanning out N concurrent calls could saturate compute. New tests cover keyed-per-user isolation + anonymous IP fallthrough. (`3b1358a`)

**Math:**

- **Deep-OTM IV solver auto-widen** — `ImpliedVol` widens the bracket once (`VolMin/10` ↔ `VolMax*2`, capped `1e-6` ↔ `10`) when residuals share sign at both ends. Previously deep-OTM 0DTE chains with σ > 5.0 returned `"no bracket"` and dropped out of the snapshot. New `TestImpliedVol_HighVolAutoWiden` covers it. Doesn't compromise the unsolvable-mid path (still `"no bracket"` after widen). (`afb7831`)
- **Synthetic generator strike recentre** — `strikeFor` now anchors on `g.Spot()` (current) instead of `cfg.Spot` (initial). After a few percent drift the entire chain became deep-OTM/ITM and downstream consumers stopped seeing realistic flow. (`afb7831`)

**Dependency hygiene:**

- **`google.golang.org/protobuf` 1.32.0 → 1.36.11** — clears `GO-2024-2611` (infinite loop in JSON unmarshaling). Indirect via `nats.go`; we don't call it ourselves but vuln scanners flag it.

**Static analysis:**

- Cleared every `staticcheck ./...` finding: dead `backtest.closeTrade`, dead `replay.formatSessionID`, unused `recordingPub.failOn` test field. `go vet` + `staticcheck` + `govulncheck` all clean.

**CI:**

- New **`security` job** in `.github/workflows/test.yml` runs `staticcheck` + `govulncheck` on every PR. Fails the build on any new dead-code finding or known-CVE bump.

**Coverage:**

- New `internal/store/state_writer_test.go` covers the offline lifecycle paths exposed by the deep-review redesign: buffer-full backpressure, write-after-close rejection, `closeOnce` guard against close-of-closed under concurrent Close, single-Run guard via `running.CompareAndSwap`, `derefF` nil-safety. Doesn't need a real pgxpool. (`91a5dab`)
- New databento offline tests: `dbnFixedString` trim cases, `resolveSymbol` malformed-input rejection, `parseFutureSymbol` NQ→NDX path, `convertCmbp1` (OPRA consolidated quote) round-trip, `bootstrapDataset` guard clauses. Coverage on `internal/feed/databento` climbs from ~14% to 23% — remaining gap is live-stream paths waiting on OPRA unlock. (`a7b8a78`)

### Defense-in-depth follow-up (post-2026-05-27)

Backstop layer for the per-IP rate limiter — addresses distributed credential-stuffing where each attempt rotates IPs.

- **Account lockout** (migration 0007) — 10 consecutive failed logins lock the account for 15 minutes. `users.failed_login_count` + `users.locked_until` columns; lock is a wall-clock timestamp so it auto-expires without a sweeper job. Successful login resets both. Locked accounts get `429 Too Many Requests` + `Retry-After`; **even the correct password is refused during the lock window** — the canonical attacker-defense behavior, since a guess after the lock tripped is the exact case we're protecting against. New audit kinds `auth.login.locked_trip` (just got locked) + `auth.login.locked_out` (already-locked attempt blocked), both at WARN level. (`3065252`)

### Continuous load testing

- **Nightly `ws_stress` CI workflow** — `.github/workflows/nightly.yml` runs at 03:00 UTC + on-demand via `workflow_dispatch`. Brings up `docker compose --profile demo`, polls `/health/ready`, then runs three escalating tiers: 100c/30s smoke → 500c/60s soak → 1000c/60s target (matches launch-readiness goal). Each tier exits non-zero if the api couldn't sustain the load. On failure: dumps `/metrics` tail + recent compose logs. Closes the `ws_stress not yet a CI gate` deferred note. (`cde848e`)

### Final hardening pass (post-backstop)

Finishing the production-proven posture — every remaining "could a hostile client wedge this" surface gets a cap or a metric.

- **WebSocket inbound read limits** — `/ws/live` `SetReadLimit(4096)` and `/ws/replay` `SetReadLimit(1024)`. Inbound shapes are tiny (subscribe / unsubscribe JSON; pause / resume / set_speed JSON); anything beyond is malformed or hostile. The connection is killed on overflow before it can pin server memory. (`8d36519`)
- **`/ws/replay` origin default-deny** — replaced lingering `InsecureSkipVerify=true` with `OriginPatterns: cfg.API.CORSOrigins`, mirroring the `/ws/live` posture. `/ws/replay` no longer accepts cross-origin upgrades unless the origin is explicitly allowlisted. (`8d36519`)
- **Global HTTP body cap** — new `api.BodyLimit` middleware caps `r.Body` at 1 MiB via `http.MaxBytesReader`. Every chi-mounted route inherits the cap; existing per-handler `io.LimitReader` (4–16 KiB) stays as the tighter inner bound. Defense for routes that ever forget the inner limit. (`8d36519`)
- **`/health/live` alias** — added next to `/health` so k8s `livenessProbe` + `readinessProbe` can use canonical names (`/health/live` + `/health/ready`). `/health` stays as the legacy alias. (`8d36519`)
- **Auth Prometheus metrics** — `flowgreeks_auth_login_attempts_total{result=ok|fail|locked}`, `…_signup_attempts_total{result=ok|fail}`, `…_refresh_attempts_total{result=ok|fail|reuse_detected}`, `…_logouts_total`, `…_account_lockouts_total`. Cardinality bounded — no per-user / per-email labels. Pairs with the existing slog audit log so SIEM rules can alert on rate-of-change instead of polling logs. (`8d36519`)
- **Auth Prometheus alert rules** — new `flowgreeks-auth` rule group: `AuthLoginFailureBurst` (warn, > 5/s for 2m), `AuthLockoutTripBurst` (page, > 0.5 lockouts/s for 5m), `AuthRefreshReuseDetected` (page, immediate). Lives in `deploy/prometheus/flowgreeks.rules.yml`. (`b8fff04`)
- **Security response headers + SECURITY.md** — new `api.SecurityHeaders` middleware sets `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`, `Cross-Origin-Resource-Policy: same-origin`. HSTS gated on `r.TLS || X-Forwarded-Proto=https` so reverse-proxy deployments flip it on without breaking local dev. New `SECURITY.md` documents the reporting channel, six-layer defense posture, auth model, audit + telemetry plumbing, outbound SSRF guard, CI/nightly verification, and the production-config refusal list. (`f1fa3c1`)

### Hot-path benchmarks (zero-alloc)

| Component | Target | Achieved |
|---|---|---|
| Black-Scholes | < 200ns | 105ns |
| Greeks (all five) | < 500ns | 259ns |
| IV solver | < 5µs | 1.03µs |
| GEX aggregator (200 strikes) | < 50µs | 5.2µs |
| Lee-Ready classifier | < 100ns | 71ns |
| Position Apply | < 100ns | 49ns |
| Basis Update | < 200ns | 156ns |

### Live data status

- **GLBX.MDP3** (futures) — end-to-end verified: Databento → ingest → NATS → archive → Postgres
- **OPRA.PILLAR** (options) — blocked on Databento account unlock; bootstrap path verified against Python reference

### Known gaps (deferred — pending Databento unlock or post-launch)

- **Frontend (M5)** — separate Next.js track owned by user, lives at `../web/`
- **Stripe billing (M6)**
- **CSRF token** — REST is bearer-token only today, so impact is low. Required iff frontend ever switches to cookie sessions.
- **Backfill execute path** — skeleton only; needs Databento unlock to wire `dbn_hist.GetRange`
- **Live OPRA verification** — Databento account locked since the bootstrap-debug session, support unlock required. GLBX.MDP3 (futures) end-to-end already verified.
- **DPI / Charm Clock / Pin Probability calibration** — math is correct, but weight + threshold values are intuition-based. Needs backtest validation against historical 0DTE realisations once OPRA is live.
- **External pentest** — internal review pass complete; third-party pentest still recommended pre-public-launch.
- **Race detector locally** — Windows host has no `gcc`, so `-race` runs on CI only. CI's `test.yml` `build` job runs `go test -race ./...` on every PR.
