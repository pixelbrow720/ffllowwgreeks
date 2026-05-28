CREATE TABLE IF NOT EXISTS dealer_state_1s (
    ts                 TIMESTAMPTZ NOT NULL,
    symbol             SMALLINT    NOT NULL,
    spot               DOUBLE PRECISION,
    basis_smooth       DOUBLE PRECISION,
    net_gex            DOUBLE PRECISION,
    zero_gamma         DOUBLE PRECISION,
    call_wall          DOUBLE PRECISION,
    put_wall           DOUBLE PRECISION,
    expected_mv        DOUBLE PRECISION,
    regime             SMALLINT,
    charm_zone         SMALLINT,
    charm_velocity     DOUBLE PRECISION,
    dpi_composite      REAL,
    dpi_net_gamma      REAL,
    dpi_charm_velocity REAL,
    dpi_vanna          REAL,
    dpi_ttc            REAL,
    dpi_flow           REAL,
    pulse_gamma        REAL,
    pulse_charm        REAL,
    pulse_vanna        REAL,
    pulse_total        REAL,
    pin_active         BOOLEAN,
    pin_top_strike     DOUBLE PRECISION,
    pin_top_prob       REAL
);

SELECT create_hypertable('dealer_state_1s', 'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_dealer_state_1s_sym_ts
    ON dealer_state_1s (symbol, ts DESC);

ALTER TABLE dealer_state_1s SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby = 'ts DESC'
);

DO $$
BEGIN
    PERFORM add_compression_policy('dealer_state_1s', INTERVAL '7 days');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END;
$$;

DO $$
BEGIN
    PERFORM add_retention_policy('dealer_state_1s', INTERVAL '14 months');
EXCEPTION WHEN duplicate_object THEN
    NULL;
END;
$$;

INSERT INTO schema_version (version, description)
VALUES (4, 'dealer_state_1s hypertable for backtest archive')
ON CONFLICT (version) DO NOTHING;
