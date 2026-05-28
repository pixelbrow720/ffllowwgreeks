"""Consolidated snapshot endpoint (Agent 5 — streaming API).

Returns every metric type the pipeline produces in a single envelope so
client indicators can populate their entire UI in one round-trip.

The payload returned here is also re-used by the WebSocket and SSE
streaming endpoints (`stream.py`) — they push a fresh copy of this
payload after every successful pipeline tick.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from sqlalchemy import desc, func, over, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import limiter, require_symbol_access
from app.api.endpoints.data import (
    _latest_metrics,
    _latest_metrics_batch,
    _walls_payload,
    _walls_payload_from_rows,
)
from app.api.endpoints.flow import _serialise_event
from app.api.schemas import DataEnvelope
from app.config import get_settings
from app.db.models import FlowEvent
from app.db.session import get_db, get_session_factory
from app.processing.futures_levels import build_futures_levels
from app.processing.session import session_snapshot

if TYPE_CHECKING:
    from app.processing.pipeline import PipelineResult

router = APIRouter()


_SYMBOL_PATTERN = r"^[A-Z][A-Z0-9]{0,11}$"


# ── In-process snapshot prime cache ──────────────────────────────────────────
# A reconnect storm (deploy, network blip) can trigger N×26 metric_type
# queries when N WS clients all prime concurrently. We keep the most-recent
# ``(payload, computed_at)`` per symbol with a short TTL so successive primes
# share the same DB read. The pipeline writes through on every successful
# publish via :func:`set_cached_snapshot`, so the cache is generally warm
# for ``COMPUTE_INTERVAL_SECONDS`` after each tick — well past the typical
# reconnect window.
_SNAPSHOT_CACHE_TTL_SECONDS: float = 10.0
_snapshot_cache: dict[
    str, tuple[float, dict[str, Any], datetime | None]
] = {}

# ── Rev 8 OPS-4: single-flight in-flight futures ────────────────────────────
# A reconnect storm of N WS clients all priming on a cold cache used to
# trigger N parallel batch reads against ``computed_metrics``. With this
# map, the first caller starts the build, every other caller awaits the
# same future, and the result is cached on completion. The map is keyed
# on the upper-case symbol; the future is removed once it resolves so a
# subsequent miss re-runs the build.
_in_flight_builds: dict[str, asyncio.Future[tuple[dict[str, Any], datetime | None]]] = {}


def get_cached_snapshot(
    symbol: str,
) -> tuple[dict[str, Any], datetime | None] | None:
    """Return ``(payload, computed_at)`` for ``symbol`` if fresh, else ``None``."""
    sym_u = symbol.upper()
    entry = _snapshot_cache.get(sym_u)
    if entry is None:
        return None
    cached_at, payload, computed_at = entry
    from time import monotonic

    if monotonic() - cached_at > _SNAPSHOT_CACHE_TTL_SECONDS:
        _snapshot_cache.pop(sym_u, None)
        return None
    return payload, computed_at


def set_cached_snapshot(
    symbol: str,
    payload: dict[str, Any],
    computed_at: datetime | None,
) -> None:
    """Write a fresh snapshot into the prime cache."""
    from time import monotonic

    _snapshot_cache[symbol.upper()] = (monotonic(), payload, computed_at)


def reset_snapshot_cache_for_tests() -> None:
    """Test helper: clear the cache so tests start with a cold prime."""
    _snapshot_cache.clear()
    _in_flight_builds.clear()


# ── Rev 8 OPS-4: single-flight cold-prime helper ────────────────────────────


async def build_snapshot_payload_single_flight(
    session: AsyncSession, symbol: str
) -> tuple[dict[str, Any], datetime | None]:
    """Run :func:`build_snapshot_payload` with single-flight semantics.

    A reconnect storm with N clients on a cold cache otherwise triggers N
    parallel ~26-metric batch reads. Here the first caller seeds an
    in-flight future, every other caller awaits the same future, and the
    cache is populated when it resolves. The future is removed on
    completion so a subsequent miss re-runs the build.
    """
    sym_u = symbol.upper()
    existing = _in_flight_builds.get(sym_u)
    if existing is not None and not existing.done():
        return await existing
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[tuple[dict[str, Any], datetime | None]] = loop.create_future()
    _in_flight_builds[sym_u] = fut
    try:
        payload, computed_at = await build_snapshot_payload(session, sym_u)
    except Exception as exc:
        if not fut.done():
            fut.set_exception(exc)
        # Retrieve the exception once so asyncio doesn't log "Future exception
        # was never retrieved" when no concurrent caller stacked behind us.
        try:
            fut.exception()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass
        _in_flight_builds.pop(sym_u, None)
        raise
    if not fut.done():
        fut.set_result((payload, computed_at))
    set_cached_snapshot(sym_u, payload, computed_at)
    _in_flight_builds.pop(sym_u, None)
    return payload, computed_at


# ── Rev 8 OPS-13: stale-spot health classification ──────────────────────────


def _classify_health(spot_payload: dict[str, Any] | None) -> str:
    """Return ``"healthy"`` or ``"stale_spot"`` from the SPOT envelope.

    The pipeline emits ``source='stale_cache'`` in :class:`SpotResult` when
    the futures-basis chain falls back to the prior tick's price. The
    envelope exposes that as a top-level health flag so consumers can
    surface a "feed offline" badge without parsing nested extras.
    """
    if not spot_payload:
        return "healthy"
    source = spot_payload.get("source")
    if source == "stale_cache":
        return "stale_spot"
    return "healthy"


# ── Rev 8 ARCH-7: in-memory PipelineResult → envelope payload ──────────────


def payload_from_pipeline_result(
    result: PipelineResult,
) -> tuple[dict[str, Any], datetime | None]:
    """Synthesize the snapshot envelope directly from the just-finished tick.

    Avoids the read-after-write of ``build_snapshot_payload`` after a
    pipeline tick that just persisted these exact metrics. Output mirrors
    :func:`build_snapshot_payload` for the fields the streaming envelope
    needs; downstream HIRO / FlowEvents are sourced from the in-memory
    result when available, otherwise emitted as empty defaults — the WS
    primer (which IS allowed to read DB) backfills them on cold reconnect.
    """
    sym_u = result.symbol.upper()

    def _gex_payload(g) -> dict[str, Any]:
        return {
            "net_total": float(g.net_total) if g.net_total is not None else 0.0,
            "underlying_price": g.underlying_price,
            "curve": list(g.curve or []),
            "top_positive": list(g.top_positive or []),
            "top_negative": list(g.top_negative or []),
            "zero_gamma": getattr(g, "zero_gamma", None),
            "weight_col": g.weight_col,
            "weight_source": getattr(g, "weight_source", None),
        }

    gex_payload = _gex_payload(result.gex)
    gex_vol_payload = _gex_payload(result.gex_volume)
    zero_gamma = (
        gex_vol_payload.get("zero_gamma")
        if gex_vol_payload.get("zero_gamma") is not None
        else gex_payload.get("zero_gamma")
    )

    walls_oi = {
        "call_wall": list(result.walls.by_oi.get("call_wall", [])),
        "put_wall": list(result.walls.by_oi.get("put_wall", [])),
        "weight_source": result.walls.by_oi.get("weight_source"),
    }
    walls_volume = {
        "call_wall": list(result.walls.by_volume.get("call_wall", [])),
        "put_wall": list(result.walls.by_volume.get("put_wall", [])),
        "weight_source": result.walls.by_volume.get("weight_source"),
    }

    iv_payload = {
        "atm_iv": result.iv.atm_iv,
        "skew_per_expiry": {str(k): float(v) for k, v in (result.iv.skew_per_expiry or {}).items()},
        "surface": list(result.iv.surface or []),
    }

    def _greek_total(greek) -> dict[str, Any]:
        return {
            "net_total": float(greek.net_total or 0.0),
            "underlying_price": greek.underlying_price,
            "curve": list(greek.curve or []),
            "top_positive": list(greek.top_positive or []),
            "top_negative": list(greek.top_negative or []),
            "weight_col": greek.weight_col,
        }

    def _greek_levels(greek) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for level in greek.curve or []:
            entry = dict(level)
            entry.setdefault("strike", level.get("strike"))
            value = level.get("vanna_exposure", level.get("charm_exposure", 0.0))
            entry["value"] = float(value or 0.0)
            out.append(entry)
        return sorted(out, key=lambda x: float(x.get("strike", 0.0)))

    vanna_total = _greek_total(result.vanna)
    charm_total = _greek_total(result.charm)
    vanna_level = _greek_levels(result.vanna)
    charm_level = _greek_levels(result.charm)

    def _regime_entry(mode) -> dict[str, Any]:
        return {
            "score": float(mode.score),
            "label": mode.label,
            "call_wall_total": float(mode.call_wall_total or 0.0),
            "put_wall_total": float(mode.put_wall_total or 0.0),
            "net_gex": float(mode.net_gex or 0.0),
        }

    regime_payload = {
        "oi": _regime_entry(result.regime.oi),
        "vol": _regime_entry(result.regime.vol),
        "label": result.regime.oi.label,
        "score": float(result.regime.oi.score),
    }

    pin_probability = sorted(
        [dict(entry) for entry in (result.pin_probability or [])],
        key=lambda x: float(x.get("strike", 0.0) or 0.0),
    )

    move_tracker = {
        "underlying_price": result.move_tracker.underlying_price,
        "open_price": result.move_tracker.open_price,
        "realized_move": result.move_tracker.realized_move,
        "implied_move": result.move_tracker.implied_move,
        "implied_dte": result.move_tracker.implied_dte,
        "ratio": result.move_tracker.ratio,
        "reason": result.move_tracker.reason,
    }

    iv_term_structure = sorted(
        [dict(entry) for entry in (result.term_structure or [])],
        key=lambda x: str(x.get("expiration", "")),
    )
    risk_reversal_25d = [
        {
            "expiration": str(entry.get("expiration", "")),
            "value": float(entry["risk_reversal_25d"]),
            "call_25d_iv": entry.get("call_25d_iv"),
            "put_25d_iv": entry.get("put_25d_iv"),
        }
        for entry in (result.term_structure or [])
        if entry.get("risk_reversal_25d") is not None
    ]

    max_pain_payload = {
        "per_expiry": [
            {
                "expiration": str(entry.get("expiration", "")),
                "strike": float(entry.get("strike") or 0.0),
                "pain": entry.get("pain"),
            }
            for entry in (result.max_pain.per_expiry or [])
            if entry.get("strike") is not None
        ],
        "aggregate": (
            {
                "strike": float(result.max_pain.aggregate_strike),
                "value": float(result.max_pain.aggregate_value or 0.0),
            }
            if result.max_pain.aggregate_strike is not None
            else None
        ),
    }

    spot_payload = spot_result_to_envelope(result.spot)

    if result.zero_dte is not None:
        zdte = result.zero_dte
        zero_dte_payload = {
            "gex_oi": _gex_payload(zdte.gex_oi),
            "gex_volume": _gex_payload(zdte.gex_vol),
            "charm_total": _gex_payload(zdte.charm),
            "charm_decay_rate": float(zdte.charm_decay_rate or 0.0),
            "flip_speed": float(zdte.flip_speed or 0.0),
        }
    else:
        zero_dte_payload = {
            "gex_oi": {"net_total": 0.0, "curve": [], "top_positive": [], "top_negative": [], "zero_gamma": None, "reason": "no_0dte_today"},
            "gex_volume": {"net_total": 0.0, "curve": [], "top_positive": [], "top_negative": [], "zero_gamma": None, "reason": "no_0dte_today"},
            "charm_total": {"net_total": 0.0, "curve": [], "reason": "no_0dte_today"},
            "charm_decay_rate": 0.0,
            "flip_speed": 0.0,
        }

    if result.back_month is not None:
        back_month_payload = {
            "gex_oi": _gex_payload(result.back_month.gex_oi),
            "gex_volume": _gex_payload(result.back_month.gex_vol),
        }
    else:
        back_month_payload = {
            "gex_oi": {"net_total": 0.0, "curve": [], "top_positive": [], "top_negative": [], "zero_gamma": None},
            "gex_volume": {"net_total": 0.0, "curve": [], "top_positive": [], "top_negative": [], "zero_gamma": None},
        }

    session_state = result.session_state or session_snapshot(symbol=sym_u)
    # Rev 8 OPS-7: lift session_open_price_set into session_state so the
    # consumer can render a "open price pending" banner without joining
    # session_events.
    open_price_set = result.move_tracker.open_price is not None
    session_state = dict(session_state)
    session_state["session_open_price_set"] = bool(open_price_set)

    payload: dict[str, Any] = {
        "gex": gex_payload,
        "gex_volume": gex_vol_payload,
        "max_pain": max_pain_payload,
        "walls_oi": walls_oi,
        "walls_volume": walls_volume,
        "walls": {**walls_oi, **walls_volume},
        "iv": iv_payload,
        "vanna_total": vanna_total,
        "charm_total": charm_total,
        "vanna_level": vanna_level,
        "charm_level": charm_level,
        "regime": regime_payload,
        "zero_gamma": zero_gamma,
        "pin_probability": pin_probability,
        "move_tracker": move_tracker,
        "risk_reversal_25d": risk_reversal_25d,
        "iv_term_structure": iv_term_structure,
        # HIRO and flow are not part of the chain pipeline result — leave
        # empty stubs; cold-reconnect callers go through ``build_snapshot_
        # payload`` which fills them from DB.
        "hiro_cumulative": 0.0,
        "hiro": None,
        "flow_events_last_hour": 0,
        "flow": [],
        "session_state": session_state,
        "spot": spot_payload,
        "zero_dte": zero_dte_payload,
        "back_month": back_month_payload,
        # Rev 8 OPS-13: top-level health flag for consumers.
        "health": _classify_health(spot_payload),
    }
    return payload, result.ts


def spot_result_to_envelope(spot) -> dict[str, Any] | None:
    """Best-effort serialiser for a ``SpotResult`` into the envelope dict."""
    if spot is None:
        return None
    out: dict[str, Any] = {
        "price": float(spot.price),
        "source": spot.source,
        "futures_price": getattr(spot, "futures_price", None),
        "basis": getattr(spot, "basis", None),
        "basis_age_seconds": getattr(spot, "basis_age_seconds", None),
        "parity_price": getattr(spot, "parity_price", None),
        "parity_deviation_pct": getattr(spot, "parity_deviation_pct", None),
    }
    cached_at = getattr(spot, "cached_at", None)
    if cached_at is not None:
        out["cached_at"] = cached_at.isoformat() if hasattr(cached_at, "isoformat") else cached_at
    return out


async def build_snapshot_payload(session: AsyncSession, symbol: str) -> tuple[dict[str, Any], datetime | None]:
    """Build the comprehensive snapshot ``data`` payload for ``symbol``.

    Returns ``(payload, computed_at)`` where ``computed_at`` is the
    freshest ``ComputedMetric.ts`` observed across all queried metric
    types, or ``None`` when no metrics are stored yet.

    The payload is also the shape pushed by the streaming endpoints.
    """
    sym = symbol.upper()

    # Batch every per-metric_type lookup into 2 round-trips total. Each
    # bucket below is a ``list[ComputedMetric]`` ordered by Postgres
    # natural sort — same shape ``_latest_metrics`` would return — so the
    # downstream payload-building code is unchanged. Walls and the flow
    # count remain on their own helpers / queries.
    _METRIC_TYPES = (
        "GEX_NET_TOTAL",
        "GEX_NET_TOTAL_VOL",
        "MAX_PAIN",
        "MAX_PAIN_AGG",
        "ATM_IV",
        "IV_SKEW",
        "IV_SURFACE",
        "VANNA_NET_TOTAL",
        "CHARM_NET_TOTAL",
        "VANNA_LEVEL",
        "CHARM_LEVEL",
        "REGIME_OI",
        "REGIME_VOL",
        "PIN_PROBABILITY",
        "MOVE_TRACKER",
        "IV_TERM_STRUCTURE",
        "RISK_REVERSAL_25D",
        "HIRO",
        "GEX_0DTE_NET_TOTAL",
        "GEX_0DTE_NET_TOTAL_VOL",
        "GEX_BACK_NET_TOTAL",
        "GEX_BACK_NET_TOTAL_VOL",
        "CHARM_0DTE_NET_TOTAL",
        "CHARM_0DTE_DECAY_RATE",
        "GEX_0DTE_FLIP_SPEED",
        "SPOT",
        "CALL_WALL_OI",
        "PUT_WALL_OI",
        "CALL_WALL_VOL",
        "PUT_WALL_VOL",
    )
    # Rev 9 PF-9: run the flow tail query concurrently with the metric
    # batch read instead of waiting for metrics to finish first. ``AsyncSession``
    # is not safe for concurrent use, so the flow query opens its own
    # session via the factory; the two round-trips overlap.
    since = datetime.now(UTC) - timedelta(hours=1)
    total_count_col = over(func.count()).label("total_count")
    flow_stmt = (
        select(FlowEvent, total_count_col)
        .where(FlowEvent.symbol == sym, FlowEvent.ts >= since)
        .order_by(desc(FlowEvent.ts))
        .limit(50)
    )

    async def _run_flow() -> list:
        factory = get_session_factory()
        async with factory() as flow_session:
            return (await flow_session.execute(flow_stmt)).all()

    metrics, flow_result = await asyncio.gather(
        _latest_metrics_batch(session, sym, _METRIC_TYPES),
        _run_flow(),
    )

    gex_rows = metrics["GEX_NET_TOTAL"]
    gex_payload: dict[str, Any] = {
        "net_total": 0.0,
        "curve": [],
        "top_positive": [],
        "top_negative": [],
        "zero_gamma": None,
    }
    if gex_rows:
        r = gex_rows[0]
        gex_payload = dict(r.extra_json or {})
        gex_payload["net_total"] = float(r.value or 0)
        gex_payload.setdefault("zero_gamma", None)

    gex_vol_rows = metrics["GEX_NET_TOTAL_VOL"]
    gex_vol_payload: dict[str, Any] = {
        "net_total": 0.0,
        "curve": [],
        "top_positive": [],
        "top_negative": [],
        "zero_gamma": None,
    }
    if gex_vol_rows:
        r = gex_vol_rows[0]
        gex_vol_payload = dict(r.extra_json or {})
        gex_vol_payload["net_total"] = float(r.value or 0)
        gex_vol_payload.setdefault("zero_gamma", None)

    # Top-level zero_gamma summary: prefer the volume-weighted variant
    # (what the MotiveWave indicator renders by default) but expose both.
    zero_gamma: float | None = (
        gex_vol_payload.get("zero_gamma")
        if gex_vol_payload.get("zero_gamma") is not None
        else gex_payload.get("zero_gamma")
    )

    mp_rows = metrics["MAX_PAIN"]
    mp_agg_rows = metrics["MAX_PAIN_AGG"]
    max_pain_payload = {
        "per_expiry": sorted(
            [
                {
                    "expiration": str(r.expiration),
                    "strike": float(r.strike),
                    "pain": float(r.value or 0),
                }
                for r in mp_rows
            ],
            key=lambda x: x["expiration"],
        ),
        "aggregate": (
            {"strike": float(mp_agg_rows[0].strike), "value": float(mp_agg_rows[0].value or 0)}
            if mp_agg_rows
            else None
        ),
    }

    walls_oi = _walls_payload_from_rows(metrics, "oi")
    walls_volume = _walls_payload_from_rows(metrics, "volume")

    iv_atm = metrics["ATM_IV"]
    iv_skew = metrics["IV_SKEW"]
    iv_surface = metrics["IV_SURFACE"]
    iv_payload = {
        "atm_iv": float(iv_atm[0].value) if iv_atm and iv_atm[0].value is not None else None,
        "skew_per_expiry": {str(r.expiration): float(r.value or 0) for r in iv_skew},
        "surface": (iv_surface[0].extra_json or {}).get("surface") if iv_surface else [],
    }

    # Vanna & Charm — total ("net") + per-strike level curve.
    vanna_total_rows = metrics["VANNA_NET_TOTAL"]
    charm_total_rows = metrics["CHARM_NET_TOTAL"]
    vanna_level_rows = metrics["VANNA_LEVEL"]
    charm_level_rows = metrics["CHARM_LEVEL"]

    def _greek_total(rows: list) -> dict[str, Any]:
        if not rows:
            return {"net_total": 0.0, "curve": [], "top_positive": [], "top_negative": []}
        r = rows[0]
        payload = dict(r.extra_json or {})
        payload["net_total"] = float(r.value or 0)
        return payload

    def _greek_level(rows: list) -> list[dict[str, Any]]:
        return sorted(
            [
                {**(r.extra_json or {}), "strike": float(r.strike), "value": float(r.value or 0)}
                for r in rows
            ],
            key=lambda x: x.get("strike", 0.0),
        )

    vanna_total = _greek_total(vanna_total_rows)
    charm_total = _greek_total(charm_total_rows)
    vanna_level = _greek_level(vanna_level_rows)
    charm_level = _greek_level(charm_level_rows)

    # Regime — OI + volume scores with labels.
    regime_oi_rows = metrics["REGIME_OI"]
    regime_vol_rows = metrics["REGIME_VOL"]

    def _regime_entry(rows: list) -> dict[str, Any] | None:
        if not rows:
            return None
        r = rows[0]
        extra = dict(r.extra_json or {})
        return {
            "score": float(r.value or 0.0),
            "label": extra.get("label", "neutral"),
            "call_wall_total": float(extra.get("call_wall_total") or 0.0),
            "put_wall_total": float(extra.get("put_wall_total") or 0.0),
            "net_gex": float(extra.get("net_gex") or 0.0),
        }

    regime_payload = {
        "oi": _regime_entry(regime_oi_rows),
        "vol": _regime_entry(regime_vol_rows),
        "label": (_regime_entry(regime_oi_rows) or {}).get("label", "neutral"),
        "score": (_regime_entry(regime_oi_rows) or {}).get("score", 0.0),
    }

    # Pin probability — heatmap entries persisted per strike.
    pin_rows = metrics["PIN_PROBABILITY"]
    pin_probability = sorted(
        [
            {**(r.extra_json or {}), "strike": float(r.strike), "prob": float(r.value or 0.0)}
            for r in pin_rows
        ],
        key=lambda x: x.get("strike", 0.0),
    )

    # Realised vs implied move tracker (single row).
    move_rows = metrics["MOVE_TRACKER"]
    move_tracker = dict(move_rows[0].extra_json or {}) if move_rows else None

    # IV term structure + risk reversal — one row per expiration.
    term_rows = metrics["IV_TERM_STRUCTURE"]
    iv_term_structure = sorted(
        [dict(r.extra_json or {}) for r in term_rows],
        key=lambda x: str(x.get("expiration", "")),
    )
    rr_rows = metrics["RISK_REVERSAL_25D"]
    risk_reversal_25d = sorted(
        [
            {
                "expiration": str(r.expiration),
                "value": float(r.value or 0.0),
                **(r.extra_json or {}),
            }
            for r in rr_rows
        ],
        key=lambda x: x.get("expiration", ""),
    )

    # HIRO — cumulative + full series for the latest bucket window. The
    # snapshot exposes both shapes:
    #   * ``hiro_cumulative`` (legacy scalar — pre-Rev 6 consumers)
    #   * ``hiro`` (Rev 6 — full payload mirroring /v1/{symbol}/hiro so
    #     a consumer that primes from /snapshot can render the chart
    #     immediately without a second roundtrip)
    hiro_rows = metrics["HIRO"]
    hiro_cumulative = float(hiro_rows[0].value or 0.0) if hiro_rows else 0.0
    if hiro_rows:
        _hiro_extra = dict(hiro_rows[0].extra_json or {})
        hiro_payload: dict[str, Any] | None = {
            "bucket_size": _hiro_extra.get("bucket_size", "1min"),
            "cumulative": float(_hiro_extra.get("cumulative") or hiro_cumulative),
            "series": list(_hiro_extra.get("series") or []),
            "weight_source": _hiro_extra.get("weight_source", "signed_premium"),
        }
    else:
        hiro_payload = None

    # Rev 4 — 0DTE/back-month cohort splits. All already in the batch above.
    gex_0dte_oi_rows = metrics["GEX_0DTE_NET_TOTAL"]
    gex_0dte_vol_rows = metrics["GEX_0DTE_NET_TOTAL_VOL"]
    gex_back_oi_rows = metrics["GEX_BACK_NET_TOTAL"]
    gex_back_vol_rows = metrics["GEX_BACK_NET_TOTAL_VOL"]
    charm_0dte_rows = metrics["CHARM_0DTE_NET_TOTAL"]
    charm_decay_rows = metrics["CHARM_0DTE_DECAY_RATE"]
    flip_rows = metrics["GEX_0DTE_FLIP_SPEED"]

    def _gex_summary(rows: list) -> dict[str, Any]:
        if not rows:
            return {
                "net_total": 0.0,
                "curve": [],
                "top_positive": [],
                "top_negative": [],
                "zero_gamma": None,
                "reason": "no_0dte_today",
            }
        r = rows[0]
        payload = dict(r.extra_json or {})
        payload["net_total"] = float(r.value or 0)
        return payload

    zero_dte_payload = {
        "gex_oi": _gex_summary(gex_0dte_oi_rows),
        "gex_volume": _gex_summary(gex_0dte_vol_rows),
        "charm_total": _gex_summary(charm_0dte_rows),
        "charm_decay_rate": (
            float(charm_decay_rows[0].value or 0.0) if charm_decay_rows else 0.0
        ),
        "flip_speed": float(flip_rows[0].value or 0.0) if flip_rows else 0.0,
    }
    back_month_payload = {
        "gex_oi": _gex_summary(gex_back_oi_rows),
        "gex_volume": _gex_summary(gex_back_vol_rows),
    }

    # Rev 4 — session_state block (RTH gate + 0DTE tau snapshot).
    session_state = session_snapshot(symbol=sym)

    # Rev 4 — spot resolution block (futures_basis | parity | stale_cache).
    spot_rows = metrics["SPOT"]
    spot_payload: dict[str, Any] | None = None
    if spot_rows:
        r = spot_rows[0]
        spot_payload = dict(r.extra_json or {})
        spot_payload.setdefault("price", float(r.value or 0.0))

    # Flow events in the last hour (count + the most recent 50 for the
    # snapshot tail). Run concurrently with the metric batch read above
    # (Rev 9 PF-9): the count is identical on every row so we read it from
    # the first row, defaulting to 0 when the window is empty.
    flow_events_last_hour = int(flow_result[0].total_count) if flow_result else 0
    flow_tail = [_serialise_event(row[0]) for row in flow_result]

    payload = {
        "gex": gex_payload,
        "gex_volume": gex_vol_payload,
        "max_pain": max_pain_payload,
        "walls_oi": walls_oi["payload"],
        "walls_volume": walls_volume["payload"],
        # Back-compat: legacy ``walls`` shape used by the older indicator
        # builds; combines both modes into a single dict like the existing
        # /walls endpoint.
        "walls": {**walls_oi["payload"], **walls_volume["payload"]},
        "iv": iv_payload,
        "vanna_total": vanna_total,
        "charm_total": charm_total,
        "vanna_level": vanna_level,
        "charm_level": charm_level,
        "regime": regime_payload,
        "zero_gamma": zero_gamma,
        "pin_probability": pin_probability,
        "move_tracker": move_tracker,
        "risk_reversal_25d": risk_reversal_25d,
        "iv_term_structure": iv_term_structure,
        "hiro_cumulative": hiro_cumulative,
        "hiro": hiro_payload,
        "flow_events_last_hour": flow_events_last_hour,
        "flow": flow_tail,
        # Rev 4 additions.
        "session_state": session_state,
        "spot": spot_payload,
        "zero_dte": zero_dte_payload,
        "back_month": back_month_payload,
        # Rev 8 OPS-13: top-level health flag derived from ``spot.source``.
        # ``stale_spot`` when the resolver is on the prior-tick fallback;
        # ``healthy`` otherwise. Documented as a contract change for
        # consumers.
        "health": _classify_health(spot_payload),
    }

    all_rows = (
        gex_rows + gex_vol_rows + mp_rows + mp_agg_rows
        + iv_atm + iv_skew + iv_surface
        + vanna_total_rows + charm_total_rows + vanna_level_rows + charm_level_rows
        + regime_oi_rows + regime_vol_rows
        + pin_rows + move_rows + term_rows + rr_rows + hiro_rows
        # Rev 4 metric rows
        + gex_0dte_oi_rows + gex_0dte_vol_rows
        + gex_back_oi_rows + gex_back_vol_rows
        + charm_0dte_rows + charm_decay_rows + flip_rows
        + spot_rows
        # Walls now folded into the batch read.
        + metrics.get("CALL_WALL_OI", []) + metrics.get("PUT_WALL_OI", [])
        + metrics.get("CALL_WALL_VOL", []) + metrics.get("PUT_WALL_VOL", [])
    )
    candidates = [r.ts for r in all_rows if r.ts is not None]
    computed_at = max(candidates, default=None) if candidates else None

    return payload, computed_at


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


@router.get("/v1/{symbol}/snapshot", response_model=DataEnvelope)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_snapshot(
    request: Request,  # noqa: ARG001
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> DataEnvelope:
    sym = symbol.upper()
    if sym not in [s.upper() for s in get_settings().supported_symbols]:
        raise HTTPException(status_code=404, detail=f"Unsupported symbol {sym}")
    # Rev 9 DT-11: route through the cached/single-flight helper so a
    # reconnect storm or burst of concurrent /snapshot reads coalesces
    # into one batch query instead of N.
    cached = get_cached_snapshot(sym)
    if cached is not None:
        payload, computed_at = cached
    else:
        payload, computed_at = await build_snapshot_payload_single_flight(session, sym)
    return _envelope(symbol, computed_at, payload)


@router.get("/v1/{symbol}/0dte", response_model=DataEnvelope)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_zero_dte(
    request: Request,  # noqa: ARG001
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> DataEnvelope:
    """Rev 4 — thin envelope around the 0DTE + back-month cohorts.

    Returns the same data the snapshot would, but filtered down to the
    Rev 4 0DTE-first fields so the 0DTE-focused page can fetch a
    smaller payload. ``session_state`` is included so the front-end can
    show the RTH banner without a second roundtrip.
    """
    sym = symbol.upper()
    if sym not in [s.upper() for s in get_settings().supported_symbols]:
        raise HTTPException(status_code=404, detail=f"Unsupported symbol {sym}")
    cached = get_cached_snapshot(sym)
    if cached is not None:
        full, computed_at = cached
    else:
        full, computed_at = await build_snapshot_payload_single_flight(session, sym)
    # Curate the response — only the 0DTE-relevant blocks.
    payload: dict[str, Any] = {
        "session_state": full.get("session_state"),
        "spot": full.get("spot"),
        "zero_dte": full.get("zero_dte"),
        "back_month": full.get("back_month"),
        "pin_probability": full.get("pin_probability"),
        "move_tracker": full.get("move_tracker"),
    }
    return _envelope(symbol, computed_at, payload)


@router.get("/v1/{symbol}/spot", response_model=DataEnvelope)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_spot(
    request: Request,  # noqa: ARG001
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> DataEnvelope:
    """Rev 4 — standalone spot resolution endpoint.

    Useful for the dashboard's spot-source badge and for downstream
    consumers that only need the current spot price plus its
    provenance (``futures_basis`` / ``parity`` / ``stale_cache``).
    """
    sym = symbol.upper()
    if sym not in [s.upper() for s in get_settings().supported_symbols]:
        raise HTTPException(status_code=404, detail=f"Unsupported symbol {sym}")
    cached = get_cached_snapshot(sym)
    if cached is not None:
        full, computed_at = cached
    else:
        full, computed_at = await build_snapshot_payload_single_flight(session, sym)
    payload: dict[str, Any] = {
        "session_state": full.get("session_state"),
        "spot": full.get("spot"),
    }
    return _envelope(symbol, computed_at, payload)


@router.get("/v1/{symbol}/futures-levels", response_model=DataEnvelope)
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def get_futures_levels(
    request: Request,  # noqa: ARG001
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    session: AsyncSession = Depends(get_db),
    _api_key=Depends(require_symbol_access()),
) -> DataEnvelope:
    """Rev 4 — SpotGamma-style key levels translated into futures space.

    The chain that drives Zero Gamma / Call Wall / Put Wall / Max Pain
    / top GEX strikes is in cash index space (SPXW / NDXP). Most of our
    users trade the corresponding CME future (ES / NQ), so this endpoint
    translates every level into futures coordinates using the EMA basis
    persisted by the spot resolver: ``futures_level = cash_strike - basis``.

    When the futures feed is offline (no ``basis`` or ``futures_price``)
    the response still includes the cash levels with ``futures_level``
    set, but ``distance_pts`` / ``distance_pct`` are ``None`` so the
    front-end can render a "futures feed offline" banner.
    """
    sym = symbol.upper()
    if sym not in [s.upper() for s in get_settings().supported_symbols]:
        raise HTTPException(status_code=404, detail=f"Unsupported symbol {sym}")

    # SPOT (basis + futures price + provenance).
    spot_rows = await _latest_metrics(session, sym, "SPOT")
    spot_extra: dict[str, Any] | None = None
    spot_value: float | None = None
    spot_ts: datetime | None = None
    if spot_rows:
        r = spot_rows[0]
        spot_extra = dict(r.extra_json or {})
        spot_value = float(r.value) if r.value is not None else None
        spot_ts = r.ts

    # GEX (volume-weighted preferred for flip + top GEX, OI as fallback).
    gex_vol_rows = await _latest_metrics(session, sym, "GEX_NET_TOTAL_VOL")
    gex_oi_rows = await _latest_metrics(session, sym, "GEX_NET_TOTAL")
    gex_extra = dict(gex_vol_rows[0].extra_json or {}) if gex_vol_rows else None
    gex_oi_extra = dict(gex_oi_rows[0].extra_json or {}) if gex_oi_rows else None

    # 0DTE GEX cohort.
    gex_0dte_vol_rows = await _latest_metrics(session, sym, "GEX_0DTE_NET_TOTAL_VOL")
    zero_dte_gex_extra = (
        dict(gex_0dte_vol_rows[0].extra_json or {}) if gex_0dte_vol_rows else None
    )

    # Aggregate Max Pain.
    mp_agg_rows = await _latest_metrics(session, sym, "MAX_PAIN_AGG")
    max_pain_aggregate: dict[str, Any] | None = None
    if mp_agg_rows:
        r = mp_agg_rows[0]
        max_pain_aggregate = {
            "strike": float(r.strike),
            "value": float(r.value or 0.0),
        }

    # Walls (OI-weighted) — reuse the helper from data.py for consistent shape.
    walls_oi_bundle = await _walls_payload(session, sym, "oi")
    walls_oi_payload = walls_oi_bundle.get("payload") or {}

    snapshot = build_futures_levels(
        cash_symbol=sym,
        spot_extra=spot_extra,
        spot_value=spot_value,
        spot_ts=spot_ts,
        gex_extra=gex_extra,
        gex_oi_extra=gex_oi_extra,
        walls_oi=walls_oi_payload,
        max_pain_aggregate=max_pain_aggregate,
        zero_dte_gex_extra=zero_dte_gex_extra,
    )

    # Pick the freshest ts among everything we read.
    candidates: list[datetime] = []
    for rows in (
        spot_rows, gex_vol_rows, gex_oi_rows, gex_0dte_vol_rows, mp_agg_rows,
    ):
        for r in rows:
            if r.ts is not None:
                candidates.append(r.ts)
    walls_ts = walls_oi_bundle.get("computed_at")
    if walls_ts is not None:
        candidates.append(walls_ts)
    computed_at = max(candidates, default=None) if candidates else None

    payload = asdict(snapshot)
    # ``levels`` is already a list of dicts after asdict; explicit no-op for clarity.
    return _envelope(symbol, computed_at, payload)
