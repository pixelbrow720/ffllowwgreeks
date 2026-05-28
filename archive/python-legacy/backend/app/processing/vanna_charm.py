"""Aggregate Vanna and Charm dealer-exposure surfaces.

These complement the GEX module: while GEX captures dealer hedging
sensitivity to *spot moves*, Vanna captures sensitivity to *implied
volatility moves* (a major flow driver around Fed days, CPI, and other
vol shocks), and Charm captures sensitivity to *time decay* (the dominant
0DTE pinning mechanic in the last 90 minutes of the session).

Sign convention (dealer's book is short customer flow):

* Calls contribute **+** vanna and **+** charm (dealer holds a short call).
* Puts contribute **−** vanna and **−** charm (dealer holds a short put).

This matches the SqueezeMetrics convention used by ``compute_gex``.

Output schema mirrors ``GexSummary`` so persistence / API code can stay
uniform: net total + per-strike curve + top-3 long/short levels.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.processing import bsm
from app.processing.session import TAU_FLOOR_YEARS, _now_eastern, calendar_tau_years

CONTRACT_MULTIPLIER = 100

# Charm has units of delta-per-year. We rescale ``dollar_charm`` to a
# *per-business-day* quantity for the dashboard so the y-axis reads as
# "dollars of dealer hedge to roll over the next trading session".
# 252 = the standard NYSE / CME business-day count; intentionally
# different from the 365.0 calendar-day basis used by ``calendar_tau_years``
# (no overnight charm-rolling on weekends / holidays).
BUSINESS_DAYS_PER_YEAR: float = 252.0


@dataclass
class GreekSummary:
    underlying_price: float | None
    net_total: float
    curve: list[dict]
    top_positive: list[dict]
    top_negative: list[dict]
    weight_col: str = "oi"


def _empty(weight_col: str) -> GreekSummary:
    return GreekSummary(
        underlying_price=None,
        net_total=0.0,
        curve=[],
        top_positive=[],
        top_negative=[],
        weight_col=weight_col,
    )


def _prepare(
    df: pd.DataFrame,
    *,
    weight_col: str,
    today: pd.Timestamp | None,
) -> tuple[pd.DataFrame | None, float | None]:
    """Validate the chain DataFrame and return an array-friendly subset.

    Returns ``(work_df, S)`` or ``(None, None)`` if computation is not
    possible (missing columns, empty chain, all-zero weights, non-finite
    spot, …).
    """
    required = {"strike", "option_type", "iv", "expiration", "underlying_price", weight_col}
    if df.empty or not required.issubset(df.columns):
        return None, None

    spot_series = pd.to_numeric(df["underlying_price"], errors="coerce").dropna()
    spot_series = spot_series[np.isfinite(spot_series)]
    if spot_series.empty:
        return None, None
    S = float(spot_series.iloc[-1])
    if not np.isfinite(S) or S <= 0:
        return None, None

    if today is None:
        today = pd.Timestamp(_now_eastern().date())
    today_d = today.date() if hasattr(today, "date") else today

    work = df[["strike", "option_type", "iv", "expiration", weight_col]].copy()
    work["weight"] = pd.to_numeric(work[weight_col], errors="coerce").fillna(0.0)
    work.loc[~np.isfinite(work["weight"]), "weight"] = 0.0
    work["iv"] = pd.to_numeric(work["iv"], errors="coerce")
    work["strike"] = pd.to_numeric(work["strike"], errors="coerce")
    work["tau"] = calendar_tau_years(work["expiration"], today=today_d)
    work = work[
        (work["weight"].abs() > 0)
        & np.isfinite(work["weight"])
        & work["iv"].notna()
        & np.isfinite(work["iv"])
        & (work["iv"] > 0)
        & work["strike"].notna()
        & np.isfinite(work["strike"])
        & (work["strike"] > 0)
        & (work["tau"] > 0)
        & np.isfinite(work["tau"])
    ]
    if work.empty:
        return None, None

    return work, S


def _signed_aggregate(work: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """Sum ``value_col`` per strike, with calls positive and puts negative.

    Non-finite per-row values (NaN/inf produced by extreme BSM inputs) are
    zero-filled so they cannot poison the per-strike aggregate. This matches
    the dealer-hedging sign convention shared with ``compute_gex``: long-call
    customer flow is positive and long-put customer flow is negative.
    """
    sign = np.where(
        work["option_type"].astype(str).str.upper() == "C", 1.0, -1.0
    )
    raw = pd.to_numeric(work[value_col], errors="coerce").fillna(0.0)
    raw = raw.where(np.isfinite(raw), 0.0)
    work["_signed"] = sign * raw
    out = (
        work.groupby("strike", as_index=False)["_signed"]
        .sum()
        .rename(columns={"_signed": value_col})
        .sort_values("strike")
        .reset_index(drop=True)
    )
    out[value_col] = pd.to_numeric(out[value_col], errors="coerce").fillna(0.0)
    out.loc[~np.isfinite(out[value_col]), value_col] = 0.0
    return out


def _summarise(curve_df: pd.DataFrame, value_col: str, *,
               S: float, weight_col: str, top_n: int) -> GreekSummary:
    if curve_df.empty:
        return _empty(weight_col)
    top_pos = (
        curve_df.sort_values(value_col, ascending=False).head(top_n)
        .to_dict(orient="records")
    )
    top_neg = (
        curve_df.sort_values(value_col, ascending=True).head(top_n)
        .to_dict(orient="records")
    )
    net_total = float(curve_df[value_col].sum())
    if not np.isfinite(net_total):
        net_total = 0.0
    return GreekSummary(
        underlying_price=S,
        net_total=net_total,
        curve=curve_df.to_dict(orient="records"),
        top_positive=top_pos,
        top_negative=top_neg,
        weight_col=weight_col,
    )


def compute_vanna(
    df: pd.DataFrame,
    *,
    weight_col: str = "oi",
    risk_free_rate: float = 0.05,
    today: pd.Timestamp | None = None,
    top_n: int = 5,
    tau_years: float | None = None,
) -> GreekSummary:
    """Aggregate dealer **Vanna** exposure curve, in dollars per +1 vol point.

    ``tau_years`` mirrors :func:`compute_charm` — when provided, every
    row's calendar-day τ is overridden with that value (e.g. the
    session-aware 0DTE τ from
    :func:`app.processing.session.time_to_expiry_0dte_years`). All τ
    inputs are floored at :data:`TAU_FLOOR_YEARS` (15 minutes) so the
    BSM Greeks stay numerically stable in the last few minutes before
    expiry.
    """
    work, S = _prepare(df, weight_col=weight_col, today=today)
    if work is None:
        return _empty(weight_col)

    K = work["strike"].to_numpy(dtype=float)
    sigma = work["iv"].to_numpy(dtype=float)
    if tau_years is not None and np.isfinite(tau_years) and tau_years > 0:
        tau = np.full(len(work), float(tau_years), dtype=float)
    else:
        tau = work["tau"].to_numpy(dtype=float)
    tau = np.maximum(tau, TAU_FLOOR_YEARS)
    weight = work["weight"].to_numpy(dtype=float)

    # Per-option vanna (∂Δ/∂σ). Convert to per-1-vol-point by ×0.01.
    vanna_per = bsm.vanna(S, K, tau, sigma, r=risk_free_rate)
    # Dollar-vanna = vanna × M × weight × 0.01 (per +1 vol pt) × S
    dollar_vanna = vanna_per * weight * CONTRACT_MULTIPLIER * S * 0.01

    work["_vanna"] = dollar_vanna
    curve_df = _signed_aggregate(work, "_vanna").rename(
        columns={"_vanna": "vanna_exposure"}
    )
    return _summarise(curve_df, "vanna_exposure",
                      S=S, weight_col=weight_col, top_n=top_n)


def compute_charm(
    df: pd.DataFrame,
    *,
    weight_col: str = "oi",
    risk_free_rate: float = 0.05,
    today: pd.Timestamp | None = None,
    top_n: int = 5,
    tau_years: float | None = None,
) -> GreekSummary:
    """Aggregate dealer **Charm** exposure curve, in dollars per business day.

    When ``tau_years`` is provided, every row's ``tau`` is overridden with
    that value. This lets 0DTE callers pass a session-aware τ (e.g.
    :func:`app.processing.session.time_to_expiry_0dte_years`) so the
    per-strike CHARM_0DTE_LEVEL rows match the scalar
    CHARM_0DTE_DECAY_RATE. ``None`` keeps the calendar-day floor used by
    the multi-expiry default path.

    All τ inputs are floored at :data:`TAU_FLOOR_YEARS` (15 minutes) to
    keep Greeks stable as expiry approaches.
    """
    work, S = _prepare(df, weight_col=weight_col, today=today)
    if work is None:
        return _empty(weight_col)

    K = work["strike"].to_numpy(dtype=float)
    sigma = work["iv"].to_numpy(dtype=float)
    if tau_years is not None and np.isfinite(tau_years) and tau_years > 0:
        tau = np.full(len(work), float(tau_years), dtype=float)
    else:
        tau = work["tau"].to_numpy(dtype=float)
    tau = np.maximum(tau, TAU_FLOOR_YEARS)
    weight = work["weight"].to_numpy(dtype=float)
    is_call = work["option_type"].astype(str).str.upper().to_numpy() == "C"

    # Per-option charm is in delta-per-year. Multiply by S to convert to
    # dollar-delta change per year, then scale to per-business-day.
    charm_per = bsm.charm(S, K, tau, sigma, r=risk_free_rate,
                          option_type=np.where(is_call, "C", "P"))
    dollar_charm = (
        charm_per * weight * CONTRACT_MULTIPLIER * S
    ) / BUSINESS_DAYS_PER_YEAR

    work["_charm"] = dollar_charm
    curve_df = _signed_aggregate(work, "_charm").rename(
        columns={"_charm": "charm_exposure"}
    )
    return _summarise(curve_df, "charm_exposure",
                      S=S, weight_col=weight_col, top_n=top_n)
