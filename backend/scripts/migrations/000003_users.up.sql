CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         CITEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    tier          SMALLINT NOT NULL DEFAULT 0,  -- 0=free, 1=recon, 2=edge, 3=quant
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE EXTENSION IF NOT EXISTS citext;

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

INSERT INTO schema_version (version, description)
VALUES (3, 'auth: users table with bcrypt password + tier')
ON CONFLICT (version) DO NOTHING;
