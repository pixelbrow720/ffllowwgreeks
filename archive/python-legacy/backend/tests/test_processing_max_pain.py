"""Tests for max-pain computation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.processing.max_pain import CONTRACT_MULTIPLIER, _expiry_max_pain, compute_max_pain


def test_max_pain_picks_minimum_loss_strike():
    """A symmetric chain centered on 100 should peg max pain at 100."""
    today = pd.Timestamp.utcnow().normalize()
    expiry = today + pd.Timedelta(days=7)
    strikes = [90, 95, 100, 105, 110]
    rows = []
    for K in strikes:
        rows.append(
            {"expiration": expiry, "strike": K, "option_type": "C", "oi": 1000, "volume": 0}
        )
        rows.append(
            {"expiration": expiry, "strike": K, "option_type": "P", "oi": 1000, "volume": 0}
        )
    df = pd.DataFrame(rows)
    summary = compute_max_pain(df)
    assert len(summary.per_expiry) == 1
    assert summary.per_expiry[0]["strike"] == 100


def test_max_pain_per_expiry_independent():
    today = pd.Timestamp.utcnow().normalize()
    e1 = today + pd.Timedelta(days=7)
    e2 = today + pd.Timedelta(days=14)
    rows = []
    # Expiry 1: heavy call OI at 105 -> max pain skews lower
    for K, oi_c, oi_p in [(95, 100, 100), (100, 100, 100), (105, 5000, 100)]:
        rows.append({"expiration": e1, "strike": K, "option_type": "C", "oi": oi_c, "volume": 0})
        rows.append({"expiration": e1, "strike": K, "option_type": "P", "oi": oi_p, "volume": 0})
    # Expiry 2: heavy put OI at 95 -> max pain skews higher
    for K, oi_c, oi_p in [(95, 100, 5000), (100, 100, 100), (105, 100, 100)]:
        rows.append({"expiration": e2, "strike": K, "option_type": "C", "oi": oi_c, "volume": 0})
        rows.append({"expiration": e2, "strike": K, "option_type": "P", "oi": oi_p, "volume": 0})
    df = pd.DataFrame(rows)
    summary = compute_max_pain(df)
    assert len(summary.per_expiry) == 2
    by_expiry = {e["expiration"]: e["strike"] for e in summary.per_expiry}
    # First expiry: heavy call OI at 105 means call holders lose more if S > 105,
    # so max pain (min loss to all holders) is at or below 100.
    assert by_expiry[str(e1.date())] <= 100
    # Second expiry: heavy put OI at 95 means put holders lose more if S < 95,
    # so max pain is at or above 100.
    assert by_expiry[str(e2.date())] >= 100
    assert summary.aggregate_strike is not None


def test_max_pain_empty_dataframe():
    summary = compute_max_pain(pd.DataFrame())
    assert summary.per_expiry == []
    assert summary.aggregate_strike is None


# ──────────────────────────────────────────────────────────────────────────
# Rev 11 — DR-15 regression
# ──────────────────────────────────────────────────────────────────────────


def test_compute_max_pain_filters_expired_expiries():
    """DR-15: yesterday-expired rows must be dropped from both the
    per-expiry list and the aggregate. Only expiries on or after
    ``today`` participate.
    """
    today = pd.Timestamp("2026-05-24")
    yesterday = today - pd.Timedelta(days=1)  # 2026-05-23
    future = today + pd.Timedelta(days=7)
    rows = []
    # Yesterday-expired strikes — must be filtered out entirely.
    for K in (5800, 5810):
        rows.append({"expiration": yesterday, "strike": K, "option_type": "C",
                     "oi": 1000, "volume": 0})
        rows.append({"expiration": yesterday, "strike": K, "option_type": "P",
                     "oi": 1000, "volume": 0})
    # Future expiry concentrated at 5805.
    for K in (5800, 5805, 5810):
        oi = 5000 if K == 5805 else 0
        rows.append({"expiration": future, "strike": K, "option_type": "C",
                     "oi": oi, "volume": 0})
        rows.append({"expiration": future, "strike": K, "option_type": "P",
                     "oi": oi, "volume": 0})
    df = pd.DataFrame(rows)

    summary = compute_max_pain(df, today=today)
    # Only the future expiry should appear.
    assert len(summary.per_expiry) == 1
    assert summary.per_expiry[0]["expiration"] == str(future.date())
    assert summary.per_expiry[0]["strike"] == 5805.0
    # Aggregate is driven by the future expiry only.
    assert summary.aggregate_strike == 5805.0


def test_compute_max_pain_today_expiry_is_included():
    """DR-15 boundary: an expiry equal to ``today`` (0DTE) is still
    in scope and must NOT be dropped.
    """
    today = pd.Timestamp("2026-05-24")
    rows = []
    for K in (5800, 5805, 5810):
        oi = 5000 if K == 5805 else 0
        rows.append({"expiration": today, "strike": K, "option_type": "C",
                     "oi": oi, "volume": 0})
        rows.append({"expiration": today, "strike": K, "option_type": "P",
                     "oi": oi, "volume": 0})
    df = pd.DataFrame(rows)
    summary = compute_max_pain(df, today=today)
    assert len(summary.per_expiry) == 1
    assert summary.per_expiry[0]["strike"] == 5805.0


def test_compute_max_pain_explicit_expiry_query_bypasses_today_filter():
    """DR-15 guarantee: an explicit ``expiry=`` query gets an answer
    even when the requested expiry has already passed. Operators
    answering historical questions need this path.
    """
    today = pd.Timestamp("2026-05-24")
    yesterday = today - pd.Timedelta(days=1)
    rows = []
    for K in (5800, 5805, 5810):
        oi = 5000 if K == 5805 else 0
        rows.append({"expiration": yesterday, "strike": K, "option_type": "C",
                     "oi": oi, "volume": 0})
        rows.append({"expiration": yesterday, "strike": K, "option_type": "P",
                     "oi": oi, "volume": 0})
    df = pd.DataFrame(rows)
    summary = compute_max_pain(df, expiry=yesterday, today=today)
    # Single-expiry path returns the requested expired expiry's answer.
    assert len(summary.per_expiry) == 1
    assert summary.per_expiry[0]["strike"] == 5805.0


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 — NUM2-3 vectorisation regression
# ──────────────────────────────────────────────────────────────────────────


def _scalar_expiry_max_pain(sub: pd.DataFrame) -> tuple[float | None, float | None, list[dict]]:
    """Reference implementation: the pre-vectorisation scalar loop, kept
    here ONLY to pin equivalence of the vectorised path. Mirrors the
    Rev 7-pre-C4 / Rev 12-pre-NUM2-3 code byte-for-byte.
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
    cs = calls["strike"].to_numpy(dtype=float)
    co = calls["oi"].to_numpy(dtype=float)
    ps = puts["strike"].to_numpy(dtype=float)
    po = puts["oi"].to_numpy(dtype=float)
    curve: list[dict] = []
    best_strike: float | None = None
    best_value: float | None = None
    for s_star in strikes:
        cl = float((np.maximum(s_star - cs, 0.0) * co).sum())
        pl = float((np.maximum(ps - s_star, 0.0) * po).sum())
        total = (cl + pl) * CONTRACT_MULTIPLIER
        if not np.isfinite(total):
            total = 0.0
        curve.append({"strike": float(s_star), "pain": total})
        if best_value is None or total < best_value:
            best_value = total
            best_strike = float(s_star)
    return best_strike, best_value, curve


def test_vectorised_expiry_max_pain_matches_scalar_loop():
    """NUM2-3 / NUM2-2: the broadcasting path must agree with the
    pre-fix scalar loop on a synthetic SPX-shaped chain, including
    tie-breaking (lowest strike wins on ties — both implementations
    pick the first occurrence over a sorted strike axis).
    """
    rng = np.random.default_rng(20260524)
    strikes = np.arange(5000.0, 6001.0, 5.0)  # 201 strikes — keeps test fast
    rows = []
    for K in strikes:
        rows.append(
            {
                "strike": float(K),
                "option_type": "C",
                "oi": float(rng.integers(0, 50_000)),
            }
        )
        rows.append(
            {
                "strike": float(K),
                "option_type": "P",
                "oi": float(rng.integers(0, 50_000)),
            }
        )
    df = pd.DataFrame(rows)

    vec_strike, vec_value, vec_curve = _expiry_max_pain(df)
    scal_strike, scal_value, scal_curve = _scalar_expiry_max_pain(df)

    assert vec_strike == scal_strike
    assert vec_value == scal_value
    assert len(vec_curve) == len(scal_curve)
    np.testing.assert_array_equal(
        np.array([row["strike"] for row in vec_curve]),
        np.array([row["strike"] for row in scal_curve]),
    )
    np.testing.assert_allclose(
        np.array([row["pain"] for row in vec_curve]),
        np.array([row["pain"] for row in scal_curve]),
        rtol=1e-12,
        atol=1e-6,
    )


def test_vectorised_expiry_max_pain_tie_break_lowest_strike():
    """NUM2-2: on a flat-OI chain every strike yields identical pain;
    the convention is "lowest strike wins on ties" (np.argmin returns
    first occurrence over the sorted strike axis).
    """
    rows = []
    for K in (100.0, 105.0, 110.0):
        rows.append({"strike": K, "option_type": "C", "oi": 1000.0})
        rows.append({"strike": K, "option_type": "P", "oi": 1000.0})
    df = pd.DataFrame(rows)
    strike, _, _ = _expiry_max_pain(df)
    # Symmetric chain centered at 105 -> min pain is 105, not a tie.
    # Construct an actual tie by zeroing OI everywhere:
    rows = []
    for K in (100.0, 105.0, 110.0):
        rows.append({"strike": K, "option_type": "C", "oi": 0.0})
        rows.append({"strike": K, "option_type": "P", "oi": 0.0})
    df = pd.DataFrame(rows)
    strike, value, _ = _expiry_max_pain(df)
    assert strike == 100.0
    assert value == 0.0


def test_vectorised_expiry_max_pain_handles_missing_side():
    """Calls-only or puts-only chain must not crash the matmul path."""
    rows = [
        {"strike": 100.0, "option_type": "C", "oi": 1000.0},
        {"strike": 105.0, "option_type": "C", "oi": 2000.0},
        {"strike": 110.0, "option_type": "C", "oi": 3000.0},
    ]
    df = pd.DataFrame(rows)
    strike, value, curve = _expiry_max_pain(df)
    # Pure-call OI: pain rises with S, so min is the lowest strike.
    assert strike == 100.0
    assert value == 0.0
    assert len(curve) == 3
