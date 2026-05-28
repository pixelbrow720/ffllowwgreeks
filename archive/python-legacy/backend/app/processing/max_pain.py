"""Classic max-pain calculation per expiration plus an aggregate over the nearest 5.

Max pain answers: at expiration, which underlying price ``K*`` minimises the
total dollar loss across all open contracts (calls + puts × OI)?

Behaviour:

* ``compute_max_pain(df)`` (default) — preserves the historical pipeline
  behaviour: one max-pain strike per distinct expiration, plus an
  ``aggregate_*`` pair folded across the nearest ``aggregate_n`` expirations.
* ``compute_max_pain(df, expiry=<value>)`` — restricts the calculation to a
  single expiration. Both the per-expiry list and the aggregate strike then
  reflect just that expiry, which mirrors the optional ``expiry`` query
  parameter on ``GET /v1/{symbol}/max-pain``.
* ``compute_max_pain(df, expiry=None, fold_all=True)`` — folds every
  expiration into a single OI distribution before solving. Useful for a
  global pin level when the caller doesn't care about expiration buckets.

NaN/inf safety: every numeric column is coerced to numeric and non-finite
values are dropped or zero-filled, so the resulting pain curve and strike
are guaranteed finite floats (or ``None`` when no contracts qualify).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app.processing.session import _now_eastern

CONTRACT_MULTIPLIER = 100


def _today_eastern() -> date:
    return _now_eastern().date()


@dataclass
class MaxPainSummary:
    per_expiry: list[dict]
    aggregate_strike: float | None
    aggregate_value: float | None


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows missing strike / option_type and coerce numerics to finite floats."""
    if df.empty or "strike" not in df.columns or "option_type" not in df.columns:
        return df.iloc[0:0]
    out = df.copy()
    out["strike"] = pd.to_numeric(out["strike"], errors="coerce")
    out = out[np.isfinite(out["strike"])]
    if "oi" not in out.columns:
        out["oi"] = 0.0
    out["oi"] = pd.to_numeric(out["oi"], errors="coerce").fillna(0.0)
    out.loc[~np.isfinite(out["oi"]), "oi"] = 0.0
    return out


def _expiry_max_pain(sub: pd.DataFrame) -> tuple[float | None, float | None, list[dict]]:
    """Return (strike, total dollar pain at strike, full pain curve).

    Vectorised pain curve via broadcasting (Rev 12 NUM2-3 — restores
    the Rev 7 C4 fix, which had been reverted in the interim). For
    candidate strikes ``S = strikes`` (shape ``(K,)``), call-side
    strikes/OI ``cs/co`` (shape ``(M,)``), and put-side ``ps/po``
    (shape ``(N,)``):

        call_pay = np.maximum(S[:,None] - cs[None,:], 0) @ co  # (K,)
        put_pay  = np.maximum(ps[None,:] - S[:,None], 0) @ po  # (K,)
        total    = (call_pay + put_pay) * CONTRACT_MULTIPLIER

    Replaces an O(K·(M+N)) Python loop with a single matmul. ~50×
    speedup on SPX (K~6500). Tie-breaking matches the prior loop:
    strikes are sorted ascending and ``np.argmin`` returns the first
    occurrence, so the lowest strike wins on ties (NUM2-2).
    """
    if sub.empty:
        return None, None, []

    strikes = np.sort(sub["strike"].dropna().unique())
    strikes = strikes[np.isfinite(strikes)]
    if strikes.size == 0:
        return None, None, []

    is_call = sub["option_type"].astype(str).str.upper() == "C"
    calls = sub[is_call]
    puts = sub[~is_call]

    call_strikes = calls["strike"].to_numpy(dtype=float)
    call_oi = calls["oi"].to_numpy(dtype=float)
    put_strikes = puts["strike"].to_numpy(dtype=float)
    put_oi = puts["oi"].to_numpy(dtype=float)

    if call_strikes.size:
        call_loss = (
            np.maximum(strikes[:, None] - call_strikes[None, :], 0.0) @ call_oi
        )
    else:
        call_loss = np.zeros(strikes.shape, dtype=float)

    if put_strikes.size:
        put_loss = (
            np.maximum(put_strikes[None, :] - strikes[:, None], 0.0) @ put_oi
        )
    else:
        put_loss = np.zeros(strikes.shape, dtype=float)

    total = (call_loss + put_loss) * CONTRACT_MULTIPLIER
    # Match prior per-strike fallback: non-finite contributions become 0.
    total = np.where(np.isfinite(total), total, 0.0)

    pain_curve: list[dict] = [
        {"strike": float(strikes[i]), "pain": float(total[i])}
        for i in range(strikes.size)
    ]
    best_idx = int(np.argmin(total))
    return float(strikes[best_idx]), float(total[best_idx]), pain_curve


def compute_max_pain(
    df: pd.DataFrame,
    *,
    aggregate_n: int = 5,
    expiry: str | pd.Timestamp | None = None,
    fold_all: bool = False,
    today: date | pd.Timestamp | None = None,
) -> MaxPainSummary:
    """Compute max pain per expiration and an aggregate across the nearest ``aggregate_n``.

    Args:
        df: Option chain with ``strike``, ``option_type``, ``oi``, and
            (when applicable) ``expiration`` columns.
        aggregate_n: Number of nearest expirations folded into the
            aggregate pain distribution. Ignored when ``fold_all`` is set
            or ``expiry`` is provided.
        expiry: Optional single expiration to filter to. When set, both the
            per-expiry list and the aggregate reflect that expiry only.
        fold_all: When True (and ``expiry`` is ``None``), every row is folded
            into a single distribution rather than bucketed per-expiry.
        today: Reference "today" used by the expired-row filter
            (DR-15). Defaults to the eastern-time today; tests can pin a
            deterministic date by passing it explicitly. Ignored on the
            single-``expiry`` path so explicit queries still get an
            answer for any date.

    Returns:
        ``MaxPainSummary`` with ``per_expiry`` rows, ``aggregate_strike`` and
        ``aggregate_value``. The strike/value are ``None`` when no contracts
        qualify.
    """
    df = _clean(df)
    if df.empty:
        return MaxPainSummary(per_expiry=[], aggregate_strike=None, aggregate_value=None)

    has_expiration = "expiration" in df.columns and not df["expiration"].isna().all()

    # Parse once. The per-row equality check used inside the per-expiry loop
    # (and the single-expiry filter below) used to call ``pd.to_datetime`` on
    # the full ``expiration`` Series for every iteration — at 30 expiries on
    # SPX that's 30 reparses of the same column per tick. Caching once
    # collapses it to one reparse plus N elementwise comparisons.
    parsed_all: pd.Series | None = None
    active_mask: pd.Series | None = None
    if has_expiration:
        parsed_all = pd.to_datetime(df["expiration"], errors="coerce")
        # DR-15: drop rows whose expiration has already passed before the
        # per-expiry / aggregate loops run. Yesterday-expired rows would
        # otherwise sit in both the per-expiry list and the aggregate
        # OI distribution; explicit ``expiry=`` queries still get an
        # answer (the single-expiry branch below skips this filter).
        if today is None:
            today_d = _today_eastern()
        elif isinstance(today, pd.Timestamp):
            today_d = today.date()
        else:
            today_d = today
        # Normalise tz-awareness for the comparison: ``pd.to_datetime``
        # will return a tz-aware DatetimeArray when the source carries
        # timezone offsets and tz-naive otherwise. Coerce to date so the
        # comparison is tz-agnostic.
        parsed_dates = parsed_all.apply(
            lambda v: v.date() if pd.notna(v) else None
        )
        active_mask = parsed_dates.apply(
            lambda d: d is not None and d >= today_d
        )

    # Single-expiry filter takes precedence.
    if expiry is not None:
        if not has_expiration or parsed_all is None:
            return MaxPainSummary(per_expiry=[], aggregate_strike=None, aggregate_value=None)
        try:
            target = pd.Timestamp(expiry)
        except (TypeError, ValueError):
            return MaxPainSummary(per_expiry=[], aggregate_strike=None, aggregate_value=None)
        sub = df[parsed_all == target]
        strike, value, curve = _expiry_max_pain(sub)
        per_expiry: list[dict] = []
        if strike is not None:
            per_expiry = [
                {
                    "expiration": str(target.date()),
                    "strike": strike,
                    "pain": value,
                    "curve": curve,
                }
            ]
        return MaxPainSummary(
            per_expiry=per_expiry,
            aggregate_strike=strike,
            aggregate_value=value,
        )

    # Fold-all path: single distribution across every expiration.
    if fold_all or not has_expiration:
        fold_df = df
        if has_expiration and parsed_all is not None:
            fold_df = df.loc[active_mask]
        strike, value, curve = _expiry_max_pain(fold_df)
        per_expiry = (
            [{"expiration": "all", "strike": strike, "pain": value, "curve": curve}]
            if strike is not None
            else []
        )
        return MaxPainSummary(
            per_expiry=per_expiry,
            aggregate_strike=strike,
            aggregate_value=value,
        )

    # Default: per-expiry list + aggregate across the nearest ``aggregate_n``.
    per_expiry = []
    assert parsed_all is not None  # has_expiration guard above
    parsed_active = parsed_all.where(active_mask)
    parsed_unique = parsed_active.dropna().unique()
    expiries_sorted = sorted(pd.to_datetime(parsed_unique))
    for exp_ts in expiries_sorted:
        sub = df[parsed_all == exp_ts]
        strike, value, curve = _expiry_max_pain(sub)
        per_expiry.append(
            {
                "expiration": str(pd.Timestamp(exp_ts).date()),
                "strike": strike,
                "pain": value,
                "curve": curve,
            }
        )

    nearest = [pd.Timestamp(e) for e in expiries_sorted[:aggregate_n]]
    sub = df[parsed_all.isin(nearest)]
    agg_strike, agg_value, _ = _expiry_max_pain(sub)

    return MaxPainSummary(
        per_expiry=per_expiry,
        aggregate_strike=agg_strike,
        aggregate_value=agg_value,
    )
