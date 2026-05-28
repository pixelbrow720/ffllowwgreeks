-- Account lockout: track consecutive failed logins and a temporary
-- lock window. The middleware enforces the lock; this migration
-- only adds the columns and the index for fast lookup.
--
-- Design notes:
--   * failed_login_count resets to 0 on successful login.
--   * locked_until is null when not locked, or a future timestamp
--     when the account is in lockout. We use a timestamp instead
--     of a boolean so the lock auto-expires without a sweeper job.
--   * Both columns are nullable so existing rows backfill cheaply
--     (Postgres avoids a table rewrite when the default is null).

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS failed_login_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until       TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_users_locked_until
    ON users (locked_until)
    WHERE locked_until IS NOT NULL;

INSERT INTO schema_version (version, description)
VALUES (7, 'auth: account lockout — failed_login_count + locked_until')
ON CONFLICT (version) DO NOTHING;
