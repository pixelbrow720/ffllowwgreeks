"""Drop public-auth tables (users, access_requests, user_sessions).

Project simplification - admin-only platform now.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-01 00:00:00
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401  (alembic convention)
from alembic import op


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("user_sessions")
    op.drop_table("access_requests")
    op.drop_table("users")


def downgrade() -> None:
    # Best-effort recreation. Refer to migration 0006 for the original schema.
    raise NotImplementedError(
        "Re-create the public-auth tables via migration 0006 if needed."
    )
