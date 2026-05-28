"""Rev 4 — 0DTE-first analytics engine.

This module splits the chain into two cohorts and runs the standard GEX
+ Charm + Pin probability + Flip-speed math on each separately. The
intuition behind the split:

* **0DTE** (expires *today*): driver of intraday gamma squeezes, charm
  pinning, and end-of-day mechanical flows. Dealer hedging is highly
  reactive to spot moves because Γ is large near ATM.
* **Back-month** (expires later): the structural "regime" cohort. GEX
  here moves slowly; flip levels here define the multi-day backdrop.

The two cohorts are computed with the *same* primitives (``compute_gex``,
``compute_charm``, etc.) so signs, weights, and downstream consumers
don't have to know about the split — they only see the new
``metric_type`` discriminators.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.processing import bsm
from app.processing.gex import GexSummary, compute_gex
from app.processing.session import (
    TAU_FLOOR_YEARS,
    _now_eastern,
    time_to_expiry_0dte_years,
)
from app.processing.vanna_charm import GreekSummary, compute_charm

logger = get_logger(__name__)

# Calendar-year convention shared with the rest of the processing layer
# (iv, vanna_charm, zero_gamma, pin_probability all consume τ in
# 365.0-day-years). The intraday session-aware τ helper in session.py
# uses ``365.25 * 86400`` seconds-per-year for sub-day precision, but
# the calendar floor and per-hour scaling here stay on the 365.0 axis so
# every downstream module agrees on what "1 day" means.
HOURS_PER_YEAR = 365.0 * 24.0


def _today_eastern() -> date:
    """Today's calendar date in America/New_York (DST-aware)."""
    return _now_eastern().date()


@dataclass
class ZeroDteSummary:
    """Bundle of every 0DTE-specific metric computed from one snapshot."""

    has_0dte: bool
    gex_oi: GexSummary
    gex_vol: GexSummary
    charm: GreekSummary
    charm_decay_rate: float
    """Mean |Δ-per-hour| across ATM 0DTE rows, expressed as a fraction."""
    flip_speed: float
    """``|net_gex_now − net_gex_prev| / Δt_seconds``. 0 on the first tick."""
    tau_years: float
    """The intraday tau used when computing these summaries."""


@dataclass
class BackMonthSummary:
    """Bundle of back-month metrics — same shape as the chain-wide Rev 3 output."""

    gex_oi: GexSummary
    gex_vol: GexSummary


# ──────────────────────────────────────────────────────────────────────────


def _to_date(value) -> date | None:
    """Coerce a pandas/datetime-y value into a plain ``date``."""
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return None
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if pd.isna(ts):
        return None
    return ts.date()


def split_by_expiry(
    df: pd.DataFrame,
    *,
    today: date | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split a chain snapshot into ``(zero_dte, back_month)`` slices.

    A row is 0DTE when its ``expiration`` resolves to the same calendar
    date as ``today``. Rows without a usable expiration are kept on the
    back-month side so they still contribute to the long-horizon view
    (the 0DTE side stays strictly defined).
    """
    if df.empty or "expiration" not in df.columns:
        return df.iloc[0:0].copy(), df.copy()
    if today is None:
        today = _today_eastern()

    work = df.copy()
    work["_expiry_date"] = work["expiration"].map(_to_date)
    is_today = work["_expiry_date"] == today
    zero = work.loc[is_today].drop(columns=["_expiry_date"])
    back = work.loc[~is_today].drop(columns=["_expiry_date"])
    return zero.reset_index(drop=True), back.reset_index(drop=True)


# ──────────────────────────────────────────────────────────────────────────
# Charm-decay rate (Rev 4): mean |Δ-per-hour| across ATM 0DTE rows
# ──────────────────────────────────────────────────────────────────────────


def compute_charm_decay_rate(
    zero_dte_df: pd.DataFrame,
    *,
    atm_band_pct: float = 0.005,
    tau_years: float | None = None,
    risk_free_rate: float = 0.05,
) -> float:
    """Per-hour |Δ-decay| across ATM 0DTE rows, expressed as a fraction.

    Computes BSM charm in-line (the upstream feed never populates a
    ``charm`` column) and then aggregates the absolute value across rows
    within ``atm_band_pct`` of spot. Charm from :mod:`app.processing.bsm`
    is per-year, so we divide by ``365 * 24`` to land on a per-hour rate
    (e.g. 0.012 = 1.2 %/hr of delta lost to time).

    Methodology: only ATM rows contribute because that's where the
    pinning pressure lives — far OTM/ITM 0DTE charm is dominated by
    boundary effects that aren't actionable.

    Returns 0.0 when the cohort is empty or unusable.
    """
    if zero_dte_df.empty:
        return 0.0
    needed = {"strike", "underlying_price", "iv", "option_type"}
    if not needed.issubset(zero_dte_df.columns):
        return 0.0

    spot_series = pd.to_numeric(zero_dte_df["underlying_price"], errors="coerce").dropna()
    if spot_series.empty:
        return 0.0
    spot = float(spot_series.iloc[-1])
    if not np.isfinite(spot) or spot <= 0:
        return 0.0

    band = spot * atm_band_pct
    strikes_all = pd.to_numeric(zero_dte_df["strike"], errors="coerce")
    near_atm = (strikes_all - spot).abs() <= band
    sub = zero_dte_df.loc[near_atm].copy()
    if sub.empty:
        return 0.0

    sub["iv"] = pd.to_numeric(sub["iv"], errors="coerce")
    sub["strike"] = pd.to_numeric(sub["strike"], errors="coerce")
    sub = sub[sub["iv"].notna() & (sub["iv"] > 0) & sub["strike"].notna() & (sub["strike"] > 0)]
    if sub.empty:
        return 0.0

    tau = float(tau_years) if tau_years is not None else time_to_expiry_0dte_years()
    tau = max(TAU_FLOOR_YEARS, tau)

    K = sub["strike"].to_numpy(dtype=float)
    sigma = sub["iv"].to_numpy(dtype=float)
    is_call = sub["option_type"].astype(str).str.upper().to_numpy() == "C"
    charm_arr = bsm.charm(
        spot, K, tau, sigma, r=risk_free_rate,
        option_type=np.where(is_call, "C", "P"),
    )
    charm_arr = np.asarray(charm_arr, dtype=float)
    charm_arr = charm_arr[np.isfinite(charm_arr)]
    if charm_arr.size == 0:
        return 0.0

    return float(np.mean(np.abs(charm_arr)) / HOURS_PER_YEAR)


# ──────────────────────────────────────────────────────────────────────────
# Flip-speed (Rev 4): time-derivative of net GEX
# ──────────────────────────────────────────────────────────────────────────


def compute_flip_speed(
    *,
    net_gex_now: float,
    net_gex_prev: float | None,
    elapsed_seconds: float,
) -> float:
    """Numerical time-derivative of net GEX.

    Used as an indicator of *how fast* the dealer book is flipping from
    long-gamma to short-gamma (or vice versa). Defensive: returns 0 when
    we don't yet have a prior observation or when elapsed time is
    pathologically small.
    """
    if net_gex_prev is None or not np.isfinite(net_gex_prev):
        return 0.0
    if not np.isfinite(net_gex_now) or not np.isfinite(elapsed_seconds):
        return 0.0
    if elapsed_seconds < 0:
        # Clock rewind (NTP step / DST glitch) — treat as no-op so we don't
        # report a fictitious flip from a negative dt.
        return 0.0
    if elapsed_seconds <= 0.5:  # noise floor
        return 0.0
    return float(abs(net_gex_now - net_gex_prev) / elapsed_seconds)


# ──────────────────────────────────────────────────────────────────────────
# High-level entry points
# ──────────────────────────────────────────────────────────────────────────


def compute_zero_dte_summary(
    df: pd.DataFrame,
    *,
    risk_free_rate: float,
    atm_band_pct: float = 0.005,
    today: date | None = None,
    prev_net_gex: float | None = None,
    prev_ts_seconds: float | None = None,
    now_ts_seconds: float | None = None,
) -> ZeroDteSummary:
    """Build the full 0DTE summary from a chain snapshot.

    Empty / non-0DTE days are handled gracefully: ``has_0dte=False`` and
    every metric falls back to zero so downstream callers still write a
    full set of rows (with ``extra_json={"reason": "no_0dte_today"}``).
    """
    zero, _ = split_by_expiry(df, today=today)
    if zero.empty:
        empty_summary = GexSummary(
            underlying_price=None,
            net_total=0.0,
            curve=[],
            top_positive=[],
            top_negative=[],
            zero_gamma=None,
            weight_col="oi",
        )
        empty_charm = GreekSummary(
            underlying_price=None,
            net_total=0.0,
            curve=[],
            top_positive=[],
            top_negative=[],
            weight_col="oi",
        )
        return ZeroDteSummary(
            has_0dte=False,
            gex_oi=empty_summary,
            gex_vol=GexSummary(
                underlying_price=None,
                net_total=0.0,
                curve=[],
                top_positive=[],
                top_negative=[],
                zero_gamma=None,
                weight_col="volume",
            ),
            charm=empty_charm,
            charm_decay_rate=0.0,
            flip_speed=0.0,
            tau_years=time_to_expiry_0dte_years(),
        )

    tau = time_to_expiry_0dte_years()
    gex_oi = compute_gex(
        zero, weight_col="oi", risk_free_rate=risk_free_rate,
        compute_zero_gamma=False,
    )
    gex_vol = compute_gex(
        zero, weight_col="volume", risk_free_rate=risk_free_rate,
        compute_zero_gamma=False,
    )
    charm_summary = compute_charm(
        zero, weight_col="oi", risk_free_rate=risk_free_rate, tau_years=tau
    )
    charm_decay = compute_charm_decay_rate(
        zero,
        atm_band_pct=atm_band_pct,
        tau_years=tau,
        risk_free_rate=risk_free_rate,
    )
    flip = 0.0
    if (
        prev_ts_seconds is not None
        and now_ts_seconds is not None
        and prev_net_gex is not None
    ):
        flip = compute_flip_speed(
            net_gex_now=gex_oi.net_total,
            net_gex_prev=prev_net_gex,
            elapsed_seconds=float(now_ts_seconds - prev_ts_seconds),
        )

    return ZeroDteSummary(
        has_0dte=True,
        gex_oi=gex_oi,
        gex_vol=gex_vol,
        charm=charm_summary,
        charm_decay_rate=charm_decay,
        flip_speed=flip,
        tau_years=tau,
    )


def compute_back_month_summary(
    df: pd.DataFrame,
    *,
    risk_free_rate: float,
    today: date | None = None,
) -> BackMonthSummary:
    """Same metrics as Rev 3's chain-wide GEX, but restricted to non-0DTE rows."""
    _, back = split_by_expiry(df, today=today)
    gex_oi = compute_gex(
        back, weight_col="oi", risk_free_rate=risk_free_rate,
        compute_zero_gamma=False,
    )
    gex_vol = compute_gex(
        back, weight_col="volume", risk_free_rate=risk_free_rate,
        compute_zero_gamma=False,
    )
    return BackMonthSummary(gex_oi=gex_oi, gex_vol=gex_vol)


__all__ = [
    "BackMonthSummary",
    "ZeroDteSummary",
    "compute_back_month_summary",
    "compute_charm_decay_rate",
    "compute_flip_speed",
    "compute_zero_dte_summary",
    "split_by_expiry",
]
