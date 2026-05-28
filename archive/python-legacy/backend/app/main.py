"""FastAPI application entrypoint."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import urllib.parse
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from slowapi.errors import RateLimitExceeded
from sqlalchemy import delete, text

from app.api.deps import (
    _jwt_revocation_prune_loop,
    _usage_flush_loop,
    flush_usage_deltas,
    limiter,
)
from app.api.endpoints import (
    admin,
    data,
    flow,
    health,
    hiro,
    history,
    inspector,
    snapshot,
    stream,
)
from app.api.endpoints.stream import (
    shutdown_all_websockets,
    start_global_revocation_watcher,
    stop_global_revocation_watcher,
)
from app.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import is_default_admin_password, is_default_jwt_secret
from app.db.models import AdminAuditEvent
from app.db.session import dispose_engine, get_session_factory
from app.ingestion.bulk_writers import (
    get_flow_event_writer,
    get_futures_tick_writer,
    get_liquidity_snapshot_writer,
    get_options_trade_writer,
)
from app.ingestion.databento_eod_oi import run_eod_oi_ingestion
from app.ingestion.databento_globex import get_globex_live_ingester
from app.ingestion.databento_historical import (
    run_historical_backfill,
    run_historical_quotes_backfill,
)
from app.ingestion.databento_live import get_live_ingester
from app.ingestion.dlq import cleanup_dlq_older_than, get_dlq
from app.ingestion.writer import get_writer
from app.processing import move_tracker as move_tracker_mod
from app.processing.pipeline import orphan_sweep_loop, run_pipeline_for_symbol
from app.processing.scheduler import start_scheduler

logger = get_logger(__name__)


# ── Periodic-job intervals ─────────────────────────────────────────────────
# DLQ cleanup runs every 6h; admin audit prune runs daily. Module-level so
# tests can override via monkeypatch without poking lifespan internals.
_DLQ_CLEANUP_INTERVAL_S: float = 6 * 60 * 60.0
_ADMIN_AUDIT_PRUNE_INTERVAL_S: float = 24 * 60 * 60.0


def _testing_mode() -> bool:
    return os.getenv("PYTEST_CURRENT_TEST") is not None or os.getenv("APP_TESTING") == "1"


# ── Rev 9 DT-3: DLQ retention loop ──────────────────────────────────────────
async def _dlq_cleanup_loop() -> None:
    """Periodically prune ``dead_letter_queue`` rows past retention.

    Calls :func:`app.ingestion.dlq.cleanup_dlq_older_than` every
    ``_DLQ_CLEANUP_INTERVAL_S`` seconds with the operator-configured
    retention window. The cleanup primitive is itself defensive (logs +
    swallows DB errors) so a transient DB failure here just means the
    next tick retries — the loop never dies on its own.
    """
    settings = get_settings()
    while True:
        try:
            await asyncio.sleep(_DLQ_CLEANUP_INTERVAL_S)
            deleted = await cleanup_dlq_older_than(
                settings.ingestion_dlq_retention_days
            )
            if deleted:
                logger.info("dlq_cleanup_loop_pruned", rows=deleted)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("dlq_cleanup_loop_error")


# ── Rev 9 DT-4: admin_audit_events retention loop ──────────────────────────
async def _admin_audit_prune_loop() -> None:
    """Periodically delete ``admin_audit_events`` rows past retention.

    Default retention is 365 days (``ADMIN_AUDIT_RETENTION_DAYS``).
    Without this, the audit table grows monotonically — every key-delete
    + every future destructive admin action appends a row and nothing
    ever removes them. A non-positive retention is treated as a no-op
    so a misconfigured env can't accidentally truncate the table.
    """
    settings = get_settings()
    while True:
        try:
            await asyncio.sleep(_ADMIN_AUDIT_PRUNE_INTERVAL_S)
            retention_days = int(settings.admin_audit_retention_days)
            if retention_days <= 0:
                logger.info(
                    "admin_audit_prune_skipped_non_positive_retention",
                    retention_days=retention_days,
                )
                continue
            cutoff = datetime.now(UTC) - timedelta(days=retention_days)
            factory = get_session_factory()
            async with factory() as session:
                result = await session.execute(
                    delete(AdminAuditEvent).where(
                        AdminAuditEvent.ts < cutoff
                    )
                )
                await session.commit()
            deleted = int(getattr(result, "rowcount", 0) or 0)
            if deleted:
                logger.info(
                    "admin_audit_prune_complete",
                    deleted=deleted,
                    cutoff=cutoff.isoformat(),
                    retention_days=retention_days,
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("admin_audit_prune_loop_error")


# ── Rev 9 DT-5: Lifespan composition helpers ────────────────────────────────
#
# Each helper owns one phase of startup, returns a list of teardown
# callables (sync or async), and preserves the ordering documented at
# call site. Lifespan composes them in strict order:
#
#   observability → ingestion → pipeline_workers → observability_jobs
#
# Teardown is the reverse. Splitting like this keeps the lifespan
# context manager small and lets each phase be reasoned about (and
# unit-tested) in isolation.

# Cleanup callables run during shutdown. ``None`` results are filtered out.
TeardownFn = Callable[[], Awaitable[None] | None]


async def _start_observability(app: FastAPI) -> list[TeardownFn]:
    """Configure logging + production guardrails + orphan-sweep snapshot.

    Invariant: runs before *any* IO so log redaction is in place when
    the first ingestion connect attempt fires, and the production
    guardrails get a chance to abort startup before resources are
    allocated.

    No teardown actions: log filters live for the process lifetime and
    the orphan sweep is a one-shot UPDATE.
    """
    settings = get_settings()
    configure_logging(settings.log_level)
    _install_uvicorn_log_redaction()

    if not _testing_mode():
        if is_default_admin_password(settings.admin_password):
            raise RuntimeError(
                "ADMIN_PASSWORD is unset or default; refusing to start in production mode"
            )
        # SRE-10: catch a non-default-but-too-short admin password before
        # boot. ADMIN_PASSWORD is permitted to be a bcrypt hash starting
        # with ``$2`` — those are full credentials regardless of length.
        # Plaintext passwords < 12 chars are refused so the operator can
        # never accidentally ship a weak password just because they used
        # something other than ``changeme``.
        if not settings.admin_password.startswith("$2") and len(
            settings.admin_password
        ) < 12:
            raise RuntimeError(
                "ADMIN_PASSWORD plaintext must be >= 12 characters or "
                "supplied as a bcrypt hash ($2…)"
            )
        if is_default_jwt_secret(settings.jwt_secret):
            raise RuntimeError(
                "JWT_SECRET is unset or default; refusing to start in production mode"
            )
        # SRE-10: explicit length floor on JWT_SECRET. ``is_default_jwt_secret``
        # already covers most cases (it returns True for any secret <
        # 32 chars), but we re-check here so the failure message is
        # specific and easier to triage from logs.
        if len(settings.jwt_secret) < 32:
            raise RuntimeError(
                "JWT_SECRET must be at least 32 characters"
            )

    logger.info("startup", supported_symbols=settings.supported_symbols)

    # Pipeline-runs orphan sweep — a hard crash leaves stale ``running``
    # rows that would otherwise look like still-in-flight work to the
    # completeness checker. Best-effort; never blocks startup.
    try:
        factory = get_session_factory()
        async with factory() as s:
            res = await s.execute(
                text(
                    "UPDATE pipeline_runs SET status='aborted', "
                    "error='process restarted while running' "
                    "WHERE status='running' AND started_at < NOW() - INTERVAL '15 minutes'"
                )
            )
            await s.commit()
            logger.info(
                "pipeline_runs_orphan_sweep",
                rows=getattr(res, "rowcount", None),
            )
    except Exception:  # noqa: BLE001
        logger.exception("pipeline_runs_orphan_sweep_failed")

    return []


# ── Rev 10 SRE-7: single-instance advisory lock ────────────────────────────
# Lock key chosen to NOT collide with the per-migration alembic lock used
# by the migration env (Postgres advisory locks share a single 64-bit
# namespace per database). This key is held for the entire process
# lifetime; the connection that holds it is kept open in the engine pool
# until ``dispose_engine`` runs at lifespan teardown.
_INSTANCE_LOCK_KEY: int = 5_746_728_934_252


async def _acquire_single_instance_lock() -> None:
    """Refuse to boot if another backend already holds the advisory lock.

    Postgres ``pg_try_advisory_lock`` returns False when another session
    owns the lock; we treat that as "another replica is running" and
    abort. The operator can opt into multi-replica mode by setting
    ``ALLOW_MULTI_INSTANCE=true`` — see ``Settings.allow_multi_instance``
    for the trade-offs.

    The lock is session-scoped: keeping the connection in the pool keeps
    the lock held. ``dispose_engine`` releases it on shutdown. Skipped
    under ``APP_TESTING=1`` because the test harness pools share a DB
    and would deadlock the second test runner trying to start.
    """
    if _testing_mode():
        return
    settings = get_settings()
    if settings.allow_multi_instance:
        logger.warning(
            "single_instance_lock_skipped",
            reason="allow_multi_instance=true",
            hint=(
                "in-process state is not consistent across replicas; "
                "see Settings.allow_multi_instance"
            ),
        )
        return

    try:
        # Take a dedicated connection from the pool that the engine keeps
        # open for the lifetime of the process. We do NOT use ``async
        # with`` because that would release the connection (and the
        # lock) on exit. Instead, we open the connection, acquire the
        # lock, and intentionally leak the handle into a module-global
        # so the lock survives until ``dispose_engine``. Best-effort:
        # any failure here logs and falls through to startup so a flaky
        # DB doesn't strand the process forever.
        from app.db.session import get_engine

        engine = get_engine()
        conn = await engine.connect()
        result = await conn.execute(
            text("SELECT pg_try_advisory_lock(:k)"),
            {"k": _INSTANCE_LOCK_KEY},
        )
        got_lock = bool(result.scalar())
        if not got_lock:
            await conn.close()
            raise RuntimeError(
                "Another backend instance is already running "
                "(pg_advisory_lock contention). Set "
                "ALLOW_MULTI_INSTANCE=true to override (advanced)."
            )
        # Park the connection so the lock outlives this function. We
        # never call ``conn.close()`` here — engine disposal at
        # teardown handles it.
        global _instance_lock_conn
        _instance_lock_conn = conn
        logger.info("single_instance_lock_acquired", key=_INSTANCE_LOCK_KEY)
    except RuntimeError:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("single_instance_lock_acquire_failed")


_instance_lock_conn: object | None = None


async def _start_ingestion(
    app: FastAPI, settings: Settings
) -> tuple[list[asyncio.Task], list[TeardownFn]]:
    """Boot bulk writers + historical backfill + EOD OI + live ingesters.

    Invariant: writers must be flushing before any historical / live
    ingester emits records, and EOD-OI runs after the historical chain
    is in place so OI weights are available for the first compute tick.

    Returns ``(background_tasks, teardown_callables)``. Background tasks
    are owned by the lifespan and cancelled on shutdown alongside other
    phases' tasks.
    """
    tasks: list[asyncio.Task] = []
    teardown: list[TeardownFn] = []

    # Bulk-writer flush loops + DLQ flush + per-table writers.
    writer = get_writer()
    tasks.append(
        asyncio.create_task(writer.periodic_flush_loop(), name="writer_flush")
    )
    tasks.append(
        asyncio.create_task(get_dlq().periodic_flush_loop(), name="dlq_flush")
    )
    for w in (
        get_futures_tick_writer(),
        get_options_trade_writer(),
        get_flow_event_writer(),
        get_liquidity_snapshot_writer(),
    ):
        tasks.append(
            asyncio.create_task(
                w.periodic_flush_loop(),
                name=f"writer_flush_{w.model.__tablename__}",
            )
        )

    # Historical backfill: contract definitions → cmbp-1 NBBO snapshots.
    # Best-effort — graceful no-op if API key missing.
    registry: dict = {}
    try:
        registry = await run_historical_backfill()
    except Exception:  # noqa: BLE001
        logger.exception("historical_backfill_unhandled_error")
    try:
        await run_historical_quotes_backfill(registry)
    except Exception:  # noqa: BLE001
        logger.exception("historical_quotes_backfill_unhandled_error")

    # EOD Open Interest — gives walls/GEX real weights from session 1.
    try:
        inserted_oi = await run_eod_oi_ingestion()
        logger.info("eod_oi_startup_done", rows=inserted_oi)
    except Exception:  # noqa: BLE001
        logger.exception("eod_oi_startup_error")

    # Live ingesters last — they emit into writers that are already running.
    try:
        get_live_ingester().start()
    except Exception:  # noqa: BLE001
        logger.exception("live_ingestion_start_failed")
    try:
        get_globex_live_ingester().start()
    except Exception:  # noqa: BLE001
        logger.exception("globex_live_start_failed")

    async def _stop_live_ingesters() -> None:
        try:
            await get_live_ingester().stop()
        except Exception:  # noqa: BLE001
            logger.exception("live_ingester_stop_error")
        try:
            await get_globex_live_ingester().stop()
        except Exception:  # noqa: BLE001
            logger.exception("globex_ingester_stop_error")

    async def _final_writer_flush() -> None:
        try:
            await get_writer().flush()
        except Exception:  # noqa: BLE001
            logger.exception("final_flush_error")
        try:
            await get_dlq().flush()
        except Exception:  # noqa: BLE001
            logger.exception("dlq_final_flush_error")
        for w in (
            get_futures_tick_writer(),
            get_options_trade_writer(),
            get_flow_event_writer(),
            get_liquidity_snapshot_writer(),
        ):
            try:
                await w.flush()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "final_bulk_flush_error", table=w.model.__tablename__
                )

    teardown.append(_stop_live_ingesters)
    teardown.append(_final_writer_flush)
    return tasks, teardown


async def _start_pipeline_workers(
    app: FastAPI, settings: Settings
) -> tuple[list[asyncio.Task], list[TeardownFn], object]:
    """Hydrate move-tracker, force startup ticks, then start the scheduler.

    Invariant: move-tracker must be hydrated *before* the forced startup
    tick so ``move_tracker.realized_move`` doesn't fall back to an
    overnight stale tick on a mid-session restart. The scheduler starts
    last so its first interval doesn't race the forced tick.

    Returns ``(background_tasks, teardown_callables, scheduler)`` —
    ``scheduler`` may be ``None`` if scheduler startup failed.
    """
    tasks: list[asyncio.Task] = []
    teardown: list[TeardownFn] = []

    try:
        await move_tracker_mod.hydrate_session_open_prices_from_db(
            list(settings.supported_symbols)
        )
    except Exception:  # noqa: BLE001
        logger.exception("session_open_hydrate_failed")

    # Force one pipeline tick per symbol so ``/last-close`` has data
    # immediately even when the RTH gate is closed. Per-symbol guard
    # keeps one failure from killing the rest.
    for symbol in settings.supported_symbols:
        try:
            result = await run_pipeline_for_symbol(symbol)
            logger.info(
                "startup_pipeline_tick_done",
                symbol=symbol,
                has_result=result is not None,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "startup_pipeline_tick_error", symbol=symbol
            )

    scheduler = None
    try:
        scheduler = start_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("scheduler_start_failed")

    if scheduler is not None:
        captured_scheduler = scheduler

        async def _stop_scheduler() -> None:
            try:
                captured_scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                logger.exception("scheduler_shutdown_error")

        teardown.append(_stop_scheduler)

    return tasks, teardown, scheduler


async def _start_observability_jobs(
    app: FastAPI,
) -> tuple[list[asyncio.Task], list[TeardownFn]]:
    """Start background loops: usage flush, JWT prune, orphan sweep, DLQ + audit cleanup, global WS revocation watcher.

    Invariant: the global revocation watcher boots eagerly so
    ``/admin/metrics`` can introspect the task name during smoke tests
    even before the first WS subscriber connects. Periodic loops are
    independent — failure to start one never blocks the others.
    """
    tasks: list[asyncio.Task] = []
    teardown: list[TeardownFn] = []

    tasks.append(
        asyncio.create_task(_usage_flush_loop(), name="api_key_usage_flush")
    )
    tasks.append(
        asyncio.create_task(
            _jwt_revocation_prune_loop(), name="jwt_revocation_prune"
        )
    )
    # Rev 8 ARCH-2 — in-process orphan sweep alongside the scheduler.
    tasks.append(
        asyncio.create_task(orphan_sweep_loop(), name="pipeline_orphan_sweep")
    )
    # Rev 9 DT-3 — DLQ retention.
    tasks.append(
        asyncio.create_task(_dlq_cleanup_loop(), name="dlq_cleanup_loop")
    )
    # Rev 9 DT-4 — admin_audit_events retention.
    tasks.append(
        asyncio.create_task(
            _admin_audit_prune_loop(), name="admin_audit_prune_loop"
        )
    )

    # Rev 8 ARCH-6 — single global WS revocation watcher.
    try:
        await start_global_revocation_watcher()
    except Exception:  # noqa: BLE001
        logger.exception("global_revocation_watcher_start_failed")

    async def _stop_revocation_watcher() -> None:
        try:
            await stop_global_revocation_watcher()
        except Exception:  # noqa: BLE001
            logger.exception("global_revocation_watcher_stop_error")

    teardown.append(_stop_revocation_watcher)
    return tasks, teardown


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    # Phase 1: observability + production guardrails + crashed-tick reaper.
    await _start_observability(app)

    # Phase 1b (Rev 10 SRE-7): single-instance advisory lock. Skipped
    # under APP_TESTING=1; can be opted out via ALLOW_MULTI_INSTANCE=true.
    await _acquire_single_instance_lock()

    background_tasks: list[asyncio.Task] = []
    teardown_callables: list[TeardownFn] = []

    if not _testing_mode():
        # Phase 2: writers + historical backfill + EOD OI + live ingesters.
        ing_tasks, ing_teardown = await _start_ingestion(app, settings)
        background_tasks.extend(ing_tasks)
        teardown_callables.extend(ing_teardown)

        # Phase 3: hydrate registries + force startup tick + scheduler.
        pw_tasks, pw_teardown, _scheduler = await _start_pipeline_workers(
            app, settings
        )
        background_tasks.extend(pw_tasks)
        teardown_callables.extend(pw_teardown)

        # Phase 4: periodic observability + retention loops + WS watcher.
        obs_tasks, obs_teardown = await _start_observability_jobs(app)
        background_tasks.extend(obs_tasks)
        teardown_callables.extend(obs_teardown)

    try:
        yield
    finally:
        logger.info("shutdown")
        # Rev 10 SRE-6: close every live WS with code 1012 BEFORE we
        # tear down the engine / cancel pump tasks so clients see a
        # clean protocol-level signal to reconnect after the restart
        # rather than an ungraceful TCP drop.
        try:
            await shutdown_all_websockets()
        except Exception:  # noqa: BLE001
            logger.exception("shutdown_all_websockets_failed")

        # Teardown in reverse order so the most recently started phase
        # tears down first — symmetric with how layered acquires
        # naturally release.
        for cleanup in reversed(teardown_callables):
            try:
                result = cleanup()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001
                logger.exception("lifespan_teardown_callable_error")

        for t in background_tasks:
            t.cancel()
        for t in background_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

        # Rev 10 SRE-8: final best-effort usage-delta drain. The
        # ``_usage_flush_loop`` already drains on its own
        # ``CancelledError`` branch, but a hard ``kill -9`` skips the
        # cancellation handler entirely. Running one more flush here
        # closes the gap between the loop's last sleep and the
        # cancellation, maximising drain on a graceful restart and
        # making it cheaper to lose data on a crash. Failures are
        # swallowed — a teardown shouldn't poison shutdown logs.
        try:
            factory = get_session_factory()
            async with factory() as session:
                drained = await flush_usage_deltas(session)
            if drained:
                logger.info("final_usage_delta_drain_complete", rows=drained)
        except Exception as exc:  # noqa: BLE001
            logger.warning("final_usage_delta_drain_failed", error=str(exc))

        await dispose_engine()


class _BodySizeLimitMiddleware:
    """Reject requests whose body exceeds ``settings.max_request_body_bytes``.

    Rev 8 SEC-4: every JSON-bodied route in this app is well under
    64 KiB. Without a hard cap, a malicious client can post megabytes
    of JSON, forcing the FastAPI request parser through them before
    any authn / Pydantic check runs. Implemented as pure-ASGI so it
    composes with httpx ``ASGITransport`` under tests (same constraint
    that already keeps SlowAPI's middleware out of this app).

    WebSocket / SSE streams are exempt — they have no fixed body and
    flow control is per-frame.
    """

    def __init__(self, app, *, max_size: int):
        self.app = app
        self._max_size = int(max_size)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Cheap path: if the client advertised a content-length, reject
        # over-large payloads before reading any body bytes.
        for k, v in scope.get("headers", []):
            if k.lower() == b"content-length":
                try:
                    declared = int(v)
                except ValueError:
                    declared = -1
                if declared > self._max_size:
                    await self._reject_413(send)
                    return
                break

        # Defensive path: cap streamed bodies. ``content-length`` is
        # not mandatory (chunked transfer encoding, missing header), so
        # we count bytes as we proxy ``http.request`` messages and
        # short-circuit when we cross the cap.
        seen = 0
        max_size = self._max_size
        rejected = False

        async def receive_capped():
            nonlocal seen, rejected
            message = await receive()
            if rejected:
                return message
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if body:
                    seen += len(body)
                    if seen > max_size:
                        rejected = True
                        await self._reject_413(send)
                        # Drain quietly so upstream ``await receive()``
                        # callers don't hang on the next byte.
                        return {"type": "http.disconnect"}
            return message

        await self.app(scope, receive_capped, send)

    @staticmethod
    async def _reject_413(send) -> None:
        body = json.dumps({"detail": "Request body too large"}).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class _AcceptVersionMiddleware:
    """Read ``Accept-Version`` header, log it, expose on ``request.state``.

    Rev 12 BC-13: ``API_POLICY.md`` § 5 reserves the ``Accept-Version``
    header for v1.2+ behavioural toggles within the same major. This
    middleware does NOT branch behaviour on the value — it only
    captures it for telemetry so we can measure adoption before the
    first real toggle ships, AND so partner integrations can begin
    echoing the header against pre-Rev-12 servers without breaking
    against Rev-12+ servers that start interpreting it.

    Pure ASGI (not BaseHTTPMiddleware) for the same httpx-ASGITransport
    composability reason as the security-headers middleware. Skipped
    for non-HTTP scopes (WS, lifespan).
    """

    _HEADER_NAME: bytes = b"accept-version"

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        accept_version: str | None = None
        for k, v in scope.get("headers", []):
            if k.lower() == self._HEADER_NAME:
                try:
                    accept_version = v.decode("ascii", errors="replace")
                except Exception:  # noqa: BLE001
                    accept_version = None
                break

        # Stash on the ASGI state so route handlers / future toggles
        # can introspect without re-parsing the headers.
        if accept_version is not None:
            state = scope.setdefault("state", {})
            state["accept_version"] = accept_version
            # Log at debug so an operator can flip telemetry on without
            # drowning structured logs at INFO. The structlog processor
            # in core.logging redacts well-known sensitive keys; this
            # one is operator-visible by design.
            logger.debug(
                "accept_version_header",
                accept_version=accept_version,
                path=scope.get("path", ""),
            )

        await self.app(scope, receive, send)


class _SecurityHeadersMiddleware:
    """Inject conservative security headers on every HTTP response.

    Implemented as a pure-ASGI middleware (not Starlette's
    ``BaseHTTPMiddleware``) so it composes cleanly with httpx
    ``ASGITransport`` under tests — the same constraint that already
    keeps SlowAPI's middleware out of this app.

    Headers are only added when the underlying response did not already
    set them, so per-route overrides keep working.
    """

    _BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
        (
            b"strict-transport-security",
            b"max-age=63072000; includeSubDomains; preload",
        ),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"x-frame-options", b"DENY"),
        (
            b"permissions-policy",
            b"camera=(), microphone=(), geolocation=(), interest-cohort=()",
        ),
    )

    # Tight CSP for the HTML surfaces FastAPI renders itself
    # (``/docs``, ``/redoc``). The JSON API itself does not execute
    # script in a browser context, but those Swagger / ReDoc pages do —
    # so we lock script + style to self + the well-known CDNs that
    # FastAPI serves from, and disable plugins / framing entirely. JSON
    # responses get a stricter "deny everything" CSP because they should
    # never be interpreted as an HTML document.
    _HTML_CSP: bytes = (
        b"default-src 'self'; "
        b"script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        b"style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        b"img-src 'self' data: https://fastapi.tiangolo.com; "
        b"font-src 'self' data: https://cdn.jsdelivr.net; "
        b"connect-src 'self'; "
        b"frame-ancestors 'none'; "
        b"object-src 'none'; "
        b"base-uri 'self'"
    )
    _JSON_CSP: bytes = (
        b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    )

    # Paths that legitimately need the relaxed HTML CSP (Swagger / ReDoc
    # render inline JS + CSS). Any other HTML response gets the strict
    # JSON CSP — closing the door on a future static page silently
    # inheriting Swagger's relaxation.
    _HTML_CSP_PATHS: frozenset[str] = frozenset(
        {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_html_csp_path = path in self._HTML_CSP_PATHS

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {k.lower() for k, _ in headers}
                for k, v in self._BASE_HEADERS:
                    if k not in existing:
                        headers.append((k, v))
                if b"content-security-policy" not in existing:
                    content_type = b""
                    for k, v in headers:
                        if k.lower() == b"content-type":
                            content_type = v.lower()
                            break
                    # Only relax CSP for the explicit Swagger/ReDoc path
                    # set AND when the response is HTML — both conditions
                    # must hold. Anything else (custom error pages,
                    # future static surface) gets the strict JSON CSP.
                    # ``startswith`` (not ``in``) so an attacker-controlled
                    # ``Content-Type`` containing the literal substring
                    # ``text/html`` cannot trip the relaxed CSP — defence
                    # in depth on top of the path allowlist.
                    if is_html_csp_path and content_type.startswith(b"text/html"):
                        headers.append(
                            (b"content-security-policy", self._HTML_CSP)
                        )
                    else:
                        headers.append(
                            (b"content-security-policy", self._JSON_CSP)
                        )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# ── Log redaction ────────────────────────────────────────────────────────────
#
# SSE / WebSocket auth tokens are passed in the query string because
# EventSource / browser WebSocket clients cannot set custom headers.
# Uvicorn's default access logger writes the full request line (including
# the query string) to stdout, which would expose those tokens to anyone
# who can read container logs. We install a logging filter on the
# uvicorn loggers that scrubs ``token=...`` and ``key=...`` query
# parameters before the line reaches a handler.

_SENSITIVE_QUERY_KEYS = (
    "token",
    "key",
    "code",
    "state",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "password",
    "client_secret",
    "bot_token",
)
_REDACTED = "REDACTED"
_QUERY_REDACT_RE = re.compile(
    r"([?&](?:" + "|".join(_SENSITIVE_QUERY_KEYS) + r")=)[^&\s\"]+",
    re.IGNORECASE,
)
_AUTH_HEADER_RE = re.compile(
    r"(authorization\s*:\s*\S+\s+)\S+", re.IGNORECASE
)


def _redact(value: str) -> str:
    # Pre-decode the query string before pattern matching so percent-
    # escaped variants of sensitive keys (``%6Bey=``, ``Auth%6Frization:``)
    # are caught the same as their plaintext form (Rev 8 SEC-8). The
    # decode is best-effort: malformed percent-escapes pass through to
    # the regex unchanged so the redactor never raises and never lets a
    # log line through unmodified by virtue of crashing first.
    try:
        decoded = urllib.parse.unquote(value)
    except Exception:  # noqa: BLE001
        decoded = value
    decoded = _QUERY_REDACT_RE.sub(rf"\1{_REDACTED}", decoded)
    decoded = _AUTH_HEADER_RE.sub(rf"\1{_REDACTED}", decoded)
    return decoded


class _RedactSensitiveQueryFilter(logging.Filter):
    """Strip auth tokens out of stdlib log records before emission.

    Targeted at uvicorn's access logger but safe to attach broadly: the
    filter only rewrites the formatted message and known string args, so
    structured (``structlog``) records pass through unchanged.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        try:
            if isinstance(record.msg, str):
                record.msg = _redact(record.msg)
            if record.args:
                if isinstance(record.args, tuple):
                    record.args = tuple(
                        _redact(a) if isinstance(a, str) else a
                        for a in record.args
                    )
                elif isinstance(record.args, dict):
                    record.args = {
                        k: (_redact(v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
        except Exception:  # noqa: BLE001 - never let the filter break logging
            return True
        return True


def _install_uvicorn_log_redaction() -> None:
    """Attach :class:`_RedactSensitiveQueryFilter` to the relevant loggers."""
    redact = _RedactSensitiveQueryFilter()
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", ""):
        target = logging.getLogger(name)
        # Avoid stacking duplicate filters across reloads.
        if not any(isinstance(f, _RedactSensitiveQueryFilter) for f in target.filters):
            target.addFilter(redact)


def create_app() -> FastAPI:
    settings = get_settings()
    docs_enabled = settings.enable_openapi_docs
    app = FastAPI(
        title="Options Flow Analytics Platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)
    # Note: we deliberately do NOT add SlowAPIMiddleware. It is based on
    # Starlette's BaseHTTPMiddleware which is incompatible with httpx
    # ASGITransport + anyio task groups. The @limiter.limit decorators on
    # individual routes still enforce limits; the middleware only adds extra
    # response headers we don't depend on.
    cors_origins = settings.admin_cors_origin_list or ["http://localhost:3000"]
    use_wildcard = "*" in cors_origins
    if not settings.admin_cors_origin_list and not _testing_mode():
        # Empty ``ADMIN_CORS_ORIGINS`` used to silently fall back to ``["*"]``,
        # which is unsafe with credentialed endpoints. Refuse the wildcard
        # default and pin to a sane localhost for dev.
        logger.warning(
            "admin_cors_origins_unset_using_localhost_default",
            default=cors_origins,
            hint="set ADMIN_CORS_ORIGINS to explicit origins in production",
        )
    app.add_middleware(
        CORSMiddleware,
        # Production deployments should set ``ADMIN_CORS_ORIGINS`` to
        # explicit origins. When the env var is empty we default to a
        # localhost dev origin rather than wildcard. ``allow_credentials``
        # MUST be False whenever the origin list contains ``*`` because
        # browsers refuse the combination.
        allow_origins=cors_origins,
        allow_credentials=not use_wildcard,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key"],
        expose_headers=["Content-Type", "Cache-Control"],
        max_age=600,
    )
    # GZip compression for any response >= 1KB. Snapshot / inspector
    # payloads are routinely 5–50 KB JSON and compress to a fraction of
    # that, dramatically reducing bandwidth on the public Cloudflare
    # tunnel and improving TTFB for browser clients. Streaming
    # (text/event-stream) responses already set ``Cache-Control: no-cache``
    # and Starlette's GZipMiddleware skips them by virtue of the
    # incremental body iterator.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    # Lightweight security-headers middleware. Pure ASGI (not
    # BaseHTTPMiddleware) so it stays compatible with httpx + anyio task
    # groups under tests. Defense-in-depth: even though an admin client
    # also injects these via its host for its own surface, the API itself
    # serves error pages and OpenAPI docs that benefit from the same
    # baseline guarantees.
    app.add_middleware(_SecurityHeadersMiddleware)

    # Rev 12 BC-13: capture the ``Accept-Version`` request header for
    # telemetry. No-op behaviourally; reserved for v1.2+ toggles
    # (``API_POLICY.md`` § 5).
    app.add_middleware(_AcceptVersionMiddleware)

    # Body-size cap. Added LAST so it executes FIRST (Starlette wraps in
    # reverse order) — a 100 MB POST is rejected before any other
    # middleware allocates memory for it. Configurable via
    # ``MAX_REQUEST_BODY_BYTES`` env (default 64 KiB).
    app.add_middleware(
        _BodySizeLimitMiddleware, max_size=settings.max_request_body_bytes
    )

    app.include_router(health.router)
    # Agent 5 streaming surface — registered BEFORE the broader data router so
    # the comprehensive snapshot in ``snapshot.py`` takes precedence over the
    # narrower legacy ``/v1/{symbol}/snapshot`` route registered by
    # ``data.py``. Route order matters: Starlette matches in declaration order.
    app.include_router(snapshot.router)
    app.include_router(stream.router)
    app.include_router(flow.router)
    app.include_router(hiro.router)
    # Rev 13 FE-2: ``GET /v1/{symbol}/history`` — bucketed time-series
    # over ``computed_metrics``. Registered before ``data.router`` for
    # the same precedence reason as above (data.py registers a
    # catch-all ``/v1/{symbol}`` family).
    app.include_router(history.router)
    app.include_router(data.router)
    app.include_router(admin.router)
    app.include_router(inspector.router)
    return app


def _rate_limit_handler(request, exc: RateLimitExceeded):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.detail}"},
    )


app = create_app()
