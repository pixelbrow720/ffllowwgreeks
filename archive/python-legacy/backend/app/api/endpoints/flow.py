"""Flow events feed (Agent 5 — streaming API).

Returns detected SWEEP / BLOCK / UOA events for a symbol, filtered by type
and time window. Mirrors the ``X-API-Key`` auth + symbol ACL of the rest of
the data API.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import limiter, require_symbol_access
from app.config import get_settings
from app.db.models import FlowEvent
from app.db.session import get_db

router = APIRouter()


_SYMBOL_PATTERN = r"^[A-Z][A-Z0-9]{0,11}$"
_ALLOWED_EVENT_TYPES = {"SWEEP", "BLOCK", "UOA", "all"}


def _serialise_event(row: FlowEvent) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "ts": row.ts.isoformat() if row.ts else None,
        "symbol": row.symbol,
        "expiration": row.expiration.isoformat() if row.expiration else None,
        "strike": float(row.strike) if row.strike is not None else None,
        "option_type": row.option_type,
        "event_type": row.event_type,
        "side": int(row.side) if row.side is not None else 0,
        "size": int(row.size) if row.size is not None else 0,
        "price": float(row.price) if row.price is not None else None,
        "legs": int(row.legs) if row.legs is not None else 1,
        "venues": list(row.venues or []),
        "meta": dict(row.meta or {}),
    }


@router.get("/v1/{symbol}/flow")
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_flow(
    request: Request,  # noqa: ARG001
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    event_type: str = Query("all"),
    since: datetime | None = Query(
        default=None,
        description=(
            "Start of the window. Defaults to ``now - 1h``. Values older "
            "than 24 hours or in the future return HTTP 400 (Rev 12 BC-19) — "
            "the persisted FlowEvent buffer never extends further back than "
            "24h, so out-of-range requests are rejected eagerly rather than "
            "silently clamped."
        ),
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=1000,
        description=(
            "Maximum events to return. Values outside ``[1, 1000]`` return "
            "HTTP 422 (Pydantic validation, Rev 12 BC-18); the cap is NOT "
            "silently clamped so caller-side bugs (e.g. requesting 100000) "
            "fail loudly rather than scanning a hot hypertable."
        ),
    ),
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> dict[str, Any]:
    """Return detected flow events ordered by ``ts`` descending.

    Filters: ``event_type`` (``SWEEP`` / ``BLOCK`` / ``UOA`` / ``all``),
    ``since`` (default: last 1 hour, max 24h back, no future timestamps —
    out-of-range returns HTTP 400, Rev 12 BC-19), ``limit`` (default: 100,
    max 1000 — out-of-range returns HTTP 422 from Pydantic validation
    rather than silent clamping, Rev 12 BC-18).
    """
    sym_u = symbol.upper()
    event_type_u = event_type.upper() if event_type != "all" else "all"
    if event_type_u not in _ALLOWED_EVENT_TYPES:
        return {"symbol": sym_u, "since": None, "event_type": event_type, "events": []}

    now = datetime.now(UTC)
    if since is None:
        since = now - timedelta(hours=1)
    elif since.tzinfo is None:
        since = since.replace(tzinfo=UTC)

    # Bound ``since`` to a sane window — values outside this range are
    # almost always a client bug (clock skew, year-2038 overflow) and
    # would otherwise scan the entire FlowEvent table.
    if since < now - timedelta(hours=24) or since > now:
        raise HTTPException(
            status_code=400,
            detail="since must be within the last 24 hours and not in the future",
        )

    stmt = (
        select(FlowEvent)
        .where(FlowEvent.symbol == sym_u, FlowEvent.ts >= since)
        .order_by(desc(FlowEvent.ts))
        .limit(limit)
    )
    if event_type_u != "all":
        stmt = stmt.where(FlowEvent.event_type == event_type_u)

    rows = (await session.execute(stmt)).scalars().all()
    # REV12-V2: wrap envelope. /v1/{symbol}/flow is intentionally NOT
    # wrapped in the standard ``{symbol, computed_at,
    # next_update_in_seconds, data}`` envelope — see ``API_POLICY.md``
    # § 1 ("v1 stability exemptions"). When v2 ships every endpoint
    # under /v2/ MUST emit the canonical envelope; the current bare
    # shape is preserved at /v1/ for back-compat.
    return {
        "symbol": sym_u,
        "event_type": event_type_u,
        "since": since.isoformat(),
        "limit": limit,
        "events": [_serialise_event(r) for r in rows],
    }
