"""Basis tracker + Realized vs Implied move tests."""

from __future__ import annotations

import pandas as pd

from app.processing.basis import compute_basis
from app.processing.move_tracker import compute_move_tracker


def test_basis_simple():
    out = compute_basis(spot=5800.0, futures=5810.0)
    # Convention: basis = spot - futures (so cash ≈ futures + basis).
    # Front-month ES typically trades over cash → negative basis.
    assert out.basis == -10.0
    assert out.basis_pct is not None
    assert abs(out.basis_pct - (-10.0 / 5800.0)) < 1e-9


def test_basis_handles_missing_inputs():
    out = compute_basis(spot=None, futures=5810.0)
    assert out.basis is None
    out = compute_basis(spot=0.0, futures=5810.0)
    assert out.basis is None


def test_move_tracker_returns_implied_only_when_open_missing():
    today = pd.Timestamp("2026-01-02")
    # Chain has underlying_price; open_price=None triggers fallback to
    # earliest non-null underlying_price → realized_move = |S - open|.
    # Here open derives to 5800 (chain value), and S is also 5800 → realized=0.
    chain = pd.DataFrame([
        {"strike": 5800, "expiration": today.date(), "option_type": "C",
         "last_price": 30.0, "underlying_price": 5800.0},
        {"strike": 5800, "expiration": today.date(), "option_type": "P",
         "last_price": 28.0, "underlying_price": 5800.0},
    ])
    out = compute_move_tracker(chain, open_price=None, today=today)
    assert out.implied_move == 58.0
    # Fallback derived open_price from chain → realized_move computed (0.0).
    assert out.realized_move == 0.0
    assert out.open_price == 5800.0
    assert out.reason is None  # fallback succeeded


def test_move_tracker_reason_when_no_underlying_price():
    """When underlying_price column has no usable values, reason is set."""
    today = pd.Timestamp("2026-01-02")
    chain = pd.DataFrame([
        {"strike": 5800, "expiration": today.date(), "option_type": "C",
         "last_price": 30.0, "underlying_price": None},
        {"strike": 5800, "expiration": today.date(), "option_type": "P",
         "last_price": 28.0, "underlying_price": None},
    ])
    out = compute_move_tracker(chain, open_price=None, today=today)
    assert out.realized_move is None
    assert out.ratio is None
    assert out.reason == "open_price_unset"


def test_move_tracker_computes_realized_and_ratio():
    today = pd.Timestamp("2026-01-02")
    chain = pd.DataFrame([
        {"strike": 5800, "expiration": today.date(), "option_type": "C",
         "last_price": 30.0, "underlying_price": 5810.0},
        {"strike": 5800, "expiration": today.date(), "option_type": "P",
         "last_price": 28.0, "underlying_price": 5810.0},
    ])
    out = compute_move_tracker(chain, open_price=5800.0, today=today)
    assert out.realized_move == 10.0
    assert out.implied_move == 58.0
    assert abs(out.ratio - (10.0 / 58.0)) < 1e-6
