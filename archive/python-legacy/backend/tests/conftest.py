"""Shared pytest fixtures.

The DB-backed tests (API + admin) require a Postgres instance. We use one of:

  1. ``TEST_DATABASE_URL`` env var if set — must be a postgresql+asyncpg URL.
  2. Otherwise, a Postgres testcontainer (when ``testcontainers`` is installed
     and a Docker daemon is reachable).
  3. If neither is available, DB-backed tests are skipped automatically.

Pure-function tests (processing, security primitives) do NOT depend on the DB
and always run.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

os.environ.setdefault("APP_TESTING", "1")
os.environ.setdefault("DATABENTO_API_KEY", "")
os.environ.setdefault("DATABENTO_API_KEY_OPRA", "")
os.environ.setdefault("DATABENTO_API_KEY_GLOBEX", "")
os.environ.setdefault("DISABLE_LIVE_INGESTION", "true")
os.environ.setdefault("DISABLE_HISTORICAL_BACKFILL", "true")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("SUPPORTED_SYMBOLS", "SPXW,NDXP")


def _get_postgres_url_from_env() -> str | None:
    return os.getenv("TEST_DATABASE_URL")


def _try_start_testcontainer() -> str | None:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        return None
    try:
        container = PostgresContainer("postgres:15-alpine")
        container.start()
    except Exception:  # noqa: BLE001 -- Docker not available
        return None
    sync_url = container.get_connection_url()
    # PostgresContainer returns "postgresql+psycopg2://..." or "postgresql://..." depending on version.
    async_url = sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    pytest._postgres_container = container  # type: ignore[attr-defined]  # keep alive
    return async_url


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def database_url() -> AsyncIterator[str | None]:
    url = _get_postgres_url_from_env() or _try_start_testcontainer()
    yield url
    container = getattr(pytest, "_postgres_container", None)
    if container is not None:
        try:
            container.stop()
        except Exception:  # noqa: BLE001
            pass


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def engine_for_tests(database_url: str | None):
    if database_url is None:
        pytest.skip("Postgres not available; set TEST_DATABASE_URL or install Docker.")
    os.environ["DATABASE_URL"] = database_url

    from app.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    from app.db import models  # noqa: F401 - register models
    from app.db.session import Base, dispose_engine, get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await dispose_engine()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(engine_for_tests):
    from app.db.session import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def app_client(engine_for_tests):
    """HTTP client bound to the FastAPI app with DB sessions overridden."""
    import httpx

    from app.db.session import get_db
    from app.main import create_app

    app = create_app()

    async def _override_get_db():
        from app.db.session import get_session_factory
        async with get_session_factory()() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# ── TQ-4 (Rev 9): module-state reset between tests ──────────────────────────
# Several modules cache state at import time (basis EMA, session-open prices,
# snapshot cache, key-revocation subscribers, usage counters, BSM clip count,
# alert pipeline last-payload, flip-speed, streaming-publish failure counter,
# pending session-open capture, HIRO incremental cache). Without an explicit
# reset between tests the order of execution can leak state across files —
# e.g. a Tuesday-cached expiration set surviving into a non-Tue test flips
# ``is_expiration_day``, or a stale snapshot cache short-circuits a fresh
# build_snapshot_payload_single_flight call. The fixture below clears every
# known holder after each test. Each entry is wrapped in try/except so a
# rename or removed attribute does not break the suite.


def _clear_dict(attr: object) -> None:
    if attr is None:
        return
    try:
        attr.clear()  # type: ignore[attr-defined]
    except (AttributeError, TypeError):
        pass


def _set_zero(mod: object, name: str) -> None:
    try:
        setattr(mod, name, 0)
    except Exception:  # noqa: BLE001
        pass


def _reset_module_state() -> None:
    """Reset every known module-state holder. Robust to renames / removals."""

    # (module_path, attribute_name, action)
    # action is one of:  "clear" (call .clear()), "zero" (setattr 0), "none"
    # (setattr None — for module-level scalar caches like the /ready 5s cache)
    targets: list[tuple[str, str, str]] = [
        ("app.processing.session", "_AVAILABLE_EXPIRATIONS", "clear"),
        ("app.processing.move_tracker", "_SESSION_OPEN_PRICES", "clear"),
        ("app.api.endpoints.snapshot", "_snapshot_cache", "clear"),
        ("app.api.endpoints.stream", "_KEY_REVOCATION_SUBSCRIBERS", "clear"),
        ("app.api.endpoints.stream", "_LIVE_WEBSOCKETS", "clear"),
        ("app.api.deps", "_USAGE_DELTA", "clear"),
        ("app.api.deps", "_USAGE_LAST_SEEN", "clear"),
        ("app.processing.bsm", "_clipped_count", "zero"),
        ("app.processing.alert_pipeline", "_LAST_PAYLOAD", "clear"),
        ("app.processing.pipeline", "_flip_speed_cache", "clear"),
        ("app.processing.pipeline", "_streaming_publish_failures", "clear"),
        ("app.processing.pipeline", "_pending_session_open_capture", "clear"),
        ("app.processing.spot", "_basis_cache", "clear"),
        ("app.processing.spot", "_last_spot_cache", "clear"),
        # Rev 11 DR-23: per-symbol futures contract + post-roll annotation
        # budget. Without explicit reset, a roll detected in one test would
        # leak into the next test's resolve_spot call.
        ("app.processing.spot", "_basis_contract", "clear"),
        ("app.processing.spot", "_basis_post_roll_remaining", "clear"),
        ("app.processing.flow_pipeline", "_hiro_state", "clear"),
        # Rev 11 SRE-3: 5s readiness probe cache. Two scalars, both nulled
        # via the public test helper so the lock is released cleanly.
        ("app.api.endpoints.health", "_ready_cache", "none"),
        ("app.api.endpoints.health", "_ready_cache_expires_at", "none"),
    ]

    for module_name, attr_name, action in targets:
        try:
            mod = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - module may not exist in all builds
            continue
        attr = getattr(mod, attr_name, None)
        if attr is None and action != "zero" and action != "none":
            continue
        try:
            if action == "clear":
                _clear_dict(attr)
            elif action == "zero":
                _set_zero(mod, attr_name)
            elif action == "none":
                setattr(mod, attr_name, None)
        except Exception:  # noqa: BLE001
            pass

    # Pipeline counters — reset via the public test helper if available so we
    # also catch any Rev 9+ counters added without updating this list.
    try:
        pipeline_mod = importlib.import_module("app.processing.pipeline")
        helper = getattr(pipeline_mod, "reset_pipeline_counters_for_tests", None)
        if helper is not None:
            helper()
    except Exception:  # noqa: BLE001
        pass

    # Rev 12 SRE-19: pipeline runtime flags (paused + skipped calculator set)
    # are module-level globals guarded by a threading.Lock. Without an
    # explicit reset, an admin pause flag set by one test would persist
    # into the next and silently downgrade unrelated pipeline runs to
    # ``status=partial`` with ``error=pipeline_paused=True``.
    try:
        runtime_flags_mod = importlib.import_module(
            "app.processing.pipeline_runtime_flags"
        )
        runtime_flags_mod.reset_for_tests()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture(autouse=True)
def reset_module_state():
    """Function-scoped autouse fixture that wipes module-level caches.

    Runs AFTER every test so the next test starts with a clean slate. Also
    runs once before the very first test to guard against import-time state
    that may have leaked from collection.
    """
    _reset_module_state()
    yield
    _reset_module_state()


# ── TQ-4 sentinel tests ─────────────────────────────────────────────────────
# Two tests that confirm the fixture actually wipes state. Test ordering is
# not guaranteed but each function-scoped reset means even if the polluter
# runs LAST the next test still starts clean.


def test_tq4_state_reset_pollutes_session_cache() -> None:
    """Sentinel: pollute a known module cache. The reset fixture must wipe
    this before the next test runs."""
    from datetime import date as _date

    from app.processing.session import _AVAILABLE_EXPIRATIONS
    _AVAILABLE_EXPIRATIONS["TQ4_SENTINEL"] = frozenset({_date(2099, 1, 1)})
    assert "TQ4_SENTINEL" in _AVAILABLE_EXPIRATIONS


def test_tq4_state_reset_clears_session_cache() -> None:
    """Sentinel: assert the cache from the polluter test is gone."""
    from app.processing.session import _AVAILABLE_EXPIRATIONS
    assert "TQ4_SENTINEL" not in _AVAILABLE_EXPIRATIONS



