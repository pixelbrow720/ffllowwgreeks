-- Pivot from per-user auth to API-key model.
--
-- FlowGreeks is now an add-on on flowjob.id (parent site owns billing,
-- user accounts, add-on activation). The api binary only needs to
-- authenticate inbound requests against API keys provisioned by the
-- parent site — there's no signup, no password, no refresh token, no
-- per-account lockout, no tier gating here anymore.
--
-- Drop everything user-auth-related. Add the api_keys table.
--
-- Schema decisions:
--   - key_hash is SHA-256 of the secret. We never store the secret.
--   - parent_user_id is an opaque text id from flowjob.id — no FK
--     because we don't own that table. Use it for rate-limit + audit
--     correlation, nothing else.
--   - rate_limit_rps + rate_burst are per-key so the parent site can
--     provision different tiers (e.g. quant tier gets a higher cap).
--   - expires_at is optional; null = never expires (until revoked).
--   - The partial index on key_hash WHERE revoked_at IS NULL is the
--     hot lookup path; revoked rows stay around for audit but don't
--     bloat the index.

DROP TABLE IF EXISTS refresh_tokens CASCADE;
DROP TABLE IF EXISTS users CASCADE;

CREATE TABLE IF NOT EXISTS api_keys (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    key_hash        BYTEA NOT NULL UNIQUE,
    parent_user_id  TEXT,
    rate_limit_rps  REAL NOT NULL DEFAULT 1.0,
    rate_burst      INTEGER NOT NULL DEFAULT 30,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash_active
    ON api_keys (key_hash)
    WHERE revoked_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_api_keys_parent_user
    ON api_keys (parent_user_id)
    WHERE revoked_at IS NULL;

INSERT INTO schema_version (version, description)
VALUES (8, 'auth pivot: drop users + refresh_tokens, add api_keys')
ON CONFLICT (version) DO NOTHING;
