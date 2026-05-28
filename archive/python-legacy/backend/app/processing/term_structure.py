"""IV term-structure and Risk Reversal computation.

Per-expiry features extracted from the live options chain:

* **ATM IV** — implied vol at the strike closest to spot.
* **Risk Reversal (25Δ)** — call IV minus put IV at ±25Δ. Positive RR
  ⇒ call skew is rich (bullish premium); negative RR ⇒ put skew is rich
  (bearish premium / crash hedge bid).

The function is purely vectorised; outputs are JSON-serialisable lists
ready to drop into ``computed_metrics.extra_json``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.processing.session import _now_eastern


@dataclass
class TermStructurePoint:
    expiration: str           # ISO date
    days_to_expiry: int
    atm_iv: float | None
    call_25d_iv: float | None
    put_25d_iv: float | None
    risk_reversal_25d: float | None


def compute_term_structure(
    df: pd.DataFrame,
    *,
    today: pd.Timestamp | None = None,
) -> list[dict]:
    """Build a term-structure of IV + 25Δ risk-reversal per expiration.

    Required columns: ``strike``, ``expiration``, ``option_type``, ``iv``,
    ``delta``, ``underlying_price``.
    """
    needed = {"strike", "expiration", "option_type", "iv", "delta", "underlying_price"}
    if df.empty or not needed.issubset(df.columns):
        return []

    spot_series = df["underlying_price"].dropna()
    if spot_series.empty:
        return []
    S = float(spot_series.iloc[-1])

    if today is None:
        # CORR-2 (Rev 12): default to the eastern session date so
        # term_structure agrees with max_pain / iv / zero_dte on which
        # expirations are "expired". Using ``pd.Timestamp.utcnow`` here
        # disagreed with the rest of the pipeline between 20:00–04:00 UTC
        # on weekday-eves (UTC=Tue, ET=Mon), letting a Mon-expiry row leak
        # in with ``dte=0`` after every other module had filtered it.
        today = pd.Timestamp(_now_eastern().date())
    today_d = today.date() if hasattr(today, "date") else today

    work = df.copy()
    work["iv"] = pd.to_numeric(work["iv"], errors="coerce")
    work["delta"] = pd.to_numeric(work["delta"], errors="coerce")
    work["strike"] = pd.to_numeric(work["strike"], errors="coerce")
    work = work[
        work["iv"].notna()
        & (work["iv"] > 0)
        & work["strike"].notna()
        & work["expiration"].notna()
    ]
    if work.empty:
        return []

    out: list[dict] = []
    for expiration, group in work.groupby("expiration"):
        try:
            exp_d = pd.Timestamp(expiration).date()
            dte = max(0, (exp_d - today_d).days)
        except (TypeError, ValueError):
            continue

        atm_iv = _atm_iv(group, S)
        call_25d = _delta_iv(group, target_delta=0.25, option_type="C")
        put_25d = _delta_iv(group, target_delta=-0.25, option_type="P")
        rr = None
        if call_25d is not None and put_25d is not None:
            rr = float(call_25d - put_25d)
        out.append(
            {
                "expiration": exp_d.isoformat(),
                "days_to_expiry": dte,
                "atm_iv": atm_iv,
                "call_25d_iv": call_25d,
                "put_25d_iv": put_25d,
                "risk_reversal_25d": rr,
            }
        )
    out.sort(key=lambda r: r["days_to_expiry"])
    return out


def _atm_iv(group: pd.DataFrame, spot: float) -> float | None:
    """Average call+put IV at the strike closest to spot."""
    if group.empty:
        return None
    nearest = group.iloc[(group["strike"] - spot).abs().argsort()[:2]]
    iv_vals = nearest["iv"].dropna()
    if iv_vals.empty:
        return None
    return float(iv_vals.mean())


def _delta_iv(
    group: pd.DataFrame,
    *,
    target_delta: float,
    option_type: str,
    proximity_threshold: float = 0.07,
) -> float | None:
    """IV at the option whose delta is closest to ``target_delta``.

    For calls we expect 0 ≤ delta ≤ 1; for puts -1 ≤ delta ≤ 0. We filter
    by option_type and find the row whose delta is nearest to the target.

    On a sparse expiry the row "closest" to ±0.25Δ may be a 50Δ ATM print —
    that's not a 25Δ skew, it's just the only contract listed. Returning
    its IV pollutes the term-structure surface (reported call_25d_iv ends
    up reading the ATM smile, not the wing). We require the nearest delta
    to be within ``proximity_threshold`` (default 0.07, i.e. accept rows
    whose delta sits in the 0.18-0.32 band for a 25Δ target). Rows further
    than the threshold return ``None``; the consumer already filters
    ``None`` so the risk-reversal field is suppressed for that expiry.
    """
    sub = group[group["option_type"].astype(str).str.upper() == option_type.upper()]
    sub = sub[sub["delta"].notna()]
    if sub.empty:
        return None
    diffs = np.abs(sub["delta"].to_numpy(dtype=float) - target_delta)
    idx = int(np.argmin(diffs))
    if diffs[idx] > proximity_threshold:
        return None
    iv = sub.iloc[idx]["iv"]
    if pd.isna(iv):
        return None
    return float(iv)
