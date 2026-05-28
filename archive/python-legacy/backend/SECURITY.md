# Production Hardening Guide

This service is safe to run on a private LAN with the bundled defaults,
but **must** be re-configured before exposing it on a public network
(Cloudflare tunnel, public IP, etc.). Items below are required for any
deployment that accepts traffic from the internet.

## Required environment variables (rotate before public deploy)

| Var                          | Why                                                        |
| ---------------------------- | ---------------------------------------------------------- |
| `ADMIN_PASSWORD`             | Default is `changeme`. Brute-force target.                 |
| `JWT_SECRET`                 | Signs admin JWTs.                                          |

The application refuses to boot in non-test mode whenever either is
unset or matches a known dev default.

### Generating strong secrets

```
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

## Rate limits

Enforced via `slowapi` decorators on individual routes:

* `POST /admin/login` — `5/minute` per IP (brute-force protection)
* `GET  /v1/{symbol}/*` — `RATE_LIMIT_PER_MINUTE` (default 120) per API key

429 responses include a `detail` field with the limit that fired.

## Compression

`GZipMiddleware` compresses any response ≥ 1 KB. Inspector and snapshot
payloads (5–50 KB JSON) shrink ~80% — verify with
`curl -i -H 'accept-encoding: gzip' .../v1/SPXW/snapshot | head`.

## DB connection pool

Tunable via env: `DB_POOL_SIZE` (20), `DB_MAX_OVERFLOW` (10),
`DB_POOL_RECYCLE_SECONDS` (3600), `DB_POOL_PRE_PING` (true).

## Bcrypt cost

API-key + admin-password hashes use cost factor 12 (~250 ms / hash).
Configured in `app/core/security.py::BCRYPT_ROUNDS`. Existing hashes
keep their stored cost — raising the constant only affects keys minted
after the change.

## Backups

Snapshot the `db_data` Docker volume nightly (`pg_dump -Fc`). The
`pipeline_runs` + `flow_events` tables are append-only and grow ~50 MB/day
under default load — provision accordingly.
