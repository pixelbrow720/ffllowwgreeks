# 02 — API-key auth

> Validated against commit `3e5b0ec`.
>
> Source files referenced throughout:
> - [`internal/apikey/middleware.go`](../../internal/apikey/middleware.go) — Bearer / X-API-Key auth middleware
> - [`internal/apikey/store.go`](../../internal/apikey/store.go) — `api_keys` persistence
> - [`internal/apikey/ratelimit.go`](../../internal/apikey/ratelimit.go) — per-key token bucket
> - [`internal/apikey/audit.go`](../../internal/apikey/audit.go) — `AuditEvent` + `AuditSink`
> - [`internal/apikey/metrics.go`](../../internal/apikey/metrics.go) — Prometheus counters
> - [`internal/apikey/types.go`](../../internal/apikey/types.go) — `APIKey` type + `Generate` / `HashSecret`

## Why this looks different from a typical auth section

FlowGreeks runs as an add-on inside flowjob.id. The parent site owns user accounts, billing, and add-on activation; this binary only authenticates inbound requests against **opaque API keys** provisioned by the parent site.

There is no signup, no password, no refresh token, no per-account lockout, no tier gating here. If you're looking for those, they belong on the parent site, not in this codebase.

## Wire format

```
Authorization: Bearer <secret>
                 -- or --
X-API-Key: <secret>
```

`extractSecret` ([`middleware.go`](../../internal/apikey/middleware.go)) tries Bearer first, then X-API-Key. Both paths land at the same lookup.

The secret is a **32-byte cryptographically-random hex string** (64 chars after encoding). Mint via `apikey.Generate()`. The plaintext only ever exists in the parent site's response to its own user; this binary stores only its **SHA-256 digest** in `api_keys.key_hash`.

## Per-request flow

```
Inbound request
   │
   ▼
extractSecret(r)
   │
   ├─ no secret? ──► 401 ErrNoCredentials + audit AuditAuthMissing + metric "missing"
   │
   │ secret present
   ▼
Store.LookupByHash(SHA-256(secret))
   │
   ├─ ErrUnknownKey ──► 401 ErrUnknownKey + audit AuditAuthUnknown + metric "unknown"
   ├─ other error  ──► 500 ErrLookupFailed + audit AuditAuthLookupFailed + metric "lookup_error"
   │
   │ row found
   ▼
APIKey.IsActive(now) ?
   │
   ├─ revoked_at != nil ──► 401 ErrRevokedKey + audit AuditAuthRevoked + metric "revoked"
   ├─ now >= expires_at ──► 401 ErrExpiredKey + audit AuditAuthExpired + metric "expired"
   │
   │ active
   ▼
shouldTouch(last_used_at, now) ?
   │
   │ last_used_at is null OR now-last_used_at > 1m
   ▼
go Store.TouchLastUsed(id)        ← async, never blocks the hot path
   │
   ▼
withAPIKey(ctx, key)              ← installs APIKey on request context
   │
   ▼
audit AuditAuthOK + metric "ok"
   │
   ▼
next.ServeHTTP(w, r)              ← downstream sees apikey.FromContext(ctx)
```

The `LookupTimeout = 2 * time.Second` constant ([`types.go`](../../internal/apikey/types.go)) caps the DB call. A degraded Postgres can't tail-latency every request — it bumps `apikey.auth.lookup_failed` and 500s.

The `touchInterval = 1 * time.Minute` coalesces `UPDATE last_used_at = NOW()` writes ([`store.go`](../../internal/apikey/store.go)). A hot client doing 100 req/s only writes once per minute, not 100 times.

## Rate limit

```
APIKey.RateLimitRPS, APIKey.RateBurst   ← stored on the row by flowjob.id
                  │
                  ▼
       per-request middleware bucket lookup:
         key       = "k:" + APIKey.ID  (resolved via apikey.FromContext)
                   = "ip:" + remoteIP   (anonymous fallback)
         rate      = APIKey.RateLimitRPS, default 1.0
         burst     = APIKey.RateBurst, default 30
                   │
                   ▼
       token bucket with current (rate, burst):
         tokens += elapsed × rate     (capped at burst)
         if tokens >= 1: tokens--, allow
         else: 429 + Retry-After
```

A few things worth calling out:

- **Tier change is hot-swappable.** Each call passes (rate, burst) into `Allow`, so when flowjob.id upgrades a key from recon-tier to quant-tier, the next request picks up the new budget without process restart.
- **Anonymous keying** uses the IP, so unauth traffic isn't unbounded. Useful when `APIKEY_ENABLED=false` for local dev.
- **Janitor** evicts buckets unseen for 1 hour ([`ratelimit.go`](../../internal/apikey/ratelimit.go)) so memory stays bounded under churn.

## Schema (migration 0008)

```
api_keys
─────────────────────────────────────
  id              BIGSERIAL PK
  name            TEXT
  key_hash        BYTEA UNIQUE              -- SHA-256(secret)
  parent_user_id  TEXT NULL                  -- opaque flowjob.id user id
  rate_limit_rps  REAL NOT NULL DEFAULT 1.0
  rate_burst      INTEGER NOT NULL DEFAULT 30
  revoked_at      TIMESTAMPTZ NULL
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
  last_used_at    TIMESTAMPTZ NULL
  expires_at      TIMESTAMPTZ NULL           -- null = never expires

partial index on (key_hash) WHERE revoked_at IS NULL  -- hot lookup path
partial index on (parent_user_id) WHERE revoked_at IS NULL
```

`parent_user_id` is text + opaque because we don't own the table on flowjob.id and don't want a foreign-key dependency between systems. Use it for audit correlation and rate-limit-tier provisioning.

The hot-path index is **partial on `revoked_at IS NULL`** — revoked rows stay around for audit but don't bloat the lookup index. Re-using a hash for a fresh key would be a bug; the database enforces uniqueness across the whole table.

## How the parent site provisions a key

Out of band from this binary — the parent site does:

1. `secret, hash, _ := apikey.Generate()` (or equivalent in their language)
2. `INSERT INTO api_keys (name, key_hash, parent_user_id, rate_limit_rps, rate_burst, expires_at) VALUES (...)`
3. Return `secret` (one-shot) to the user
4. Discard `secret` server-side

The `apikey.Generate` + `apikey.HashSecret` helpers are stable so the parent site can implement the same routine if it's also Go, or replicate "32 bytes of crypto/rand → hex → SHA-256" in any language.

## Revocation

```
flowjob.id   ──►  UPDATE api_keys SET revoked_at = NOW() WHERE id = $1
```

Next request with that secret hits `LookupByHash` → returns the revoked row → `IsActive` returns false → 401 `ErrRevokedKey`.

There's no cache between this binary and Postgres on the auth path, so revocation propagates within `LookupTimeout` (2s upper bound, usually <50ms).

## Audit + metrics

| Event | Audit kind (slog) | Counter | Level |
|---|---|---|---|
| Auth OK | `apikey.auth.ok` | `flowgreeks_apikey_auth_attempts_total{result=ok}` | INFO |
| Missing creds | `apikey.auth.missing` | `…{result=missing}` | **WARN** |
| Unknown key | `apikey.auth.unknown` | `…{result=unknown}` | **WARN** |
| Revoked key | `apikey.auth.revoked` | `…{result=revoked}` | **WARN** |
| Expired key | `apikey.auth.expired` | `…{result=expired}` | **WARN** |
| Lookup failed | `apikey.auth.lookup_failed` | `…{result=lookup_error}` | **WARN** |
| Rate limited | `apikey.auth.rate_limited` | `flowgreeks_apikey_rate_limited_total` | **WARN** |

Pair with the Prometheus alert rules ([`deploy/prometheus/flowgreeks.rules.yml`](../../deploy/prometheus/flowgreeks.rules.yml) `flowgreeks-apikey` group):

- `APIKeyAuthFailureBurst` — > 5 unknown/missing per second for 2m — coordinated probe
- `APIKeyRevokedKeyAttempts` — > 3 revoked-key uses in 5m — leak suspected or stale client
- `APIKeyRateLimitedBurst` — sustained per-key throttling — misbehaving client or under-budgeted tier

## Test coverage map

| Test | Covers |
|---|---|
| `TestMiddleware_AcceptsBearer` | Bearer header path |
| `TestMiddleware_AcceptsXAPIKey` | X-API-Key header path |
| `TestMiddleware_RejectsMissing` | no creds → 401 |
| `TestMiddleware_RejectsUnknown` | unknown hash → 401 |
| `TestMiddleware_RejectsRevoked` | revoked_at set → 401 + ErrRevokedKey body |
| `TestMiddleware_RejectsExpired` | now ≥ expires_at → 401 |
| `TestExtractSecret_BearerWinsOverXAPIKey` | precedence rule |
| `TestExtractSecret_TolerantOfBearerCase` | "bearer "/"Bearer " both accepted |
| `TestGenerate_DistinctSecrets` | 64-char hex, no collisions |
| `TestHashSecret_Deterministic` | same input → same hash |
| `TestRateLimiter_AllowsBurstThenBlocks` | burst path |
| `TestRateLimiter_PerKeyIsolation` | one key blowing budget doesn't starve another |
| `TestRateLimiter_Refills` | refill math |
| `TestRateLimiter_TierChangePicksUpNewBudget` | hot tier upgrade |
| `TestRateLimiterMiddleware_429WithRetryAfter` | 429 + header |
| `TestRateLimiterMiddleware_AnonymousFallsBackToIP` | per-IP fallback |

All in `internal/apikey/*_test.go`.

## What this section does **not** cover

- WS broker fanout mechanics → see [`01-data-pipeline.md`](01-data-pipeline.md) §8.
- Predicate semantics for backtest reuse → see [`05-time-machine.md`](05-time-machine.md).
- The `auth.AuditSink` from prior versions — that surface was removed in the auth pivot. The new sink is `apikey.AuditSink`, same shape but key-scoped instead of user-scoped.
