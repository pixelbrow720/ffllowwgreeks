# Security policy

## Reporting a vulnerability

If you believe you've found a security issue in FlowGreeks, please **do not open a public GitHub issue**. Email the maintainer directly at the address in the repository owner's profile, with:

- A description of the issue and its impact
- Steps to reproduce (or proof-of-concept code)
- Any relevant logs / payloads

Expect an acknowledgement within 72 hours and an initial assessment within 7 days.

## Supported versions

This project is pre-1.0. Only the `main` branch is supported. Security fixes land on `main` and ship in the next tagged release.

## Auth model

FlowGreeks runs as an add-on inside flowjob.id. The parent site owns user accounts, billing, and add-on activation. This binary authenticates inbound requests with **opaque API keys** provisioned by the parent site — there is no signup, no password, no refresh token, no per-account lockout, no tier gating here.

Wire format:

```
Authorization: Bearer <secret>
                 -- or --
X-API-Key: <secret>
```

Bearer wins when both are present. The secret is a 32-byte random hex (64 chars). Server-side we only ever store its SHA-256 digest — a snapshot of `api_keys` is useless on its own for forging sessions.

Per-key budgets (`rate_limit_rps`, `rate_burst`) live on the row, so the parent site can provision tier-specific budgets without redeploying this binary. Anonymous (no key resolved) traffic falls back to per-IP at 1 rps / 30 burst.

Keys revoke on demand (`api_keys.revoked_at`) and optionally expire (`api_keys.expires_at`). Both checks happen on every request.

## Defense-in-depth posture

The api binary applies five layered defenses for inbound traffic:

| Layer | What it protects against | Where it lives |
|---|---|---|
| API-key middleware | Anonymous access to protected REST + WS surface | `internal/apikey/middleware.go` |
| Per-key rate limit | Single-client DoS via concurrent /api/simulate or /api/backtest | `internal/apikey/ratelimit.go` |
| HTTP body cap (1 MiB) + WS read limits (1–4 KiB) | Hostile clients pinning server memory | `internal/api/bodylimit.go`, `c.SetReadLimit` in WS handlers |
| Security response headers (HSTS / CSP / nosniff / DENY-frame) | Browser-side attacks (clickjack, MIME confusion, mixed-content) | `internal/api/security_headers.go` |
| Audit log + Prometheus metrics + alert rules | Operator visibility into all auth-relevant events | `internal/apikey/audit.go`, `internal/apikey/metrics.go`, `deploy/prometheus/flowgreeks.rules.yml` |

## Audit + telemetry

Every auth event is emitted twice:

1. **slog audit log** — structured records with `kind`, `key_id`, `parent_user_id`, `ip`, `user_agent`, `detail`. WARN level for anomalies (`apikey.auth.missing|unknown|revoked|expired|lookup_failed|rate_limited`); INFO for `apikey.auth.ok`.
2. **Prometheus counters** — `flowgreeks_apikey_auth_attempts_total{result=ok|missing|unknown|revoked|expired|lookup_error}`, `flowgreeks_apikey_rate_limited_total`, `flowgreeks_apikey_keys_revoked_total`. Bounded cardinality (no per-key labels).

Pair with the `deploy/prometheus/flowgreeks.rules.yml` rules — `APIKeyAuthFailureBurst`, `APIKeyRevokedKeyAttempts`, and `APIKeyRateLimitedBurst` will page on suspicious patterns.

## Outbound safety

Webhook delivery (`internal/alerts/delivery.go`) validates URLs at sink construction. `http(s)` only; blocks loopback, RFC 1918, link-local (incl. cloud metadata `169.254.169.254`), CGNAT, IPv6 ULA, and site-local ranges. Hostile alert rules fail on save, not on first fire.

## Continuous verification

Every PR runs:

- `go build ./...` + `go test -race ./...`
- `golangci-lint` (gosec / gocritic / bodyclose / etc.)
- `staticcheck ./...`
- `govulncheck ./...`
- A docker-compose smoke job that boots the demo stack and runs `scripts/smoke/e2e`

Nightly:

- 1000-concurrent-client `ws_stress` against the demo stack (`scripts/ws_stress`)

See `.github/workflows/test.yml` and `.github/workflows/nightly.yml`.

## Production prerequisites

The `cmd/api` binary refuses to boot under `APP_ENV=production` if any of the following are true (`internal/config/config.go`):

- `POSTGRES_PASSWORD` is the dev default
- `APIKEY_ENABLED=false` (would leave the protected surface open)
- `LOG_LEVEL=debug` (debug logs may leak Authorization headers)
- `API_CORS_ORIGINS` is empty (would default-deny everything anyway, but the explicit refusal catches misconfigured deploys)

API keys are provisioned by flowjob.id; this binary does not mint them.

## Out of scope

We have not yet engaged a third-party pentester; that is recommended pre-public-launch. Until then, the posture above is internal-review level.
