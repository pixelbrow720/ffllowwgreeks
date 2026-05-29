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

**Last session:** 2026-05-29 — Python bridge load verified + smoke pipeline replay→compute→dealer_state_1s end-to-end. 211M ticks loaded across 9 trading days (2026-02-02 → 2026-02-12), 27 TimescaleDB chunks, 2.4 GB. Two P0 bugs surfaced + fixed during smoke: (1) **`bus.Publisher.subjectFor` did not handle `TickTypeOI`** — every OI tick was rejected silently, blocking `PositionTracker.SeedFromOI` and therefore the entire downstream state pipeline. Added `SubjectTickOI` + case in `subjectFor`. (2) **`PositionTracker` had no concurrency guard** despite documented "single-threaded by design" — `cmd/compute` actually drives writers (NATS callback) and readers (aggregator loop) on separate goroutines, causing a fatal `concurrent map iteration and map write` panic the moment positions accumulated. Added `sync.RWMutex` guarding all map access. After the fixes, smoke run materialized 250 rows in `dealer_state_1s` at 1 Hz cadence — **plumbing proven end-to-end** for the first time on real Databento ticks. All math fields (spot, NetGEX, DPI, etc.) currently 0 in the smoke output: see two deferred bugs in the next-actions list. `go build/vet/test` all green post-fix (17 packages).

**Previous session:** 2026-05-28 — Wide-range Databento pull (9 full + 1 partial day archived under `data/databento/`, account locked twice in the process). Math validation extended 36 → 108 parity tests, 321,108 strikes, 100% PASS at p99 < 1e-4 vs scipy. Multi-agent integration audit landed (`docs/INTEGRATION_PLAN.md` + integration/design/methodology subfolders). P0 contract fixes: openapi DELETE alerts under apiKeyAuth, WS endpoints behind `apikey.Middleware`. P1 security: per-IP rate limit ahead of auth via new `apikey.IPMiddleware` in `cmd/api/main.go`. Replayer pipeline (`cmd/replay_dbn`) blocked — dbn-go v0.9.1 can't decode DBN v1 InstrumentDef; Python bridge `scripts/dbn_to_postgres.py` chosen as workaround, running in background at session end. See decisions log entry for the full session breakdown.

**Previous session:** 2026-05-27 — Auth pivot landed. FlowGreeks repositioned as an add-on inside flowjob.id; the parent site owns user accounts + billing. Removed `internal/auth/` (~1500 lines: signup/login/refresh/lockout/JWT) + migrations 0003/0005/0006/0007. Added `internal/apikey/` package with `Generate`/`HashSecret`/`Middleware`/`RateLimiter`/`AuditSink` + migration 0008 (`api_keys` table). Defense-in-depth dropped from 7 layers to 5 — no more account lockout / refresh rotation / per-user rate limit since there are no user accounts. New per-key rate limit reads `rate_limit_rps` + `rate_burst` straight off the `api_keys` row so flowjob.id can hot-swap tier budgets without redeploying this binary.

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

1. **Replay reader futures-contract reconstruction** (deferred from 2026-05-29 smoke). `internal/replay/reader.go:91` scans rows from `ticks` but the schema has no `futures_contract` column, so reconstructed `feed.Tick` for futures has empty `FuturesContract[12]`. `bus.Publisher.subjectFor` rejects with `bus: publish: future tick missing FuturesContract` (528,913/586,418 rejects in this session's smoke). Fix: derive contract symbol from `(symbol, ts)` using CME conventions — front month is the next quarterly H/M/U/Z after `ts`. SPX→ES, NDX→NQ. With this fix, basis tracker gets futures, spot estimate becomes non-zero, and downstream math comes alive.
2. **`cmd/compute` aggregator wall-clock vs event-time mismatch** (deferred from 2026-05-29 smoke). `runAggregator` uses `time.Now()` and `fillGreeks` uses `time.Now()` for `TimeToExpiryYears`. When replaying historical ticks (Feb 2026), `time.Now()` is months past the chain expiries → `years <= 0` → all Greeks zero → NetGEX/DPI/Pulse all zero. Live ingest is fine. Replay needs a "virtual now" wired into the pipeline: replay binary owns the clock, publishes a clock signal or threads event-time through the aggregator. Cleanest fix: aggregator subscribes to a `clock.<sym>` heartbeat the replay runner emits, falling back to `time.Now()` if no heartbeat in N seconds.
3. **`cmd/compute` NATS payload limit** (deferred from 2026-05-29 smoke). `state.spx.gex` JSON exceeds default 1 MiB max-payload once strike count grows (657 strikes IV cache @ smoke peak). Spurious `nats: maximum payload exceeded` warns once/sec. Options: tighten to top-N strikes by |dealer_pos|, raise NATS `max_payload`, or split per-strike to a parallel subject. Doesn't block state writer (which writes a flat row, not the full JSON).
4. Wait for OPRA unlock → run live ingest + compute during US market hours → verify SPX/NDX option strike matrix populates and state flows.
5. Once `dealer_state_1s` has a few sessions of real data (post-actions 1+2), exercise `POST /api/backtest/run` against real signals.
6. M5 frontend integration (`../web/`) — `/ws/live`, `/ws/replay/{id}`, `/api/snapshot`, `/api/simulate`, `/api/backtest/run`, `/api/alerts/rules` all wired and waiting.
7. flowjob.id ↔ FlowGreeks API-key provisioning protocol — parent site mints + revokes via `apikey.Generate` (or equivalent in TS), INSERTs into shared `api_keys` table; flip `APIKEY_ENABLED=true` once provisioning is live.
8. Stress test 1000 concurrent WS clients (deferred to launch readiness).

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
- **2026-05-28** — **Wide-range Databento pull, math validation extension, integration audit, P0/P1 fixes.** Multi-track session. Captured here for cross-session continuity.
  - **Databento 9-day archive landed.** Plan C wide-range pull (target: 78 trading days). Got 9 full days + 1 partial under `data/databento/` before Databento auto-locked the account twice. Root cause: pull script designed as a 780-call loop instead of one wide call per schema. Lesson recorded in personal memory; vendor support contact pending. Commit: `fbd17aa`.
  - **Math validation 36 → 108 parity tests.** 9 days × 2 roots (SPX/NDX) × 6 snapshots, 321,108 strikes covered, 100% PASS at p99 < 1e-4 vs scipy reference. 11/11 BS invariants pass under hypothesis n=200. 19 smile gallery PNGs generated. Docs added: `docs/methodology/parity-9day.md`, `greeks-parity.md`, `property-tests.md`, `smile-gallery.md`. No backend code changes — offline cross-validation only.
  - **Integration audit (multi-agent).** Produced `docs/INTEGRATION_PLAN.md` (15 items, 4 P0), `docs/integration/{contract-drift,websocket-contract,type-mapping}.md`, `docs/design/dashboard-redesign-proposal.md`, and `docs/methodology/research-paper.md` (1036 lines, ~6.5k words). All in workspace `docs/`, not `backend/docs/`.
  - **P0 fixes applied.** C1: openapi `DELETE /api/alerts/rules/{id}` declared under apiKeyAuth (was undocumented public). C2: `/ws/live` and `/ws/replay/{id}` now mounted behind `apikey.Middleware`. C4: tailwind accent tokens added (frontend, not backend).
  - **P1 security: per-IP rate limit at root, before auth.** New `apikey.IPMiddleware` token bucket mounted in `cmd/api/main.go` ahead of the API-key middleware. Closes the credential-stuffing window before the per-key bucket can fire. Lifts the defense-in-depth posture from 5 → 5+ (per-IP layer is now ahead of per-key).
  - **Replayer pipeline blocked.** `cmd/replay_dbn` smoke test failed: dbn-go v0.9.1 doesn't support the DBN v1 InstrumentDef format Databento served us. Workaround chosen: Python bridge `scripts/dbn_to_postgres.py` loads DBN files directly into the `ticks` table via the `databento` Python SDK. Currently running in background (day 2/9 as of session end, ~50 min ETA). Day 1 needs verification — a prior agent died mid-load and may have left a partial.
  - **Docs cleanup audit.** Trimmed workspace `docs/README.md`, `docs/ROADMAP.md`, `docs/PROGRESS.md`. Stripped `design-reference/` references from 7 files (folder doesn't exist in this consolidated workspace). 3 file deletions blocked by rm permission denial — see `docs/_cleanup-audit.md`. Commit: `fbd17aa`.

- **2026-05-29** — **Python bridge load verified + first end-to-end smoke replay→compute→dealer_state_1s.** Two P0 plumbing bugs surfaced and fixed; two deferred bugs documented for next session.
  - **Bridge load complete.** `dbn_to_postgres.py` had finished prior to this session start (no python process running, last `_run3.out` line at 2026-05-28T12:44:11Z = Databento account-locked, NQ.FUT 02-13 only). Postgres `ticks` hypertable: 211,395,854 rows, 27 chunks, 2.4 GB total, range 2026-02-02 → 2026-02-12. Per-day SPX+NDX × {Quote, Trade, OI} all populated except: (a) day 02-10 NDX quotes partial (3.4M vs ~10M expected — NQ.FUT mbp-1 ZIP only 72MB vs ~150MB normal; ledger shows mid-pull HTTP 000 retry, the retry succeeded but covered only the post-failure window), (b) day 02-12 NDX OI missing entirely (statistics file 118KB vs ~1.5MB normal — partial pull from 504 retry storm), (c) day 02-13 absent (NQ.FUT mbp-1 hit `auth_account_locked` 403 mid-pull, bridge never started for this date). Day-level coverage is otherwise complete.
  - **Smoke decision tree → Outcome B (load lengkap).** 7/9 days fully populated, gaps minor and OPRA-blocked for repair. Proceeded to Step 3.
  - **Smoke 1: 60-second SPX window** (2026-02-12 19:00–19:01 UTC). Replay published 2,932 / consumed 39,460 (8% success rate). Aggregator never wrote state. Root cause **bug #1 — TickTypeOI rejected**: `bus.Publisher.subjectFor` (`internal/bus/publisher.go:194-199`) only handled Quote + Trade for options; OI ticks raised "unsupported tick type". With OI rejected, `PositionTracker.SeedFromOI` never ran, `Snapshot()` returned empty, `compute/main.go:420-423` short-circuited the aggregator, no state row was emitted.
  - **Smoke 2: OI-inclusive 3-hour window** (11:29–14:35 UTC). Same rejection rate (publisher bug not yet fixed). Confirmed root cause via tick-type breakdown.
  - **Fix #1 applied.** Added `SubjectTickOI` (`internal/bus/subjects.go`) and a `case feed.TickTypeOI` branch to `subjectFor` (`internal/bus/publisher.go`). `internal/bus/publisher_test.go::TestSubjectFor/option_oi_unsupported` updated to assert the new contract. `go test ./internal/bus/...` green.
  - **Smoke 3: same window, post-fix.** Published rose to 57,505 (= 31,267 quote+trade + 26,238 OI). State writer flushed 4 rows... then **panic: `concurrent map iteration and map write` in `dealer.PositionTracker.Snapshot`** (`internal/dealer/position.go:86`). Root cause **bug #2 — undocumented races**: `PositionTracker` doc said "single-threaded by design" but `cmd/compute` actually runs writers (NATS callback `handleTick`) and readers (aggregator `runAggregator`) on different goroutines. The single-threaded invariant was always false; live OPRA never ran long enough to surface it.
  - **Fix #2 applied.** Wrapped all `pos` map access in `internal/dealer/position.go` with a `sync.RWMutex`. Writers (`SeedFromOI`, `Apply`, `PruneExpired`) take the write lock; readers (`Get`, `Snapshot`) take the read lock. Updated the type's concurrency contract comment. `go test ./internal/dealer/... -count=1` green; `go test ./...` 17/17 packages green.
  - **Smoke 4: same window, both fixes applied.** Replay: 57,505 published, 528,913 futures rejected (deferred bug — see next-actions). Compute: stable, no panic, state writer flushed continuously (5 rows / 5s = exactly 1 Hz × 1 symbol). `dealer_state_1s` end state: **250 rows, 1 symbol, ts span 4m09s** at 1 Hz cadence, monotonically advancing. **Pipeline plumbing proven end-to-end on real Databento data for the first time.**
  - **Math values are zero in the smoke output.** Two deferred bugs prevent meaningful numeric validation: (i) replay reader cannot reconstruct `Tick.FuturesContract[12]` (the `ticks` schema has no such column) → publisher rejects all futures → basis tracker stays empty → `pipelineSpot` falls back to a stale literal, but the aggregator overwrites with `basis.Snapshot()` so it ends up 0; (ii) `runAggregator` and `fillGreeks` use `time.Now()` for time-to-expiry, but expiries in the historical archive are months in the past relative to wall clock, so every TTE is 0 and Greeks collapse to zero. Both fixes need design choices — captured in next-actions #1 and #2.
  - **Other observations.** `state.spx.gex` JSON publish exceeds NATS default 1 MiB max payload once strike cache grows past ~600 strikes — spurious warning loop, doesn't block the state archive writer (which writes a small flat row).
  - **Build state.** `go vet ./...` clean. `go test ./... -count=1` 17/17 packages green. No dependencies bumped this session.

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
