"""HIRO sign-convention property tests.

Per the SpotGamma definition (see ``docs/api_reference.md`` and
``app/processing/hiro.py``), HIRO must satisfy:

|  Customer flow    | Dealer hedge       | HIRO sign |
|-------------------|--------------------|-----------|
|  Buy  CALL        | Buy  underlying    |    +      |
|  Sell CALL        | Sell underlying    |    -      |
|  Buy  PUT         | Sell underlying    |    -      |
|  Sell PUT         | Buy  underlying    |    +      |

Both the canonical *delta-notional* path and the *signed-premium* fallback
must produce the same sign for any combination of inputs. This module
generates random combinations and verifies the invariant.

V8 of the original audit had a sign-flip regression in this area; the
property tests guard against it returning.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.processing.hiro import compute_hiro

pytestmark = pytest.mark.property


# Strategies — bounded so Hypothesis doesn't waste cycles on degenerate
# floats while still exploring the full sign cross-product.
_size_st = st.integers(min_value=1, max_value=10_000)
_price_st = st.floats(
    min_value=0.01, max_value=500.0, allow_nan=False, allow_infinity=False
)
_call_delta_st = st.floats(
    min_value=0.01, max_value=0.99, allow_nan=False, allow_infinity=False
)
_put_delta_st = st.floats(
    min_value=-0.99, max_value=-0.01, allow_nan=False, allow_infinity=False
)
_side_st = st.sampled_from([-1, 1])


def _build_df(
    side: int,
    size: int,
    price: float,
    option_type: str,
    delta: float | None,
) -> pd.DataFrame:
    row: dict = {
        "ts": pd.Timestamp("2026-01-02T14:30:00", tz="UTC"),
        "side": side,
        "size": size,
        "price": price,
        "option_type": option_type,
    }
    if delta is not None:
        row["delta"] = delta
        row["expiration"] = pd.Timestamp("2026-01-02").date()
    return pd.DataFrame([row])


def _expected_sign(side: int, option_type: str) -> int:
    """The four-case dealer-hedge sign matrix in one place."""
    if option_type == "C":
        return 1 if side > 0 else -1
    return -1 if side > 0 else 1


# ── Delta-notional canonical path ────────────────────────────────────────────


@given(side=_side_st, size=_size_st, price=_price_st, delta=_call_delta_st)
@settings(
    max_examples=120, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_call_delta_notional_sign(
    side: int, size: int, price: float, delta: float
) -> None:
    df = _build_df(side, size, price, "C", delta)
    out = compute_hiro(df, bucket="1min")
    assert len(out.series) == 1
    bucket = out.series[0]
    expected = _expected_sign(side, "C")
    if expected > 0:
        assert bucket["net_delta_notional"] > 0
        assert bucket["call_delta_notional"] > 0
    else:
        assert bucket["net_delta_notional"] < 0
        assert bucket["call_delta_notional"] < 0
    # Magnitude check: |delta_notional| == |side · size · delta · 100|
    assert math.isclose(
        abs(bucket["net_delta_notional"]),
        abs(side * size * delta * 100),
        rel_tol=1e-9,
    )


@given(side=_side_st, size=_size_st, price=_price_st, delta=_put_delta_st)
@settings(
    max_examples=120, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_put_delta_notional_sign(
    side: int, size: int, price: float, delta: float
) -> None:
    df = _build_df(side, size, price, "P", delta)
    out = compute_hiro(df, bucket="1min")
    bucket = out.series[0]
    expected = _expected_sign(side, "P")
    if expected > 0:
        assert bucket["net_delta_notional"] > 0
        assert bucket["put_delta_notional"] > 0
    else:
        assert bucket["net_delta_notional"] < 0
        assert bucket["put_delta_notional"] < 0


# ── Signed-premium fallback path (no delta column) ──────────────────────────


@given(side=_side_st, size=_size_st, price=_price_st)
@settings(
    max_examples=120, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_call_signed_premium_fallback_sign(
    side: int, size: int, price: float
) -> None:
    df = _build_df(side, size, price, "C", delta=None)
    out = compute_hiro(df, bucket="1min")
    bucket = out.series[0]
    expected = _expected_sign(side, "C")
    if expected > 0:
        assert bucket["net_premium"] > 0
        assert bucket["call_premium"] > 0
    else:
        assert bucket["net_premium"] < 0
        assert bucket["call_premium"] < 0
    # Magnitude check
    assert math.isclose(
        abs(bucket["net_premium"]),
        abs(side * size * price * 100),
        rel_tol=1e-9,
    )
    assert bucket["weight_source"] == "signed_premium"


@given(side=_side_st, size=_size_st, price=_price_st)
@settings(
    max_examples=120, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_put_signed_premium_fallback_sign(
    side: int, size: int, price: float
) -> None:
    df = _build_df(side, size, price, "P", delta=None)
    out = compute_hiro(df, bucket="1min")
    bucket = out.series[0]
    expected = _expected_sign(side, "P")
    if expected > 0:
        assert bucket["net_premium"] > 0
        assert bucket["put_premium"] > 0
    else:
        assert bucket["net_premium"] < 0
        assert bucket["put_premium"] < 0


# ── Cross-path consistency: both paths agree on sign ────────────────────────


@given(side=_side_st, size=_size_st, price=_price_st, delta=_call_delta_st)
@settings(
    max_examples=80, deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
def test_delta_notional_and_signed_premium_agree_on_call_sign(
    side: int, size: int, price: float, delta: float
) -> None:
    """The two HIRO paths may differ in magnitude but never in sign.

    A future implementation change that diverges sign-wise between the
    canonical (delta-notional) and fallback (signed-premium) paths would
    silently corrupt downstream consumers — this property catches that
    class of regression.
    """
    df_with_delta = _build_df(side, size, price, "C", delta)
    df_no_delta = _build_df(side, size, price, "C", None)

    canonical = compute_hiro(df_with_delta, bucket="1min").series[0]
    fallback = compute_hiro(df_no_delta, bucket="1min").series[0]

    canonical_sign = (
        1 if canonical["net_delta_notional"] > 0
        else -1 if canonical["net_delta_notional"] < 0
        else 0
    )
    fallback_sign = (
        1 if fallback["net_premium"] > 0
        else -1 if fallback["net_premium"] < 0
        else 0
    )
    assert canonical_sign == fallback_sign, (
        f"sign mismatch: canonical={canonical_sign}, fallback={fallback_sign} "
        f"(side={side}, size={size}, price={price}, delta={delta})"
    )
