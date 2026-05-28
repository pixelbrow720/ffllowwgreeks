-- 000002_ticks_hypertable down migration: drop the hypertable.
-- TimescaleDB drops the hypertable wrapping plus all chunks; policies and
-- compression settings cascade with the table.

DROP TABLE IF EXISTS ticks;

DELETE FROM schema_version WHERE version = 2;
