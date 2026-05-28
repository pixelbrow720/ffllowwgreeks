CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER     PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT        NOT NULL
);

INSERT INTO schema_version (version, description)
VALUES (1, 'initial schema_version table')
ON CONFLICT (version) DO NOTHING;

CREATE EXTENSION IF NOT EXISTS timescaledb;
