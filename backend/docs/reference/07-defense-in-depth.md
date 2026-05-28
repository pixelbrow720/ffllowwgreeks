# 07 — Defense in depth

> Validated against commit `3e5b0ec` (auth pivot to API keys).

Five layers, none sufficient on its own. The ranking is hostile-traffic order: the further down a request gets, the more layers it has bypassed.

```
                        ┌────────────────────────────────────────────────┐
                        │                client traffic                   │
                        └────────────────────────────────────────────────┘
                                                 │
   ── Layer 1 ────────────────────────────────────────────────────────────
                       API-key middleware
                       (Bearer / X-API-Key → SHA-256 → api_keys lookup;
                        revoked / expired keys 401 immediately)
                                                 │
   ── Layer 2 ────────────────────────────────────────────────────────────
                       Per-key rate limit
                       (rate_limit_rps + rate_burst on the api_keys row;
                        anonymous fallback per-IP at 1 rps / 30 burst)
                                                 │
   ── Layer 3 ────────────────────────────────────────────────────────────
                       HTTP body cap (1 MiB) + WS read limits (1–4 KiB)
                       (a hostile client can't pin server memory)
                                                 │
   ── Layer 4 ────────────────────────────────────────────────────────────
                       Security response headers
                       (HSTS, CSP, nosniff, DENY-frame, no-referrer, CORP)
                                                 │
   ── Layer 5 ────────────────────────────────────────────────────────────
                       Audit log + Prometheus metrics + alert rules
                       (paged escalation on suspicious patterns)
                                                 │
                                              handler
```

## Layer 1 — API-key middleware

```
Source:   internal/apikey/middleware.go
Wired:    cmd/api/main.go setupAPIKey → apiKeyMW.Handler on protected sub-router
Applies:  /api/simulate/{symbol}, /api/alerts/*, /api/backtest/run
          (and anything else mounted on the protected router)
```

Inbound `Authorization: Bearer <secret>` or `X-API-Key: <secret>` is hashed with SHA-256 and looked up against the `api_keys` table. Revoked rows (`revoked_at != NULL`) and expired rows (`now >= expires_at`) are refused with 401.

The lookup runs against the configured `Store` with a 2s `LookupTimeout` so a degraded Postgres can't tail-latency every request — it surfaces as 500 + `apikey.auth.lookup_error` instead.

`last_used_at` writes are coalesced to one per key per minute via `shouldTouch` so a hot client doesn't hammer Postgres on every request.

See [`02-auth.md`](02-auth.md) §"Per-request flow" for the full state diagram.

## Layer 2 — Per-key rate limit

```
Source:   internal/apikey/ratelimit.go
Wired:    cmd/api/main.go setupAPIKey returns *RateLimiter; protected.Use(limiter.Middleware(...))
Applies:  /api/simulate/{symbol}, /api/alerts/*, /api/backtest/run
```

Token bucket keyed by `APIKey.ID` (resolved by Layer 1), with rate + burst sourced from the row. The parent site can provision tier-specific budgets without redeploying this binary — bucket params are read on every `Allow` call so a tier upgrade is hot-swappable.

Anonymous traffic (no key resolved, e.g. `APIKEY_ENABLED=false` for local dev) falls back to per-IP at 1 rps / 30 burst, so the surface stays bounded even when Layer 1 is disabled.

`/api/simulate` and `/api/backtest/run` each carry a 30s server-side deadline. Without per-key gating, one client could fan out N concurrent requests and saturate compute.

## Layer 3 — Body + WS read caps

```
Source:
  internal/api/bodylimit.go             — global HTTP body cap (1 MiB)
  internal/api/ws.go                    — /ws/live SetReadLimit(4096)
  internal/replay/ws.go                 — /ws/replay SetReadLimit(1024)
Wired:    cmd/api/main.go — chi.Use(api.BodyLimit) on root router
```

Three caps. The HTTP cap is global; the WS caps are per-handler because the `coder/websocket` library exposes `SetReadLimit` after the upgrade.

| Surface | Cap | Why that number |
|---|---|---|
| HTTP request body (any chi route) | 1 MiB | Largest legitimate payload is `alerts.Rule` JSON, well under 16 KiB. 1 MiB is a wide guard. |
| `/ws/live` inbound | 4 KiB | Inbound shapes are subscribe / unsubscribe JSON. |
| `/ws/replay` inbound | 1 KiB | Inbound shapes are pause/resume/set_speed JSON. Even smaller. |

Each handler also imposes its own tighter `io.LimitReader` (4–16 KiB). Layer 3 is the fallback for any future route that forgets to set its own.

`http.MaxBytesReader` returns `*http.MaxBytesError` on overflow, which the JSON decoder surfaces as a 400 — visible to the client; not silently truncated.

## Layer 4 — Security response headers

```
Source:   internal/api/security_headers.go
Wired:    cmd/api/main.go — chi.Use(api.SecurityHeaders) before BodyLimit
```

| Header | Value | Protects against |
|---|---|---|
| `X-Content-Type-Options` | `nosniff` | MIME-type confusion attacks |
| `X-Frame-Options` | `DENY` | Clickjacking via iframe |
| `Referrer-Policy` | `no-referrer` | Token leakage in `Referer` header to outbound links |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'` | Backs up X-Frame for modern browsers; api emits JSON only |
| `Cross-Origin-Resource-Policy` | `same-origin` | Backstop on cross-origin embed attempts |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (only when TLS) | Browser pin to HTTPS |

HSTS is gated on `r.TLS != nil || X-Forwarded-Proto == "https"` so reverse-proxy deployments still flip it on, but local dev (`http://localhost`) doesn't get a header that would lock the browser into HTTPS for that hostname forever.

CSP `default-src 'none'` is correct **for the api binary** — it never serves HTML or executable content. The flowjob.id host frontend serves the SPA and applies its own CSP there.

## Layer 5 — Audit + metrics + alert rules

```
Source:
  internal/apikey/audit.go        — AuditEvent + AuditSink + SlogAuditSink
  internal/apikey/metrics.go      — Prometheus counters
  deploy/prometheus/flowgreeks.rules.yml — alert rules (flowgreeks-apikey group)
```

Every auth event lands in three places:

1. **slog audit log** (structured) — `kind`, `key_id`, `parent_user_id`, `ip`, `user_agent`, `detail`, `occurred_at`. WARN on every anomalous kind (`missing`, `unknown`, `revoked`, `expired`, `lookup_failed`, `rate_limited`); INFO on `apikey.auth.ok`.
2. **Prometheus counter** — bounded cardinality (no per-key labels). Counter + result label only.
3. **Prometheus alert rule** — pages humans on rate-of-change thresholds.

| Event | Audit kind | Counter | Alert rule |
|---|---|---|---|
| Auth OK | `apikey.auth.ok` | `flowgreeks_apikey_auth_attempts_total{result=ok}` | — |
| Missing creds | `apikey.auth.missing` | `…{result=missing}` | rolls into `APIKeyAuthFailureBurst` |
| Unknown key | `apikey.auth.unknown` | `…{result=unknown}` | rolls into `APIKeyAuthFailureBurst` |
| Revoked | `apikey.auth.revoked` | `…{result=revoked}` | `APIKeyRevokedKeyAttempts` (page) |
| Expired | `apikey.auth.expired` | `…{result=expired}` | — |
| Lookup failed | `apikey.auth.lookup_failed` | `…{result=lookup_error}` | (operator-defined SIEM rule) |
| Rate limited | `apikey.auth.rate_limited` | `flowgreeks_apikey_rate_limited_total` | `APIKeyRateLimitedBurst` (warn) |
| Webhook async error | — | `flowgreeks_alerts_webhook_async_errors_total` | (operator-defined SIEM rule) |

The slog log is the audit trail (WHAT + WHO + WHEN); the metrics are the alert surface (HOW MUCH); the rules turn metrics into pages.

## Outbound — SSRF guard

Not a request-side layer, but the same defense-in-depth posture for outbound traffic.

```
Source:   internal/alerts/delivery.go (NewWebhookSink validateWebhookURL)
Applied:  POST /api/alerts/rules with a webhook config — fails at sink construction
```

Refuses webhook URLs that resolve to:

```
loopback                    127.0.0.0/8, ::1
private (RFC 1918)          10.0.0.0/8, 172.16/12, 192.168/16
link-local unicast          169.254.0.0/16   ← cloud metadata!
link-local multicast        224.0.0.0/24-ish
interface-local multicast
unspecified                 0.0.0.0
CGNAT                       100.64.0.0/10
IPv6 ULA                    fc00::/7
site-local
```

Returns `ErrWebhookBlockedTarget` from `NewWebhookSink` so a hostile rule fails on `POST /api/alerts/rules` (400), not on first fire. The failure is immediate and visible to the rule author.

## Production refusals

Not a runtime layer, but worth listing here. `cmd/api` refuses to boot under `APP_ENV=production` if any of the following hold ([`internal/config/config.go`](../../internal/config/config.go)):

- `POSTGRES_PASSWORD` is the dev default
- `APIKEY_ENABLED=false` (would leave the protected surface open)
- `LOG_LEVEL=debug` (debug logs may leak Authorization headers)
- `API_CORS_ORIGINS` is empty (would default-deny everything anyway, but the explicit refusal catches misconfigured deploys)

API keys themselves are provisioned by flowjob.id — this binary doesn't mint them, doesn't have an admin UI for them, and never sees them in plaintext outside the `apikey.Generate()` helper (which the parent site is expected to call when minting on its own infrastructure).

## What this section does **not** cover

- Per-handler validation logic (input shape checks, etc) → grep handler files directly.
- TLS termination — that's the deployment layer's responsibility (reverse proxy / ingress); this binary doesn't terminate TLS itself.
- Kubernetes NetworkPolicy / cloud security groups — operator concern.

## Test coverage map

Layer-specific tests:

| Test | Covers |
|---|---|
| `TestMiddleware_*` | Layer 1 — Bearer / X-API-Key / unknown / revoked / expired |
| `TestRateLimiter_*` + `TestRateLimiterMiddleware_*` | Layer 2 |
| (HTTP body limit — implicit via chi middleware test) | Layer 3 |
| `TestSecurityHeaders_BasicSet` | Layer 4 |
| `TestSecurityHeaders_HSTSOnHTTPSForwarded` | Layer 4 TLS gate |
| `TestNewWebhookSink_*` | SSRF guard |
| `TestProductionConfigGuard_*` | Production refusals |
