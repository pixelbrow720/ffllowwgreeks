"""End-user data endpoints (require X-API-Key)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import limiter, require_symbol_access
from app.api.schemas import (
    DataEnvelope,
    GexResponse,
    IvResponse,
    MaxPainResponse,
    WallsResponse,
)
from app.config import get_settings
from app.db.models import ComputedMetric
from app.db.session import get_db

router = APIRouter()


_SYMBOL_PATTERN = r"^[A-Z][A-Z0-9]{0,11}$"

# GEX metric type per mode.
_GEX_METRIC_BY_MODE: dict[str, str] = {
    "oi": "GEX_NET_TOTAL",
    "volume": "GEX_NET_TOTAL_VOL",
}

# Rev 10 BC-8: deprecation window for ``IvResponse.skew``. The replacement
# is ``IvResponse.skew_per_expiry`` (identical content, clearer name).
# Headers ride along on every /iv response that includes the deprecated
# field. Dates align with API_POLICY.md § 4.
_IV_SKEW_DEPRECATION_HEADER = "Mon, 01 Jan 2027 00:00:00 GMT"
_IV_SKEW_SUNSET_HEADER = "Thu, 01 Jul 2027 00:00:00 GMT"


def _parse_iso_date_or_400(value: str, field: str) -> date:
    """Parse ``YYYY-MM-DD`` strictly; raise HTTP 400 on malformed input.

    We use 400 (not 422) because the value lives inside an open-ended
    string ``Query`` rather than a typed parameter, so FastAPI's default
    422 validation does not fire — we surface a deliberate ``400 Bad
    Request`` so the client can distinguish "the date you sent is not a
    valid ISO-8601 date" from a generic schema violation.
    """
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{field} must be 'nearest', 'all', or an ISO-8601 date (YYYY-MM-DD)",
        ) from exc


async def _latest_metrics(
    session: AsyncSession,
    symbol: str,
    metric_type: str,
) -> list[ComputedMetric]:
    """Return rows of a given metric_type for the most recent ts of that type."""
    latest_ts_q = (
        select(ComputedMetric.ts)
        .where(ComputedMetric.symbol == symbol, ComputedMetric.metric_type == metric_type)
        .order_by(desc(ComputedMetric.ts))
        .limit(1)
    )
    latest_ts = (await session.execute(latest_ts_q)).scalar_one_or_none()
    if latest_ts is None:
        return []
    rows_q = select(ComputedMetric).where(
        ComputedMetric.symbol == symbol,
        ComputedMetric.metric_type == metric_type,
        ComputedMetric.ts == latest_ts,
    )
    return list((await session.execute(rows_q)).scalars().all())


async def _latest_metrics_batch(
    session: AsyncSession,
    symbol: str,
    metric_types: Iterable[str],
) -> dict[str, list[ComputedMetric]]:
    """Batched variant of :func:`_latest_metrics` for many metric_types.

    Collapses 2*N round-trips (the per-metric "latest_ts then rows" pattern)
    into 2 queries total: one ``GROUP BY metric_type`` to find the latest
    ts per type, and one ``IN (...)`` fetch to pull all rows pinned to
    that latest ts. Result preserves the per-key ``list[ComputedMetric]``
    shape so callers can swap ``_latest_metrics`` for a dict lookup with
    no further changes.
    """
    types = list({mt for mt in metric_types if mt})
    out: dict[str, list[ComputedMetric]] = {mt: [] for mt in types}
    if not types:
        return out

    # 1) Latest ts per metric_type for this symbol.
    ts_rows = (
        await session.execute(
            select(
                ComputedMetric.metric_type,
                func.max(ComputedMetric.ts).label("max_ts"),
            )
            .where(
                ComputedMetric.symbol == symbol,
                ComputedMetric.metric_type.in_(types),
            )
            .group_by(ComputedMetric.metric_type)
        )
    ).all()
    if not ts_rows:
        return out
    latest_ts_by_type: dict[str, datetime] = {
        row.metric_type: row.max_ts for row in ts_rows if row.max_ts is not None
    }
    if not latest_ts_by_type:
        return out

    # 2) Pull all rows pinned to (metric_type, latest_ts) in one shot.
    conds = [
        and_(
            ComputedMetric.metric_type == mt,
            ComputedMetric.ts == ts,
        )
        for mt, ts in latest_ts_by_type.items()
    ]
    rows_q = select(ComputedMetric).where(
        ComputedMetric.symbol == symbol,
        or_(*conds),
    )
    rows = (await session.execute(rows_q)).scalars().all()
    for r in rows:
        bucket = out.get(r.metric_type)
        if bucket is not None:
            bucket.append(r)
    return out


def _envelope(symbol: str, computed_at: datetime | None, data: dict[str, Any]) -> DataEnvelope:
    settings = get_settings()
    next_in = settings.compute_interval_seconds
    if computed_at is not None:
        elapsed = (datetime.now(UTC) - computed_at).total_seconds()
        next_in = max(0, int(settings.compute_interval_seconds - elapsed))
    return DataEnvelope(
        symbol=symbol.upper(),
        computed_at=computed_at,
        next_update_in_seconds=next_in,
        data=data,
    )


# ── /v1/{symbol}/gex ────────────────────────────────────────────────────────


@router.get("/v1/{symbol}/gex", response_model=DataEnvelope[GexResponse])
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_gex(
    request: Request,  # noqa: ARG001
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    mode: str = Query("oi", pattern="^(oi|volume)$"),
    expiry: str = Query("all"),  # noqa: ARG001 - reserved for future use
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> DataEnvelope:
    metric_type = _GEX_METRIC_BY_MODE[mode]
    rows = await _latest_metrics(session, symbol.upper(), metric_type)
    if not rows:
        return _envelope(symbol, None, {"net_total": 0.0, "curve": [], "top_positive": [], "top_negative": []})
    row = rows[0]
    payload = dict(row.extra_json or {})
    payload["net_total"] = float(row.value or 0)
    payload.setdefault("curve", [])
    payload.setdefault("top_positive", [])
    payload.setdefault("top_negative", [])
    return _envelope(symbol, row.ts, payload)


# ── /v1/{symbol}/max-pain ───────────────────────────────────────────────────


@router.get("/v1/{symbol}/max-pain", response_model=DataEnvelope[MaxPainResponse])
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_max_pain(
    request: Request,  # noqa: ARG001
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    expiry: str = Query("nearest", min_length=1, max_length=10),
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> DataEnvelope:
    sym = symbol.upper()

    # Strict validation: expiry must be 'nearest', 'all' or a real ISO date.
    target_date: date | None = None
    if expiry not in ("nearest", "all"):
        target_date = _parse_iso_date_or_400(expiry, "expiry")

    per_expiry_rows = await _latest_metrics(session, sym, "MAX_PAIN")
    aggregate_rows = await _latest_metrics(session, sym, "MAX_PAIN_AGG")

    per_expiry = sorted(
        [
            {
                "expiration": str(r.expiration),
                "strike": float(r.strike),
                "pain": float(r.value or 0),
            }
            for r in per_expiry_rows
        ],
        key=lambda x: x["expiration"],
    )
    if target_date is not None:
        per_expiry = [e for e in per_expiry if e["expiration"] == target_date.isoformat()]
    elif expiry == "nearest":
        per_expiry = per_expiry[:1]

    aggregate = None
    if aggregate_rows:
        r = aggregate_rows[0]
        aggregate = {"strike": float(r.strike), "value": float(r.value or 0)}

    computed_at = (
        per_expiry_rows[0].ts if per_expiry_rows else (aggregate_rows[0].ts if aggregate_rows else None)
    )
    return _envelope(symbol, computed_at, {"per_expiry": per_expiry, "aggregate": aggregate})


# ── /v1/{symbol}/walls ──────────────────────────────────────────────────────


async def _walls_payload(session: AsyncSession, symbol: str, mode: str) -> dict:
    """Latest call/put walls for ``symbol`` keyed by mode.

    Single batch query (latest_ts + rows) regardless of how many wall
    metric_types are requested. ``weight_source`` from each wall summary
    (``oi`` / ``volume_fallback`` / ``premium_fallback`` / ``uniform_fallback``)
    is surfaced per-mode so consumers can tell whether the ranking was
    driven by real OI vs the uniform-weight fallback.
    """
    items: list[tuple[str, str]] = []
    if mode in ("oi", "both"):
        items.extend([("CALL_WALL_OI", "call_wall_oi"), ("PUT_WALL_OI", "put_wall_oi")])
    if mode in ("volume", "both"):
        items.extend([("CALL_WALL_VOL", "call_wall_volume"), ("PUT_WALL_VOL", "put_wall_volume")])

    metric_types = [mt for mt, _ in items]
    rows_by_type = await _latest_metrics_batch(session, symbol, metric_types)
    return _walls_payload_from_rows(rows_by_type, mode)


def _walls_payload_from_rows(
    rows_by_type: dict[str, list[ComputedMetric]],
    mode: str,
) -> dict:
    """Shape walls payload from a pre-fetched ``_latest_metrics_batch`` result.

    Lets callers that already pulled wall rows in a parent batch (e.g.
    ``build_snapshot_payload``) avoid the extra ``_walls_payload`` round-trip.
    """
    items: list[tuple[str, str]] = []
    if mode in ("oi", "both"):
        items.extend([("CALL_WALL_OI", "call_wall_oi"), ("PUT_WALL_OI", "put_wall_oi")])
    if mode in ("volume", "both"):
        items.extend([("CALL_WALL_VOL", "call_wall_volume"), ("PUT_WALL_VOL", "put_wall_volume")])

    payload: dict[str, Any] = {}
    computed_at: datetime | None = None
    weight_source_oi: str | None = None
    weight_source_volume: str | None = None

    for metric_type, key in items:
        rows = rows_by_type.get(metric_type, [])
        if rows and computed_at is None:
            computed_at = rows[0].ts
        payload[key] = sorted(
            [
                {
                    "rank": int((r.extra_json or {}).get("rank", 0)),
                    "strike": float(r.strike),
                    "value": float(r.value or 0),
                }
                for r in rows
            ],
            key=lambda x: x["rank"] or 999,
        )
        if rows and metric_type in ("CALL_WALL_OI", "PUT_WALL_OI") and weight_source_oi is None:
            weight_source_oi = (rows[0].extra_json or {}).get("weight_source")
        if rows and metric_type in ("CALL_WALL_VOL", "PUT_WALL_VOL") and weight_source_volume is None:
            weight_source_volume = (rows[0].extra_json or {}).get("weight_source")

    if mode in ("oi", "both"):
        payload["weight_source_oi"] = weight_source_oi
    if mode in ("volume", "both"):
        payload["weight_source_volume"] = weight_source_volume
    return {"computed_at": computed_at, "payload": payload}


@router.get("/v1/{symbol}/walls", response_model=DataEnvelope[WallsResponse])
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_walls(
    request: Request,  # noqa: ARG001
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    mode: str = Query("both", pattern="^(oi|volume|both)$"),
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> DataEnvelope:
    res = await _walls_payload(session, symbol.upper(), mode)
    payload: dict[str, Any] = {
        "call_wall_oi": [],
        "put_wall_oi": [],
        "call_wall_volume": [],
        "put_wall_volume": [],
        "weight_source_oi": None,
        "weight_source_volume": None,
    }
    payload.update(res["payload"])
    return _envelope(symbol, res["computed_at"], payload)


# ── /v1/{symbol}/iv ─────────────────────────────────────────────────────────


@router.get("/v1/{symbol}/iv", response_model=DataEnvelope[IvResponse])
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_iv(
    request: Request,  # noqa: ARG001
    response: Response,
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> DataEnvelope:
    sym = symbol.upper()
    atm_rows = await _latest_metrics(session, sym, "ATM_IV")
    skew_rows = await _latest_metrics(session, sym, "IV_SKEW")
    surface_rows = await _latest_metrics(session, sym, "IV_SURFACE")

    atm = float(atm_rows[0].value) if atm_rows and atm_rows[0].value is not None else None
    skew = {str(r.expiration): float(r.value or 0) for r in skew_rows}
    surface = (surface_rows[0].extra_json or {}).get("surface") if surface_rows else []

    computed_at = (
        atm_rows[0].ts if atm_rows else (skew_rows[0].ts if skew_rows else (surface_rows[0].ts if surface_rows else None))
    )
    # Rev 10 BC-8: ``skew`` is kept for backward compatibility with existing
    # consumers; ``skew_per_expiry`` is the canonical name. When the
    # deprecated field is populated we ride ``Deprecation:`` and
    # ``Sunset:`` headers on the response so codegen clients can surface
    # the warning. Dates aligned with ``API_POLICY.md`` § 4.
    if skew:
        response.headers["Deprecation"] = _IV_SKEW_DEPRECATION_HEADER
        response.headers["Sunset"] = _IV_SKEW_SUNSET_HEADER
        response.headers["Link"] = (
            '<https://github.com/flowgreeks/flowgreeks-engine/blob/main/API_POLICY.md>; '
            'rel="deprecation"; type="text/markdown"'
        )
    return _envelope(
        symbol,
        computed_at,
        {
            "atm_iv": atm,
            "skew": skew,
            "skew_per_expiry": skew,
            "surface": surface,
        },
    )
