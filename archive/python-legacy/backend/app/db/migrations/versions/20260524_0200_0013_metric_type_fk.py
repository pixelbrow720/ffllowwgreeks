"""Rev 9 DT-6 — constrain ``computed_metrics.metric_type`` against the registry.

Plain Postgres-compatible (no TimescaleDB calls) and reversible.

This migration ships three coupled changes so the FK constraint cannot
fail to apply on a populated database:

1. **Backfill the metric-type registry** with every discriminator the
   chain pipeline currently emits. Migration 0005 seeded the registry
   with the metric types that existed at Rev 4 time; the Rev 4-onwards
   additions (``GEX_BACK_NET_TOTAL_VOL``, ``GEX_BACK_LEVEL_VOL``,
   ``CHARM_0DTE_NET_TOTAL``, ``CHARM_0DTE_LEVEL``, ``SPOT``) were never
   added — so a naive FK would fail closed on every existing row that
   carried one of those types.

2. **Batched data cleanup** — delete any ``computed_metrics`` row
   whose ``metric_type`` is unknown to the registry. The original Rev 9
   landing did this in a single statement; on a busy hypertable that
   competes with live UPSERTs for per-chunk row-locks. Rev 10 MIG-2
   converts the cleanup to a ``LIMIT 10000`` loop with a ``RAISE NOTICE``
   audit count so operators see exactly how many rows the migration
   dropped.

3. **Add the FK constraint with NOT VALID** — Rev 10 MIG-1: the
   original landing added the FK without ``NOT VALID``, which forced
   PostgreSQL into a full validation scan (``AccessExclusiveLock`` on
   every hypertable chunk). The fix is the standard two-step pattern:
   ``ADD CONSTRAINT ... NOT VALID`` here (cheap — only takes a brief
   ``ShareRowExclusiveLock`` while the catalog row is written), then a
   separate ``VALIDATE CONSTRAINT`` in migration 0014 that takes only
   ``ShareUpdateExclusiveLock`` and can run concurrently with writes.
   Migration 0014 should be applied during off-hours.

   ``ON UPDATE CASCADE`` so a future rename in the registry propagates;
   ``ON DELETE`` is intentionally ``NO ACTION`` (the default): removing
   a metric type from the registry should fail loudly if rows still
   carry it, not silently nuke the time-series.

Why FK over CHECK: the registry table already exists with the
discriminator as its primary key (so already UNIQUE), is the documented
catalogue per ``CLAUDE.md`` ("New metrics need an entry in
``metric_type_registry``"), and an FK lets new metric types be added by
INSERTing one row instead of editing a migration. ``ON UPDATE CASCADE``
preserves rename semantics if a discriminator is ever renamed.

----

Rev 12 MIG-14 — hypertable -> regular-table FK lookup cost
----------------------------------------------------------

The FK target ``metric_type_registry`` is a tiny lookup table (rows on
the order of dozens). PostgreSQL implements the per-row INSERT check
as an internal index probe against the registry's primary key, which on
a small B-tree resident in shared_buffers costs O(log N) measured in
**single-digit microseconds per row**. The chain pipeline writes a few
hundred rows to ``computed_metrics`` per tick, so the per-tick FK
overhead lives in the **sub-millisecond range** — well below the noise
floor of the existing ``_persist_metrics`` budget.

That estimate is the only justification we have without a staging
benchmark. Operators should treat it as such.

**Recommended monitoring** (closes the loop):
* Scrape ``flowgreeks_pipeline_partial_total`` and the lifespan-exposed
  per-stage timings; track the **p99 of ``_persist_metrics`` duration**
  via ``pipeline_runs.duration_ms`` joined to a per-stage breakdown.
* Alert if the post-0014 p99 of ``_persist_metrics`` **doubles**
  relative to the 7-day pre-0013 baseline, or exceeds the pipeline's
  60s budget headroom.
* Bound: at typical write rates (a few hundred rows/tick, every 60s)
  even a 50us-per-row FK lookup is ~25ms across a tick — the alert
  threshold above will fire long before this becomes the bottleneck.

**Rollback path** (if a measured regression materially exceeds the
budget):
* New migration ``00XX_drop_metric_type_fk.py`` issues
  ``ALTER TABLE computed_metrics DROP CONSTRAINT IF EXISTS
  fk_computed_metrics_metric_type_registry;`` and re-introduces the
  pre-0013 ``CHECK (metric_type IN (SELECT metric_type FROM
  metric_type_registry))`` semantics as a CHECK against a static
  enum, OR drops the constraint entirely and reverts catalog
  enforcement to the application layer (``EXPECTED_METRIC_TYPES``).
* The registry table itself stays — it remains useful for
  documentation and runtime catalog reads even without the FK.
* Drop is online; takes ``AccessExclusiveLock`` only briefly to
  remove the catalog row.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-24 02:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Metric types that ``EXPECTED_METRIC_TYPES`` declares but the migration-0005
# seed missed. Inserted with ON CONFLICT DO NOTHING so re-running the
# migration on a partially-populated registry is a no-op for already-present
# rows. Each entry mirrors the column shape used by the 0005 seed.
_REGISTRY_TOPUP: tuple[tuple[str, str, str, bool, str], ...] = (
    (
        "GEX_BACK_NET_TOTAL_VOL",
        "gex",
        "Aggregate back-month GEX (volume-weighted).",
        False,
        "rev4",
    ),
    (
        "GEX_BACK_LEVEL_VOL",
        "gex",
        "Per-strike back-month GEX (volume-weighted).",
        False,
        "rev4",
    ),
    (
        "CHARM_0DTE_NET_TOTAL",
        "0dte",
        "Aggregate dealer charm restricted to 0DTE contracts.",
        True,
        "rev4",
    ),
    (
        "CHARM_0DTE_LEVEL",
        "0dte",
        "Per-strike 0DTE charm curve point.",
        True,
        "rev4",
    ),
    (
        "SPOT",
        "spot",
        "Resolved spot snapshot (price + provenance in extra_json).",
        False,
        "rev4",
    ),
)

_FK_NAME: str = "fk_computed_metrics_metric_type_registry"

# MIG-9: Parameterised INSERT for the registry top-up. Replaces the
# original f-string interpolation. ``op.execute(sa.text(...).bindparams(...))``
# routes the values through the driver's parameter binding, eliminating any
# possibility of SQL injection from a future maintainer adding a value with
# an embedded apostrophe (the values are project-internal constants today,
# but the parameterised form is the right pattern regardless).
_REGISTRY_INSERT_SQL = sa.text(
    """
    INSERT INTO metric_type_registry
        (metric_type, category, description, is_0dte, added_in_rev)
    VALUES
        (:metric_type, :category, :description, :is_0dte, :added_in_rev)
    ON CONFLICT (metric_type) DO NOTHING
    """
)


def upgrade() -> None:
    # 1. Top up the registry so the FK validates against every metric_type
    #    the chain pipeline currently emits. MIG-9: parameterised binds
    #    instead of f-string interpolation.
    for metric_type, category, description, is_0dte, added_in_rev in _REGISTRY_TOPUP:
        op.execute(
            _REGISTRY_INSERT_SQL.bindparams(
                metric_type=metric_type,
                category=category,
                description=description,
                is_0dte=is_0dte,
                added_in_rev=added_in_rev,
            )
        )

    # 2. MIG-2: Batched cleanup of rows the FK would reject. The original
    #    landing did this single-shot, which on a busy hypertable can
    #    compete with live UPSERTs for per-chunk row-locks. The DO block
    #    deletes in 10k-row batches and emits a single ``RAISE NOTICE``
    #    with the audit count operators expected from the original
    #    docstring.
    op.execute(
        """
        DO $$
        DECLARE
            deleted_count integer := 0;
            total_deleted integer := 0;
        BEGIN
            LOOP
                DELETE FROM computed_metrics
                 WHERE ctid IN (
                    SELECT ctid FROM computed_metrics
                     WHERE metric_type NOT IN (
                         SELECT metric_type FROM metric_type_registry
                     )
                     LIMIT 10000
                 );
                GET DIAGNOSTICS deleted_count = ROW_COUNT;
                total_deleted := total_deleted + deleted_count;
                EXIT WHEN deleted_count = 0;
            END LOOP;
            RAISE NOTICE 'orphan computed_metrics rows deleted: %', total_deleted;
        END $$;
        """
    )

    # 3. MIG-1: Constrain ``computed_metrics.metric_type`` with NOT VALID.
    #    A normal ``ADD CONSTRAINT ... FOREIGN KEY`` triggers a full table
    #    validation scan that takes ``AccessExclusiveLock`` on every
    #    hypertable chunk and blocks pipeline writes for the duration.
    #    NOT VALID skips the scan; existing rows are assumed to satisfy
    #    the constraint (we just deleted the orphans above), and new
    #    writes are checked from this point forward. Migration 0014 then
    #    runs ``VALIDATE CONSTRAINT`` separately during off-hours, which
    #    only takes ``ShareUpdateExclusiveLock`` and runs concurrently
    #    with writes.
    op.execute(
        f"""
        ALTER TABLE computed_metrics
        ADD CONSTRAINT {_FK_NAME}
        FOREIGN KEY (metric_type)
        REFERENCES metric_type_registry (metric_type)
        ON UPDATE CASCADE
        NOT VALID
        """
    )


def downgrade() -> None:
    # Drop the FK first; leave the registry top-up rows in place. Removing
    # them on downgrade would just churn — they are harmless without the
    # constraint and re-running ``upgrade`` will re-issue the inserts via
    # ON CONFLICT DO NOTHING.
    op.execute(
        f"ALTER TABLE computed_metrics DROP CONSTRAINT IF EXISTS {_FK_NAME}"
    )
