"""Add ``flow_events`` ``(symbol, ts DESC)`` index and ``databento_api_keys.last_test_at``.

Two small DBA-driven fixes from REVIEW_NEW.md Section D:

* **D1** — every read of ``flow_events`` is ``ORDER BY ts DESC LIMIT N``
  (see ``snapshot.py`` and ``flow.py``). The pre-existing
  ``ix_flow_events_symbol_ts`` is ascending, forcing Postgres to walk
  the partition in reverse and adding ~5–20 ms per call at scale.
  Replace it with ``ix_flow_events_symbol_ts_desc`` whose key matches
  the access pattern. Grep across ``backend/`` (excluding migrations)
  confirms no consumer references the old index by name, so the drop
  is safe.

  **Rev 10 MIG-3:** the swap (CREATE new + DROP old) is now issued
  ``CONCURRENTLY`` inside an ``autocommit_block``. ``flow_events`` is
  written to on every pipeline tick (and a bursty source — alert
  pipeline writes a row per detected event), so a non-concurrent build
  takes a ``ShareLock`` and blocks those writes for the duration.

* **D2** — add ``databento_api_keys.last_test_at`` so the admin UI can
  surface "tested 5m ago / never" without operator memory. The admin
  endpoint that updates this lives in another lane; the model carries
  a ``REV7-LANE-X`` TODO marker.

Plain-Postgres compatible — no TimescaleDB calls.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-24 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── D1: flow_events (symbol, ts DESC) index (Rev 10 MIG-3) ─────────
    # CREATE/DROP CONCURRENTLY cannot run inside a transaction. We
    # build the new descending index first, then drop the old ascending
    # one — never leaving the access path without an index, and never
    # taking a write-blocking lock on a hot table.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_flow_events_symbol_ts_desc "
            "ON flow_events (symbol, ts DESC)"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_flow_events_symbol_ts"
        )

    # ── D2: databento_api_keys.last_test_at ─────────────────────────────
    op.add_column(
        "databento_api_keys",
        sa.Column("last_test_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("databento_api_keys", "last_test_at")

    # Same rationale as upgrade: build the ascending replacement first,
    # then drop the descending one — both CONCURRENTLY.
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_flow_events_symbol_ts "
            "ON flow_events (symbol, ts)"
        )
        op.execute(
            "DROP INDEX CONCURRENTLY IF EXISTS ix_flow_events_symbol_ts_desc"
        )
