"""Add ``api_keys.key_lookup`` (keyed BLAKE2b digest) for O(1) auth lookup.

Pre-existing rows have ``key_lookup = NULL`` and continue to work via
the prefix-scan fallback; the auth path lazily backfills the column on
the next successful verify so the population grows organically.

**Rev 10 MIG-4:** the original landing called
``op.create_unique_constraint(...)``, which under the hood issues
``ALTER TABLE ... ADD CONSTRAINT ... UNIQUE (...)``. That builds a
non-concurrent UNIQUE INDEX, taking an ``AccessExclusiveLock`` on
``api_keys`` for the duration — every auth write
(``last_used_at`` / ``usage_count`` updates on cache miss, plus admin
mutations) blocks while the index builds.

Fix: two-step pattern that's the canonical PostgreSQL recipe for
adding a unique constraint without a write-blocking build.

  1. ``CREATE UNIQUE INDEX CONCURRENTLY`` — builds the index without
     blocking writes. Runs outside a transaction (autocommit block).
  2. ``ALTER TABLE ... ADD CONSTRAINT ... UNIQUE USING INDEX`` —
     promotes the existing index into a unique constraint. This is a
     fast catalog-only operation; no scan.

The unique index has no ``WHERE`` clause: ``key_lookup`` is nullable
and PostgreSQL treats NULLs as distinct in a unique index by default,
so legacy ``key_lookup IS NULL`` rows coexist with the unique
constraint without conflict (matches the model's ``Column(nullable=True,
unique=True)`` semantics).

Revision ID: 0010
Revises: 0009
Create Date: 2027-01-15 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_UQ_NAME: str = "uq_api_keys_key_lookup"


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("key_lookup", sa.Text(), nullable=True),
    )

    # Rev 10 MIG-4: build the unique index CONCURRENTLY (no write block),
    # then promote it to a unique constraint via ``USING INDEX``. The
    # promotion step is fast — it only writes a catalog row.
    with op.get_context().autocommit_block():
        op.execute(
            f"CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {_UQ_NAME} "
            f"ON api_keys (key_lookup)"
        )

    op.execute(
        f"ALTER TABLE api_keys "
        f"ADD CONSTRAINT {_UQ_NAME} UNIQUE USING INDEX {_UQ_NAME}"
    )


def downgrade() -> None:
    # Dropping the unique constraint also drops the underlying index
    # (because the index was promoted into the constraint via USING
    # INDEX). No separate DROP INDEX needed.
    op.drop_constraint(_UQ_NAME, "api_keys", type_="unique")
    op.drop_column("api_keys", "key_lookup")
