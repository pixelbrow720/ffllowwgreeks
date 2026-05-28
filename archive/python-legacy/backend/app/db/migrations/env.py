"""Alembic environment configured for the async SQLAlchemy engine.

**Rev 10 MIG-13 advisory-lock guard:** alembic auto-runs on backend
container startup (per ``Dockerfile`` CMD). In a multi-replica
deployment this means N replicas race to ``alembic upgrade head`` at
boot — two of them grabbing different versions of the
``alembic_version`` row, one clobbering the other, both running DDL
that's only safe to run once.

The fix is a session-level Postgres advisory lock acquired before
``context.run_migrations()`` and released after. Replicas that lose the
race block on the lock until the leader finishes; they then re-read
``alembic_version`` and (typically) become a fast no-op since the
schema is already at head.

The lock key is a stable 64-bit integer derived from the project name
so it does not collide with any other advisory locks the application
may use at runtime. ``5746728934251`` is the chosen value — pick a
fresh constant if forking this codebase.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import models  # noqa: F401  (ensure models are imported)
from app.db.session import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


# Stable 64-bit advisory-lock key for migration coordination across
# replicas. Project-scoped (chosen at random for flowgreeks-engine);
# fork it if you reuse this env.py in another codebase. ``pg_advisory_lock``
# accepts a single ``bigint``; this constant fits in the signed-bigint
# range so we don't need the two-int variant.
_MIGRATION_LOCK_KEY: int = 5746728934251


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Rev 10 MIG-13: acquire a session-level advisory lock so multiple
    # replicas booting simultaneously serialise their migration runs
    # instead of racing on ``alembic_version``. ``pg_advisory_lock`` is
    # session-scoped (held until ``pg_advisory_unlock`` or session end)
    # and reentrant for the same session. Loser replicas block here
    # until the leader commits and releases.
    connection.execute(
        sa.text("SELECT pg_advisory_lock(:key)").bindparams(
            key=_MIGRATION_LOCK_KEY
        )
    )
    try:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    finally:
        # Best-effort release. If this fails (connection already torn
        # down on error), the lock is dropped automatically when the
        # session closes — Postgres guarantees that.
        try:
            connection.execute(
                sa.text("SELECT pg_advisory_unlock(:key)").bindparams(
                    key=_MIGRATION_LOCK_KEY
                )
            )
        except Exception:
            pass


async def run_async_migrations() -> None:
    connectable = create_async_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
