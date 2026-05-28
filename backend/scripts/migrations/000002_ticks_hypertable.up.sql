-- 000002_ticks_hypertable: ticks hypertable per docs/DATA_MODEL.md.
-- Option-specific columns (expiry, strike, side) are nullable so the same
-- table can hold futures ticks; instrument_id distinguishes contracts.

CREATE TABLE IF NOT EXISTS ticks (
    ts             TIMESTAMPTZ      NOT NULL,
    recv_ts        TIMESTAMPTZ      NOT NULL,
    symbol         SMALLINT         NOT NULL,
    expiry         DATE,
    strike         INTEGER,
    side           SMALLINT,
    tick_type      SMALLINT         NOT NULL,
    price          DOUBLE PRECISION,
    size           INTEGER,
    bid            DOUBLE PRECISION,
    ask            DOUBLE PRECISION,
    bid_size       INTEGER,
    ask_size       INTEGER,
    open_interest  INTEGER,
    aggressor      SMALLINT,
    exchange       SMALLINT,
    instrument_id  BIGINT
);

SELECT create_hypertable(
    'ticks', 'ts',
    chunk_time_interval => INTERVAL '6 hours',
    if_not_exists       => TRUE
);

CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts
    ON ticks (symbol, ts DESC);

CREATE INDEX IF NOT EXISTS idx_ticks_strike
    ON ticks (symbol, expiry, strike, side, ts DESC);

ALTER TABLE ticks SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, expiry, strike, side',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy('ticks', INTERVAL '2 days',     if_not_exists => TRUE);
SELECT add_retention_policy  ('ticks', INTERVAL '14 months',  if_not_exists => TRUE);

INSERT INTO schema_version (version, description)
VALUES (2, 'ticks hypertable')
ON CONFLICT (version) DO NOTHING;
