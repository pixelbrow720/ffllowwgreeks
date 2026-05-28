"""Rev 10 MIG-1 follow-up — validate the metric_type FK constraint added by 0013.

Migration 0013 (Rev 10 fix) adds the ``computed_metrics.metric_type`` FK with
``NOT VALID`` to avoid the ``AccessExclusiveLock`` validation scan during a
normal release. This migration runs the validation separately so operators
can schedule it during off-hours.

``ALTER TABLE ... VALIDATE CONSTRAINT`` only takes a
``ShareUpdateExclusiveLock`` on each chunk; writes (UPSERTs) and reads
proceed concurrently. The validation scan still costs CPU + IO proportional
to the live row count, so the recommendation is:

* Run during a low-traffic window (overnight in production timezone).
* Watch ``pg_stat_activity`` for the validation query if you want to see
  per-chunk progress.
* If 0013 already ran cleanly (orphans deleted, FK in place with NOT VALID),
  this migration is purely a metadata flip — once VALIDATE completes the
  catalog row flips ``convalidated = true`` and the constraint becomes
  trustable for query planner purposes.

If the FK is already validated (e.g. an existing deployment that re-ran
the original Rev 9 0013 with full validation), the VALIDATE here is a
fast no-op — PostgreSQL skips re-validation when ``convalidated`` is
already true.

Revision ID: 0014
Revises: 0013
Create Date: 2026-05-24 03:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_FK_NAME: str = "fk_computed_metrics_metric_type_registry"


def upgrade() -> None:
    # ShareUpdateExclusiveLock — concurrent with INSERT / UPDATE / DELETE
    # but blocks DDL on the same table. Safe to run during business hours
    # if the table is small; recommended off-hours for a multi-million-row
    # hypertable to keep IO budget for the live pipeline.
    op.execute(
        f"ALTER TABLE computed_metrics VALIDATE CONSTRAINT {_FK_NAME}"
    )


def downgrade() -> None:
    # No-op: there is no SQL primitive to "un-validate" a constraint
    # without dropping it. Dropping the FK belongs to the 0013 downgrade
    # path, which is reachable by rolling back through this revision
    # first. Leaving this empty is the canonical alembic pattern for
    # validation-only migrations.
    pass
