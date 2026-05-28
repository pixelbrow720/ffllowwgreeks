"""Initial schema with TimescaleDB hypertables.

**Rev 10 MIG-6 idempotency audit:** every ``op.create_index`` carries
``if_not_exists=True``, every ``op.create_table`` is gated on a
``has_table`` reflection check, and the TimescaleDB extension creation
already uses ``CREATE EXTENSION IF NOT EXISTS``. The intent is
partial-failure idempotency: if a migration is interrupted mid-run
(SIGTERM during deploy, network blip while alembic is still inside the
versioning logic), re-applying ``0001`` from scratch becomes a safe
no-op for the objects already created. Without this, the Rev 10 review
flagged that a single rerun of 0001 would error on
``relation "options_chain" already exists`` and require manual cleanup.

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.db.migrations.tsdb_helper import safe_execute_tsdb

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    """Return True if ``table_name`` already exists on the bound connection.

    Used by the upgrade path to make ``op.create_table`` calls
    idempotent — alembic's ``op.create_table`` does not accept
    ``if_not_exists`` directly, so we reflect the catalog instead.
    """
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    # Extension (idempotent — skipped silently on providers without TimescaleDB).
    #
    # **Rev 11 MIG-12:** the original ``EXCEPTION WHEN OTHERS THEN NULL``
    # block swallowed every error class — including syntax errors and
    # unexpected SQLSTATE values that genuinely warrant a deploy abort.
    # Catching only the two SQLSTATE codes that map to "managed Postgres
    # without superuser / extension not packaged" preserves the
    # plain-Postgres fallback contract while letting real bugs surface.
    #
    # SQLSTATE codes caught:
    #   - ``42501`` (``insufficient_privilege``) — the migration role
    #     can't run ``CREATE EXTENSION`` (typical on RDS without the
    #     ``rds_superuser`` grant or on Aurora before the extension is
    #     pre-installed by the operator).
    #   - ``0A000`` (``feature_not_supported``) — TimescaleDB shared
    #     library isn't loaded on this server (vanilla Postgres,
    #     Postgres-flavoured forks without the extension packaged).
    op.execute(
        """
        DO $$
        BEGIN
            CREATE EXTENSION IF NOT EXISTS timescaledb;
        EXCEPTION
            WHEN insufficient_privilege THEN
                RAISE NOTICE 'TimescaleDB extension not available (insufficient_privilege); falling back to plain Postgres';
            WHEN feature_not_supported THEN
                RAISE NOTICE 'TimescaleDB extension not supported on this server; falling back to plain Postgres';
        END $$;
        """
    )

    # ── options_chain ────────────────────────────────────────────────────────
    if not _table_exists("options_chain"):
        op.create_table(
            "options_chain",
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("symbol", sa.Text(), nullable=False),
            sa.Column("expiration", sa.Date(), nullable=False),
            sa.Column("strike", sa.Numeric(20, 6), nullable=False),
            sa.Column("option_type", sa.CHAR(1), nullable=False),
            sa.Column("oi", sa.BigInteger(), nullable=True),
            sa.Column("volume", sa.BigInteger(), nullable=True),
            sa.Column("iv", sa.Numeric(20, 8), nullable=True),
            sa.Column("delta", sa.Numeric(20, 8), nullable=True),
            sa.Column("gamma", sa.Numeric(20, 8), nullable=True),
            sa.Column("last_price", sa.Numeric(20, 6), nullable=True),
            sa.Column("bid", sa.Numeric(20, 6), nullable=True),
            sa.Column("ask", sa.Numeric(20, 6), nullable=True),
            sa.Column("underlying_price", sa.Numeric(20, 6), nullable=True),
            sa.PrimaryKeyConstraint("ts", "symbol", "expiration", "strike", "option_type"),
        )
    op.create_index(
        "ix_options_chain_symbol_ts",
        "options_chain",
        ["symbol", "ts"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_options_chain_symbol_expiry",
        "options_chain",
        ["symbol", "expiration"],
        if_not_exists=True,
    )

    safe_execute_tsdb("SELECT create_hypertable('options_chain', 'ts', if_not_exists => TRUE, migrate_data => TRUE)")
    safe_execute_tsdb("SELECT add_retention_policy('options_chain', INTERVAL '7 days', if_not_exists => TRUE)")

    # ── computed_metrics ─────────────────────────────────────────────────────
    if not _table_exists("computed_metrics"):
        op.create_table(
            "computed_metrics",
            sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
            sa.Column("symbol", sa.Text(), nullable=False),
            sa.Column("metric_type", sa.Text(), nullable=False),
            sa.Column(
                "strike",
                sa.Numeric(20, 6),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "expiration",
                sa.Date(),
                nullable=False,
                server_default=sa.text("'1970-01-01'::date"),
            ),
            sa.Column(
                "computed_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.Column("value", sa.Numeric(30, 8), nullable=True),
            sa.Column("extra_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.PrimaryKeyConstraint("ts", "symbol", "metric_type", "strike", "expiration"),
        )
    op.create_index(
        "ix_computed_metrics_symbol_type_ts",
        "computed_metrics",
        ["symbol", "metric_type", "ts"],
        if_not_exists=True,
    )
    safe_execute_tsdb("SELECT create_hypertable('computed_metrics', 'ts', if_not_exists => TRUE, migrate_data => TRUE)")
    safe_execute_tsdb("SELECT add_retention_policy('computed_metrics', INTERVAL '7 days', if_not_exists => TRUE)")

    # ── api_keys ─────────────────────────────────────────────────────────────
    if not _table_exists("api_keys"):
        op.create_table(
            "api_keys",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("key_hash", sa.Text(), nullable=False),
            sa.Column("key_prefix", sa.String(32), nullable=False),
            sa.Column("label", sa.Text(), nullable=False),
            sa.Column(
                "allowed_symbols",
                postgresql.ARRAY(sa.Text()),
                nullable=False,
                server_default=sa.text("'{}'::text[]"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "usage_count",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
        )
    op.create_index(
        "ix_api_keys_key_prefix",
        "api_keys",
        ["key_prefix"],
        if_not_exists=True,
    )

    # ── admin_users ──────────────────────────────────────────────────────────
    if not _table_exists("admin_users"):
        op.create_table(
            "admin_users",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("username", sa.Text(), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("NOW()"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("username"),
        )

    # Compress data older than 1 day on options_chain (best-effort).
    op.execute(
        """
        ALTER TABLE options_chain SET (
          timescaledb.compress,
          timescaledb.compress_segmentby = 'symbol, option_type'
        );
        """
    )
    safe_execute_tsdb("SELECT add_compression_policy('options_chain', INTERVAL '1 day', if_not_exists => TRUE)")


def downgrade() -> None:
    op.drop_table("admin_users")
    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
    op.drop_table("api_keys")
    op.drop_index("ix_computed_metrics_symbol_type_ts", table_name="computed_metrics")
    op.drop_table("computed_metrics")
    op.drop_index("ix_options_chain_symbol_expiry", table_name="options_chain")
    op.drop_index("ix_options_chain_symbol_ts", table_name="options_chain")
    op.drop_table("options_chain")
