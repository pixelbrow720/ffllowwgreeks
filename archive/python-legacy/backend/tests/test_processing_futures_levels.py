"""Tests for the futures key-levels translator.

These are pure-function tests — no DB, no async. They cover the
``cash_strike → futures_level`` translation, missing-data tolerance, and
the ranking / sorting contract that the front-end relies on.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.processing.futures_levels import (
    FuturesKeyLevel,
    FuturesLevelsSnapshot,
    build_futures_levels,
)

# ──────────────────────────────────────────────────────────────────────────
# Fixture builders
# ──────────────────────────────────────────────────────────────────────────


def _spot_extra(
    *,
    basis: float | None = -25.0,
    futures_price: float | None = 5800.0,
    futures_contract: str | None = "ESM6",
    spot_source: str = "futures_basis",
    basis_age_seconds: float | None = 4.2,
) -> dict:
    """Mirror the shape persisted by ``spot_result_to_payload``."""
    return {
        "price": 5775.0 if futures_price is None or basis is None else (futures_price + basis),
        "source": spot_source,
        "spot_source": spot_source,
        "futures_price": futures_price,
        "futures_contract": futures_contract,
        "basis": basis,
        "basis_age_seconds": basis_age_seconds,
        "parity_price": 5775.0,
        "parity_deviation_pct": 0.01,
    }


def _gex_extra() -> dict:
    """Realistic GEX_NET_TOTAL_VOL ``extra_json``."""
    return {
        "underlying_price": 5775.0,
        "zero_gamma": 5825.0,
        "curve": [],
        "top_positive": [
            {"strike": 5850.0, "net_gex": 1.5e9},
            {"strike": 5825.0, "net_gex": 1.1e9},
            {"strike": 5800.0, "net_gex": 0.7e9},
        ],
        "top_negative": [
            {"strike": 5750.0, "net_gex": -1.6e9},
            {"strike": 5775.0, "net_gex": -0.8e9},
        ],
    }


def _zero_dte_extra() -> dict:
    return {
        "zero_gamma": 5780.0,
        "top_positive": [
            {"strike": 5790.0, "net_gex": 5.0e8},
            {"strike": 5810.0, "net_gex": 2.0e8},
        ],
        "top_negative": [
            {"strike": 5760.0, "net_gex": -4.0e8},
        ],
    }


def _walls_oi() -> dict:
    return {
        "call_wall_oi": [
            {"rank": 1, "strike": 5900.0, "value": 50_000.0},
            {"rank": 2, "strike": 5950.0, "value": 35_000.0},
            {"rank": 3, "strike": 6000.0, "value": 20_000.0},
        ],
        "put_wall_oi": [
            {"rank": 1, "strike": 5700.0, "value": 60_000.0},
            {"rank": 2, "strike": 5650.0, "value": 40_000.0},
        ],
    }


def _max_pain_aggregate() -> dict:
    return {"strike": 5780.0, "value": 1.2e9}


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────


def test_translates_cash_strike_to_futures_space():
    """Core invariant: futures_level = cash_strike - basis (basis < 0)."""
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(basis=-25.0, futures_price=5800.0),
        spot_value=5775.0,
        spot_ts=datetime(2026, 5, 21, tzinfo=UTC),
        gex_extra={"zero_gamma": 5825.0, "top_positive": [], "top_negative": []},
        gex_oi_extra=None,
        walls_oi={},
        max_pain_aggregate=None,
        zero_dte_gex_extra=None,
    )

    assert snap.futures_root == "ES"
    assert snap.futures_contract == "ESM6"
    assert snap.basis == -25.0
    assert snap.futures_price == 5800.0
    assert snap.spot_source == "futures_basis"

    assert len(snap.levels) == 1
    flip = snap.levels[0]
    assert flip.label == "zero_gamma"
    assert flip.kind == "flip"
    assert flip.cash_strike == 5825.0
    # cash_strike - basis = 5825 - (-25) = 5850
    assert flip.futures_level == pytest.approx(5850.0)
    # 5850 - 5800 = 50 points above front-month future
    assert flip.distance_pts == pytest.approx(50.0)
    assert flip.distance_pct == pytest.approx(50.0 / 5800.0 * 100.0)


def test_unmapped_symbol_returns_empty_snapshot():
    snap = build_futures_levels(
        cash_symbol="SPY",
        spot_extra=_spot_extra(),
        spot_value=575.0,
        spot_ts=None,
        gex_extra=_gex_extra(),
        gex_oi_extra=None,
        walls_oi=_walls_oi(),
        max_pain_aggregate=_max_pain_aggregate(),
        zero_dte_gex_extra=_zero_dte_extra(),
    )
    assert isinstance(snap, FuturesLevelsSnapshot)
    assert snap.futures_root == ""
    assert snap.levels == []
    assert snap.futures_contract is None
    assert snap.basis is None


def test_missing_basis_keeps_level_drops_distance():
    """No basis → futures_level falls back to cash_strike, no distances."""
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(basis=None, futures_price=5800.0),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra={"zero_gamma": 5825.0, "top_positive": [], "top_negative": []},
        gex_oi_extra=None,
        walls_oi={},
        max_pain_aggregate=None,
        zero_dte_gex_extra=None,
    )
    assert snap.basis is None
    assert snap.futures_price == 5800.0
    assert len(snap.levels) == 1
    flip = snap.levels[0]
    # Basis unknown → futures_level falls back to the cash strike itself.
    assert flip.futures_level == pytest.approx(5825.0)
    # We still have a futures_price, so distance can be computed against
    # the (now identity-mapped) level.
    assert flip.distance_pts == pytest.approx(25.0)


def test_missing_futures_price_drops_distances():
    """No futures price → translation runs, distances stay None."""
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(basis=-25.0, futures_price=None),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra={"zero_gamma": 5825.0, "top_positive": [], "top_negative": []},
        gex_oi_extra=None,
        walls_oi={},
        max_pain_aggregate=None,
        zero_dte_gex_extra=None,
    )
    assert snap.basis == -25.0
    assert snap.futures_price is None
    assert len(snap.levels) == 1
    flip = snap.levels[0]
    assert flip.futures_level == pytest.approx(5850.0)
    assert flip.distance_pts is None
    assert flip.distance_pct is None


def test_ndx_maps_to_nq():
    snap = build_futures_levels(
        cash_symbol="NDXP",
        spot_extra=_spot_extra(basis=-50.0, futures_price=20_000.0, futures_contract="NQM6"),
        spot_value=19_950.0,
        spot_ts=None,
        gex_extra={"zero_gamma": 19_500.0, "top_positive": [], "top_negative": []},
        gex_oi_extra=None,
        walls_oi={},
        max_pain_aggregate=None,
        zero_dte_gex_extra=None,
    )
    assert snap.futures_root == "NQ"
    assert snap.futures_contract == "NQM6"
    assert snap.levels[0].futures_level == pytest.approx(19_550.0)


def test_spx_maps_to_es():
    """SPX (without the W suffix) must map to ES too."""
    snap = build_futures_levels(
        cash_symbol="SPX",
        spot_extra=_spot_extra(basis=-25.0, futures_price=5800.0),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra={"zero_gamma": 5825.0, "top_positive": [], "top_negative": []},
        gex_oi_extra=None,
        walls_oi={},
        max_pain_aggregate=None,
        zero_dte_gex_extra=None,
    )
    assert snap.futures_root == "ES"


def test_zero_gamma_falls_back_to_oi_when_volume_missing():
    """If volume-weighted GEX has no zero_gamma, OI variant takes over."""
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra={"top_positive": [], "top_negative": []},
        gex_oi_extra={"zero_gamma": 5810.0, "top_positive": [], "top_negative": []},
        walls_oi={},
        max_pain_aggregate=None,
        zero_dte_gex_extra=None,
    )
    flips = [L for L in snap.levels if L.kind == "flip"]
    assert len(flips) == 1
    assert flips[0].cash_strike == 5810.0


def test_walls_preserve_rank_and_top3_only():
    """Walls list must keep its rank and clip to the top 3 entries."""
    walls = {
        "call_wall_oi": [
            {"rank": 1, "strike": 5900.0, "value": 50_000.0},
            {"rank": 2, "strike": 5950.0, "value": 35_000.0},
            {"rank": 3, "strike": 6000.0, "value": 20_000.0},
            {"rank": 4, "strike": 6050.0, "value": 10_000.0},
        ],
        "put_wall_oi": [
            {"rank": 1, "strike": 5700.0, "value": 60_000.0},
        ],
    }
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(basis=-25.0, futures_price=5800.0),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra=None,
        gex_oi_extra=None,
        walls_oi=walls,
        max_pain_aggregate=None,
        zero_dte_gex_extra=None,
    )
    call_walls = [L for L in snap.levels if L.kind == "wall_call"]
    put_walls = [L for L in snap.levels if L.kind == "wall_put"]
    assert [L.rank for L in call_walls] == [1, 2, 3]
    assert [L.label for L in call_walls] == [
        "call_wall_oi_1",
        "call_wall_oi_2",
        "call_wall_oi_3",
    ]
    # Top-N clip — the rank-4 wall is dropped.
    assert all(L.cash_strike != 6050.0 for L in call_walls)
    assert [L.cash_strike for L in call_walls] == [5900.0, 5950.0, 6000.0]
    assert [L.weight_value for L in call_walls] == [50_000.0, 35_000.0, 20_000.0]
    assert [L.rank for L in put_walls] == [1]
    assert put_walls[0].label == "put_wall_oi_1"


def test_top_gex_strikes_get_ranks():
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(basis=-25.0, futures_price=5800.0),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra=_gex_extra(),
        gex_oi_extra=None,
        walls_oi={},
        max_pain_aggregate=None,
        zero_dte_gex_extra=None,
    )
    pos = sorted(
        [L for L in snap.levels if L.kind == "gex_pos"],
        key=lambda L: L.rank or 0,
    )
    neg = sorted(
        [L for L in snap.levels if L.kind == "gex_neg"],
        key=lambda L: L.rank or 0,
    )
    assert [L.rank for L in pos] == [1, 2, 3]
    assert [L.label for L in pos] == [
        "gex_top_pos_1",
        "gex_top_pos_2",
        "gex_top_pos_3",
    ]
    # |net_gex| is the weight magnitude (sign already in kind).
    assert pos[0].weight_value == pytest.approx(1.5e9)
    assert neg[0].weight_value == pytest.approx(1.6e9)


def test_levels_sorted_ascending_by_futures_level():
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(basis=-25.0, futures_price=5800.0),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra=_gex_extra(),
        gex_oi_extra=None,
        walls_oi=_walls_oi(),
        max_pain_aggregate=_max_pain_aggregate(),
        zero_dte_gex_extra=_zero_dte_extra(),
    )
    assert snap.levels, "expected non-empty level list"
    levels: list[FuturesKeyLevel] = snap.levels
    futures_levels = [L.futures_level for L in levels]
    assert futures_levels == sorted(futures_levels)
    # Sanity: every level translated correctly with basis = -25.
    for L in levels:
        assert L.futures_level == pytest.approx(L.cash_strike - (-25.0))


def test_max_pain_emitted_when_present():
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(basis=-25.0, futures_price=5800.0),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra=None,
        gex_oi_extra=None,
        walls_oi={},
        max_pain_aggregate={"strike": 5780.0, "value": 1.2e9},
        zero_dte_gex_extra=None,
    )
    pains = [L for L in snap.levels if L.kind == "max_pain"]
    assert len(pains) == 1
    assert pains[0].label == "max_pain_agg"
    assert pains[0].futures_level == pytest.approx(5805.0)
    assert pains[0].weight_value == pytest.approx(1.2e9)


def test_zero_dte_flip_and_top_strikes():
    snap = build_futures_levels(
        cash_symbol="SPXW",
        spot_extra=_spot_extra(basis=-25.0, futures_price=5800.0),
        spot_value=5775.0,
        spot_ts=None,
        gex_extra=None,
        gex_oi_extra=None,
        walls_oi={},
        max_pain_aggregate=None,
        zero_dte_gex_extra=_zero_dte_extra(),
    )
    labels = [L.label for L in snap.levels]
    assert "flip_0dte" in labels
    assert "gex_0dte_top_pos_1" in labels
    assert "gex_0dte_top_neg_1" in labels
