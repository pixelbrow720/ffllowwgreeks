DROP INDEX IF EXISTS idx_users_locked_until;
ALTER TABLE users
    DROP COLUMN IF EXISTS failed_login_count,
    DROP COLUMN IF EXISTS locked_until;

DELETE FROM schema_version WHERE version = 7;
