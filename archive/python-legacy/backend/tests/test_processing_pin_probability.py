"""Pin probability heatmap sanity tests."""

from __future__ import annotations

import pandas as pd

from app.processing.pin_probability import compute_pin_probability


def _zero_dte_chain(spot: float = 5800.0):
    today = pd.Timestamp("2026-01-02")
    rows = []
    for strike in (5790, 5795, 5800, 5805, 5810):
        for opt in ("C", "P"):
            rows.append({
                "strike": strike,
                "expiration": today.date(),
                "option_type": opt,
                "iv": 0.18,
                "underlying_price": spot,
                "oi": 1000 if strike == 5800 else 200,
            })
    return pd.DataFrame(rows)


def test_distribution_normalises_to_one():
    out = compute_pin_probability(
        _zero_dte_chain(),
        today=pd.Timestamp("2026-01-02"),
    )
    assert out, "expected non-empty pin probability"
    total = sum(entry["prob"] for entry in out)
    assert abs(total - 1.0) < 1e-9


def test_atm_strike_gets_highest_probability():
    out = compute_pin_probability(
        _zero_dte_chain(),
        today=pd.Timestamp("2026-01-02"),
    )
    top = out[0]
    assert top["strike"] == 5800.0  # large OI + zero distance


def test_no_zero_dte_returns_empty():
    today = pd.Timestamp("2026-01-02")
    chain = pd.DataFrame([
        {
            "strike": 5800,
            "expiration": (today + pd.Timedelta(days=30)).date(),
            "option_type": "C",
            "iv": 0.18,
            "underlying_price": 5800.0,
            "oi": 1000,
        }
    ])
    assert compute_pin_probability(chain, today=today) == []


# NUM-4: pin_probability must keep OI and charm in *share-equivalent*
# units so the charm contribution actually informs the ranking. With
# the unscaled |charm|·τ form, charm came in 6 OOM smaller than OI and
# was purely cosmetic.


def _flat_oi_high_charm_chain(spot: float = 5800.0):
    """OI flat across strikes, but charm peaks at ATM. With dimensional
    scaling, charm should pull the top rank to the ATM strike."""
    today = pd.Timestamp("2026-01-02")
    rows = []
    for strike in (5780, 5790, 5800, 5810, 5820):
        for opt in ("C", "P"):
            rows.append({
                "strike": strike,
                "expiration": today.date(),
                "option_type": opt,
                "iv": 0.50,
                "underlying_price": spot,
                "oi": 500,
            })
    return pd.DataFrame(rows)


def test_charm_contributes_to_ranking_when_oi_is_flat():
    """Sanity check that ATM still ranks #1 on a flat-OI 0DTE chain
    near close — charm + Gaussian kernel together must pick out ATM.

    Pre-fix: charm was 6 OOM smaller than OI and the kernel alone drove
    the ranking. Post-fix: charm is in shares (|charm|·τ·100) so it
    sits on the same axis as OI·|Δ|·100 and contributes a measurable
    delta to the score, while still leaving the kernel as the dominant
    location signal at modest distances.
    """
    out = compute_pin_probability(
        _flat_oi_high_charm_chain(),
        today=pd.Timestamp("2026-01-02"),
        tau_years=5.0 / (365.0 * 24.0 * 60.0),  # 5 min to close
    )
    assert out
    assert out[0]["strike"] == 5800.0
    # Charm must register a non-trivial value at ATM in the new units;
    # under the old |charm|·τ form on a 5-min 0DTE this was ~1e-3.
    assert out[0]["abs_charm"] > 1.0


def test_unfloored_tau_kernel_collapses_in_last_5_minutes():
    """NUM-8: with 5min to close and σ=0.50, the kernel σ_pts using the
    *unfloored* τ should be much smaller than under the 15-min floor.

    σ_pts(unfloored) = 5800 · 0.50 · √(5/525960) ≈ 8.94 pts
    σ_pts(floored)   = 5800 · 0.50 · √(15/525960) ≈ 15.5 pts

    A strike 25 pts from spot must therefore receive *less* probability
    in the unfloored regime than under a 15-min floor. We compare the
    unfloored result to a synthetic 15-min-floored result by overriding
    tau_years to the floor value.
    """
    df = _flat_oi_high_charm_chain()
    # Add a strike 25 pts away to test the kernel falloff.
    today = pd.Timestamp("2026-01-02")
    extra = pd.DataFrame([
        {"strike": 5825.0, "expiration": today.date(), "option_type": "C",
         "iv": 0.50, "underlying_price": 5800.0, "oi": 500},
        {"strike": 5825.0, "expiration": today.date(), "option_type": "P",
         "iv": 0.50, "underlying_price": 5800.0, "oi": 500},
    ])
    df = pd.concat([df, extra], ignore_index=True)

    floor = 15.0 / (365.0 * 24.0 * 60.0)
    five_min = 5.0 / (365.0 * 24.0 * 60.0)

    # Under the 15-min override (kernel uses τ_kernel = 15 min) the 5825
    # strike picks up significant probability because σ_pts is wide.
    out_floor = compute_pin_probability(
        df, today=today, tau_years=floor,
    )
    # Under the actual 5-min intraday τ the kernel σ_pts is narrower and
    # the 5825 strike gets less.
    out_5min = compute_pin_probability(
        df, today=today, tau_years=five_min,
    )
    by_strike_floor = {row["strike"]: row["prob"] for row in out_floor}
    by_strike_5min = {row["strike"]: row["prob"] for row in out_5min}
    assert by_strike_5min[5825.0] < by_strike_floor[5825.0]
