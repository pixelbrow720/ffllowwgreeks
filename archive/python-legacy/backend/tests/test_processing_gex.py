"""Tests for the GEX (gamma exposure) computation."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from app.processing.gex import compute_gex
from app.processing.session import _now_eastern


def test_compute_gex_handles_empty():
    summary = compute_gex(pd.DataFrame())
    assert summary.curve == []
    assert summary.top_positive == []
    assert summary.top_negative == []
    assert summary.net_total == 0.0


def test_compute_gex_call_positive_put_negative():
    """A pure-call book yields positive net GEX; a pure-put book yields negative."""
    S = 100.0
    df_call = pd.DataFrame(
        [
            {
                "strike": 100,
                "option_type": "C",
                "oi": 1000,
                "gamma": 0.05,
                "underlying_price": S,
            }
        ]
    )
    summary = compute_gex(df_call)
    expected = 1 * 0.05 * 1000 * 100 * (S**2) * 0.01
    assert math.isclose(summary.curve[0]["call_gex"], expected, rel_tol=1e-9)
    assert summary.net_total > 0
    assert summary.top_positive[0]["strike"] == 100

    df_put = pd.DataFrame(
        [
            {
                "strike": 100,
                "option_type": "P",
                "oi": 1000,
                "gamma": 0.05,
                "underlying_price": S,
            }
        ]
    )
    summary = compute_gex(df_put)
    assert summary.net_total < 0
    assert summary.top_negative[0]["strike"] == 100


def test_compute_gex_ranks_top_levels():
    S = 100.0
    rows = []
    # Many strikes with varying gamma * OI to produce distinct GEX values.
    for strike, gamma, oi in [
        (95, 0.05, 100),
        (100, 0.10, 500),
        (105, 0.04, 200),
        (110, 0.02, 800),
    ]:
        rows.append(
            {
                "strike": strike,
                "option_type": "C",
                "oi": oi,
                "gamma": gamma,
                "underlying_price": S,
            }
        )
    df = pd.DataFrame(rows)
    summary = compute_gex(df, top_n=2)
    assert len(summary.curve) == 4
    assert len(summary.top_positive) == 2
    assert summary.top_positive[0]["strike"] == 100  # highest gamma*OI
    assert summary.top_positive[0]["net_gex"] > summary.top_positive[1]["net_gex"]


def test_compute_gex_skips_when_no_underlying_price():
    df = pd.DataFrame(
        [{"strike": 100, "option_type": "C", "oi": 100, "gamma": 0.05, "underlying_price": None}]
    )
    summary = compute_gex(df)
    assert summary.underlying_price is None
    assert summary.curve == []


def test_compute_gex_volume_mode_uses_volume_weight():
    """Volume-weighted GEX flips magnitude when OI != volume."""
    S = 100.0
    df = pd.DataFrame(
        [
            {"strike": 100, "option_type": "C", "oi": 100, "volume": 5000,
             "gamma": 0.05, "underlying_price": S},
            {"strike": 100, "option_type": "P", "oi": 5000, "volume": 100,
             "gamma": 0.05, "underlying_price": S},
        ]
    )
    by_oi = compute_gex(df, weight_col="oi")
    by_vol = compute_gex(df, weight_col="volume")
    # Under OI: puts dominate (5000 vs 100) -> negative net GEX.
    assert by_oi.net_total < 0
    assert by_oi.weight_col == "oi"
    # Under Volume: calls dominate (5000 vs 100) -> positive net GEX.
    assert by_vol.net_total > 0
    assert by_vol.weight_col == "volume"


def test_compute_gex_returns_empty_when_weight_column_all_zero():
    df = pd.DataFrame(
        [{"strike": 100, "option_type": "C", "oi": 0, "volume": 0,
          "gamma": 0.05, "underlying_price": 100.0}]
    )
    by_oi = compute_gex(df, weight_col="oi")
    by_vol = compute_gex(df, weight_col="volume")
    assert by_oi.net_total == 0.0
    assert by_oi.curve == []
    assert by_vol.net_total == 0.0
    assert by_vol.curve == []


def test_compute_gex_skips_zero_gamma_when_disabled():
    """Rev 9 PF-2: ``compute_zero_gamma=False`` must skip the grid scan.

    The pipeline runs ``compute_gex`` 6 times per tick (chain × {oi, vol},
    0DTE × {oi, vol}, back × {oi, vol}). Only the chain-volume variant
    feeds the snapshot envelope's ``zero_gamma`` field — the other 5 had
    no consumer for the level. Pin that opting out of the embedded grid
    scan returns ``zero_gamma=None`` while leaving the rest of the
    summary unchanged.
    """
    S = 100.0
    df = pd.DataFrame(
        [
            {"strike": 100, "option_type": "C", "oi": 1000, "gamma": 0.05,
             "underlying_price": S, "iv": 0.20,
             "expiration": pd.Timestamp("2026-12-31").date()},
            {"strike": 105, "option_type": "P", "oi": 800, "gamma": 0.03,
             "underlying_price": S, "iv": 0.22,
             "expiration": pd.Timestamp("2026-12-31").date()},
        ]
    )
    enabled = compute_gex(df, weight_col="oi", compute_zero_gamma=True)
    skipped = compute_gex(df, weight_col="oi", compute_zero_gamma=False)
    # Only the zero_gamma scalar should differ; net_total + curve identical.
    assert math.isclose(enabled.net_total, skipped.net_total, rel_tol=1e-12)
    assert len(enabled.curve) == len(skipped.curve)
    assert skipped.zero_gamma is None


@pytest.mark.asyncio
async def test_pipeline_invokes_zero_gamma_exactly_once_per_tick(monkeypatch):
    """Rev 9 PF-2 spy: only the chain-volume call computes zero_gamma.

    Pipeline runs ``compute_gex`` 6× per tick across the chain / 0DTE /
    back-month cohorts × {oi, volume}. Before this fix every call ran the
    embedded grid scan; only the chain-volume scalar reaches the snapshot
    envelope, so the other 5 were ~250ms of pure waste. Spy on the
    ``zero_gamma.compute_zero_gamma`` symbol that ``app.processing.gex``
    imports (aliased as ``_compute_zero_gamma``) and assert call count == 1.
    """
    from app.processing import gex as gex_mod
    from app.processing import pipeline as pipeline_mod
    from app.processing import zero_dte as zero_dte_mod

    call_count = {"n": 0}
    real = gex_mod._compute_zero_gamma

    def _spy(*args, **kwargs):
        call_count["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(gex_mod, "_compute_zero_gamma", _spy)

    today = pd.Timestamp(_now_eastern().date())
    expiry_today = today.date()
    expiry_back = (today + pd.Timedelta(days=21)).date()
    rows: list[dict] = []
    for K in (95.0, 100.0, 105.0):
        for exp in (expiry_today, expiry_back):
            rows.append(
                {"strike": K, "option_type": "C", "oi": 1000, "volume": 200,
                 "gamma": 0.05, "delta": 0.5, "iv": 0.20,
                 "underlying_price": 100.0, "bid": 1.0, "ask": 1.1,
                 "last_price": 1.05, "expiration": exp}
            )
            rows.append(
                {"strike": K, "option_type": "P", "oi": 800, "volume": 150,
                 "gamma": 0.04, "delta": -0.5, "iv": 0.22,
                 "underlying_price": 100.0, "bid": 1.0, "ask": 1.1,
                 "last_price": 1.05, "expiration": exp}
            )
    df = pd.DataFrame(rows)

    class _S:
        risk_free_rate = 0.05
        atm_band_pct_0dte = 0.005

    # Drive _compute_metrics directly — it is the function that issues all
    # 6 compute_gex calls per tick (chain×oi, chain×vol, 0DTE×oi, 0DTE×vol,
    # back×oi, back×vol).
    ts = pd.Timestamp(_now_eastern()).to_pydatetime()
    pipeline_mod._compute_metrics(df=df, symbol="SPXW", ts=ts, settings=_S())

    # Sanity: prove we routed through the same 0DTE codepath that used to
    # double-call zero_gamma — silences a stub regression where split_by_expiry
    # short-circuits to has_0dte=False.
    zero, _back = zero_dte_mod.split_by_expiry(df)
    assert not zero.empty

    assert call_count["n"] == 1, (
        f"Expected exactly one zero_gamma call per tick (chain-volume only); "
        f"got {call_count['n']}. Other cohorts must pass compute_zero_gamma=False."
    )
