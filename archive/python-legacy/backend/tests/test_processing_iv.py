"""Tests for IV calculation: BS inversion + summary statistics."""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.processing.iv import (
    IV_LOWER_BOUND,
    IV_UPPER_BOUND,
    _bs_price,
    _years_to_expiry,
    compute_iv_summary,
    fill_missing_iv,
    implied_vol,
)
from app.processing.session import TAU_FLOOR_YEARS


def test_bs_inversion_round_trip_call():
    S, K, T, r, sigma = 100.0, 100.0, 0.5, 0.05, 0.20
    price = _bs_price(S, K, T, r, sigma, is_call=True)
    iv = implied_vol(price=price, S=S, K=K, T=T, r=r, is_call=True)
    assert iv is not None
    assert math.isclose(iv, sigma, rel_tol=1e-3, abs_tol=1e-3)


def test_bs_inversion_round_trip_put():
    S, K, T, r, sigma = 100.0, 110.0, 1.0, 0.04, 0.35
    price = _bs_price(S, K, T, r, sigma, is_call=False)
    iv = implied_vol(price=price, S=S, K=K, T=T, r=r, is_call=False)
    assert iv is not None
    assert math.isclose(iv, sigma, rel_tol=2e-3, abs_tol=2e-3)


def test_implied_vol_returns_none_for_arbitrage_violation():
    S, K, T, r = 100.0, 100.0, 0.25, 0.05
    # Price below intrinsic -> no solution.
    iv = implied_vol(price=-1.0, S=S, K=K, T=T, r=r, is_call=True)
    assert iv is None


def test_implied_vol_clamps_within_bounds():
    iv = implied_vol(price=0.5, S=100.0, K=100.0, T=0.001, r=0.05, is_call=True)
    if iv is not None:
        assert IV_LOWER_BOUND <= iv <= IV_UPPER_BOUND


def test_fill_missing_iv_only_replaces_invalid():
    df = pd.DataFrame(
        [
            {
                "expiration": pd.Timestamp.utcnow().normalize() + pd.Timedelta(days=30),
                "strike": 100.0,
                "option_type": "C",
                "last_price": 5.0,
                "underlying_price": 100.0,
                "iv": 0.25,
                "delta": None,
                "gamma": None,
            },
            {
                "expiration": pd.Timestamp.utcnow().normalize() + pd.Timedelta(days=30),
                "strike": 105.0,
                "option_type": "C",
                "last_price": 2.5,
                "underlying_price": 100.0,
                "iv": None,  # to be filled
                "delta": None,
                "gamma": None,
            },
        ]
    )
    out = fill_missing_iv(df, risk_free_rate=0.05)
    assert math.isclose(float(out.loc[0, "iv"]), 0.25)
    iv_filled = out.loc[1, "iv"]
    assert iv_filled is not None and IV_LOWER_BOUND <= float(iv_filled) <= IV_UPPER_BOUND


def test_compute_iv_summary_returns_atm_and_skew():
    today = pd.Timestamp.utcnow().normalize()
    expiry = today + pd.Timedelta(days=30)
    rows = [
        {"expiration": expiry, "strike": 95.0, "option_type": "P", "iv": 0.35,
         "delta": -0.25, "underlying_price": 100.0},
        {"expiration": expiry, "strike": 100.0, "option_type": "C", "iv": 0.20,
         "delta": 0.50, "underlying_price": 100.0},
        {"expiration": expiry, "strike": 100.0, "option_type": "P", "iv": 0.22,
         "delta": -0.50, "underlying_price": 100.0},
        {"expiration": expiry, "strike": 105.0, "option_type": "C", "iv": 0.18,
         "delta": 0.25, "underlying_price": 100.0},
    ]
    df = pd.DataFrame(rows)
    summary = compute_iv_summary(df)
    assert summary.atm_iv is not None
    assert math.isclose(summary.atm_iv, (0.20 + 0.22) / 2, rel_tol=1e-6)
    expiry_str = str(expiry.date())
    assert expiry_str in summary.skew_per_expiry
    assert math.isclose(summary.skew_per_expiry[expiry_str], 0.18 - 0.35, rel_tol=1e-6)
    assert len(summary.surface) == 4


def test_compute_iv_summary_handles_empty():
    summary = compute_iv_summary(pd.DataFrame())
    assert summary.atm_iv is None
    assert summary.skew_per_expiry == {}
    assert summary.surface == []


# NUM-1 / NUM-2: 0DTE rows must use session-aware τ instead of the
# 1-day calendar floor; otherwise IV inversion + Greek-fill run at
# τ ≈ 1/365 every afternoon and σ is biased downward by ~3×.

_EASTERN = ZoneInfo("America/New_York")


def test_years_to_expiry_uses_session_aware_tau_for_0dte():
    """An expiry equal to ``today`` must collapse to the intraday τ
    (capped at TAU_FLOOR_YEARS), not the 1-day calendar floor."""
    today = pd.Timestamp("2026-01-02")
    # 13:00 ET on a regular trading day → 3h15m ≈ 11700s remaining.
    now = datetime(2026, 1, 2, 13, 0, tzinfo=_EASTERN)
    tau = _years_to_expiry(today, today, now=now)
    # Real intraday τ at 13:00 ET ≈ 3h15m / (365.25·86400) ≈ 3.71e-4.
    # Old behaviour was 1/365 = 2.74e-3 → the new value must be at least
    # 5× smaller and far from the calendar floor.
    assert tau < 1.0 / 365.0 / 5.0
    assert tau >= TAU_FLOOR_YEARS


def test_years_to_expiry_floors_at_tau_floor_at_close():
    """At RTH close the intraday τ collapses to 0; we floor at
    TAU_FLOOR_YEARS so downstream BSM expressions don't blow up."""
    today = pd.Timestamp("2026-01-02")
    now = datetime(2026, 1, 2, 16, 14, tzinfo=_EASTERN)  # 1 min before close
    tau = _years_to_expiry(today, today, now=now)
    assert tau == TAU_FLOOR_YEARS


def test_years_to_expiry_keeps_calendar_for_non_0dte():
    """Non-0DTE rows must keep the simple calendar-day formula."""
    today = pd.Timestamp("2026-01-02")
    expiry = pd.Timestamp("2026-01-09")  # +7 days
    tau = _years_to_expiry(today, expiry, now=datetime(2026, 1, 2, 13, 0, tzinfo=_EASTERN))
    assert math.isclose(tau, 7.0 / 365.0, rel_tol=1e-9)


def test_fill_missing_iv_uses_session_aware_tau_for_0dte_rows():
    """Greek-fill on a 0DTE row at 13:00 ET must produce IV consistent
    with the intraday τ, not the 1-day calendar floor.

    For a fixed ATM price, σ ≈ √(2π/τ) · (price/S). At 13:00 ET intraday
    τ ≈ 3.71e-4 vs calendar floor 1/365 ≈ 2.74e-3 → σ ratio ≈ √7.4 ≈ 2.7.
    The IV recovered for the same observed price must therefore be
    materially higher under the new (correct) path. Downstream
    consumers (pin / term_structure) read σ and pair it with their own
    intraday τ, so a deflated σ here cascades.
    """
    today = pd.Timestamp("2026-01-02")
    now = datetime(2026, 1, 2, 13, 0, tzinfo=_EASTERN)
    df = pd.DataFrame(
        [
            {
                "expiration": today.date(),
                "strike": 5_800.0,
                "option_type": "C",
                "last_price": 5.0,
                "bid": 4.9,
                "ask": 5.1,
                "underlying_price": 5_800.0,
                "iv": None,
                "delta": None,
                "gamma": None,
            }
        ]
    )
    out = fill_missing_iv(df, risk_free_rate=0.05, today=today, now=now)
    iv_intraday = float(out.loc[0, "iv"])
    gamma_row = float(out.loc[0, "gamma"])
    assert IV_LOWER_BOUND <= iv_intraday <= IV_UPPER_BOUND
    assert math.isfinite(gamma_row) and gamma_row > 0

    # Compare against the legacy 1-day-floor behaviour: re-run with the
    # expiration set 1 day out so the function falls into the calendar
    # branch — IV recovered at that τ must be materially smaller because
    # the price is the same but more wall-clock time is implied.
    df_legacy = df.copy()
    df_legacy.at[0, "expiration"] = (today + pd.Timedelta(days=1)).date()
    out_legacy = fill_missing_iv(
        df_legacy, risk_free_rate=0.05, today=today, now=now
    )
    iv_calendar = float(out_legacy.loc[0, "iv"])
    # σ ratio expected ≈ √(τ_cal / τ_int) ≈ 2.7. Use a tame floor (>2×)
    # to give the brentq solver headroom on the low-precision boundary.
    assert iv_intraday > 2.0 * iv_calendar


# ──────────────────────────────────────────────────────────────────────────
# Rev 11 — DR-6 staleness regression
# ──────────────────────────────────────────────────────────────────────────


def test_iv_inversion_skips_stale_last_price():
    """DR-6: when a chain row's two-sided book is missing AND
    ``last_event_ts`` is older than ``last_price_max_age_seconds``
    (default 60s), ``_row_price`` must return 0.0 so the IV inversion
    bails out and IV remains NaN.
    """
    from app.processing.iv import _row_price

    # ``last_event_ts`` 120s in the past — should be considered stale.
    stale_ts = pd.Timestamp.utcnow() - pd.Timedelta(seconds=120)
    if stale_ts.tzinfo is None:
        stale_ts = stale_ts.tz_localize("UTC")
    row = pd.Series({
        "bid": float("nan"),
        "ask": float("nan"),
        "last_price": 5.0,
        "last_event_ts": stale_ts,
    })
    price = _row_price(row, last_price_max_age_seconds=60.0)
    assert price == 0.0


def test_iv_inversion_accepts_fresh_last_price():
    """DR-6 negative: when the row's ``last_event_ts`` is fresh, the
    fallback ``last_price`` is still consulted.
    """
    from app.processing.iv import _row_price

    fresh_ts = pd.Timestamp.utcnow() - pd.Timedelta(seconds=10)
    if fresh_ts.tzinfo is None:
        fresh_ts = fresh_ts.tz_localize("UTC")
    row = pd.Series({
        "bid": float("nan"),
        "ask": float("nan"),
        "last_price": 5.0,
        "last_event_ts": fresh_ts,
    })
    price = _row_price(row, last_price_max_age_seconds=60.0)
    assert price == 5.0


def test_iv_inversion_prefers_mid_over_last_when_both_present():
    """DR-6 boundary: a usable mid (bid<ask, both positive) wins over
    last_price even when the last is fresher. Staleness only matters
    on the ``last_price``-only fallback path.
    """
    from app.processing.iv import _row_price

    fresh_ts = pd.Timestamp.utcnow()
    if fresh_ts.tzinfo is None:
        fresh_ts = fresh_ts.tz_localize("UTC")
    row = pd.Series({
        "bid": 4.9,
        "ask": 5.1,
        "last_price": 100.0,  # absurd value; must NOT be used.
        "last_event_ts": fresh_ts,
    })
    price = _row_price(row, last_price_max_age_seconds=60.0)
    assert price == pytest.approx(5.0, abs=1e-9)


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 DR-22 — 0DTE IV lower bound
# ──────────────────────────────────────────────────────────────────────────


def test_iv_inversion_uses_lower_bound_for_0dte():
    """DR-22: a 0DTE row whose true σ rounds below the historical 1%
    floor must invert successfully when ``iv_lower=0.005``. The same
    price under the legacy ``iv_lower=0.01`` floor must reject (return
    None).

    Strategy: round-trip a synthetic σ=0.007 through ``_bs_price`` to
    derive the market price, then run ``implied_vol`` against both
    floors. 0.005-floor recovers σ ≈ 0.007; 0.01-floor rejects.
    """
    S, K, T, r = 5800.0, 5800.0, 3.71e-4, 0.05  # ATM 0DTE near 13:00 ET
    sigma_true = 0.007
    price = _bs_price(S, K, T, r, sigma_true, is_call=True)

    # 0.005-floor (DR-22 path): recovers σ.
    iv_low = implied_vol(
        price=price, S=S, K=K, T=T, r=r, is_call=True, iv_lower=0.005
    )
    assert iv_low is not None, (
        "DR-22: 0DTE inversion with 0.005 floor must recover σ"
    )
    assert math.isclose(iv_low, sigma_true, rel_tol=1e-2, abs_tol=1e-3)
    assert iv_low >= 0.005

    # 0.01-floor (legacy non-0DTE): rejects because true σ < 0.01.
    iv_legacy = implied_vol(
        price=price, S=S, K=K, T=T, r=r, is_call=True, iv_lower=0.01
    )
    assert iv_legacy is None, (
        "DR-22: legacy 0.01 floor must reject σ that rounds below 1%"
    )


def test_iv_inversion_uses_default_bound_for_non_0dte():
    """DR-22 boundary: a non-0DTE row's σ floor stays at the historical
    1% bound. ``fill_missing_iv`` must pass ``iv_lower_bound`` (default
    0.01) for any expiry that is NOT today (eastern), so a row whose
    σ would round below 1% leaves IV as NaN.
    """
    today = pd.Timestamp("2026-01-02")
    expiry = today + pd.Timedelta(days=30)  # +30d → not 0DTE
    now = datetime(2026, 1, 2, 13, 0, tzinfo=_EASTERN)

    # Build a row whose true σ at +30d is well above 1% so the regular
    # path recovers an IV. This pins the non-0DTE branch — DR-22 must
    # not loosen the floor for non-0DTE rows.
    S, K, T, r = 5800.0, 5800.0, 30.0 / 365.0, 0.05
    sigma_true = 0.20
    price = _bs_price(S, K, T, r, sigma_true, is_call=True)

    df = pd.DataFrame(
        [
            {
                "expiration": expiry.date(),
                "strike": K,
                "option_type": "C",
                "bid": price - 0.01,
                "ask": price + 0.01,
                "last_price": price,
                "underlying_price": S,
                "iv": None,
                "delta": None,
                "gamma": None,
            }
        ]
    )
    out = fill_missing_iv(df, risk_free_rate=r, today=today, now=now)
    iv_filled = out.loc[0, "iv"]
    assert iv_filled is not None and not pd.isna(iv_filled)
    iv_val = float(iv_filled)
    # Non-0DTE rows must clip at the 1% historical floor.
    assert iv_val >= IV_LOWER_BOUND - 1e-9
    assert math.isclose(iv_val, sigma_true, rel_tol=2e-2, abs_tol=2e-3)
