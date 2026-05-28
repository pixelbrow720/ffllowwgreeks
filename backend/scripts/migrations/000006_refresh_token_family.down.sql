DROP INDEX IF EXISTS idx_refresh_tokens_family;
ALTER TABLE refresh_tokens DROP COLUMN IF EXISTS family_id;

DELETE FROM schema_version WHERE version = 6;
