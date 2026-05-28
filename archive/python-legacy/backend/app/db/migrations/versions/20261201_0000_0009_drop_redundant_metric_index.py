"""Drop redundant ``ix_computed_metrics_symbol_type_ts``.

This index is fully covered by ``ix_computed_metrics_symbol_type_exp_ts``
(which has a wider key but the same leading columns), so every metric
upsert was paying double the index-write cost. Dropping it halves write
amplification on the hottest write path (~36 metric_types × dozens of
strikes × every 60 s per supported symbol).

Revision ID: 0009
Revises: 0008
Create Date: 2026-12-01 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(
        "ix_computed_metrics_symbol_type_ts",
        table_name="computed_metrics",
        if_exists=True,
    )


def downgrade() -> None:
    op.create_index(
        "ix_computed_metrics_symbol_type_ts",
        "computed_metrics",
        ["symbol", "metric_type", "ts"],
    )
