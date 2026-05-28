"""Reference Black-Scholes + IV solver using scipy/numpy.

Self-contained, no third-party BS package. Used as ground truth for
FlowGreeks parity checks. Formulas match the standard convention:

    d1 = (ln(S/K) + (r - q + sigma^2 / 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    call = S * exp(-q*T) * N(d1) - K * exp(-r*T) * N(d2)
    put  = K * exp(-r*T) * N(-d2) - S * exp(-q*T) * N(-d1)

This is the same convention used in FlowGreeks's internal/greeks/pricing.go.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm


def bs_price(spot: float, strike: float | np.ndarray, t: float | np.ndarray,
             r: float, q: float, sigma: float, kind: str) -> float | np.ndarray:
    """Black-Scholes-Merton price for European call ('c') or put ('p')."""
    if np.isscalar(t) and t <= 0:
        return max(0.0, (spot - strike) if kind == "c" else (strike - spot))
    sqrt_t = np.sqrt(t)
    d1 = (np.log(spot / strike) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    df_q = np.exp(-q * t)
    df_r = np.exp(-r * t)
    if kind == "c":
        return spot * df_q * norm.cdf(d1) - strike * df_r * norm.cdf(d2)
    return strike * df_r * norm.cdf(-d2) - spot * df_q * norm.cdf(-d1)


def implied_vol_one(price: float, spot: float, strike: float, t: float,
                     r: float, q: float, kind: str,
                     vol_min: float = 1e-3, vol_max: float = 5.0) -> float:
    """Solve IV via Brent's method. Returns NaN if no bracket found.

    Mirrors FlowGreeks's internal/greeks/solver.go bracket auto-widen behavior:
    if residuals share sign at the initial bracket, widen once and retry.
    """
    if t <= 0 or price <= 0 or spot <= 0 or strike <= 0:
        return float("nan")
    intrinsic = max(0.0, (spot * np.exp(-q * t) - strike * np.exp(-r * t)) if kind == "c"
                          else (strike * np.exp(-r * t) - spot * np.exp(-q * t)))
    if price < intrinsic - 1e-6:
        return float("nan")  # below intrinsic — broken quote

    def residual(sigma: float) -> float:
        return bs_price(spot, strike, t, r, q, sigma, kind) - price

    try:
        return float(brentq(residual, vol_min, vol_max, xtol=1e-6, maxiter=100))
    except ValueError:
        # widen once
        try:
            return float(brentq(residual, max(vol_min / 10, 1e-6),
                                 min(vol_max * 2, 10.0), xtol=1e-6, maxiter=100))
        except ValueError:
            return float("nan")


def implied_vol_vec(prices: np.ndarray, spot: float, strikes: np.ndarray,
                     ts: np.ndarray, r: float, q: float, kinds: np.ndarray) -> np.ndarray:
    """Vectorised wrapper — Python loop, but kept here so callers don't import scipy."""
    out = np.full(len(prices), np.nan, dtype=np.float64)
    for i in range(len(prices)):
        out[i] = implied_vol_one(
            float(prices[i]), spot, float(strikes[i]), float(ts[i]),
            r, q, str(kinds[i]).lower(),
        )
    return out


def compute_greeks_vec(spot: float, strikes: np.ndarray, ts: np.ndarray,
                        r: float, q: float, sigmas: np.ndarray,
                        kinds: np.ndarray) -> dict[str, np.ndarray]:
    """Reference Black-Scholes Greeks (Δ Γ Θ Vega Charm) vectorised.

    Conventions match FlowGreeks's internal/greeks/greeks.go::All():
      - Vega scaled per 1 vol pt (already divided by 100).
      - Theta and Charm in per-year (caller divides by 365 / 525600 as needed).
      - Continuous dividend yield q.

    Returns NaN per row where t<=0, sigma<=0, spot<=0, strike<=0, or
    kinds is not 'c'/'p'. Output dict keys: delta, gamma, theta, vega, charm.
    """
    n = len(strikes)
    delta = np.full(n, np.nan, dtype=np.float64)
    gamma = np.full(n, np.nan, dtype=np.float64)
    theta = np.full(n, np.nan, dtype=np.float64)
    vega = np.full(n, np.nan, dtype=np.float64)
    charm = np.full(n, np.nan, dtype=np.float64)

    K = np.asarray(strikes, dtype=np.float64)
    T = np.asarray(ts, dtype=np.float64)
    sig = np.asarray(sigmas, dtype=np.float64)
    kinds_l = np.array([str(k).lower() for k in kinds])

    valid = (T > 0) & (sig > 0) & (K > 0) & (spot > 0) & np.isfinite(sig) & \
            ((kinds_l == "c") | (kinds_l == "p"))
    if not valid.any():
        return {"delta": delta, "gamma": gamma, "theta": theta,
                "vega": vega, "charm": charm}

    Kv = K[valid]
    Tv = T[valid]
    sv = sig[valid]
    kv = kinds_l[valid]

    sqrt_t = np.sqrt(Tv)
    sig_sqrt_t = sv * sqrt_t
    d1 = (np.log(spot / Kv) + (r - q + 0.5 * sv * sv) * Tv) / sig_sqrt_t
    d2 = d1 - sig_sqrt_t

    df_q = np.exp(-q * Tv)
    df_r = np.exp(-r * Tv)
    pd1 = norm.pdf(d1)  # φ(d1)
    Nd1 = norm.cdf(d1)
    Nd2 = norm.cdf(d2)

    # Side-independent
    gamma_v = df_q * pd1 / (spot * sig_sqrt_t)
    vega_v = spot * df_q * pd1 * sqrt_t / 100.0

    # Common factors (match greeks.go lines 41-43)
    theta_common = -spot * df_q * pd1 * sv / (2.0 * sqrt_t)
    charm_common = -df_q * pd1 * (2.0 * (r - q) * Tv - d2 * sig_sqrt_t) / \
                    (2.0 * Tv * sig_sqrt_t)

    is_call = (kv == "c")
    is_put = ~is_call

    delta_v = np.empty_like(d1)
    theta_v = np.empty_like(d1)
    charm_v = np.empty_like(d1)

    # Call branch (greeks.go lines 49-51)
    delta_v[is_call] = df_q[is_call] * Nd1[is_call]
    theta_v[is_call] = (theta_common[is_call]
                        - r * Kv[is_call] * df_r[is_call] * Nd2[is_call]
                        + q * spot * df_q[is_call] * Nd1[is_call])
    charm_v[is_call] = charm_common[is_call] - q * df_q[is_call] * Nd1[is_call]

    # Put branch (greeks.go lines 56-58)
    delta_v[is_put] = df_q[is_put] * (Nd1[is_put] - 1.0)
    theta_v[is_put] = (theta_common[is_put]
                       + r * Kv[is_put] * df_r[is_put] * (1.0 - Nd2[is_put])
                       - q * spot * df_q[is_put] * (1.0 - Nd1[is_put]))
    charm_v[is_put] = charm_common[is_put] + q * df_q[is_put] * (1.0 - Nd1[is_put])

    delta[valid] = delta_v
    gamma[valid] = gamma_v
    theta[valid] = theta_v
    vega[valid] = vega_v
    charm[valid] = charm_v

    return {"delta": delta, "gamma": gamma, "theta": theta,
            "vega": vega, "charm": charm}
