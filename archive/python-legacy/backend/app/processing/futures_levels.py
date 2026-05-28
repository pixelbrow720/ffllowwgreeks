"""Futures key-levels translator (Rev 4 — consumer support).

The MotiveWave dashboard plots SpotGamma-style key levels (Zero Gamma /
flip, Call Wall, Put Wall, Max Pain, top GEX strikes) on top of a
**futures** chart. The chain that produces those levels lives in cash
index space (SPXW / NDXP), so each cash strike has to be translated into
the front-month futures coordinate space using the basis cached by
``app.processing.spot.resolve_spot``:

    cash  = futures + basis        # basis ≈ -carry - dividends
    fut_level = cash_strike - basis

This module is **pure**: callers load the latest SPOT / GEX / walls /
max-pain rows from the DB and pass them in. The output is a snapshot
dataclass that the API layer serializes via :func:`dataclasses.asdict`.

Inputs are tolerant of missing data. If the futures feed is offline
(``futures_price`` or ``basis`` is ``None``), the snapshot still returns
with whatever cash levels we know, but ``distance_pts`` / ``distance_pct``
are ``None`` so the front-end can render "futures feed offline".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.processing.spot import _FUTURES_ROOT_FOR_SYMBOL

# ──────────────────────────────────────────────────────────────────────────
# Public types
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class FuturesKeyLevel:
    """One translated cash-strike level expressed in futures coordinates."""

    label: str
    """Human-stable identifier — e.g. ``"zero_gamma"``, ``"call_wall_oi_1"``,
    ``"gex_top_pos_3"``. Used as a stable key by the front-end."""

    kind: str
    """High-level level family: ``"flip" | "wall_call" | "wall_put" |
    "max_pain" | "gex_pos" | "gex_neg"``."""

    cash_strike: float
    """Original cash-space strike (SPXW / NDXP)."""

    futures_level: float
    """Translated level in futures space: ``cash_strike - basis``. Falls
    back to ``cash_strike`` when basis is unknown."""

    distance_pts: float | None = None
    """``futures_level - futures_price`` (signed; positive = above
    futures). ``None`` when futures price is unknown."""

    distance_pct: float | None = None
    """``distance_pts / futures_price * 100``. ``None`` when futures
    price is unknown."""

    weight_value: float | None = None
    """Underlying magnitude — OI for walls, |GEX| for top GEX, pain for
    max pain. Used by the front-end to size the level glyph."""

    rank: int | None = None
    """1-based rank among entries of the same ``kind`` (only set for the
    multi-entry families: walls, top GEX strikes)."""


@dataclass
class FuturesLevelsSnapshot:
    """Full set of futures-translated levels for one cash symbol."""

    cash_symbol: str
    futures_root: str
    futures_contract: str | None = None
    futures_price: float | None = None
    cash_spot: float | None = None
    basis: float | None = None
    basis_age_seconds: float | None = None
    spot_source: str | None = None
    levels: list[FuturesKeyLevel] = field(default_factory=list)
    computed_at: datetime | None = None


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _empty_snapshot(cash_symbol: str) -> FuturesLevelsSnapshot:
    """Snapshot returned for unmapped or unknown symbols."""
    return FuturesLevelsSnapshot(
        cash_symbol=cash_symbol.upper(),
        futures_root="",
        futures_contract=None,
        futures_price=None,
        cash_spot=None,
        basis=None,
        basis_age_seconds=None,
        spot_source=None,
        levels=[],
        computed_at=None,
    )


def _coerce_float(value: Any) -> float | None:
    """Best-effort float coercion that filters non-finite values."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _make_level(
    *,
    label: str,
    kind: str,
    cash_strike: float,
    basis: float | None,
    futures_price: float | None,
    weight_value: float | None = None,
    rank: int | None = None,
) -> FuturesKeyLevel:
    """Translate one cash-space strike into a :class:`FuturesKeyLevel`.

    When ``basis`` is unknown we fall back to ``futures_level == cash_strike``
    so the front-end at least has a coordinate to draw. ``distance_pts`` and
    ``distance_pct`` are only set when ``futures_price`` is known.
    """
    if basis is not None:
        futures_level = cash_strike - basis
    else:
        futures_level = cash_strike

    distance_pts: float | None = None
    distance_pct: float | None = None
    if futures_price is not None and futures_price > 0:
        distance_pts = futures_level - futures_price
        distance_pct = (distance_pts / futures_price) * 100.0

    return FuturesKeyLevel(
        label=label,
        kind=kind,
        cash_strike=float(cash_strike),
        futures_level=float(futures_level),
        distance_pts=distance_pts,
        distance_pct=distance_pct,
        weight_value=weight_value,
        rank=rank,
    )


def _zero_gamma_value(extra: dict | None) -> float | None:
    """Extract zero_gamma from a GEX ``extra_json`` payload, if present."""
    if not extra:
        return None
    return _coerce_float(extra.get("zero_gamma"))


def _top_strikes(extra: dict | None, key: str, limit: int) -> list[dict]:
    """Pull ``top_positive`` / ``top_negative`` lists out of GEX extras."""
    if not extra:
        return []
    raw = extra.get(key) or []
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)][:limit]


# ──────────────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────────────


def build_futures_levels(
    *,
    cash_symbol: str,
    spot_extra: dict | None,
    spot_value: float | None,
    spot_ts: datetime | None,
    gex_extra: dict | None,
    gex_oi_extra: dict | None,
    walls_oi: dict[str, list[dict]],
    max_pain_aggregate: dict | None,
    zero_dte_gex_extra: dict | None,
) -> FuturesLevelsSnapshot:
    """Translate every cash-space key level into futures coordinates.

    Parameters mirror the SPOT / GEX / walls / max-pain payloads already
    materialised in ``computed_metrics``. See module docstring for the
    full contract.
    """
    sym = cash_symbol.upper()
    root = _FUTURES_ROOT_FOR_SYMBOL.get(sym)
    if root is None:
        return _empty_snapshot(sym)

    extra = spot_extra or {}
    basis = _coerce_float(extra.get("basis"))
    futures_price = _coerce_float(extra.get("futures_price"))
    futures_contract = extra.get("futures_contract")
    if futures_contract is not None and not isinstance(futures_contract, str):
        futures_contract = str(futures_contract)
    basis_age_seconds = _coerce_float(extra.get("basis_age_seconds"))
    spot_source = extra.get("spot_source") or extra.get("source")
    if spot_source is not None and not isinstance(spot_source, str):
        spot_source = str(spot_source)
    cash_spot = _coerce_float(spot_value)
    if cash_spot is None:
        cash_spot = _coerce_float(extra.get("price"))

    levels: list[FuturesKeyLevel] = []

    # ── Flip levels (zero gamma) ──────────────────────────────────────
    # Volume-weighted GEX is what SpotGamma's dealer-flip line is
    # historically rendered against; fall back to OI-weighted if vol is
    # absent (early in the day, after a restart, etc.).
    zg = _zero_gamma_value(gex_extra)
    if zg is None:
        zg = _zero_gamma_value(gex_oi_extra)
    if zg is not None:
        levels.append(
            _make_level(
                label="zero_gamma",
                kind="flip",
                cash_strike=zg,
                basis=basis,
                futures_price=futures_price,
            )
        )

    zg_0dte = _zero_gamma_value(zero_dte_gex_extra)
    if zg_0dte is not None:
        levels.append(
            _make_level(
                label="flip_0dte",
                kind="flip",
                cash_strike=zg_0dte,
                basis=basis,
                futures_price=futures_price,
            )
        )

    # ── Walls (OI-weighted, top 3 each side) ──────────────────────────
    walls_oi = walls_oi or {}
    for side_key, kind, label_prefix in (
        ("call_wall_oi", "wall_call", "call_wall_oi"),
        ("put_wall_oi", "wall_put", "put_wall_oi"),
    ):
        entries = walls_oi.get(side_key) or []
        # The /walls payload is already rank-sorted; preserve incoming
        # order but re-emit the rank from the entry to be safe.
        for entry in entries[:3]:
            if not isinstance(entry, dict):
                continue
            cash_strike = _coerce_float(entry.get("strike"))
            if cash_strike is None:
                continue
            rank = entry.get("rank")
            try:
                rank_int = int(rank) if rank is not None else None
            except (TypeError, ValueError):
                rank_int = None
            if rank_int is None or rank_int <= 0:
                # Fall back to position-based rank when the upstream
                # payload didn't carry a rank.
                rank_int = entries.index(entry) + 1
            levels.append(
                _make_level(
                    label=f"{label_prefix}_{rank_int}",
                    kind=kind,
                    cash_strike=cash_strike,
                    basis=basis,
                    futures_price=futures_price,
                    weight_value=_coerce_float(entry.get("value")),
                    rank=rank_int,
                )
            )

    # ── Aggregate Max Pain ────────────────────────────────────────────
    if max_pain_aggregate:
        mp_strike = _coerce_float(max_pain_aggregate.get("strike"))
        if mp_strike is not None:
            levels.append(
                _make_level(
                    label="max_pain_agg",
                    kind="max_pain",
                    cash_strike=mp_strike,
                    basis=basis,
                    futures_price=futures_price,
                    weight_value=_coerce_float(max_pain_aggregate.get("value")),
                )
            )

    # ── Top GEX strikes (chain-wide, top 5 each side) ─────────────────
    for entries_key, kind, label_prefix in (
        ("top_positive", "gex_pos", "gex_top_pos"),
        ("top_negative", "gex_neg", "gex_top_neg"),
    ):
        for idx, entry in enumerate(_top_strikes(gex_extra, entries_key, 5)):
            cash_strike = _coerce_float(entry.get("strike"))
            if cash_strike is None:
                continue
            rank = idx + 1
            net_gex = _coerce_float(entry.get("net_gex"))
            weight = abs(net_gex) if net_gex is not None else None
            levels.append(
                _make_level(
                    label=f"{label_prefix}_{rank}",
                    kind=kind,
                    cash_strike=cash_strike,
                    basis=basis,
                    futures_price=futures_price,
                    weight_value=weight,
                    rank=rank,
                )
            )

    # ── Top GEX strikes — 0DTE cohort (top 3 each side) ───────────────
    for entries_key, kind, label_prefix in (
        ("top_positive", "gex_pos", "gex_0dte_top_pos"),
        ("top_negative", "gex_neg", "gex_0dte_top_neg"),
    ):
        for idx, entry in enumerate(_top_strikes(zero_dte_gex_extra, entries_key, 3)):
            cash_strike = _coerce_float(entry.get("strike"))
            if cash_strike is None:
                continue
            rank = idx + 1
            net_gex = _coerce_float(entry.get("net_gex"))
            weight = abs(net_gex) if net_gex is not None else None
            levels.append(
                _make_level(
                    label=f"{label_prefix}_{rank}",
                    kind=kind,
                    cash_strike=cash_strike,
                    basis=basis,
                    futures_price=futures_price,
                    weight_value=weight,
                    rank=rank,
                )
            )

    # Sort ascending by futures level for easy rendering. Ties keep
    # input order (Python's sort is stable) so flip < walls < max_pain
    # at the same level remain readable.
    levels.sort(key=lambda L: L.futures_level)

    return FuturesLevelsSnapshot(
        cash_symbol=sym,
        futures_root=root,
        futures_contract=futures_contract,
        futures_price=futures_price,
        cash_spot=cash_spot,
        basis=basis,
        basis_age_seconds=basis_age_seconds,
        spot_source=spot_source,
        levels=levels,
        computed_at=spot_ts,
    )
