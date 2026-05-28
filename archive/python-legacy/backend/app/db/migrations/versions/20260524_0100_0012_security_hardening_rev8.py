"""Rev 8 security hardening — SEC-1, SEC-2, SEC-10.

Three changes ship together because the application code path landing
in the same release relies on all three being present:

* **SEC-1** — close the bcrypt-amplification DoS on legacy
  ``api_keys.key_lookup IS NULL`` rows. The keyed-BLAKE2b digest is
  computed from the *plaintext* key, which we never persisted; the
  bcrypt hash is one-way, so the digest cannot be back-derived.
  Therefore the migration deactivates every such row
  (``is_active=False``) — operators must regenerate any pre-0010 keys
  via ``POST /admin/api-keys`` before they can be used again. Once this
  migration runs, the auth path drops the prefix-scan fallback and
  becomes O(1).

* **SEC-2** — server-side JWT revocation. New ``jwt_revocations``
  table indexed by ``expires_at`` so a periodic prune can drop rows
  that can no longer be presented.

* **SEC-10** — soft-delete + admin audit trail. New
  ``api_keys.deleted_at`` column and ``admin_audit_events`` audit
  table.

Plain-Postgres compatible — no TimescaleDB calls.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-24 01:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── SEC-1: deactivate rows that cannot be migrated to the fast path ──
    # The keyed-BLAKE2b lookup digest is keyed by a project constant but
    # computed from the *plaintext* API key. The plaintext is not on
    # disk (only its bcrypt hash is), so the digest cannot be
    # back-derived from existing rows. We therefore mark every
    # ``key_lookup IS NULL`` row inactive so the post-migration code
    # path — which has dropped the prefix-scan fallback — refuses them
    # cleanly. Operators must regenerate any affected keys via
    # ``POST /admin/api-keys``.
    #
    # **Rev 10 MIG-5:** the original landing ran the UPDATE silently —
    # operators had no idea how many keys this migration just
    # deactivated. The DO block captures the row count and emits a
    # ``RAISE NOTICE`` so the alembic log surfaces the impact. See
    # ``OPS.md §2b — Lazy-backfill window`` for the pre-deploy operator
    # query that should return zero before this migration runs.
    op.execute(
        """
        DO $$
        DECLARE
            deactivated integer;
        BEGIN
            UPDATE api_keys
               SET is_active = FALSE
             WHERE key_lookup IS NULL
               AND is_active = TRUE;
            GET DIAGNOSTICS deactivated = ROW_COUNT;
            RAISE NOTICE 'legacy api_keys with NULL key_lookup deactivated: %', deactivated;
        END $$;
        """
    )

    # ── SEC-10: api_keys.deleted_at ─────────────────────────────────────
    op.add_column(
        "api_keys",
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # ── SEC-2: jwt_revocations table ────────────────────────────────────
    op.create_table(
        "jwt_revocations",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("jti", sa.Text(), nullable=False),
        sa.Column(
            "revoked_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
        ),
        sa.UniqueConstraint("jti", name="uq_jwt_revocations_jti"),
    )
    op.create_index(
        "ix_jwt_revocations_expires_at",
        "jwt_revocations",
        ["expires_at"],
    )

    # ── SEC-10: admin_audit_events table ────────────────────────────────
    op.create_table(
        "admin_audit_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "ts",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("actor_username", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column(
            "extra_json", sa.dialects.postgresql.JSONB(), nullable=True
        ),
    )
    op.create_index(
        "ix_admin_audit_events_ts",
        "admin_audit_events",
        [sa.text("ts DESC")],
    )
    op.create_index(
        "ix_admin_audit_events_action_ts",
        "admin_audit_events",
        ["action", sa.text("ts DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_admin_audit_events_action_ts", table_name="admin_audit_events"
    )
    op.drop_index(
        "ix_admin_audit_events_ts", table_name="admin_audit_events"
    )
    op.drop_table("admin_audit_events")

    op.drop_index(
        "ix_jwt_revocations_expires_at", table_name="jwt_revocations"
    )
    op.drop_table("jwt_revocations")

    op.drop_column("api_keys", "deleted_at")

    # SEC-1: re-activating previously NULL-key_lookup rows on downgrade
    # would re-open the bcrypt-amplification surface that the code-path
    # half of this fix closes. We deliberately leave them inactive — the
    # downgrade is for schema rollback, not credential restoration.
