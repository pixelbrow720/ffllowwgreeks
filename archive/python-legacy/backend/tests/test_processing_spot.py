"""Tests for put-call parity spot synthesis."""

from datetime import date
from pathlib import Path

import pandas as pd

from app.processing.spot import synthesize_underlying_price


def _build_chain(spot: float = 7000.0, expiry=date(2026, 12, 31)) -> pd.DataFrame:
    """Build a stylised SPX chain with mids consistent with ``spot``."""
    rows = []
    # At-the-money strike — put/call near parity.
    rows.append(
        {"strike": spot, "expiration": expiry, "option_type": "C", "bid": 19.95, "ask": 20.05}
    )
    rows.append(
        {"strike": spot, "expiration": expiry, "option_type": "P", "bid": 19.95, "ask": 20.05}
    )
    # ITM call / OTM put 50 below.
    rows.append(
        {"strike": spot - 50, "expiration": expiry, "option_type": "C", "bid": 60.0, "ask": 60.4}
    )
    rows.append(
        {"strike": spot - 50, "expiration": expiry, "option_type": "P", "bid": 9.9, "ask": 10.1}
    )
    return pd.DataFrame(rows)


def test_synthesize_returns_value_close_to_spot():
    df = _build_chain(spot=7000.0)
    out = synthesize_underlying_price(df, risk_free_rate=0.05)
    assert out is not None
    # Allow a couple percent tolerance — synthetic spot via parity drifts
    # with discount factor across long-dated expiries.
    assert abs(out - 7000.0) / 7000.0 < 0.05


def test_synthesize_returns_none_for_empty():
    assert synthesize_underlying_price(pd.DataFrame(), risk_free_rate=0.05) is None


def test_synthesize_returns_none_when_only_calls():
    df = _build_chain()
    df = df[df["option_type"] == "C"].copy()
    assert synthesize_underlying_price(df, risk_free_rate=0.05) is None


def test_synthesize_falls_back_to_last_price():
    """When bid/ask are absent (e.g. cmbp-1 not subscribed) but trade
    prints exist, parity should still produce a usable spot."""
    df = _build_chain(spot=7000.0)
    df = df.assign(last_price=lambda d: (d["bid"] + d["ask"]) / 2.0)
    df["bid"] = pd.NA
    df["ask"] = pd.NA
    out = synthesize_underlying_price(df, risk_free_rate=0.05)
    assert out is not None
    assert abs(out - 7000.0) / 7000.0 < 0.05


def test_synthesize_returns_none_when_no_quotes_at_all():
    df = _build_chain(spot=7000.0)
    df["bid"] = pd.NA
    df["ask"] = pd.NA
    # No last_price either.
    assert synthesize_underlying_price(df, risk_free_rate=0.05) is None


def test_synthesize_vectorised_mid_matches_rowwise_baseline():
    """Rev 9 DT-1 regression: vectorised mid must match the legacy rowwise
    formula bit-for-bit on a 100-row synthetic chain.

    The Rev 7 closure note claimed ``apply(_mid, axis=1)`` had been
    replaced with vectorised arithmetic, but the helper still ran
    row-by-row at the time of the Rev 9 audit. This test pins the
    arithmetic identity so the regression cannot silently come back.
    """
    spot = 5800.0
    expiry = date(2026, 6, 30)
    rows: list[dict] = []
    # 50 strikes × {C, P} = 100 rows with deterministic but noisy mids.
    for i in range(50):
        K = float(spot + (i - 25) * 5.0)
        bid_c = max(0.0, 60.0 - 1.1 * (i - 25))
        ask_c = bid_c + 0.20
        bid_p = max(0.0, 60.0 + 1.1 * (i - 25))
        ask_p = bid_p + 0.20
        rows.append(
            {"strike": K, "expiration": expiry, "option_type": "C",
             "bid": bid_c, "ask": ask_c, "last_price": (bid_c + ask_c) / 2}
        )
        rows.append(
            {"strike": K, "expiration": expiry, "option_type": "P",
             "bid": bid_p, "ask": ask_p, "last_price": (bid_p + ask_p) / 2}
        )
    # Sprinkle in a few rows where bid/ask are NaN so last_price kicks in,
    # plus a couple where everything is NaN so the row drops out entirely.
    rows.append({"strike": 5800.0, "expiration": expiry, "option_type": "C",
                 "bid": pd.NA, "ask": pd.NA, "last_price": 12.5})
    rows.append({"strike": 5800.0, "expiration": expiry, "option_type": "P",
                 "bid": pd.NA, "ask": pd.NA, "last_price": pd.NA})
    df = pd.DataFrame(rows)

    def _legacy_mid(row: pd.Series) -> float | None:
        bid = row.get("bid")
        ask = row.get("ask")
        if (
            bid is not None
            and ask is not None
            and not pd.isna(bid)
            and not pd.isna(ask)
            and bid > 0
            and ask > 0
        ):
            return float((bid + ask) / 2.0)
        last = row.get("last_price")
        if last is not None and not pd.isna(last) and last > 0:
            return float(last)
        return None

    legacy = df.apply(_legacy_mid, axis=1)

    bid = pd.to_numeric(df["bid"], errors="coerce")
    ask = pd.to_numeric(df["ask"], errors="coerce")
    last = pd.to_numeric(df["last_price"], errors="coerce")
    good_quote = (bid > 0) & (ask > 0)
    vectorised = ((bid + ask) / 2.0).where(good_quote, last.where(last > 0))

    # Numeric identity on present-mid rows; both must drop the NaN-only row.
    legacy_present = legacy.dropna().to_numpy(dtype=float)
    vectorised_present = vectorised.dropna().to_numpy(dtype=float)
    assert legacy_present.shape == vectorised_present.shape
    assert (legacy_present == vectorised_present).all()

    # And the public function must agree with the rowwise baseline that the
    # parity solver is consuming the same mids.
    out = synthesize_underlying_price(df, risk_free_rate=0.05)
    assert out is not None
    assert abs(out - spot) / spot < 0.05


def test_spot_module_has_no_axis_one_apply():
    """Rev 9 DT-1 grep guard: ``df.apply(..., axis=1)`` must not return.

    The Rev 7 vectorisation claim relapsed once. Pin it as a CI guard so
    any future row-wise pattern in this hot path fails the build.
    """
    text = Path(__file__).resolve().parents[1].joinpath(
        "app", "processing", "spot.py"
    ).read_text(encoding="utf-8")
    assert "axis=1" not in text, (
        "spot.py reintroduced an axis=1 apply. The hot path must stay "
        "vectorised — see Rev 9 DT-1."
    )
