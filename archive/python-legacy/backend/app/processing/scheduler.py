"""APScheduler wiring for periodic metric recomputation.

Rev 3 hardening (Agent 7):

* Symbols within a single scheduler tick are now processed concurrently via
  :func:`asyncio.gather` so a slow / failing symbol does not block the rest
  of the universe. Concurrency is bounded by a semaphore (default 4) so we
  don't stampede the DB connection pool.
* Every exception that escapes a tick coroutine is caught and logged — the
  scheduler thread is never allowed to die. Failed runs are also recorded
  in ``pipeline_runs`` (status='failed') by ``run_pipeline_for_symbol``
  itself.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.core.logging import get_logger
from app.ingestion.databento_eod_oi import run_eod_oi_ingestion
from app.processing.alert_pipeline import run_alert_pipeline
from app.processing.flow_pipeline import run_flow_pipeline
from app.processing.pipeline import (
    finalize_session,
    reset_session_state,
    run_pipeline_for_symbol,
)
from app.processing.session import is_half_day, is_rth_now

logger = get_logger(__name__)


# Maximum number of symbols processed concurrently within a single scheduler
# tick. Bounded so a wide universe doesn't exhaust the DB connection pool —
# each symbol takes ~3 short-lived sessions (load → persist → completeness),
# so 4 in flight ≈ 12 connections worst case.
DEFAULT_SYMBOL_CONCURRENCY: int = 4


class PipelineRunState:
    """Tracks the most recent successful pipeline run per symbol."""

    def __init__(self) -> None:
        self.last_run: dict[str, datetime] = {}
        self.last_duration_ms: dict[str, float] = {}

    def record(self, symbol: str, ts: datetime, duration_ms: float) -> None:
        self.last_run[symbol] = ts
        self.last_duration_ms[symbol] = duration_ms


_state = PipelineRunState()


def get_pipeline_state() -> PipelineRunState:
    return _state


def _parse_hhmm(value: str, default: tuple[int, int]) -> tuple[int, int]:
    """Parse ``HH:MM`` from settings, returning ``default`` on error."""
    try:
        hh, mm = value.split(":", 1)
        return int(hh), int(mm)
    except Exception:  # noqa: BLE001
        return default


async def _on_session_open() -> None:
    """Pre-open hook (09:29 ET weekdays).

    Zero out per-session caches across every supported symbol so the
    first tick of the new session starts clean. Wrapped in try/except —
    a failure here must not stall the scheduler thread.
    """
    settings = get_settings()
    try:
        await reset_session_state(list(settings.supported_symbols))
    except Exception:  # noqa: BLE001
        logger.exception("session_open_failed")


async def _on_session_close() -> None:
    """Post-close hook (16:16 ET weekdays)."""
    settings = get_settings()
    try:
        await finalize_session(list(settings.supported_symbols))
    except Exception:  # noqa: BLE001
        logger.exception("session_close_failed")


async def _on_half_day_close() -> None:
    """Half-day post-close hook (13:01 ET on NYSE half-day sessions only).

    Fires at 13:01 ET every weekday but is a no-op on full-session days —
    the regular ``_on_session_close`` at 16:16 ET handles those. On a
    half-day (Black Friday, Christmas Eve, July 3 in some years) cash
    options stop trading at 13:00 ET so we want to flush intraday
    accumulators 3+ hours sooner. The 16:16 hook still runs and is a
    safe no-op against an already-finalised session.

    The pipeline-side downgrade-to-partial logic for ticks that fire
    after the 13:00 ET early close is deferred — see FIXES_REV8 (Rev 9
    DT-12 closure note). The ``finalize_session`` call here remains the
    operative half-day hook; the deferred work is the *intra-tick*
    bookkeeping inside ``run_pipeline_for_symbol`` that would record an
    early-close-driven partial without an audit gap.
    """
    today = datetime.now(tz=ZoneInfo("America/New_York")).date()
    if not is_half_day(today):
        return
    settings = get_settings()
    try:
        logger.info("half_day_session_close", today=str(today))
        await finalize_session(list(settings.supported_symbols))
    except Exception:  # noqa: BLE001
        logger.exception("half_day_session_close_failed")


async def _run_symbol_pipeline(symbol: str) -> None:
    """Run the chain → flow → alert pipeline trio for a single symbol.

    Every leg is wrapped in its own try/except so a failure in one stage
    never prevents the others from running. ``run_pipeline_for_symbol``
    additionally records a ``pipeline_runs`` row even on failure so the
    audit trail is preserved.
    """
    try:
        result = await run_pipeline_for_symbol(symbol)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline_error", symbol=symbol)
    else:
        if result is not None:
            _state.record(symbol, result.ts, result.duration_ms)

    try:
        await run_flow_pipeline(symbol=symbol)
    except Exception:  # noqa: BLE001
        logger.exception("flow_pipeline_error", symbol=symbol)

    try:
        await run_alert_pipeline(symbol=symbol)
    except Exception:  # noqa: BLE001
        logger.exception("alert_pipeline_error", symbol=symbol)


async def _run_all_symbols(concurrency: int = DEFAULT_SYMBOL_CONCURRENCY) -> None:
    """Fan out the per-symbol pipeline across the supported universe.

    Rev 4: gated on :func:`is_rth_now`. Outside RTH the chain pipeline
    (and its dependent flow / alert pipelines) are no-ops — the cash
    options don't trade so any computation would just churn the EMA basis
    cache with stale data. The futures ingester still runs because GLBX
    trades almost 24x6 and we keep the basis fresh against the ES print.

    Uses ``asyncio.gather(..., return_exceptions=True)`` plus a bounded
    semaphore so a slow / failing symbol does not block the others. Any
    exception that escapes :func:`_run_symbol_pipeline` (which itself is
    defensive) is converted into a log line, never an unhandled task error
    that could kill the scheduler thread.
    """
    settings = get_settings()
    symbols = settings.supported_symbols
    if not symbols:
        return
    if not is_rth_now() and not getattr(settings, "override_rth_gate", False):
        logger.debug("pipeline.skip", reason="outside_rth")
        return

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _bounded(sym: str) -> None:
        async with sem:
            await _run_symbol_pipeline(sym)

    results = await asyncio.gather(
        *(_bounded(s) for s in symbols), return_exceptions=True
    )
    for sym, res in zip(symbols, results, strict=False):
        if isinstance(res, BaseException):
            # _run_symbol_pipeline shouldn't raise, but defence-in-depth so
            # the scheduler thread cannot die from an exotic CancelledError
            # / asyncio.TimeoutError escape.
            logger.error(
                "pipeline_tick_uncaught",
                symbol=sym,
                error=f"{type(res).__name__}: {res}",
            )


def start_scheduler() -> AsyncIOScheduler:
    settings = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _run_all_symbols,
        "interval",
        seconds=settings.compute_interval_seconds,
        id="compute_pipeline",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(UTC),
    )
    # End-of-day OI snapshot: refresh once per day at 22:30 UTC (~17:30 ET,
    # after US options markets close). Also run once on startup so a fresh
    # deployment isn't stuck waiting until tomorrow for OI data.
    scheduler.add_job(
        run_eod_oi_ingestion,
        CronTrigger(hour=22, minute=30, timezone="UTC"),
        id="eod_oi_daily",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_eod_oi_ingestion,
        "date",
        run_date=datetime.now(UTC),
        id="eod_oi_startup",
        max_instances=1,
    )

    # ── Rev 4: session lifecycle ─────────────────────────────────────────
    # Cron jobs that fire one minute before / after the RTH window so the
    # rest of the system has a single, well-defined moment to flush
    # intraday accumulators. APScheduler's day_of_week='mon-fri' filter
    # keeps the job off weekends; the :func:`is_rth_now` gate inside the
    # pipeline still protects against holiday firings.
    open_hh, open_mm = _parse_hhmm(settings.rth_open_time, (9, 30))
    close_hh, close_mm = _parse_hhmm(settings.rth_close_time, (16, 15))
    # Fire 1 minute before open / 1 minute after close. The hour offsets
    # wrap around midnight — e.g. RTH_OPEN_TIME=00:00 produces the
    # pre-open hook at 23:59 the day before. The day_of_week filter is
    # NOT adjusted for that wraparound: a midnight RTH config is
    # explicitly unsupported and would fire the pre-open hook on the
    # *previous* day's date while still gated by mon-fri, so a Monday
    # 00:00 open would have its pre-open hook on Sunday and never fire.
    # The modulo prevents an APScheduler CronTrigger validation crash
    # on edge configs but does not pretend to make them correct.
    pre_open_mm = (open_mm - 1) % 60
    pre_open_hh = (open_hh - (1 if open_mm == 0 else 0)) % 24
    post_close_mm = (close_mm + 1) % 60
    post_close_hh = (close_hh + (1 if close_mm == 59 else 0)) % 24

    scheduler.add_job(
        _on_session_open,
        CronTrigger(
            day_of_week="mon-fri",
            hour=pre_open_hh,
            minute=pre_open_mm,
            timezone="America/New_York",
        ),
        id="session_open",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        _on_session_close,
        CronTrigger(
            day_of_week="mon-fri",
            hour=post_close_hh,
            minute=post_close_mm,
            timezone="America/New_York",
        ),
        id="session_close",
        max_instances=1,
        coalesce=True,
    )

    # REV8 OPS-2 — half-day session close (13:01 ET). Fires every weekday
    # but the handler short-circuits to a no-op on full-session days.
    scheduler.add_job(
        _on_half_day_close,
        CronTrigger(
            day_of_week="mon-fri",
            hour=13,
            minute=1,
            timezone="America/New_York",
        ),
        id="half_day_close",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    logger.info("scheduler_started", interval_seconds=settings.compute_interval_seconds)
    return scheduler


async def trigger_now() -> None:
    """Convenience hook for tests / startup."""
    await _run_all_symbols()
