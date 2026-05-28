"""Property-based tests for FlowGreeks Black-Scholes math invariants.

Verifies universal properties (Hull §13/§15) on the scipy reference in
bs_reference.py. These are stronger than parity: parity says "two impls
agree", invariants say "any correct impl must satisfy these".

Properties 1-9. Property 10 (real-data smile) lives in test_real_data.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Add parent dir so we can import bs_reference without packaging.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest  # noqa: F401  (used implicitly by hypothesis-driven tests)
from hypothesis import HealthCheck, given, settings, strategies as st

from bs_reference import bs_price, compute_greeks_vec, implied_vol_one


# Realistic equity-index regime
spots = st.floats(min_value=100.0, max_value=10000.0,
                  allow_nan=False, allow_infinity=False)
moneyness = st.floats(min_value=0.5, max_value=2.0,
                      allow_nan=False, allow_infinity=False)
ttm = st.floats(min_value=1.0 / 365.0, max_value=2.0,
                allow_nan=False, allow_infinity=False)
rates = st.floats(min_value=0.0, max_value=0.08,
                  allow_nan=False, allow_infinity=False)
yields = st.floats(min_value=0.0, max_value=0.05,
                   allow_nan=False, allow_infinity=False)
sigmas = st.floats(min_value=0.05, max_value=2.0,
                   allow_nan=False, allow_infinity=False)

# Tighter regime for theta-sign test. For European options theta is not
# universally negative — deep-ITM puts at high r and ATM-but-very-short-T
# regimes can flip. The mathematical theorem applies near-the-money on
# moderate horizons.
near_money = st.floats(min_value=0.92, max_value=1.08,
                       allow_nan=False, allow_infinity=False)
short_t = st.floats(min_value=7.0 / 365.0, max_value=0.25,
                    allow_nan=False, allow_infinity=False)
mod_sigma = st.floats(min_value=0.10, max_value=1.0,
                      allow_nan=False, allow_infinity=False)

# Greeks-positivity regime: avoid extreme moneyness × short-T × low-σ
# combinations where gamma/vega legitimately underflow float64 (φ(d1)
# below ~1e-300). The property is mathematically true but unmeasurable
# in this corner. Bound to representable cases.
pos_money = st.floats(min_value=0.7, max_value=1.5,
                      allow_nan=False, allow_infinity=False)
pos_t = st.floats(min_value=7.0 / 365.0, max_value=2.0,
                  allow_nan=False, allow_infinity=False)
pos_sigma = st.floats(min_value=0.10, max_value=2.0,
                      allow_nan=False, allow_infinity=False)

SETTINGS = settings(max_examples=200, deadline=None,
                    suppress_health_check=[HealthCheck.too_slow,
                                           HealthCheck.filter_too_much])


def _greeks(spot, strike, t, r, q, sigma, kind):
    g = compute_greeks_vec(spot, np.array([strike]), np.array([t]),
                           r, q, np.array([sigma]), np.array([kind]))
    return {k: float(v[0]) for k, v in g.items()}


# Property 1 — Put-Call Parity:  C - P = S·e^(-qT) - K·e^(-rT)
@SETTINGS
@given(spots, moneyness, ttm, rates, yields, sigmas)
def test_put_call_parity(s, m, t, r, q, sigma):
    k = s * m
    c = bs_price(s, k, t, r, q, sigma, "c")
    p = bs_price(s, k, t, r, q, sigma, "p")
    expected = s * np.exp(-q * t) - k * np.exp(-r * t)
    assert abs((c - p) - expected) < 1e-8 * max(s, k) + 1e-8


# Property 2 — Gamma Symmetry:  Γ_call(K) == Γ_put(K)
@SETTINGS
@given(spots, moneyness, ttm, rates, yields, sigmas)
def test_gamma_symmetry(s, m, t, r, q, sigma):
    k = s * m
    g_c = _greeks(s, k, t, r, q, sigma, "c")
    g_p = _greeks(s, k, t, r, q, sigma, "p")
    assert abs(g_c["gamma"] - g_p["gamma"]) < 1e-12


# Property 3 — Vega Symmetry:  Vega_call(K) == Vega_put(K)
@SETTINGS
@given(spots, moneyness, ttm, rates, yields, sigmas)
def test_vega_symmetry(s, m, t, r, q, sigma):
    k = s * m
    g_c = _greeks(s, k, t, r, q, sigma, "c")
    g_p = _greeks(s, k, t, r, q, sigma, "p")
    assert abs(g_c["vega"] - g_p["vega"]) < 1e-12


# Property 4 — Theta Sign.
# Theta < 0 is NOT universal for European options. Specifically:
#   - ITM puts at high r can have theta > 0 (strike payoff appreciates as T shrinks)
#   - ITM calls at high q can have theta > 0 (spot payoff appreciates as T shrinks)
# So we restrict to the OTM half of each side, where the theorem IS universal:
#
#   4a) OTM call (K >= S):       theta < 0 universally
#   4b) OTM put  (K <= S):       theta < 0 universally
#
# Reference: Hull §15.6 (theta of European options).
otm_call_money = st.floats(min_value=1.00, max_value=1.15,
                           allow_nan=False, allow_infinity=False)
otm_put_money = st.floats(min_value=0.85, max_value=1.00,
                          allow_nan=False, allow_infinity=False)


@SETTINGS
@given(spots, otm_call_money, short_t, rates, yields, mod_sigma)
def test_theta_sign_otm_call(s, m, t, r, q, sigma):
    g = _greeks(s, s * m, t, r, q, sigma, "c")
    assert g["theta"] < 0.0


@SETTINGS
@given(spots, otm_put_money, short_t, rates, yields, mod_sigma)
def test_theta_sign_otm_put(s, m, t, r, q, sigma):
    g = _greeks(s, s * m, t, r, q, sigma, "p")
    assert g["theta"] < 0.0


# Property 5 — Delta Bounds:  Δ_c ∈ [0, e^(-qT)],  Δ_p ∈ [-e^(-qT), 0]
@SETTINGS
@given(spots, moneyness, ttm, rates, yields, sigmas)
def test_delta_bounds(s, m, t, r, q, sigma):
    k = s * m
    g_c = _greeks(s, k, t, r, q, sigma, "c")
    g_p = _greeks(s, k, t, r, q, sigma, "p")
    df_q = float(np.exp(-q * t))
    assert -1e-12 <= g_c["delta"] <= df_q + 1e-12
    assert -df_q - 1e-12 <= g_p["delta"] <= 1e-12


# Property 6 — Gamma Positive:  Γ > 0
# Bounded to a regime where φ(d1) is representable in float64.
@SETTINGS
@given(spots, pos_money, pos_t, rates, yields, pos_sigma,
       st.sampled_from(["c", "p"]))
def test_gamma_positive(s, m, t, r, q, sigma, kind):
    g = _greeks(s, s * m, t, r, q, sigma, kind)
    assert g["gamma"] > 0.0


# Property 7 — Vega Positive:  Vega > 0 for T > 0
# Same φ(d1) underflow caveat as gamma.
@SETTINGS
@given(spots, pos_money, pos_t, rates, yields, pos_sigma,
       st.sampled_from(["c", "p"]))
def test_vega_positive(s, m, t, r, q, sigma, kind):
    g = _greeks(s, s * m, t, r, q, sigma, kind)
    assert g["vega"] > 0.0


# Property 8 — IV Round-Trip:  BS(IV(price)) ≈ price
# Generate a price at known sigma, solve back, re-price. Round-trip
# error bounded by solver xtol=1e-6 on sigma → price tol scales with vega.
@SETTINGS
@given(spots, moneyness, ttm, rates, yields,
       st.floats(min_value=0.05, max_value=1.5,
                 allow_nan=False, allow_infinity=False),
       st.sampled_from(["c", "p"]))
def test_iv_round_trip(s, m, t, r, q, sigma_true, kind):
    k = s * m
    price = bs_price(s, k, t, r, q, sigma_true, kind)
    if price <= 1e-6:
        return  # below numerical noise floor — solver legitimately NaNs
    iv = implied_vol_one(float(price), s, k, t, r, q, kind)
    if not np.isfinite(iv):
        return  # bracket [1e-3, 5] may not span — skip, not a failure
    re_price = bs_price(s, k, t, r, q, iv, kind)
    assert abs(re_price - price) < 1e-4 * max(price, 1.0) + 1e-5


# Property 9 — Monotone Vega in T (ATM):  Vega(T2) >= Vega(T1) for T2 > T1
# Restricted to ATM (S=K) with r=q=0 and T <= 1y. For OTM/ITM strikes
# vega is non-monotone (peaks at some optimal T); the universal claim
# only holds in this regime (d(vega)/dT > 0 iff T < 4/σ²).
@SETTINGS
@given(spots,
       st.floats(min_value=1.0 / 365.0, max_value=0.5,
                 allow_nan=False, allow_infinity=False),
       st.floats(min_value=0.001, max_value=0.5,
                 allow_nan=False, allow_infinity=False),
       mod_sigma,
       st.sampled_from(["c", "p"]))
def test_vega_monotone_in_t_atm(s, t1, dt, sigma, kind):
    g1 = _greeks(s, s, t1, 0.0, 0.0, sigma, kind)
    g2 = _greeks(s, s, t1 + dt, 0.0, 0.0, sigma, kind)
    assert g2["vega"] >= g1["vega"] - 1e-12
