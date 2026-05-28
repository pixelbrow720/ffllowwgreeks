-- Refresh token families: every login produces a new family; rotation
-- copies the family id of the parent. Replay of a revoked token is a
-- leak indicator — we revoke the entire family so a stolen device can't
-- continue refreshing on the rotated chain.

ALTER TABLE refresh_tokens
    ADD COLUMN IF NOT EXISTS family_id BIGINT NOT NULL DEFAULT 0;

-- Backfill existing rows: each pre-migration token becomes its own family.
UPDATE refresh_tokens SET family_id = id WHERE family_id = 0;

-- Drop the default once backfill is done; new inserts must supply explicitly.
ALTER TABLE refresh_tokens ALTER COLUMN family_id DROP DEFAULT;

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_family
    ON refresh_tokens (family_id);

INSERT INTO schema_version (version, description)
VALUES (6, 'auth: refresh token family for reuse detection')
ON CONFLICT (version) DO NOTHING;
