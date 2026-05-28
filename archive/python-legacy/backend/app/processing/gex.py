"""Gamma Exposure (GEX) calculations.

Following Squeeze Metrics / SpotGamma methodology:

    GEX_per_strike = gamma * <weight> * 100 * underlying_price^2 * 0.01

where ``<weight>`` is **open interest** for the classical (resting) GEX, or
**volume** for the intraday flow-weighted GEX. Both expose the same shape
(curve / net_total / top_positive / top_negative) so the front-end and
indicator can render them identically.

Calls contribute positive GEX (dealer hedging convention), puts negative.
Net GEX = call GEX − |put GEX|.

REV5 fallback (opt-in via ``enable_fallback=True``):

When the requested weight column is genuinely all-zero (no live OI and
no traded volume — common during long off-hours) the GEX curve would be
empty. Setting ``enable_fallback=True`` on the call substitutes a
secondary weight in this priority order:

1. ``volume`` (only when the primary was ``oi``)
2. ``(bid + ask) * 100``  — premium presence
3. ``1`` — uniform weight per contract

This produces a *qualitative* curve so the dashboard renders the
gamma topology even without real OI/volume. ``weight_col`` on the
result reflects the fallback that was actually used.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.processing.zero_gamma import compute_zero_gamma as _compute_zero_gamma

CONTRACT_MULTIPLIER = 100  # standard equity/index option contract size
ONE_PERCENT = 0.01


@dataclass
class GexSummary:
    underlying_price: float | None
    net_total: float
    curve: list[dict]
    top_positive: list[dict]
    top_negative: list[dict]
    zero_gamma: float | None = None
    weight_col: str = "oi"
    weight_source: str = "oi"


def _empty(weight_col: str) -> GexSummary:
    return GexSummary(
        underlying_price=None,
        net_total=0.0,
        curve=[],
        top_positive=[],
        top_negative=[],
        zero_gamma=None,
        weight_col=weight_col,
        weight_source=weight_col,
    )


def _gex_vector(df: pd.DataFrame, S: float, weight_col: str) -> np.ndarray:
    """Vectorised GEX-per-row.

    SPX live chains are ~10–20k rows. Doing this row-by-row via
    :meth:`pandas.DataFrame.apply` was the single hottest CPU path on the
    pipeline and ran 4× per tick (oi, volume, 0DTE×oi, 0DTE×volume). The
    vectorised version is ~50–100× faster and produces bit-identical
    results within the float64 epsilon.
    """
    gamma = df["gamma"].to_numpy(dtype=float, na_value=np.nan)
    weight = df[weight_col].to_numpy(dtype=float, na_value=np.nan)
    sign = np.where(
        df["option_type"].astype(str).str.upper().to_numpy() == "C", 1.0, -1.0
    )
    out = sign * gamma * weight * CONTRACT_MULTIPLIER * (S * S) * ONE_PERCENT
    out = np.where(np.isfinite(out), out, 0.0)
    return out


def compute_gex(
    df: pd.DataFrame,
    *,
    top_n: int = 5,
    weight_col: str = "oi",
    risk_free_rate: float = 0.05,
    enable_fallback: bool = False,
    compute_zero_gamma: bool = True,
) -> GexSummary:
    """Compute GEX curve, net total, top +/- levels, and zero-gamma level.

    Expects a DataFrame with: ``strike``, ``option_type``, ``gamma``,
    ``underlying_price`` and the requested ``weight_col`` (``oi`` or ``volume``).
    For zero-gamma the ``iv`` and ``expiration`` columns are also required;
    if they are missing/null on every row the level is omitted (None).

    If the weight column is missing or fully null, returns an empty summary.

    When ``enable_fallback=True`` and the primary weight is fully zero, a
    secondary weight (volume → premium → uniform) is substituted so the
    user still sees a qualitative curve. ``weight_source`` on the result
    records which fallback was used.

    ``compute_zero_gamma`` toggles the embedded zero-gamma grid scan. The
    pipeline only consumes the chain-vol variant for the snapshot envelope,
    so cohort GEX calls (0DTE, back-month, OI-weighted chain) skip the
    ~50ms scan to keep the warm-tick budget under control.
    """
    if df.empty:
        return _empty(weight_col)
    if weight_col not in df.columns:
        # Materialize the column as zero so the fallback path can still run
        # off bid/ask if requested.
        if not enable_fallback:
            return _empty(weight_col)
        df = df.copy()
        df[weight_col] = 0.0

    spot_series = pd.to_numeric(df["underlying_price"], errors="coerce").dropna()
    spot_series = spot_series[np.isfinite(spot_series)]
    if spot_series.empty:
        return _empty(weight_col)
    S = float(spot_series.iloc[-1])
    if not np.isfinite(S) or S <= 0:
        return _empty(weight_col)

    # Determine the effective weight column (primary unless fallback fires).
    weight_series = pd.to_numeric(df[weight_col], errors="coerce").fillna(0)
    weight_source = weight_col
    effective_col = weight_col

    if weight_series.abs().sum() == 0:
        if not enable_fallback:
            return GexSummary(
                underlying_price=S,
                net_total=0.0,
                curve=[],
                top_positive=[],
                top_negative=[],
                zero_gamma=None,
                weight_col=weight_col,
                weight_source=weight_col,
            )
        # Fallback chain — try in priority order.
        df = df.copy()
        if weight_col == "oi" and "volume" in df.columns:
            vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
            if vol.abs().sum() > 0:
                df["__gex_weight"] = vol
                weight_series = vol
                weight_source = "volume_fallback"
                effective_col = "__gex_weight"
        if weight_source == weight_col:
            bid = pd.to_numeric(df.get("bid"), errors="coerce").fillna(0)
            ask = pd.to_numeric(df.get("ask"), errors="coerce").fillna(0)
            premium = (bid + ask) * 100.0
            if premium.abs().sum() > 0:
                df["__gex_weight"] = premium
                weight_series = premium
                weight_source = "premium_fallback"
                effective_col = "__gex_weight"
        if weight_source == weight_col:
            uniform = pd.Series([1.0] * len(df), index=df.index)
            df["__gex_weight"] = uniform
            weight_series = uniform
            weight_source = "uniform_fallback"
            effective_col = "__gex_weight"

    df = df.copy()
    df[effective_col] = weight_series
    df["gex"] = _gex_vector(df, S, effective_col)
    df["option_type_u"] = df["option_type"].astype(str).str.upper()
    # Defensive: pandas can produce NaN if a row missed the weight column.
    df["gex"] = pd.to_numeric(df["gex"], errors="coerce").fillna(0.0)
    df.loc[~np.isfinite(df["gex"]), "gex"] = 0.0

    call_sum = (
        df.loc[df["option_type_u"] == "C"]
        .groupby("strike", as_index=False)["gex"]
        .sum()
        .rename(columns={"gex": "call_gex"})
    )
    put_sum = (
        df.loc[df["option_type_u"] == "P"]
        .groupby("strike", as_index=False)["gex"]
        .sum()
        .rename(columns={"gex": "put_gex"})
    )
    curve_df = (
        pd.merge(call_sum, put_sum, on="strike", how="outer")
        .fillna({"call_gex": 0.0, "put_gex": 0.0})
    )
    curve_df["strike"] = pd.to_numeric(curve_df["strike"], errors="coerce")
    curve_df = curve_df[np.isfinite(curve_df["strike"])].copy()
    curve_df["strike"] = curve_df["strike"].astype(float)
    curve_df["net_gex"] = curve_df["call_gex"] - curve_df["put_gex"].abs()
    curve_df = curve_df.sort_values("strike").reset_index(drop=True)
    # Collapse any residual non-finite values to 0 so NaN/inf cannot reach the DB.
    for col in ("call_gex", "put_gex", "net_gex"):
        curve_df[col] = pd.to_numeric(curve_df[col], errors="coerce").fillna(0.0)
        curve_df.loc[~np.isfinite(curve_df[col]), col] = 0.0

    top_pos = (
        curve_df.sort_values("net_gex", ascending=False).head(top_n).to_dict(orient="records")
    )
    top_neg = (
        curve_df.sort_values("net_gex", ascending=True).head(top_n).to_dict(orient="records")
    )

    if compute_zero_gamma:
        zg = _compute_zero_gamma(
            df, weight_col=effective_col, risk_free_rate=risk_free_rate
        )
    else:
        zg = None

    net_total_raw = float(curve_df["net_gex"].sum())
    net_total = net_total_raw if np.isfinite(net_total_raw) else 0.0

    return GexSummary(
        underlying_price=S,
        net_total=net_total,
        curve=curve_df.to_dict(orient="records"),
        top_positive=top_pos,
        top_negative=top_neg,
        zero_gamma=zg,
        weight_col=weight_col,
        weight_source=weight_source,
    )
