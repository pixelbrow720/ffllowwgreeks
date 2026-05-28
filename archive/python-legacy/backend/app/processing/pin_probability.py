"""0DTE pin-probability heatmap.

Models the probability that the underlying *closes the session* at each
0DTE strike, weighted by:

* **Open interest** — the larger the dealer's net long-gamma position at
  a strike, the stronger the *gamma-pin* effect (dealers buy weakness /
  sell strength to stay flat as expiry approaches).
* **Charm** — toward expiry, dealer hedges roll into the underlying at a
  rate proportional to charm. We use ``|charm|`` magnitude per strike as
  a *dynamic* pinning force.
* **Distance from current spot** — exponential decay weighted by the
  remaining ATM straddle implied move (i.e. P(close near K) ∝
  exp(−½ · ((K-S)/σ_remaining)²) ).

Final score per strike::

    raw_i      = (oi_call + oi_put + charm_weight · |charm_i|) · gauss((K_i-S)/σ)
    prob_i     = raw_i / Σ_j raw_j

This is **not** a calibrated probability — it's a relative likelihood
heatmap, and that is what we expose. Callers normalise on display.

Returns a list of ``{strike, prob, contributors}`` dicts sorted by
probability descending.

Methodology — dimensional argument (Rev 8 follow-up to Rev 7's |charm|·τ fix)
============================================================================

The pinning score must keep the OI term and the charm term in **share-
equivalent** units so charm is informational rather than cosmetic. Two
rescalings landed across the rev history:

* **OI → ``OI · |Δ_K| · 100``** — open interest weighted by the dealer's
  hedge magnitude per contract (delta) and the contract multiplier
  (100). Units: shares of dealer hedge currently held at that strike.
* **|charm| → ``|charm| · τ · 100``** — BSM charm is delta-per-year, so
  multiplying by τ collapses it to the **expected delta change over the
  remaining session** per contract; multiplying by 100 turns that into
  shares. Without the τ factor (Rev 6 bug) charm exploded as τ → 0;
  without the 100 factor (Rev 7 over-correction) charm came in 6 OOM
  smaller than OI, making it cosmetic. With both, OI and charm sit on
  the same axis and can be added meaningfully.

Two separate τ values are used (Rev 8):

* **τ_charm** = ``max(TAU_FLOOR_YEARS, intraday τ)`` — floored at 15min
  to keep ``(r-q)/(σ√τ)`` and ``d2/(2τ)`` numerically stable in the
  charm formula.
* **τ_kernel** = ``intraday τ`` (unfloored down to ``sigma_floor``) —
  used only for the distance-kernel width ``σ_pts = S · median_iv · √τ``.
  In the last 5 minutes the floored τ over-states σ_pts by ~80% and the
  Gaussian artificially smears probability across strikes that, in
  reality, the underlying cannot reach.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.processing import bsm
from app.processing.session import TAU_FLOOR_YEARS, _now_eastern, time_to_expiry_0dte_years

CONTRACT_MULTIPLIER = 100


def _today_eastern() -> date:
    return _now_eastern().date()


def compute_pin_probability(
    df: pd.DataFrame,
    *,
    today: pd.Timestamp | None = None,
    sigma_floor: float = 1e-4,
    charm_weight: float = 0.5,
    risk_free_rate: float = 0.05,
    tau_years: float | None = None,
) -> list[dict]:
    """Return a probability-weighted list of 0DTE pin candidates.

    Required columns: ``strike, expiration, option_type, oi, iv,
    underlying_price``. ``charm`` and ``delta`` are computed analytically
    inside.

    ``tau_years`` overrides the session-aware τ used when computing charm.
    Pass ``None`` (default) to derive τ from
    :func:`app.processing.session.time_to_expiry_0dte_years`, floored at
    :data:`app.processing.session.TAU_FLOOR_YEARS` (15 minutes) to keep
    the BSM expressions numerically stable as expiry approaches. The
    distance kernel always uses the unfloored intraday τ so the implied
    σ_pts collapses correctly in the last 5 minutes — see the methodology
    block at the top of this module.
    """
    needed = {"strike", "expiration", "option_type", "oi", "iv", "underlying_price"}
    if df.empty or not needed.issubset(df.columns):
        return []

    spot_series = df["underlying_price"].dropna()
    if spot_series.empty:
        return []
    S = float(spot_series.iloc[-1])

    if today is None:
        today_d = _today_eastern()
    else:
        today_d = today.date() if hasattr(today, "date") else today

    # 0DTE-only filter — vectorised date comparison, ~100× faster than .apply.
    work = df.copy()
    work["expiration_d"] = pd.to_datetime(
        work["expiration"], errors="coerce"
    ).dt.date
    work = work[work["expiration_d"] == today_d]
    if work.empty:
        return []

    work["iv"] = pd.to_numeric(work["iv"], errors="coerce")
    work["strike"] = pd.to_numeric(work["strike"], errors="coerce")
    work["oi"] = pd.to_numeric(work["oi"], errors="coerce").fillna(0)
    work = work[
        work["iv"].notna()
        & (work["iv"] > 0)
        & work["strike"].notna()
        & (work["strike"] > 0)
    ]
    if work.empty:
        return []

    # Two τ values:
    #   τ_charm  — floored at 15min for stability in (r-q)/(σ√τ) and d2/(2τ).
    #   τ_kernel — unfloored intraday τ for the σ_pts kernel width so the
    #              Gaussian collapses correctly in the final 5 minutes.
    if tau_years is None:
        intraday = time_to_expiry_0dte_years()
    else:
        intraday = float(tau_years)
    tau_charm = max(TAU_FLOOR_YEARS, intraday)
    tau_kernel = max(0.0, intraday)

    K = work["strike"].to_numpy(dtype=float)
    sigma = work["iv"].to_numpy(dtype=float)
    is_call = work["option_type"].astype(str).str.upper().to_numpy() == "C"
    option_types = np.where(is_call, "C", "P")

    charm_arr = bsm.charm(
        S, K, tau_charm, sigma, r=risk_free_rate,
        option_type=option_types,
    )
    delta_arr = bsm.delta(
        S, K, tau_charm, sigma, r=risk_free_rate,
        option_type=option_types,
    )

    # Per-row hedge-share contributions. OI and charm are now both in
    # "shares of dealer hedge" units — see the methodology block at the
    # top of this module. Both terms weight by OI so each strike's
    # contribution is the *strike-aggregate* hedge magnitude (dealer
    # hedge currently held + expected delta change over the remaining
    # session), not a per-contract Greek.
    oi_arr = work["oi"].to_numpy(dtype=float)
    work["_oi_shares"] = oi_arr * np.abs(delta_arr) * CONTRACT_MULTIPLIER
    work["_charm_shares"] = (
        oi_arr * np.abs(charm_arr) * tau_charm * CONTRACT_MULTIPLIER
    )

    by_strike = work.groupby("strike", as_index=False).agg(
        oi_shares=("_oi_shares", "sum"),
        charm_shares=("_charm_shares", "sum"),
        oi=("oi", "sum"),
        atm_iv=("iv", "median"),
    )

    # Remaining one-σ band for distance kernel. Use the median ATM IV on
    # the 0DTE chain as the daily σ proxy (in absolute pts). Use the
    # *unfloored* τ_kernel so the kernel collapses correctly in the last
    # few minutes — at 5min the floored τ over-states σ_pts by ~80% and
    # the Gaussian artificially smears probability across distant strikes.
    median_iv = float(np.nanmedian(work["iv"])) if work["iv"].notna().any() else 0.20
    sigma_pts = max(sigma_floor, S * median_iv * np.sqrt(tau_kernel))

    distance = (by_strike["strike"].to_numpy(dtype=float) - S) / sigma_pts
    kernel = np.exp(-0.5 * distance * distance)

    raw = (
        by_strike["oi_shares"].to_numpy(dtype=float)
        + charm_weight * by_strike["charm_shares"].to_numpy(dtype=float)
    ) * kernel
    total = raw.sum()
    if total <= 0 or not np.isfinite(total):
        return []
    prob = raw / total

    payload = [
        {
            "strike": float(by_strike.iloc[i]["strike"]),
            "prob": float(prob[i]),
            "oi": int(by_strike.iloc[i]["oi"]),
            "abs_charm": float(by_strike.iloc[i]["charm_shares"]),
            "atm_iv": (
                float(by_strike.iloc[i]["atm_iv"])
                if not np.isnan(by_strike.iloc[i]["atm_iv"])
                else None
            ),
        }
        for i in range(len(by_strike))
    ]
    payload.sort(key=lambda r: r["prob"], reverse=True)
    return payload
