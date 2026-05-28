# Progress

> The single source of truth for "where are we right now". Update after every meaningful work session. Cross-session continuity depends on this file being current.

**Format rules:**
- Top section: current state in 1-2 paragraphs
- Active milestone section: checklist with [x] / [ ] state
- Decisions log: dated, terse, append-only
- Open questions: things blocking progress

---

## Current state

**Phase:** M0–M9 backend + post-M9 hardening (A–H) + observability + production-readiness + deep review + production-proven hardening + **auth pivot to API keys** — **✅ COMPLETE**

**Last session:** 2026-05-27 — Auth pivot landed. FlowGreeks repositioned as an add-on inside flowjob.id; the parent site owns user accounts + billing. Removed `internal/auth/` (~1500 lines: signup/login/refresh/lockout/JWT) + migrations 0003/0005/0006/0007. Added `internal/apikey/` package with `Generate`/`HashSecret`/`Middleware`/`RateLimiter`/`AuditSink` + migration 0008 (`api_keys` table). Defense-in-depth dropped from 7 layers to 5 — no more account lockout / refresh rotation / per-user rate limit since there are no user accounts. New per-key rate limit reads `rate_limit_rps` + `rate_burst` straight off the `api_keys` row so flowjob.id can hot-swap tier budgets without redeploying this binary.

**Latest commit head:**

```
(pending — auth pivot commit)
8d36519 feat(api,auth): WS read limits + body cap + /health/live + auth metrics
f7e9c95 docs: refresh CHANGELOG/PROGRESS/REVIEW/HANDOFF for backstop layer
a7b8a78 test(databento): cover dbnFixedString + resolveSymbol edge cases
cde848e ci: nightly ws_stress against demo stack
3065252 feat(auth): account lockout after repeated failed logins
8fd6c31 docs: hardening-pass sweep + HANDOFF.md for next session
91a5dab test(store): cover StateWriter lifecycle paths offline
afb7831 fix: deep-OTM IV widen + synth recenter + bump protobuf + CI security
3b1358a feat(api): per-user rate limit on protected surface + alert audit
4100e7f feat(auth): refresh-token reuse detection + audit log
6e56041 docs: sweep CHANGELOG + PROGRESS + REVIEW + openapi for production-readiness + deep-review pass
9793e43 fix: P2 polish — sort.Slice, NaN guards, pgerrcode, epoch trap
b8a12cc refactor(api): consolidate pgxpool — single shared pool
864f330 fix(math): TZ caching + simulator sign + classifier reset + backtest annualisation
8ba94ee fix(replay): Session.cancel race between Run and Stop
d95d599 fix(api): WS Subscriber races — atomic dropped + RWMutex filter
b3748e7 fix(store,synthetic): final-flush context + lifecycle + close race
5ec2a0c security: harden auth + WS origin + webhook SSRF + alerts authn
9c03d1b feat(api): two-phase graceful shutdown with drain delay
e93fecb feat(config): postgres pool tuning from env
e561def feat(alerts,api): paginate /api/alerts/rules
ca78e40 ci: smoke job that runs docker compose --profile demo + smoke-e2e
c3d4eda feat(api): WS resume — push cached snapshots on subscribe
f559570 feat(trace): distributed trace id across HTTP + NATS
```

**Workstream coverage:**

- ✅ M0: Project foundation (Go module, docker stack, migrations, slog, /metrics)
- ✅ M1: Live tick ingest (Databento dbn-go adapter, OPRA bootstrap, NATS publisher, archive writer)
- ✅ M2: Greeks engine (BS, IV solver, analytical) + dealer position + Lee-Ready + GEX aggregator + basis tracker
- ✅ M3: DPI composite + Charm Clock zones + Flow Pulse 3-line oscillator
- ✅ M4: REST `/api/snapshot|/levels|/simulate` + WebSocket `/ws/live` with sub/unsub + heartbeat + drop-on-slow
- ⏳ M5: Frontend — Next.js 14 in `../web/`, ~35% complete (landing 9 sections + dashboard skeleton on mock data)
- ⏳ M6: flowjob.id ↔ FlowGreeks API-key provisioning — `internal/apikey/` package + migration 0008 ready; parent-site mint/revoke flow pending kawan's Node.js work
- ✅ M7 phase 1: `cmd/replay` binary, reader pacing 1× / N× / 0× (unpaced), CLI flags
- ✅ M7 phase 2: `/ws/replay/<session_id>` playback control plane (track A)
- ✅ M7 phase 3: Backtest engine (track B) + REST `POST /api/backtest/run` (track G)
- ✅ M8: What-If Dealer Simulator + `POST /api/simulate/{symbol}` + Pin Probability Engine
- ✅ M9: Rule-based narrative engine, NATS publish, api fan-out integration

**Post-M9 hardening tracks (A–H):**

- ✅ **A** — WS replay control plane: `/ws/replay/{session_id}`, pause/resume/seek/speed (`internal/replay/ws.go`)
- ✅ **B** — Backtest engine core: predicate-driven strategy validation, Sharpe/Sortino/maxDD (`internal/backtest/`)
- ✅ **C** — Alerts engine: rule evaluator + delivery sinks + REST CRUD + WS bridge (`internal/alerts/`, `internal/api/alerts.go`)
- ✅ **D** — End-to-end pipeline integration test (`internal/dealer/integration_test.go`, `internal/e2e/pipeline_test.go`)
- ✅ **E** — Auth scaffolding: JWT issuer (HS256) + bcrypt store + middleware + `/auth/{signup,login,me}` (`internal/auth/`); gated by `JWT_SECRET` env *[superseded by auth pivot 2026-05-27 — `internal/auth/` deleted, replaced by `internal/apikey/`. See [CHANGELOG.md "Auth pivot to API keys"](../CHANGELOG.md) and [docs/reference/02-auth.md](reference/02-auth.md).]*
- ✅ **F** — Persist `dealer_state_1s` to TimescaleDB: migration 0004 hypertable + `store.StateWriter` (batched COPY FROM) + compute wiring; 7d compression + 14mo retention
- ✅ **G** — REST `POST /api/backtest/run`: reads `dealer_state_1s` archive, replays through backtest engine using `alerts.Rule` predicate triple (Kind+Threshold+StringArg), 30s deadline, returns Sharpe/maxDD/trades
- ✅ **H** — GitHub Actions CI: `.github/workflows/test.yml` runs build+vet+test (with race + tidy-drift guard) + golangci-lint on push and PR

**Ops + security follow-ups (post-H, while OPRA still locked):**

- ✅ **synth_state publisher** — `scripts/synth_state` emits realistic `state.<sym>.gex` + `narrative.<sym>` at 1 Hz so frontend can be exercised without Databento
- ✅ **Docker compose end-to-end** — `deploy/Dockerfile` (multi-stage distroless) + `deploy/docker-compose.yml` with three profiles: default (infra), `app` (infra + 4 binaries + migration sidecar), `demo` (infra + api + synth_state)
- ✅ **OpenAPI 3.1 spec** — `docs/openapi.yaml` documents every REST route currently mounted, ready for frontend codegen
- ✅ **golangci-lint v2 config** — migrated from legacy v1 schema, dropped gosimple/prealloc, added gosec/gocritic/bodyclose
- ✅ **Auth gate wired** — `Handlers.MountPublic` (snapshot, levels) vs `MountProtected` (simulate, alerts, backtest); when `AUTH_ENABLED=true` protected group runs behind `auth.Middleware`
- ✅ **WS stress test** — `scripts/ws_stress` spins up N clients against `/ws/live`, reports connected count, aggregate msg/s, p50/p95/p99/max latency
- ✅ **HTTP metrics middleware** — per-route Prometheus histograms + counters (`flowgreeks_http_requests_total`, `_request_duration_seconds`, `_response_bytes`) keyed by chi route pattern so cardinality stays bounded
- ✅ **Auth rate limit** — per-IP token bucket on `/auth/{signup,login}`, 5 burst with ~12s refill, 429 + `Retry-After`

**Observability + ops follow-ups (post-rate-limit):**

- ✅ **Readiness probe** — `/health/ready` checks NATS + Postgres with 2s budget, returns per-dependency JSON; pair with k8s `readinessProbe`
- ✅ **State writer metrics** — `flowgreeks_state_rows_{written,dropped}_total`, flush duration histogram, flush errors counter
- ✅ **Backfill skeleton** — `scripts/backfill` dry-runs by default; real `dbn_hist.GetRange` glue lands on Databento unlock
- ✅ **Production config guard** — refuses to boot under `APP_ENV=production` with weak defaults (dev DB password, short / placeholder JWT, empty CORS, debug log level)
- ✅ **WS broker metrics** — subscribers gauge, publish + drop counters by symbol/kind
- ✅ **README quickstart** — three on-ramps (demo / app / local-dev) + production checklist + repo layout
- ✅ **Compute pipeline metrics** — ticks processed by symbol/tick_type, IV solver attempts + failures, aggregator iterations + duration, active strikes gauge
- ✅ **Grafana dashboard** — `deploy/grafana/flowgreeks-pipeline.json`, 10 panels covering ingest, compute, api, archives, WS broker
- ✅ **JetStream setup helper** — `scripts/jetstream_setup` idempotently creates / updates TICKS, STATE, FLOW streams to spec
- ✅ **Prometheus alert rules** — `deploy/prometheus/flowgreeks.rules.yml`, 9 rules across pipeline-liveness / backpressure / http / quote-quality
- ✅ **Replay manager metrics** — sessions active gauge, created / rejected / finished counters, ticks published, publish errors
- ✅ **Alerts engine metrics** — rules gauge, evaluations, fired / cooldown-suppressed by kind, deliveries + delivery errors by sink
- ✅ **CHANGELOG.md** — chronological reference with hot-path benchmark table + live-data status + known gaps

**Production-readiness pass (post-observability):**

- ✅ **Distributed tracing** — `internal/trace/`, `X-Trace-ID` header propagation across HTTP + NATS, `trace_id` emitted in slog beside `req_id` for cross-binary log correlation
- ✅ **WS resume on reconnect** — `/ws/live` pushes last-known snapshot per `(symbol, kind)` immediately on subscribe (`type="snapshot.replay"`); kills the up-to-1s flicker on reconnect
- ✅ **Auth refresh tokens** — short-lived access JWT (1h) + long-lived refresh (30d) with rotation on use; migration `0005_refresh_tokens` adds the table; secrets stored as SHA-256 hashes; `/auth/{refresh,logout}` endpoints
- ✅ **CI smoke job** — third workflow job that boots the demo stack via `docker compose --profile demo up -d --build`, polls `/health/ready`, runs `scripts/smoke/e2e`; catches Dockerfile / compose drift `go test` can't see
- ✅ **Pagination** on `GET /api/alerts/rules` — `?limit` (1-200, default 50), `?offset`, response envelope `{rules, total, offset, limit}`
- ✅ **Pgxpool config from env** — `POSTGRES_{MAX_CONNS,MIN_CONNS,MAX_CONN_LIFETIME,MAX_CONN_IDLE_TIME}` encoded into the DSN as pgxpool query params; per-binary tuning without per-call refactor
- ✅ **Two-phase graceful shutdown** in `cmd/api` — flips `/health/ready` to 503 + `status="draining"`, sleeps `SHUTDOWN_DRAIN_DELAY` (5s), then `srv.Shutdown(15s)`; eliminates k8s rolling-restart connection cuts
- ✅ **Pgxpool consolidation** in `cmd/api` — three independent pools (auth + replay + backtest) collapsed to one shared pool owned by `main`

**Deep review pass:**

Four `Explore` reviewer agents in parallel against package clusters → **30 findings**, **21 fixed across 7 commits**, 9 deferred / won't-fix. Full report in [docs/REVIEW.md](REVIEW.md).

- ✅ **P0 security (5)** — JWT alg pin, refresh rotation atomic CAS, WS origin default-deny, webhook SSRF guard, alerts authn from JWT (commit `5ec2a0c`)
- ✅ **P0 data-loss (3)** — archive + state-writer final-flush context, state-writer lifecycle redesign mirroring archive, synthetic close race + rng locking (commit `b3748e7`)
- ✅ **P1 races (3)** — `Subscriber.dropped` atomic, `Subscriber.filter` RWMutex, `Session.cancel` race (commits `d95d599`, `8ba94ee`)
- ✅ **P1 math (5)** — TZ caching in `TimeToExpiryYears`, simulator `NetPressure` sign, classifier two-generation reset, backtest annualisation factor from window, backtest stream-end straggler (commit `864f330`)
- ✅ **P2 polish (4)** — `sort.Slice`, NaN guards on backtest predicates, `pgconn.PgError` typed match for unique violations, `time.Unix(0,0).IsZero()` epoch trap (commit `9793e43`)

**Production-proven hardening (post-deep-review, 2026-05-27):**

Closing the structural gaps the deep review left deferred. Goal: move from "structurally production-ready" to "production-proven" before frontend integration. Every fix has tests; everything still green under `go test ./...`.

- ✅ **Refresh-token reuse detection** — migration 0006 adds `family_id`; replaying a rotated (revoked) refresh token revokes the entire family via `RevokeFamily`. Legitimate user is force-logged-out on a leak, the canonical OAuth2 defense. Tests: reuse-revokes-family, distinct-logins-stay-isolated. (`4100e7f`)
- ✅ **Audit log** — `auth.AuditEvent` + `auth.AuditSink` + slog backend; login ok/fail, signup, refresh ok/fail, **refresh.reuse_detected (WARN)**, logout. Same sink wired into `AlertHandlers` for rule create/delete. (`4100e7f`, `3b1358a`)
- ✅ **Per-user rate limit on protected routes** — `RateLimiter.MiddlewareKeyed` + `UserKeyOrIP`. `/api/{simulate,alerts,backtest}` 60 req/min, burst 30, keyed by JWT user id (per-IP fallthrough for anonymous). (`3b1358a`)
- ✅ **Deep-OTM IV solver auto-widen** — `ImpliedVol` widens once when residuals share sign at both ends. Deep-OTM 0DTE chains with σ > 5.0 no longer drop out of the snapshot. (`afb7831`)
- ✅ **Synthetic generator strike recentre** — `strikeFor` anchors on `g.Spot()` (current) instead of `cfg.Spot` (initial), so long-running synth keeps emitting strikes around live ATM. (`afb7831`)
- ✅ **CI security job** — `staticcheck` + `govulncheck` on every PR. Bumped `google.golang.org/protobuf` 1.32.0 → 1.36.11 to clear `GO-2024-2611`. Cleared dead-code findings (`backtest.closeTrade`, `replay.formatSessionID`, test-only `recordingPub.failOn`). (`afb7831`)
- ✅ **`internal/store/state_writer_test.go`** — offline lifecycle tests covering buffer-full backpressure, post-close-write rejection, `closeOnce` close-of-closed guard under concurrent Close, single-Run guard via `running.CompareAndSwap`, `derefF` nil-safety. No live pgxpool needed. (`91a5dab`)

**Backstop / continuous-load layer (post-2026-05-27, late session):**

- ✅ **Account lockout** (migration 0007) — `users.failed_login_count` + `users.locked_until`. 10 consecutive fails → 15min lock; even correct password refused during the lock window. New audit kinds `auth.login.locked_trip` + `auth.login.locked_out` at WARN. Defense-in-depth above the per-IP RateLimiter — covers distributed credential-stuffing rotating IPs against the same email. (`3065252`)
- ✅ **Nightly ws_stress CI** — `.github/workflows/nightly.yml` runs at 03:00 UTC + on-demand via `workflow_dispatch`. Three escalating tiers: 100c/30s smoke → 500c/60s soak → 1000c/60s target. Brings up `docker compose --profile demo`, fails the workflow if any tier can't sustain load, dumps `/metrics` + logs on failure. (`cde848e`)
- ✅ **Databento offline coverage** — `dbnFixedString` trim cases, `resolveSymbol` malformed-input rejection, `parseFutureSymbol` NQ→NDX path, `convertCmbp1` (OPRA consolidated quote) round-trip, `bootstrapDataset` guard clauses. Coverage on `internal/feed/databento` now 23% (live-stream paths still need OPRA). (`a7b8a78`)

**Final hardening pass (post-backstop):**

- ✅ **WebSocket inbound read limits** — `/ws/live` `SetReadLimit(4096)` + `/ws/replay` `SetReadLimit(1024)`. Inbound shapes are tiny; anything beyond kills the connection before it can pin memory. (`8d36519`)
- ✅ **`/ws/replay` origin default-deny** — replaced lingering `InsecureSkipVerify=true` with `OriginPatterns: cfg.API.CORSOrigins`, mirroring `/ws/live`. (`8d36519`)
- ✅ **Global HTTP body cap** — new `api.BodyLimit` middleware (1 MiB via `http.MaxBytesReader`) on every chi-mounted route. Existing per-handler `io.LimitReader` stays as tighter inner bound. (`8d36519`)
- ✅ **`/health/live` alias** — added next to `/health` so k8s livenessProbe + readinessProbe use canonical names. (`8d36519`)
- ✅ **Auth Prometheus metrics** — `flowgreeks_auth_{login,signup,refresh}_attempts_total{result=...}`, `…_logouts_total`, `…_account_lockouts_total`. Bounded cardinality. Pairs with slog audit log. (`8d36519`)
- ✅ **Auth Prometheus alert rules** — `AuthLoginFailureBurst` (warn), `AuthLockoutTripBurst` (page), `AuthRefreshReuseDetected` (page) in `deploy/prometheus/flowgreeks.rules.yml`. (`b8fff04`)
- ✅ **Security response headers + SECURITY.md** — `SecurityHeaders` middleware sets `nosniff` / `DENY-frame` / `no-referrer` / `default-src 'none'` CSP / `same-origin` CORP. HSTS gated on TLS via `r.TLS || X-Forwarded-Proto=https`. `SECURITY.md` documents reporting channel + 7-layer posture + auth model + audit + outbound SSRF guard + CI/nightly verification + production-config refusal list. (`f1fa3c1`)

**Reference documentation:**

- ✅ **`docs/reference/`** — 11 files (~2,700 lines) covering every subsystem in depth. Every diagram sourced from actual code with file:line citations. Numbered for recommended reading order: 00 system overview → 01 data pipeline → 02 auth → 03 math → 04 dealer model → 05 time machine → 06 alerts → 07 defense-in-depth → 08 deployment → 09 observability. README.md documents the citation contract + maintenance rules.

**Backend test surface:**

- All packages green under `go test -race ./...`: alerts, api, auth, backtest, bus, dealer (incl. e2e integration), e2e, feed, feed/databento, feed/synthetic, greeks, narrative, replay, store
- End-to-end pipeline test (`internal/dealer/integration_test.go`) drives synthetic SPX chain through full M2+M3, validates non-zero outputs from every component (DPI composite ~58, Charm zone classified, Flow Pulse non-zero)
- Benchmarks under target across the board: BS 105ns, Greeks-All 259ns, IV solver 1.03µs, GEX-Aggregate 5.2µs/200-strike, classifier 71ns, position Apply 49ns, basis-Update 156ns, all zero-alloc on hot path

**Live data status:**

- GLBX.MDP3 (futures) end-to-end verified: Databento → ingest → NATS → archive → Postgres rows
- OPRA.PILLAR (options) blocked: Databento auto-locked the account during prior debug session. Code path verified correct via Python reference cross-check; awaits unlock for live verification.

**Next concrete actions** (any order):

1. Wait for OPRA unlock → run ingest + compute live during US market hours → verify SPX/NDX option strike matrix populates and state flows
2. Once `dealer_state_1s` has a few sessions of real data, exercise `POST /api/backtest/run` against real signals
3. M5 frontend integration (`../web/`) — `/ws/live`, `/ws/replay/{id}`, `/api/snapshot`, `/api/simulate`, `/api/backtest/run`, `/api/alerts/rules` all wired and waiting
4. flowjob.id ↔ FlowGreeks API-key provisioning protocol — parent site mints + revokes via `apikey.Generate` (or equivalent in TS), INSERTs into shared `api_keys` table; flip `APIKEY_ENABLED=true` once provisioning is live
5. Stress test 1000 concurrent WS clients (deferred to launch readiness)

**Blockers:** Databento OPRA account lock (vendor-side, manual recovery).

---

## Decisions log

Append-only. Date · context · decision.

- **2026-05-25** — Backend language. Chose **Go 1.22+** over Rust. Rationale: solo-dev velocity, lower context cost when pairing with Claude, performance is sufficient for sub-100ms targets. Rust reserved for hot-path bottleneck functions only if profiler points there.
- **2026-05-25** — Storage. Chose **TimescaleDB + Redis**. SQL-compatible analytics, single-binary ops vs ClickHouse, 7GB/year capacity for SPX+NDX is trivial.
- **2026-05-25** — Message bus. Chose **NATS JetStream** over Kafka. Sub-ms latency, single binary, sufficient durability for our needs.
- **2026-05-25** — Microservices boundaries. **Process-level, not network-level.** Four binaries on one host communicating via NATS over loopback. Horizontal split deferred until needed.
- **2026-05-25** — Hosting recommendation. Bare-metal at Hetzner/OVH (€120-200/mo) over cloud. 3-5x cheaper, predictable performance for tick processing.
- **2026-05-25** — Tickers locked to **SPX + NDX only**. Explicitly NOT supporting RUT, equity options, futures options, crypto, FX. No 3,500-ticker bloat.
- **2026-05-25** — Frontend deferred until M5. Production frontend choice (SvelteKit vs Next.js) made at M5 kickoff.
- **2026-05-25** — **Desktop only**, no mobile by user mandate. UI assumes 1920×1080+, terminal-grade aesthetic.
- **2026-05-25** — Color palette locked: red `#E0183C/#A81030/#780A22/#500614` and teal `#063830/#0A6858/#18B09A/#40E0D0` on black `#000`.
- **2026-05-25** — **Spot/Futures view toggle.** All compute happens in spot space. Backend tracks live ES/NQ basis (EWMA-smoothed). Frontend applies `+ basis_smooth` shift to all displayed levels when user is in FUTURES view. Toggle is per-user pref (with optional per-symbol override). Spec'd in COMPUTE_MODEL.md §11. Backend work slated for M2, frontend work for M5.
- **2026-05-25** — **Timezone is a frontend concern.** Backend always emits UTC nanosecond timestamps. Frontend converts via `Intl.DateTimeFormat` with user-selected IANA TZ. Default to browser-detected. Common defaults: `America/New_York` (US traders), `Asia/Jakarta` (WIB, +7), `Europe/London`, `Asia/Singapore`, `Asia/Tokyo`. Slated for M5.
- **2026-05-25** — **Flow Pulse (HIRO-style oscillator, decomposed)** approved. Differentiator vs SpotGamma HIRO: 3-line decomposition (gamma / charm / vanna pulse) — show user WHICH greek drives the move. 0DTE-only. Spec'd in COMPUTE_MODEL.md §10. Backend slated M3, frontend M5.
- **2026-05-25** — **Color discipline rule:** mayoritas UI monochrome. Color (red/teal palette) hanya untuk hal yang carry semantic meaning (live values dengan direction, active state, critical alerts, key levels). Card titles, labels, axes, borders, navigation default = grayscale. User feedback "norak" untuk mockup awal yang colorful — koreksi diterapkan untuk semua mockup baru.
- **2026-05-25** — **Go 1.26.3 installed** via winget (latest stable, newer than 1.22 spec'd). `go.mod` declares `go 1.22` for compat — won't use 1.26-only features unless explicitly approved.
- **2026-05-25** — **Module path: `flowgreeks`** (local, no GitHub prefix). User will push manually later. `go mod` works fine without remote path.
- **2026-05-25** — **API_LISTEN_ADDR default `:8080`**. Smoke test ran on `:8089` to avoid conflicts but production default stays 8080.
- **2026-05-25** — **Databento Go SDK choice**: no official Go client exists. Selected `github.com/NimbleMarkets/dbn-go` (Apache-2.0, v0.9.1, active maintenance). Wrapped behind `internal/feed.Feed` interface for swappability.
- **2026-05-25** — **Tick wire format**: hand-rolled 90-byte fixed-layout binary encoder (LittleEndian) over NATS, NOT gob/JSON. Zero-alloc on hot path via sync.Pool. Decode is in same package.
- **2026-05-25** — **Archive writer uses COPY FROM** (pgx) not batched INSERT. Default batch 5000 / flush 1s / buffer 50k. Backpressure: drop on overflow with counter, never block hot path.
- **2026-05-25** — **`ticks` table allows NULL for option-specific columns** (expiry, strike, side) so the same hypertable holds futures rows. New `instrument_id BIGINT` column carries vendor instrument id for futures contract disambiguation. DATA_MODEL.md updated to match.
- **2026-05-25** — **Throughput + latency stress tests deferred from M1 to "before launch"** — they need real Databento key during US market hours and are not blocking M2 (Greeks engine) work.
- **2026-05-25** — **Live smoke test partial success.** GLBX.MDP3 (ES/NQ futures) live tick flow CONFIRMED end-to-end: dbn-go → ingest → NATS (TICKS stream 25k+ msgs) → archive writer → Postgres ticks table (14k+ rows). Compute service connected, subscribed, aggregator looping 30+ iterations. **OPRA.PILLAR delivered 0 options ticks** despite TCP connect + Subscribe success — root cause is **account live-entitlement gap**, NOT a code bug (historical OPRA works via REST). Live OPRA likely needs separate subscription on Databento dashboard.
- **2026-05-25** — **OPRA schema is `cmbp-1` not `mbp-1`** (consolidated MBP-1 across venues). Discovered during live smoke when Databento gateway reset connection on `mbp-1` subscribe. Added `feed.SchemaCMBP1`, `OnCmbp1` visitor method, `convertCmbp1` normalizer. Subscribe path now uses cmbp-1 for OPRA.PILLAR, mbp-1 for GLBX.MDP3.
- **2026-05-26** — **OPRA bootstrap implemented (the actual fix).** Reading reference Python implementation at `c:/Users/ollama/Documents/FLOWGREEKS/backend/app/ingestion/databento_live.py` revealed the missing piece: OPRA live gateway does NOT broadcast SymbolMappingMsg for parent subscriptions (unlike GLBX which does). Client must pre-fetch definition schema from Historical API to seed `instrument_id -> contract` map BEFORE Start. Without this every Cmbp1Msg is dropped because visitor's meta cache is empty. Added:
  - `internal/feed/databento/bootstrap.go` — fetches definition schema via `dbn_hist.GetRange` and decodes via `dbn.NewDbnScanner` over the byte stream, returns map of instrument_id to instrumentMeta
  - `Client.Subscribe` now calls `bootstrapDataset` for every dataset where `needsBootstrap()` returns true (currently OPRA.PILLAR only)
  - `visitor.OnInstrumentDefMsg` handles live definition records so new strikes listed mid-session join the registry
  - `cmd/ingest` subscriptions now include `definition` + `statistics` schemas for OPRA (per Python reference DEFAULT_SCHEMAS pattern)
  - `feed.SchemaDefinition` + `feed.SchemaStatistics` constants added
- **2026-05-26** — **Smoke test of bootstrap fix blocked: Databento auto-locked the account** (`auth_account_locked`, 403 across both REST and live gateway) due to repeated authentication attempts during the previous diagnostic sessions. Manual unlock required — operator must email Databento support or use account portal recovery flow. Code is correct per Python reference cross-check; verification deferred until account is reinstated.
- **2026-05-26** — **Post-M9 hardening tracks A–H landed.** A: WS replay control plane (`/ws/replay/{session_id}`). B: backtest engine core. C: alerts engine + REST + WS bridge. D: end-to-end pipeline integration test. E: auth scaffolding (JWT HS256 + bcrypt + middleware), gated by `JWT_SECRET` env. F: `dealer_state_1s` cold-path archive — migration 0004, batched COPY FROM writer, compute aggregator wiring. G: `POST /api/backtest/run` over the new archive, reusing `alerts.Rule` predicate triple so saved alerts are backtest-callable without redefinition. H: GitHub Actions test workflow (build+vet+test with race + tidy-drift guard, golangci-lint).
- **2026-05-26** — **integration_test future-expiry fix.** `greeks.TimeToExpiryYears` returns 0 once we're past 16:00 ET on the expiry date. The synthetic-SPX integration test was using "today" for the expiry, which made it deterministic only when run before the close — past 16:00 ET the IV solver never fired and `quotesSeen` stayed at 0. Switched the test to `tomorrowYYYYMMDD()` so it's wall-clock-independent and CI runs reliably.

---

## Open questions

- [ ] Hosting decision: Hetzner AX102 vs OVH equivalent vs other? (Decide before any production cutover)
- [ ] Domain name registered yet? (`flowgreeks.com`, `.io`, etc.)
- [ ] Discord vs other community platform for beta users (post-launch)

---

## Session resume protocol

When Claude Code starts a fresh session:

1. Read `CLAUDE.md` (project root)
2. Read this file (`docs/PROGRESS.md`)
3. If active milestone has unchecked items, work on the next unchecked one
4. If milestone complete, run a check: read its DoD in ROADMAP.md, verify, then advance to next milestone
5. After meaningful work, **update this file's "Current state" + relevant checkbox + decisions log**
6. Commit with message format: `<area>: <imperative summary>` (e.g. `ingest: add OPRA SBE decoder skeleton`)
