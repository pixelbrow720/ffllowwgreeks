"""Time-series history endpoint (Rev 13 FE-2).

Exposes a bucketed view of ``computed_metrics`` so a consumer wanting
``GEX_NET_TOTAL`` over the last hour can fetch one row per bucket with a
single request, rather than polling ``/snapshot`` once per second. The
endpoint is **additive**: ``/snapshot`` continues to serve "latest only"
and remains the right answer for live dashboards. ``/history`` exists for
charts, retro-analysis, and partner integrations.

Aggregation: last-value-per-bucket. For each ``interval``-second bucket
between ``since`` and ``until`` the endpoint returns the most recent
``computed_metrics.value`` whose ``ts`` falls in the bucket — i.e. the
sample the realtime consumer would have seen at the bucket-end. Earlier
samples within the bucket are superseded. Buckets that contain no rows
are omitted from the response (consumers should treat absence as "no
data published in this bucket").

Bound: ``(until - since) / interval`` is capped at 10000 to keep a
single request from triggering an unbounded TimescaleDB scan. Overruns
return HTTP 400 with the bound surfaced in the detail string so the
caller can self-correct (widen interval / narrow window).

Validation order matches the underlying type hierarchy: HTTP 422 fires
first on malformed primitives (Pydantic), then HTTP 422 on metric_type
not in :data:`EXPECTED_METRIC_TYPES` (we do not want to leak which
metric_types exist by 404), then HTTP 400 on the windowing rules
(``since < until``, ``until <= now()``, bucket cap).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import limiter, require_symbol_access
from app.api.schemas import HistoryPoint, HistoryResponse
from app.config import get_settings
from app.db.models import ApiKey, ComputedMetric
from app.db.session import get_db
from app.processing.pipeline import EXPECTED_METRIC_TYPES

router = APIRouter()

_SYMBOL_PATTERN = r"^[A-Z][A-Z0-9]{0,11}$"

# Hard cap on points returned per request. Combined with the ``interval``
# floor of 1 second this allows windows up to 10000s ≈ 2h45m at the
# tightest granularity, or arbitrarily wide windows at coarser intervals.
# Past this we 400 with a hint so the caller can widen the interval.
_MAX_POINTS_PER_REQUEST: int = 10_000


@router.get(
    "/v1/{symbol}/history",
    response_model=HistoryResponse,
    summary="Bucketed time-series history for one metric_type.",
)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_metric_history(
    request: Request,  # noqa: ARG001 - required by slowapi
    symbol: Annotated[
        str,
        Path(min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    ],
    metric: Annotated[
        str,
        Query(
            description=(
                "metric_type to fetch (e.g. ``GEX_NET_TOTAL``, "
                "``MAX_PAIN_AGG``). Must be one of "
                ":data:`EXPECTED_METRIC_TYPES` — unknown values return "
                "HTTP 422 (Pydantic-style) rather than 404 to avoid "
                "leaking which metric_types exist."
            ),
            min_length=1,
            max_length=64,
        ),
    ],
    since: Annotated[
        datetime,
        Query(description="ISO-8601 datetime — bucketing window start (inclusive)."),
    ],
    until: Annotated[
        datetime | None,
        Query(
            description=(
                "ISO-8601 datetime — bucketing window end (exclusive). "
                "Defaults to ``now()`` when omitted."
            ),
        ),
    ] = None,
    interval: Annotated[
        int,
        Query(
            description="Bucket width in seconds. 1 ≤ interval ≤ 3600.",
            ge=1,
            le=3600,
        ),
    ] = 60,
    api_key: Annotated[ApiKey, Depends(require_symbol_access())] = ...,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db),
) -> HistoryResponse:
    """Return bucketed time-series samples for ``(symbol, metric)``.

    Last-value-per-bucket aggregation. See module docstring for full
    semantics.
    """
    sym = symbol.upper()
    if sym not in [s.upper() for s in get_settings().supported_symbols]:
        raise HTTPException(status_code=404, detail=f"Unsupported symbol {sym}")

    metric_u = metric.strip().upper()
    if metric_u not in EXPECTED_METRIC_TYPES:
        # 422 — caller-side type/contract violation. We deliberately do
        # NOT 404 here because that leaks which metric_types exist on
        # this engine vs. another deployment.
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown metric: {metric!r}. "
                "Must be one of EXPECTED_METRIC_TYPES."
            ),
        )

    now = datetime.now(UTC)
    # Normalise tz on the inputs: FastAPI parses ISO-8601 into naive
    # datetimes when no offset is supplied; treat naive as UTC so the
    # comparison rules below have a single source of truth.
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    if until is None:
        until = now
    elif until.tzinfo is None:
        until = until.replace(tzinfo=UTC)

    if since >= until:
        raise HTTPException(
            status_code=400,
            detail=(
                f"since ({since.isoformat()}) must be strictly less than "
                f"until ({until.isoformat()})"
            ),
        )
    if until > now:
        raise HTTPException(
            status_code=400,
            detail=(
                f"until ({until.isoformat()}) must be at or before now "
                f"({now.isoformat()})"
            ),
        )

    span_seconds = (until - since).total_seconds()
    bucket_count = int(span_seconds // interval) + 1
    if bucket_count > _MAX_POINTS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested window would yield {bucket_count} buckets "
                f"which exceeds the {_MAX_POINTS_PER_REQUEST} point cap. "
                "Widen ``interval`` or narrow the [since, until] window."
            ),
        )

    # Pull the (ts, value) rows for the requested window. Order ascending
    # by ts so the bucket fold below sees samples in chronological order
    # and the "last value wins" semantics are deterministic. The
    # ``ix_computed_metrics_symbol_type_exp_ts`` index covers this query
    # — Postgres scans the (symbol, metric_type, *, ts) prefix and the
    # range filter on ts trims the segment.
    rows = (
        await session.execute(
            select(ComputedMetric.ts, ComputedMetric.value)
            .where(
                ComputedMetric.symbol == sym,
                ComputedMetric.metric_type == metric_u,
                ComputedMetric.ts >= since,
                ComputedMetric.ts < until,
            )
            .order_by(ComputedMetric.ts.asc())
        )
    ).all()

    # Bucket fold. ``buckets[i]`` is the last value seen in bucket i,
    # where bucket boundaries are ``[since + i*interval, since + (i+1)*interval)``.
    # We then emit one point per non-empty bucket with the bucket-end
    # timestamp so the consumer can plot stepwise.
    points: list[HistoryPoint] = []
    if rows:
        # Sweep linearly; cheap enough at the 10k cap. Avoids a server-side
        # GROUP BY which would force aggregating raw values that may be
        # NULL (the column is nullable for "computed but not measurable").
        last_value_per_bucket: dict[int, tuple[datetime, float | None]] = {}
        for ts, value in rows:
            ts_aware = ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)
            offset_s = (ts_aware - since).total_seconds()
            if offset_s < 0:
                continue
            bucket_idx = int(offset_s // interval)
            v = float(value) if value is not None else None
            last_value_per_bucket[bucket_idx] = (ts_aware, v)

        for idx in sorted(last_value_per_bucket):
            ts_aware, v = last_value_per_bucket[idx]
            points.append(HistoryPoint(ts=ts_aware, value=v))

    return HistoryResponse(
        symbol=sym,
        metric=metric_u,
        interval_seconds=interval,
        since=since,
        until=until,
        points=points,
    )
