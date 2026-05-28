# Post-pivot security audit
Date: 2026-05-28
Scope: internal/apikey, internal/api, cmd/api, migration 0008
Excluded: items in REVIEW.md / SECURITY.md
Reviewer: Claude (read-only audit)

## Summary
- findings: 9 total (P0=0, P1=1, P2=3, P3=5)
- subsystems explicitly cleared: apikey/middleware.go SQL surface, apikey/store.go parameterization, TrustAwareRealIP XFF default-deny, Bearer/X-API-Key parser, BodyLimit + WS SetReadLimit coverage, migration 0008 collision guard

## Findings

### F1 [P1]: Bad-key + public-endpoint floods bypass the rate limiter
**Location**: backend/cmd/api/main.go:144-160 (router wiring), backend/internal/apikey/middleware.go:38-87 (short-circuit on auth fail), backend/internal/api/rest.go:30-33 (`MountPublic`)
**Evidence**: protected router uses `apiKeyMW.Handler` BEFORE `apiKeyLimiter.Middleware` — auth failure returns 401 before the limiter ever runs. `MountPublic` (`/api/snapshot/{symbol}`, `/api/levels/{symbol}`) is wired on the root router with no rate limit at all.
**Attack vector**: Anonymous attacker hammers `/api/simulate/spx` with random secrets → every request consumes a DB `LookupByHash` (2s timeout) and a pgx pool slot, with no per-IP throttle. Same for `/api/snapshot/spx`. Audit alerts (`APIKeyAuthFailureBurst`) page on the pattern but do not stop the load. Pool starvation will tail-latency every legitimate request.
**Fix**: add a per-IP `httprate` (or repurposed `apikey.RateLimiter` keyed on `ip:` prefix) at root-router scope, BEFORE apikey middleware, with a low ceiling (e.g. 30 rps / 60 burst per IP). Keep the per-key limiter inside `protected` as the authenticated-tier cap.

### F2 [P2]: `/ws/live` subscribe-filter map grows unbounded across messages
**Location**: backend/internal/api/ws.go:161-189 (`applyFilter`), 56 (`maxInboundMessageBytes` is per-frame only)
**Evidence**: `StateKind(strings.ToLower(k))` accepts any string, and `applyFilter` only ever deletes on `unsubscribe`. `SetReadLimit(4096)` caps a single frame, not cumulative growth. Repeated `subscribe` messages with fresh kinds keep extending `sub.filter.Kinds` for the connection's lifetime.
**Attack vector**: 1 client sends 4 KiB subscribe frames with ~400 unique fake kinds each, repeated indefinitely. Per-connection map grows ~100 B × N entries. 1000 concurrent attackers × 40 K entries each = multi-GB resident.
**Fix**: cap map sizes inside `applyFilter` (e.g. ≤16 symbols and ≤8 kinds), and reject (or drop) entries that don't match the closed set `{gex, narrative, alert}`. Same for `Symbols` after `feed.ParseSymbol` whitelisting (already bounded — only 2 valid).

### F3 [P2]: Per-key rate-limiter `IP` fallback is dead code; misleads readers
**Location**: backend/internal/apikey/ratelimit.go:132-145 (`bucketParams`), backend/cmd/api/main.go:144-157 (mw order)
**Evidence**: `bucketParams` returns an `ip:` key when `apikey.FromContext` returns `ok=false`, but in the wired chain the limiter only runs AFTER `apikey.Middleware` succeeds — so context always carries a key. The `ip:`/anonymous branch is unreachable in production.
**Attack vector**: not directly exploitable, but anyone reading the package will reasonably believe anonymous traffic is throttled at 1 rps / 30 burst. It is not. This compounds F1.
**Fix**: either move the limiter ahead of the apikey middleware (with a `WhenAnonymousUseIP` flag) or delete the unreachable branch and document that the limiter is auth-only.

### F4 [P2]: `backtest.run` loads up to 31 days of `dealer_state_1s` rows into a single slice
**Location**: backend/internal/api/backtest.go:152-156, backend/internal/store/QueryStates (not read here)
**Evidence**: Body is capped at 64 KiB and the date range is capped at 31 days, but no row-count guard. 31 days × 24 h × 3600 s ≈ 2.68 M rows per symbol in `dealer_state_1s`. Slice is allocated up-front by `QueryStates` and held for the entire 30 s budget.
**Attack vector**: an authed caller (or someone with a leaked test key) issues 5–10 concurrent `/api/backtest/run` calls with the maximum 31 d range. Each request resident-set jumps by hundreds of MB; per-key rate limit (default 30 burst) doesn't help because the cost is per-request, not per-rps.
**Fix**: stream rows from Postgres via `pgx.Rows` directly into `backtest.Run`'s `Snapshot` channel instead of materialising the slice first. Add a hard row-count cap (e.g. 200 K) and 413 above it.

### F5 [P3]: `TouchLastUsed` fans out unbounded goroutines
**Location**: backend/internal/apikey/middleware.go:77-83
**Evidence**: `go func(id int64) { ... m.Store.TouchLastUsed(...) }(key.ID)` — no semaphore, no `sync/singleflight`. The 1-minute coalescing in `shouldTouch` (store.go:118-123) bounds writes per *key*, not concurrent goroutines.
**Attack vector**: a thundering herd at minute boundary across N active keys spawns N concurrent UPDATE goroutines. With 10 K active keys this is fine; if N grows to 100 K with a per-row UPDATE acquiring a row lock, contention rises.
**Fix**: use `golang.org/x/sync/singleflight` keyed on `key.ID`, or a buffered worker pool of e.g. 16 goroutines draining a touch channel.

### F6 [P3]: Idle-bucket eviction grants a full fresh burst
**Location**: backend/internal/apikey/ratelimit.go:65-68, 155-172 (janitor TTL = 1 h)
**Evidence**: when a bucket is evicted by the janitor, the next `Allow` re-creates it with `tokens = burst`. A client that sleeps 1 h, bursts the full quota, sleeps again, repeats — sustains burst×24/day instead of `rate × 86400`.
**Attack vector**: low-and-slow attacker shaped to dodge the burst sleep window can amplify their per-day allowance. Real-world impact small (burst is tier-bound) but worth bounding.
**Fix**: persist `lastSeen` across eviction (e.g. delete only state, keep a tombstone with `tokens=0`) or compute initial tokens as `min(burst, elapsed_since_creation × rate)`.

### F7 [P3]: NATS-sourced JSON is trusted by `simulate` decoder
**Location**: backend/internal/api/simulate.go:85-102, backend/internal/api/state.go:237-257 (`SubscribeNATS`)
**Evidence**: `SubscribeNATS` ignores `json.Unmarshal` errors and caches whatever bytes arrived. `simulate.go` then `json.Unmarshal(snap.Data, &raw)` where `raw.Strikes` is an unbounded array. Same for `/api/snapshot/{symbol}` which writes the raw bytes back.
**Attack vector**: contingent on NATS being reachable by an attacker (e.g. flat network, no JetStream auth, dev compose stack exposed). One malicious publish of `state.spx.gex` with `strikes: [...10M...]` pins memory on the next `/api/simulate` call. Treat NATS as trust boundary, not just internal.
**Fix**: cap `len(raw.Strikes)` after decode (e.g. ≤8 K — SPX option chain breadth is well under that). Validate `head.TsNs` is non-zero / within sanity range before caching.

### F8 [P3]: `idx_api_keys_hash_active` is redundant given `key_hash UNIQUE`
**Location**: backend/scripts/migrations/000008_api_keys.up.sql:29 (UNIQUE), 39-41 (partial)
**Evidence**: the `UNIQUE` constraint on `key_hash` already creates a B-tree covering every row. The partial `idx_api_keys_hash_active` (`WHERE revoked_at IS NULL`) cannot be picked by `SELECT … WHERE key_hash = $1` (no `revoked_at` predicate in `LookupByHash`). It's dead weight on writes.
**Attack vector**: not exploitable; pure perf/maintenance smell. Operators may also assume revoked rows are filtered server-side when they are not — `LookupByHash` returns the row and the middleware checks `IsActive` after.
**Fix**: drop the partial index. If the intent is to skip revoked rows in the hot path, add `AND revoked_at IS NULL` to the `LookupByHash` query and keep the partial index.

### F9 [P3]: Slog `Detail` and `UserAgent` may carry CRLF in TextHandler mode — UNCLEAR
**Location**: backend/internal/apikey/audit.go:60-79, backend/internal/api/alerts.go:144 (`Detail: "id="+rule.ID+" symbol="+...`)
**Evidence**: `rule.ID` is user-controlled JSON (decoded at alerts.go:130). `UserAgent` is fully attacker-controlled. `slog`'s `JSONHandler` quote-escapes correctly; `TextHandler` quotes values containing whitespace but I could not confirm from code alone whether embedded `\n\r` survives unescaped into the slog stream when the consumer (e.g. journald, slog text mode in dev) renders one record per line.
**Attack vector**: if logs are shipped via TextHandler to a line-oriented SIEM, an attacker with a forged `User-Agent: "x\nlevel=ERROR msg=fake"` could inject synthetic audit lines.
**Fix**: sanitize `UserAgent` and free-form `Detail` strings (strip `\r\n\t`, replace with spaces) at the sink boundary. Confirms safe for both Text and JSON handlers.

## Verified clean
- backend/internal/apikey/middleware.go:49 — `LookupByHash` SQL is parameterised; no string concat.
- backend/internal/apikey/middleware.go:113-121 — Bearer parsing is case-insensitive (RFC 6750), trimmed.
- backend/internal/apikey/middleware.go:129-143, backend/internal/api/realip.go:30-37 — `clientIP` reads `r.RemoteAddr` only; XFF rewrite is gated on trusted-proxy CIDR, default-deny.
- backend/internal/apikey/audit.go:73-74 — `parent_user_id` is opaque flowjob.id token, not PII.
- backend/internal/apikey/store.go:42-67 — all queries parameterised; `pgx.ErrNoRows` mapped to `ErrUnknownKey` without leaking driver text.
- backend/internal/api/bodylimit.go:26-33 — global 1 MiB cap applied via `r.Use` BEFORE protected mount; covers every chi-mounted route including `/api/backtest/run` (which also has its own 64 KiB inner cap).
- backend/internal/api/ws.go:71 — `SetReadLimit(4096)` correctly bounds inbound frame size; F2 above is about cross-message accumulation, not frame size.
- backend/scripts/migrations/000008_api_keys.up.sql:29 — `key_hash BYTEA NOT NULL UNIQUE` enforces collision impossibility at the DB level even if app-level `Generate` ever regressed.
- backend/internal/api/alerts.go:52-60 — `callerOwnerID` correctly prefers APIKey context; `X-User-ID` only honoured when no key resolved (config refuses production boot with `APIKEY_ENABLED=false`, per SECURITY.md prereq list).

## Recommendations (not findings)
- Add a Prom histogram on `LookupByHash` latency; F1's load profile would show as p99 spikes long before pool starvation.
- Document the `protected` mw order explicitly in `cmd/api/main.go` — current comment block says "rate limit on the protected surface" but elides that auth runs first (relevant to F1, F3).
- Consider adding `IF EXISTS` rollback for `idx_api_keys_hash_active` if F8 is acted on — current down-migration drops it cleanly.
- `/health/ready` opens a fresh `pgxpool.New` on every probe (cmd/api/main.go:281-289) — unrelated to security but will leak FDs under aggressive probing.
- The 30-second backtest deadline (api/backtest.go:149) and the 2-second auth lookup (apikey/types.go:34) are not exposed as Prom histograms — adding them will help validate F1/F4 fixes empirically.
