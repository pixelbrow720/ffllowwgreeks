"""Helper for TimescaleDB-optional migrations.

When deploying on plain PostgreSQL (e.g. Neon free tier), TimescaleDB
functions like ``create_hypertable`` and ``add_retention_policy`` are
unavailable. This module provides ``safe_execute_tsdb`` which wraps
those calls in a PL/pgSQL block that silently skips when the extension
is not installed.
"""

from __future__ import annotations

from alembic import op


def safe_execute_tsdb(sql: str) -> None:
    """Execute a TimescaleDB-specific SQL statement, skipping gracefully
    if the extension is not available.

    Wraps the statement in a DO $$ block that catches
    ``undefined_function`` (42883) errors — the error Postgres raises
    when ``create_hypertable`` etc. don't exist.
    """
    wrapped = f"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
    EXECUTE $tsdb${sql}$tsdb$;
  END IF;
END$$;
"""
    op.execute(wrapped)
