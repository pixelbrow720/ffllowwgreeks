"""Call / Put wall detection by Open Interest and Volume.

A "wall" is the strike that the dealer book has the largest gross exposure
at, either by resting Open Interest (``by_oi``) or by today's traded volume
(``by_volume``). The top-N strikes are returned per option type with
strictly positive aggregated weight.

Sanity guarantees applied here:

* Non-finite OI/volume (NaN/inf) inputs are coerced to zero before
  aggregation, so they cannot rank above legitimate strikes.
* Strikes that are missing or non-finite are dropped entirely.
* When every strike has zero (or non-finite) weight, the wall list is
  empty rather than a meaningless ordering of zeros.

REV5 fallback (opt-in via ``enable_fallback=True``):

When OI is genuinely 0/NaN across the whole chain — common during
off-hours when ``eod_open_interest`` ingestion has not yet succeeded —
the OI-weighted walls collapse to nothing and the dashboard shows an
empty Key Levels panel. With ``enable_fallback=True`` we substitute a
fallback weight in priority order so the user sees *something*:

1. ``volume``  (non-zero traded contracts today)
2. ``(bid + ask) * 100``  (premium presence — at least the quote exists)
3. ``1``  (uniform weight per contract — last resort)

The volume-weighted (``by_volume``) walls follow the same chain starting
from step 2 if volume itself is zero.

The semantics of the *normal* OI path are unchanged. The fallback only
fires when the requested weight column is fully zero/NaN.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class WallsSummary:
    by_oi: dict
    by_volume: dict


def _top_strikes(
    df: pd.DataFrame, *, value_col: str, option_type: str, top_n: int = 3
) -> list[dict]:
    sub = df[df["option_type"].astype(str).str.upper() == option_type].copy()
    if sub.empty:
        return []

    sub["strike"] = pd.to_numeric(sub.get("strike"), errors="coerce")
    sub = sub[np.isfinite(sub["strike"])].copy()
    sub[value_col] = pd.to_numeric(sub[value_col], errors="coerce").fillna(0.0)
    sub.loc[~np.isfinite(sub[value_col]), value_col] = 0.0
    if sub.empty:
        return []

    grouped = (
        sub.groupby("strike", as_index=False)[value_col]
        .sum()
        .sort_values(value_col, ascending=False)
    )
    # Drop zero-value strikes — when the underlying weight column is all zero
    # the order is arbitrary and call/put walls degenerate to the same strikes,
    # which is misleading. Better to return an empty list and let the caller
    # render "no walls available yet".
    grouped = grouped[grouped[value_col] > 0].head(top_n)
    return [
        {"strike": float(r["strike"]), "value": float(r[value_col] or 0)}
        for _, r in grouped.iterrows()
    ]


def _coerce_numeric_col(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series([0.0] * len(df), index=df.index)
    s = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    s.loc[~np.isfinite(s)] = 0.0
    return s


def _resolve_fallback_weight(
    df: pd.DataFrame, primary_col: str
) -> tuple[pd.Series, str]:
    """Return (weight_series, source_label) honoring the fallback chain.

    ``primary_col`` is the weight the caller asked for. If it sums to >0,
    return that unchanged. Otherwise step through volume → premium → 1.
    """
    primary = _coerce_numeric_col(df, primary_col)
    if primary.abs().sum() > 0:
        return primary, primary_col

    if primary_col != "volume":
        vol = _coerce_numeric_col(df, "volume")
        if vol.abs().sum() > 0:
            return vol, "volume_fallback"

    bid = _coerce_numeric_col(df, "bid")
    ask = _coerce_numeric_col(df, "ask")
    premium = (bid + ask) * 100.0
    if premium.abs().sum() > 0:
        return premium, "premium_fallback"

    if len(df) > 0:
        # Uniform weight per contract — every strike contributes equally
        # so the wall picks the strike with the most contracts, which is
        # at least informative (typically the round-number strikes).
        return pd.Series([1.0] * len(df), index=df.index), "uniform_fallback"

    return primary, primary_col


def compute_walls(
    df: pd.DataFrame, *, top_n: int = 3, enable_fallback: bool = False
) -> WallsSummary:
    if df.empty or "option_type" not in df.columns:
        return WallsSummary(by_oi={}, by_volume={})

    df = df.copy()
    for col in ("oi", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
            df.loc[~np.isfinite(df[col]), col] = 0.0
        else:
            df[col] = 0.0

    if enable_fallback:
        oi_series, oi_source = _resolve_fallback_weight(df, "oi")
        vol_series, vol_source = _resolve_fallback_weight(df, "volume")
        df["__oi_weight"] = oi_series
        df["__vol_weight"] = vol_series
        oi_value_col = "__oi_weight"
        vol_value_col = "__vol_weight"
    else:
        oi_source = "oi"
        vol_source = "volume"
        oi_value_col = "oi"
        vol_value_col = "volume"

    return WallsSummary(
        by_oi={
            "call_wall": _top_strikes(
                df, value_col=oi_value_col, option_type="C", top_n=top_n
            ),
            "put_wall": _top_strikes(
                df, value_col=oi_value_col, option_type="P", top_n=top_n
            ),
            "weight_source": oi_source,
        },
        by_volume={
            "call_wall": _top_strikes(
                df, value_col=vol_value_col, option_type="C", top_n=top_n
            ),
            "put_wall": _top_strikes(
                df, value_col=vol_value_col, option_type="P", top_n=top_n
            ),
            "weight_source": vol_source,
        },
    )
