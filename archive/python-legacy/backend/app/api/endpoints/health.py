"""Public health endpoint."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import authenticate_api_key
from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import ApiKey
from app.db.session import get_db
from app.processing.scheduler import get_pipeline_state

logger = get_logger(__name__)

router = APIRouter()

# A live feed is "connected" when its most recent record was within this
# window. 5 minutes accommodates GLBX micro-gaps + brief OPRA reconnects
# without flapping. Outside RTH the feeds are silent by design — callers
# should interpret False during off-hours as "expected".
_LIVE_FEED_FRESH_SECONDS = 5 * 60


# ── /ready cache (SRE-3) ────────────────────────────────────────────────────
# ``/ready`` is wired to load-balancer / k8s readiness probes that may
# poll every second or two from N replicas. Caching the freshness probe
# for 5s keeps the underlying ``pipeline_runs`` query off the hot path
# without meaningfully degrading the signal — the readiness threshold
# itself is ``2 × COMPUTE_INTERVAL_SECONDS`` (default 120s) so a 5s
# cache is two orders of magnitude tighter than the alarm window.
_READY_CACHE_TTL_SECONDS: float = 5.0
_ready_cache: dict[str, Any] | None = None
_ready_cache_expires_at: datetime | None = None
_ready_cache_lock = asyncio.Lock()


def _is_recent(ts_iso: str | None, *, max_age_seconds: int) -> bool:
    if not ts_iso:
        return False
    try:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts) <= timedelta(seconds=max_age_seconds)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _staleness_status(state, settings) -> str:
    """Return ``"ok"`` if every supported symbol has computed within
    ``2 * compute_interval_seconds``; otherwise ``"degraded"``.

    Used to power the public ``/health`` summary without disclosing
    per-symbol timing to anonymous callers.
    """
    threshold = max(120, settings.compute_interval_seconds * 2)
    cutoff = datetime.now(UTC) - timedelta(seconds=threshold)
    for sym in settings.supported_symbols:
        ts = state.last_run.get(sym)
        if ts is None:
            return "degraded"
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff:
            return "degraded"
    return "ok"


@router.get("/health")
async def health(response: Response) -> dict[str, Any]:
    """Anonymous liveness probe.

    Returns *only* ``status`` and ``ts`` — the rich payload moved to
    :func:`health_detail` (X-API-Key required) and
    ``/admin/system/status`` (admin JWT required).

    SRE-3 split: this endpoint is now liveness-only — it always
    returns 200 while the process is up and serving HTTP, regardless
    of pipeline freshness. ``/ready`` reports readiness with HTTP 503
    when the pipeline is stale so load balancers can drain stale
    backends.
    """
    state = get_pipeline_state()
    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    return {
        "status": _staleness_status(state, settings),
        "ts": _now_iso(),
    }


async def _compute_ready_payload(
    session: AsyncSession,
) -> tuple[dict[str, Any], int]:
    """Return ``(payload, http_status)`` for the readiness probe.

    Readiness threshold: max age across all supported symbols must be
    below ``2 × COMPUTE_INTERVAL_SECONDS``. Fresh tick = the most recent
    ``pipeline_runs.finished_at`` per symbol — uses the DB, not the
    in-memory ``get_pipeline_state``, so a freshly-restarted replica
    that hasn't ticked yet still reports its true state.
    """
    settings = get_settings()
    threshold_seconds = max(120, settings.compute_interval_seconds * 2)
    now = datetime.now(UTC)

    # Most-recent finished_at per symbol where the run actually completed
    # (i.e. ``finished_at IS NOT NULL`` — orphaned ``running`` rows from
    # a hard crash do not count toward readiness).
    rows = await session.execute(
        text(
            "SELECT symbol, MAX(finished_at) AS latest "
            "FROM pipeline_runs "
            "WHERE finished_at IS NOT NULL "
            "GROUP BY symbol"
        )
    )
    latest_by_symbol: dict[str, datetime | None] = {}
    for sym, latest in rows:
        if latest is not None and latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        latest_by_symbol[str(sym).upper()] = latest

    max_age_seconds: float = 0.0
    ready = True
    for sym in settings.supported_symbols:
        latest = latest_by_symbol.get(sym.upper())
        if latest is None:
            ready = False
            max_age_seconds = float("inf")
            continue
        age = (now - latest).total_seconds()
        if age > max_age_seconds:
            max_age_seconds = age
        if age > threshold_seconds:
            ready = False

    # Replace inf with a JSON-friendly sentinel so the body always
    # round-trips cleanly through orjson (which rejects inf by default).
    last_tick_age_value: float | None
    if max_age_seconds == float("inf"):
        last_tick_age_value = None
    else:
        last_tick_age_value = round(max_age_seconds, 3)

    payload = {
        "ready": ready,
        "last_tick_age_seconds": last_tick_age_value,
        "threshold_seconds": threshold_seconds,
        "ts": now.isoformat(),
    }
    http_status = 200 if ready else 503
    return payload, http_status


@router.get("/ready")
async def ready(
    response: Response,
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Anonymous readiness probe (SRE-3).

    Returns 200 with ``{"ready": true, ...}`` when every supported
    symbol has ticked within ``2 × COMPUTE_INTERVAL_SECONDS``. Returns
    503 otherwise so load balancers can drain stale backends. Cached
    for ``_READY_CACHE_TTL_SECONDS`` to keep the underlying SQL off
    the hot path under aggressive probing.
    """
    global _ready_cache, _ready_cache_expires_at

    now = datetime.now(UTC)
    async with _ready_cache_lock:
        cached = _ready_cache
        expires = _ready_cache_expires_at
        if cached is not None and expires is not None and now < expires:
            payload = cached
        else:
            try:
                payload, http_status = await _compute_ready_payload(session)
            except Exception:  # noqa: BLE001
                logger.exception("ready_probe_failed")
                response.status_code = 503
                response.headers["Cache-Control"] = "no-store"
                return {
                    "ready": False,
                    "last_tick_age_seconds": None,
                    "threshold_seconds": None,
                    "ts": now.isoformat(),
                }
            _ready_cache = payload
            _ready_cache_expires_at = now + timedelta(
                seconds=_READY_CACHE_TTL_SECONDS
            )
            response.status_code = http_status
            response.headers["Cache-Control"] = "no-store"
            return payload

    # Cache hit — recompute the HTTP status from the cached body so a
    # 503 stays a 503 across the cache window.
    response.status_code = 200 if payload.get("ready") else 503
    response.headers["Cache-Control"] = "no-store"
    return payload


def reset_ready_cache_for_tests() -> None:
    """Test helper: clear the readiness cache between scenarios."""
    global _ready_cache, _ready_cache_expires_at
    _ready_cache = None
    _ready_cache_expires_at = None


@router.get("/v1/symbols")
async def supported_symbols(response: Response) -> dict[str, Any]:
    """Anonymous discovery of the supported-symbols list (Rev 12 BC-17).

    Rev 8 SEC-9 thinned ``/health`` to ``{status, ts}`` and moved the
    ``supported_symbols`` list to ``/health/detail`` (X-API-Key
    required). That left no anonymous discovery channel for
    integrators bootstrapping a key request — they had to ask the
    operator out-of-band which symbols their key would be entitled to.

    This endpoint restores anonymous discovery WITHOUT re-exposing the
    operational telemetry that motivated SEC-9 (DB connectivity, live
    feed booleans, last-compute timestamps). Returns just the static
    list configured in ``Settings.supported_symbols``.
    """
    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    return {"symbols": list(settings.supported_symbols)}


@router.get("/health/detail")
async def health_detail(
    response: Response,
    _api_key: Annotated[ApiKey, Depends(authenticate_api_key)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Authenticated extended health.

    Same booleans + per-symbol last-compute the public ``/health``
    used to expose. Requires ``X-API-Key`` so an attacker cannot
    enumerate the supported-symbols list or correlate compute
    timings with traffic patterns.
    """
    settings = get_settings()
    state = get_pipeline_state()

    db_connected = False
    try:
        await session.execute(text("SELECT 1"))
        db_connected = True
    except Exception:  # noqa: BLE001
        logger.warning("health.db_ping_failed")

    live_opra_connected = False
    live_globex_connected = False
    try:
        from app.ingestion.databento_live import get_live_ingester

        opra_diag = get_live_ingester().diagnostics()
        live_opra_connected = _is_recent(
            opra_diag.get("last_record_at"),
            max_age_seconds=_LIVE_FEED_FRESH_SECONDS,
        )
    except Exception:  # noqa: BLE001
        live_opra_connected = False
    try:
        from app.ingestion.databento_globex import get_globex_live_ingester

        globex_diag = get_globex_live_ingester().diagnostics()
        live_globex_connected = _is_recent(
            globex_diag.get("last_record_at"),
            max_age_seconds=_LIVE_FEED_FRESH_SECONDS,
        )
    except Exception:  # noqa: BLE001
        live_globex_connected = False

    pipeline_running = bool(state.last_run)

    response.headers["Cache-Control"] = "no-store"
    return {
        "status": _staleness_status(state, settings),
        "now": _now_iso(),
        "supported_symbols": settings.supported_symbols,
        "compute_interval_seconds": settings.compute_interval_seconds,
        "last_compute_per_symbol": {
            sym: ts.isoformat() if ts else None for sym, ts in state.last_run.items()
        },
        "db_connected": db_connected,
        "live_opra_connected": live_opra_connected,
        "live_globex_connected": live_globex_connected,
        "pipeline_running": pipeline_running,
    }
