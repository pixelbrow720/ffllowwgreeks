# Deep Review — 2026-05-26

> Source-of-truth record of the production-readiness review pass. Captures
> findings, file:line citations, severity, and the commit that resolved
> each item so future sessions can verify (or reopen) any decision.
>
> **Note (post-2026-05-27 auth pivot):** Findings #1, #2, #5, H1, H2, H3,
> B1, F5 below reference the old `internal/auth/` package — signup, login,
> refresh tokens, account lockout, JWT verifier, tier gating. **That entire
> surface was removed when FlowGreeks pivoted to API-key auth as a
> flowjob.id add-on (commit `3e5b0ec`).** The fixes were real, the code
> they fixed no longer exists. The current auth surface is `internal/apikey/`;
> see [docs/reference/02-auth.md](reference/02-auth.md). This file is kept
> as historical audit trail — don't read it as describing current code.

## How this was done

Four reviewer subagents ran in parallel against four package clusters,
each instructed to surface correctness / concurrency / hot-path /
ergonomics gaps with file:line citations. The reviewers had no prior
session context — they read the code cold. ~30 findings total. Every
finding either landed a fix or is explicitly tracked under "Deferred /
won't-fix" below.

**Reviewers:**

1. **Cold path** — `internal/feed/`, `internal/feed/databento/`,
   `internal/feed/synthetic/`, `internal/bus/`, `internal/store/`
2. **Math core** — `internal/greeks/`, `internal/dealer/`
3. **Time machine + binaries** — `internal/replay/`,
   `internal/backtest/`, `internal/narrative/`, `cmd/{api,ingest,compute,replay}`
4. **HTTP / WS / auth surface** — `internal/api/`, `internal/auth/`,
   `internal/alerts/`

## P0 — Security (5 fixes, commit `5ec2a0c`)

| # | Finding | Location | Status |
|---|---|---|---|
| 1 | JWT verifier accepted any `*SigningMethodHMAC` → alg-confusion downgrade window | `internal/auth/jwt.go:74` | ✅ Pinned to HS256 only via `jwt.WithValidMethods`; `jwt.WithIssuer("flowgreeks")` enforced |
| 2 | Refresh rotation Lookup → IsActive → Revoke was non-atomic; two concurrent calls with the same token both passed | `internal/auth/handlers.go:223-242` | ✅ New `ConsumeForRotation` does single `UPDATE … RETURNING` CAS; loser sees `ErrRefreshTokenInactive` |
| 3 | `/ws/live` set `InsecureSkipVerify=true` when `Origins` empty — any cross-origin upgrade allowed | `internal/api/ws.go:50` | ✅ Default-deny; empty Origins now means same-origin only via coder/websocket's built-in check |
| 4 | Webhook delivery POSTed to any URL; SSRF against cloud metadata (169.254.169.254) / LAN possible | `internal/alerts/delivery.go:37` | ✅ `validateWebhookURL` blocks loopback / RFC 1918 / link-local / CGNAT / IPv6 ULA at sink construction |
| 5 | `/api/alerts/*` trusted `X-User-ID` header — any caller could read/write any user's rules | `internal/api/alerts.go:51,95,121` | ✅ `callerUserID(r)` prefers JWT claims via `auth.FromContext`; header is dev-mode escape hatch only |

## P0 — Data loss (3 fixes, commit `b3748e7`)

| # | Finding | Location | Status |
|---|---|---|---|
| 6 | ArchiveWriter shutdown flush used the already-cancelled ctx → `pgx.CopyFrom` returned `ctx.Err` immediately, batch logged + dropped | `internal/store/archive.go:179` | ✅ Switches to fresh 10s `Background` deadline when `ctx.Err() != nil` |
| 7 | StateWriter `wg.Add` inside `Run` raced `Close` → could pool-close while `Run` was still draining | `internal/store/state_writer.go:150,194-200` | ✅ Mirrored ArchiveWriter's `closeCh + done + closeOnce + atomic.Bool running`; `Close` self-drains when Run never started |
| 8 | Synthetic `Stop()` closed `g.out` while producers were mid-`select`; race with send-on-closed-chan panic. Also `g.rng` shared across 5 goroutines, not goroutine-safe | `internal/feed/synthetic/generator.go:117-126,176,226,234,246,259,289,323` | ✅ `Stop` now closes `g.stop`, `wg.Wait`s producers, THEN closes `g.out`. Added `rngMu` + `randIntn / randNorm / randFloat` helpers |

## P1 — Concurrency races (3 fixes, commits `d95d599`, `8ba94ee`)

| # | Finding | Location | Status |
|---|---|---|---|
| 9 | `Subscriber.dropped` incremented under broker RLock, read with no sync from `Dropped()` | `internal/api/state.go:139,188` | ✅ `atomic.Uint64` |
| 10 | `Subscriber.filter` mutated by WS read loop while `Broker.Publish` probed via `matches()` — concurrent map RW | `internal/api/state.go:147-159, ws.go:152-171` | ✅ Per-subscriber `filterMu sync.RWMutex` + `snapshotFilter()` for slow-iter callers |
| 11 | `Session.cancel` raced — `Stop()` could read nil before `Run()` published the func | `internal/replay/session.go:181-186,204` | ✅ `cancelMu + cancel` field + `doneOnce sync.Once` gating `close(s.done)`; Run re-checks `stopped` after publishing cancel |

## P1 — Math / correctness (5 fixes, commit `864f330`)

| # | Finding | Location | Status |
|---|---|---|---|
| 12 | `time.LoadLocation("America/New_York")` called per tick → dominant cost on hosts without memory-resident tzdata | `internal/greeks/types.go:98` | ✅ Cached at package init in `nyLoc`; falls back to UTC when tzdata missing |
| 13 | Simulator `NetPressure = ForcedNotional + CharmAid` inflated magnitude (both share `-spot×Δ` sign convention) | `internal/dealer/simulator.go:152` | ✅ Subtracts now (matches doc comment "reduces magnitude") |
| 14 | Classifier cap-hit reset wiped ALL last-trade history — every other strike's tick-test fallback regressed to UNKNOWN until it traded again | `internal/dealer/classifier.go:79-82` | ✅ Two-generation `curr/prev` maps; rotation preserves recent prices |
| 15 | Backtest `Sharpe = mean/stddev × sqrt(252)` meaningless for 0DTE strategies — no fixed trades-per-day rate | `internal/backtest/engine.go:206` | ✅ `annualisationFactor(n, from, to)` derives `sqrt(trades/year)` from window; falls back to 1 when too narrow |
| 16 | Backtest open-trade-at-stream-end set `ExitSpot=entrySpot, ReturnPct=0` → falsified P&L on truncated streams | `internal/backtest/engine.go:113-118,167-174` | ✅ Tracks `lastSpot`; uses it as exit when present |
| 17 | Backtest Sortino short-circuited to 0 when mean was exactly 0 — wrong by definition | `internal/backtest/engine.go:237-240` | ✅ Returns `mean / downsideDev` whenever `downsideDev > 0` |

## P2 — Polish (4 fixes, commit `9793e43`)

| # | Finding | Location | Status |
|---|---|---|---|
| 18 | `alerts.sortRulesByID` was O(n²) insertion sort | `internal/alerts/engine.go:102-112` | ✅ `sort.Slice` |
| 19 | `time.Unix(0, 0).IsZero()` returns FALSE (epoch is 1970, not Go zero) → `TsNs=0` snapshots evaluated cooldowns against 1970 | `internal/alerts/engine.go:118-121` | ✅ Check `s.TsNs == 0` directly before constructing time |
| 20 | `backtest.alertsRuleMatches` numeric predicates against NaN silently returned false (NaN > x is false) | `internal/backtest/predicates.go:18-32` | ✅ Explicit `math.IsNaN` guard returns false (still missing, but visible-by-design) |
| 21 | `auth.isUniqueViolation` matched on string `"23505" / "duplicate key"` — broke under locale-translated server builds | `internal/auth/store.go:114-120` | ✅ `pgconn.PgError` + `pgerrcode.UniqueViolation` |

## Refactor (commit `b8a12cc`)

- Three pgxpools (auth + replay + backtest) consolidated to one shared pool owned by `cmd/api/main`. Default `MaxConns=4×CPU` × 3 was 3× wasted budget on the same database; pool tuning per-binary now lives in `PostgresConfig`.

## Deferred / won't-fix

| # | Finding | Status |
|---|---|---|
| 22 | Databento `client.go` error channel mixes diagnostic + fatal errors; bootstrap holds `c.mu` across HTTP call | Deferred — not blocking until OPRA unlock; refactor when revisiting live verification |
| 23 | `scripts/synth_state` chain doesn't recentre on long runs | ✅ Fixed in `afb7831` (`strikeFor` anchors on `g.Spot()`) |
| 24 | Greeks deep-OTM IV solver "no bracket" for very deep-OTM 0DTE | ✅ Fixed in `afb7831` (auto-widen bracket once when residuals share sign) |
| 25 | `dealer.Aggregate` uses `sort.Slice` per second (~400 strikes) — closure alloc per call | Deferred — profiler hasn't flagged it |
| 26 | `cmd/compute` aggregator clones `flow5min` + `pinFlow` maps every tick | Deferred — measured, not bottleneck |
| 27 | Narrative engine doesn't fire on pin-probability change at the same strike | Deferred — intentional suppression for now |
| 28 | Coverage gaps (databento 14.3%, store 13.3%) | Partially fixed in `91a5dab` (offline `StateWriter` lifecycle tests). Live-side coverage waits on OPRA |
| 29 | `replay.Session` error subscriber goroutine has no return signal | Deferred — tooling-noise only, harmless |
| 30 | Greeks API ergonomics — `BSInputs` struct vs positional args | Deferred — not worth churn pre-launch |

## Hardening pass (post-2026-05-26)

User-driven follow-up after the deep review. Closes structural gaps the review left open. Goal: move from "structurally production-ready" to "production-proven" before frontend integration.

| # | Finding | Status |
|---|---|---|
| H1 | Refresh tokens lacked reuse detection — leaked + legitimate tokens both succeeded | ✅ `4100e7f` — `family_id` (migration 0006) + `RevokeFamily` on inactive-token replay. Legitimate user is force-logged-out on a leak (canonical OAuth2 defense). Tests: reuse-revokes-family, distinct-logins-stay-isolated |
| H2 | No audit log: login fail/ok, signup, refresh, rule mutations were silent | ✅ `4100e7f`, `3b1358a` — `auth.AuditEvent` + `auth.AuditSink` + slog backend. `refresh.reuse_detected` lifted to WARN. Same sink wired into `AlertHandlers` |
| H3 | Per-user rate limit only on `/auth/*` — `/api/{simulate,backtest,alerts}` exposed to single-account DoS | ✅ `3b1358a` — `RateLimiter.MiddlewareKeyed` + `UserKeyOrIP`; 60/min, burst 30 |
| H4 | No staticcheck / govulncheck in CI | ✅ `afb7831` — new `security` CI job; protobuf 1.32→1.36.11 (clears GO-2024-2611) |
| H5 | Store coverage near-zero | ✅ `91a5dab` — offline lifecycle tests for `StateWriter` (buffer-full, post-close-write reject, closeOnce guard, single-Run guard, `derefF` nil-safety) |

Still open (deferred to post-Databento-unlock or post-launch):

- ~~`ws_stress` wired into a nightly CI gate~~ — ✅ landed in `cde848e` (`.github/workflows/nightly.yml`, three escalating tiers up to 1000c/60s).
- Race detector locally — no `gcc` on Windows host. CI's `test.yml` `build` job runs `go test -race ./...` on every PR.
- External pentest — recommended pre-public-launch.
- DPI / Charm / Pin calibration vs realised 0DTE flow — needs OPRA unlock for ground truth.

## Backstop layer (post-2026-05-27)

Defense-in-depth on top of the per-IP rate limiter — for distributed credential-stuffing that rotates IPs.

| # | Finding | Status |
|---|---|---|
| B1 | Per-IP rate limit alone doesn't stop a botnet rotating IPs across attempts on the same email | ✅ `3065252` — account lockout (migration 0007). 10 consecutive failures → 15min lock; `429 Retry-After`; even correct password refused during lock window. Audit `login.locked_trip` + `login.locked_out` at WARN. |
| B2 | Databento client coverage at ~14% | ✅ `a7b8a78` — offline tests for `dbnFixedString`, `resolveSymbol` malformed inputs, `parseFutureSymbol` NQ→NDX, `convertCmbp1`, `bootstrapDataset` guard clauses. Coverage now 23%. Live-stream paths still need OPRA. |

## Final hardening pass (post-backstop)

Last sweep before frontend integration. Caps every remaining "could a hostile client wedge this" surface with a hard limit + a metric.

| # | Finding | Status |
|---|---|---|
| F1 | `/ws/live` and `/ws/replay` accepted unbounded inbound frames — a hostile client could pin server memory by sending a huge JSON payload | ✅ `8d36519` — `SetReadLimit(4096)` on `/ws/live`, `SetReadLimit(1024)` on `/ws/replay`. Connection killed on overflow. |
| F2 | `/ws/replay` still set `InsecureSkipVerify=true` after `/ws/live` had moved to default-deny | ✅ `8d36519` — `OriginPatterns: cfg.API.CORSOrigins`, mirrors `/ws/live` posture. |
| F3 | No global HTTP body cap — routes that forgot per-handler `LimitReader` had no fallback | ✅ `8d36519` — `api.BodyLimit` middleware (1 MiB via `http.MaxBytesReader`) on every chi-mounted route. |
| F4 | `/health` only — no canonical `/health/live` for k8s livenessProbe | ✅ `8d36519` — `/health/live` alias added. |
| F5 | Auth surface had structured audit log but no Prometheus counters — SIEM rules had to grep slog | ✅ `8d36519` — `flowgreeks_auth_{login,signup,refresh}_attempts_total{result=...}`, `…_logouts_total`, `…_account_lockouts_total`. Bounded cardinality. |
| F6 | Auth metrics existed but no alert rules paged on suspicious patterns | ✅ `b8fff04` — `AuthLoginFailureBurst` (warn), `AuthLockoutTripBurst` (page), `AuthRefreshReuseDetected` (page). |
| F7 | No security response headers (HSTS / CSP / nosniff / DENY-frame) — browser-side attack surface unprotected | ✅ `f1fa3c1` — `api.SecurityHeaders` middleware. HSTS gated on TLS so local dev still works. |
| F8 | No `SECURITY.md` reporting channel | ✅ `f1fa3c1` — published with six-layer defense posture, auth model, audit + telemetry plumbing, outbound SSRF guard, CI/nightly verification, production-config refusal list. |

## Files reviewed

- `internal/feed/{symbol.go, types.go}` and entire `feed/databento/` + `feed/synthetic/`
- `internal/bus/{publisher.go, subjects.go}`
- `internal/store/{archive.go, state_writer.go}`
- `internal/greeks/{normal.go, pricing.go, solver.go, types.go}`
- `internal/dealer/{basis.go, charm_clock.go, classifier.go, dpi.go, flow_pulse.go, gex.go, pin.go, position.go, quote_cache.go, simulator.go}`
- `internal/replay/{manager.go, session.go, reader.go, runner.go, ws.go, metrics.go}`
- `internal/backtest/{engine.go, predicates.go}`
- `internal/narrative/engine.go`
- `internal/api/{state.go, ws.go, rest.go, simulate.go, alerts.go, backtest.go, metrics.go}`
- `internal/auth/{handlers.go, jwt.go, store.go, refresh.go, refresh_memory.go, ratelimit.go, middleware.go, types.go}`
- `internal/alerts/{engine.go, delivery.go, types.go, metrics.go}`
- `cmd/{api,ingest,compute,replay}/main.go`

## Re-running this review

The pattern is reusable. Spawn four `Explore` agents in parallel against the same package clusters with the prompts in this commit's message. Look for new file:line citations that aren't in the table above.

---

**Last updated:** 2026-05-27 (final hardening pass + browser hardening)
**Original review:** 2026-05-26 (deep-review-pass-1)
**Hardening pass:** 2026-05-27
**Backstop layer:** 2026-05-27
**Total findings:** 30 deep-review + 5 hardening + 2 backstop + 8 final (33 fixed, 2 partially, 10 deferred / won't-fix)
**Commits:** `5ec2a0c`, `b3748e7`, `d95d599`, `8ba94ee`, `864f330`, `b8a12cc`, `9793e43`, `4100e7f`, `3b1358a`, `afb7831`, `91a5dab`, `3065252`, `cde848e`, `a7b8a78`, `8d36519`, `b8fff04`, `f1fa3c1`
