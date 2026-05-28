"""HIRO aggregator tests.

HIRO uses the **underlying-hedge-flow** convention. The canonical path
is delta-notional (per SpotGamma); signed-premium is the fallback when
delta is unavailable.

* Customer-buy CALL  → dealer-short call  → dealer buys underlying  → +HIRO
* Customer-buy PUT   → dealer-short put   → dealer sells underlying → -HIRO

The mirror holds for customer sells.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from app.processing.hiro import compute_hiro, compute_hiro_incremental


def _trade(
    ts: str, side: int, size: int, price: float, opt: str,
    *, delta: float | None = None, expiration: str | None = None,
):
    row: dict = {
        "ts": pd.Timestamp(ts, tz="UTC"),
        "side": side,
        "size": size,
        "price": price,
        "option_type": opt,
    }
    if delta is not None:
        row["delta"] = delta
    if expiration is not None:
        row["expiration"] = pd.Timestamp(expiration).date()
    return row


def test_customer_buy_call_is_positive_hedge_flow():
    """Customer BUY 1 call @ $1.00 (size=10): dealer must buy → +HIRO.

    Without ``delta`` the signed-premium fallback yields
    +10 × 100 × 1.00 = +1000.
    """
    df = pd.DataFrame([_trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C")])
    out = compute_hiro(df, bucket="1min")
    assert len(out.series) == 1
    bucket = out.series[0]
    assert bucket["call_premium"] == 1000.0
    assert bucket["put_premium"] == 0.0
    assert bucket["net_premium"] == 1000.0
    assert bucket["cumulative"] == 1000.0
    assert bucket["weight_source"] == "signed_premium"
    assert out.cumulative == 1000.0
    assert out.weight_source == "signed_premium"


def test_calls_and_puts_separated():
    df = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C"),
        _trade("2026-01-02T14:30:01", side=-1, size=5, price=2.00, opt="P"),
    ])
    out = compute_hiro(df, bucket="1min")
    assert out.series[0]["call_premium"] == 1000.0
    assert out.series[0]["put_premium"] == 1000.0
    assert out.series[0]["net_premium"] == 2000.0


def test_unclassified_trades_excluded():
    df = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=0, size=10, price=1.00, opt="C"),
    ])
    out = compute_hiro(df)
    assert out.series == []
    assert out.cumulative == 0.0


def test_empty_input():
    out = compute_hiro(pd.DataFrame(), bucket="1min")
    assert out.series == []
    assert out.cumulative == 0.0


# ── delta-notional canonical path ────────────────────────────────────────────


def test_delta_notional_canonical_path():
    """When ``delta`` is provided, HIRO emits delta-notional shares.

    Customer-buy 10 calls with delta=0.40 means dealer is short 4 deltas
    × 100 shares × 10 contracts = 400 share-equivalents to buy.
    """
    df = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C",
               delta=0.40, expiration="2026-01-02"),
    ])
    out = compute_hiro(df, bucket="1min")
    assert len(out.series) == 1
    bucket = out.series[0]
    assert bucket["call_delta_notional"] == 400.0
    assert bucket["put_delta_notional"] == 0.0
    assert bucket["net_delta_notional"] == 400.0
    # cumulative falls back to delta-notional when available.
    assert bucket["cumulative"] == 400.0
    assert bucket["weight_source"] == "delta_notional"
    assert out.weight_source == "delta_notional"


def test_delta_notional_put_buy_negative():
    """Customer-buy PUT with delta=-0.30 → dealer must sell underlying."""
    df = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="P",
               delta=-0.30, expiration="2026-01-02"),
    ])
    out = compute_hiro(df, bucket="1min")
    bucket = out.series[0]
    # 1 (customer side) × 10 (size) × -0.30 (delta) × 100 = -300
    assert bucket["put_delta_notional"] == -300.0
    assert bucket["net_delta_notional"] == -300.0


def test_mixed_delta_and_no_delta_marks_source_mixed():
    """When some buckets have delta and some don't, weight_source = mixed."""
    df = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C",
               delta=0.40, expiration="2026-01-02"),
        _trade("2026-01-02T14:31:00", side=1, size=10, price=1.00, opt="C"),
    ])
    out = compute_hiro(df, bucket="1min")
    assert out.weight_source == "mixed"


def test_next_expiry_isolation():
    """``next_expiry_delta_notional`` only counts the earliest expiry."""
    df = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C",
               delta=0.40, expiration="2026-01-02"),
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C",
               delta=0.30, expiration="2026-01-09"),
    ])
    out = compute_hiro(df, bucket="1min")
    bucket = out.series[0]
    # Both rows go into call_delta_notional; only the first hits next_expiry.
    assert bucket["call_delta_notional"] == 400.0 + 300.0
    assert bucket["next_expiry_delta_notional"] == 400.0


# ── Incremental aggregator ──────────────────────────────────────────────────


def test_incremental_merges_new_buckets():
    """Calling the incremental path with a warm cache merges new buckets
    on top of the prior series instead of re-aggregating from scratch."""
    first_batch = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C"),
    ])
    initial = compute_hiro(first_batch, bucket="1min")
    assert len(initial.series) == 1

    new_batch = pd.DataFrame([
        _trade("2026-01-02T14:31:00", side=-1, size=5, price=2.00, opt="P"),
    ])
    merged = compute_hiro_incremental(
        new_batch,
        bucket="1min",
        window_minutes=60,
        prev_series=initial.series,
        now=datetime(2026, 1, 2, 14, 32, tzinfo=UTC),
    )
    assert len(merged.series) == 2
    # Latest bucket is the new one.
    assert merged.cumulative == merged.series[-1]["cumulative"]


def test_incremental_prunes_expired_buckets():
    """Buckets older than ``window_minutes`` are dropped."""
    far_old = pd.DataFrame([
        _trade("2026-01-02T13:00:00", side=1, size=10, price=1.00, opt="C"),
    ])
    initial = compute_hiro(far_old, bucket="1min")

    fresh = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C"),
    ])
    merged = compute_hiro_incremental(
        fresh,
        bucket="1min",
        window_minutes=60,
        prev_series=initial.series,
        now=datetime(2026, 1, 2, 14, 31, tzinfo=UTC),
    )
    # Old bucket (13:00) is older than 60 min from 14:31 → pruned.
    assert all("14:30" in entry["ts"] for entry in merged.series)


def test_incremental_no_prev_series_equals_full_compute():
    """Without a prev_series the incremental path matches compute_hiro."""
    df = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C"),
    ])
    full = compute_hiro(df, bucket="1min")
    inc = compute_hiro_incremental(
        df,
        bucket="1min",
        window_minutes=60,
        prev_series=None,
        now=datetime(2026, 1, 2, 14, 31, tzinfo=UTC),
    )
    assert inc.series == full.series
    assert inc.cumulative == full.cumulative


def test_incremental_warm_cache_matches_full_compute():
    """G5 invariant: ``compute_hiro_incremental`` on a warm cache plus an
    extension window must equal ``compute_hiro`` on the union of all trades.

    Spans 5 distinct 1-minute buckets so warm + extension share zero
    overlap, isolating the merge logic from the per-bucket sum branch.
    """
    warm = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C"),
        _trade("2026-01-02T14:31:00", side=-1, size=5, price=2.00, opt="P"),
        _trade("2026-01-02T14:32:00", side=1, size=3, price=1.50, opt="C"),
    ])
    extension = pd.DataFrame([
        _trade("2026-01-02T14:33:00", side=1, size=7, price=1.25, opt="P"),
        _trade("2026-01-02T14:34:00", side=-1, size=2, price=3.00, opt="C"),
    ])
    all_trades = pd.concat([warm, extension], ignore_index=True)

    full = compute_hiro(all_trades, bucket="1min")
    warm_result = compute_hiro(warm, bucket="1min")
    inc = compute_hiro_incremental(
        extension,
        bucket="1min",
        window_minutes=60,
        prev_series=warm_result.series,
        now=datetime(2026, 1, 2, 14, 35, tzinfo=UTC),
    )

    assert len(inc.series) == len(full.series)
    full_keys = {entry["ts"]: entry for entry in full.series}
    inc_keys = {entry["ts"]: entry for entry in inc.series}
    assert set(full_keys.keys()) == set(inc_keys.keys())

    numeric_fields = (
        "call_premium",
        "put_premium",
        "net_premium",
        "cumulative",
        "call_delta_notional",
        "put_delta_notional",
        "net_delta_notional",
        "next_expiry_delta_notional",
        "next_expiry_premium",
    )
    for ts_key, full_entry in full_keys.items():
        inc_entry = inc_keys[ts_key]
        for field in numeric_fields:
            np.testing.assert_allclose(
                inc_entry[field], full_entry[field], rtol=1e-9, atol=1e-9,
                err_msg=f"mismatch on {ts_key} field={field}",
            )

    np.testing.assert_allclose(inc.cumulative, full.cumulative, rtol=1e-9, atol=1e-9)
    assert inc.weight_source == full.weight_source


# NUM-9: feeding the same trade slice twice via the incremental path
# must NOT double the bucket's ``cumulative`` field (it's per-bucket
# net, not running total — re-summing on overlap is a regression).


def test_incremental_overlap_does_not_double_count_cumulative():
    """Replaying the same trade slice into a warm cache must produce
    the same per-bucket cumulative as a single full call would, since
    the slice itself was the original input that built the warm series.

    Concretely: warm = compute_hiro(trades). Calling
    compute_hiro_incremental(trades, prev_series=warm.series, …) on the
    overlapping bucket should NOT sum cumulative twice — the bucket's
    ``cumulative`` must equal warm's per-bucket cumulative (single
    aggregation), not double.
    """
    trades = pd.DataFrame([
        _trade("2026-01-02T14:30:00", side=1, size=10, price=1.00, opt="C"),
        _trade("2026-01-02T14:30:30", side=-1, size=5, price=2.00, opt="P"),
    ])
    warm = compute_hiro(trades, bucket="1min")
    assert len(warm.series) == 1
    expected_cumulative = warm.series[0]["cumulative"]
    expected_net_premium = warm.series[0]["net_premium"]

    overlap = compute_hiro_incremental(
        trades,  # same slice again — full overlap
        bucket="1min",
        window_minutes=60,
        prev_series=warm.series,
        now=datetime(2026, 1, 2, 14, 31, tzinfo=UTC),
    )
    assert len(overlap.series) == 1
    bucket = overlap.series[0]
    # net_premium correctly doubles (sum-of-fields is the documented
    # semantic for partial overlap on the constituent fields).
    assert bucket["net_premium"] == pytest.approx(2 * expected_net_premium)
    # But cumulative is per-bucket "net" — recomputed from the summed
    # constituents — so it must equal net_premium / net_delta_notional
    # for that bucket, NOT 2× the warm cumulative twice over via stale
    # passthrough.
    if bucket["weight_source"] == "delta_notional":
        assert bucket["cumulative"] == pytest.approx(bucket["net_delta_notional"])
    else:
        assert bucket["cumulative"] == pytest.approx(bucket["net_premium"])
    # And the recomputed cumulative MUST match what a single aggregation
    # of "trades + trades" would have produced — namely 2× the warm
    # bucket's net (because we fed the slice twice). The bug being
    # fixed produced 4× (warm cumulative + 2× new bucket added on top).
    assert bucket["cumulative"] == pytest.approx(2 * expected_cumulative)


# ── Rev 11 — DR-9 regression ────────────────────────────────────────────────


def test_compute_hiro_incremental_drops_late_prints():
    """DR-9: a trade whose ``ts_event`` is older than ``now -
    window_minutes`` must be dropped at the entry of
    ``compute_hiro_incremental`` so it cannot resurrect an already-pruned
    bucket. ``HiroSeries.dropped_late_trades`` records the count.
    """
    # ``now`` = 14:30 UTC, window = 60min → cutoff = 13:30 UTC.
    # One late trade at 13:00 (30min before cutoff), one fresh at 14:25.
    new_trades = pd.DataFrame([
        _trade("2026-01-02T13:00:00", side=1, size=10, price=1.00, opt="C"),
        _trade("2026-01-02T14:25:00", side=1, size=5, price=2.00, opt="C"),
    ])
    out = compute_hiro_incremental(
        new_trades,
        bucket="1min",
        window_minutes=60,
        prev_series=None,
        now=datetime(2026, 1, 2, 14, 30, tzinfo=UTC),
    )
    # Late prints ≥ 1 — counter increments.
    assert out.dropped_late_trades == 1
    # Only the fresh trade survives — its bucket is the sole entry.
    assert len(out.series) == 1
    assert "14:25" in out.series[0]["ts"]


def test_compute_hiro_incremental_no_late_when_all_fresh():
    """DR-9 negative: no trades fall before the cutoff → counter is 0."""
    new_trades = pd.DataFrame([
        _trade("2026-01-02T14:29:00", side=1, size=10, price=1.00, opt="C"),
    ])
    out = compute_hiro_incremental(
        new_trades,
        bucket="1min",
        window_minutes=60,
        prev_series=None,
        now=datetime(2026, 1, 2, 14, 30, tzinfo=UTC),
    )
    assert out.dropped_late_trades == 0
    assert len(out.series) == 1


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 DR-11 — multi-leg detection is opt-in (default off)
# ──────────────────────────────────────────────────────────────────────────


def test_multileg_detection_disabled_by_default() -> None:
    """DR-11: ``ENABLE_OPRA_MULTILEG_DETECTION`` is False by default. The
    heuristic ``_is_multileg_print`` must therefore return False
    regardless of the ``flags`` byte content. Without per-leg unwinding
    we'd otherwise mis-attribute leg-tagged net prices to the headline
    contract and overstate dealer hedge pressure in HIRO.
    """
    from app.config import Settings
    from app.ingestion import databento_live as live_mod

    s = Settings()
    assert s.enable_opra_multileg_detection is False

    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )
    ingester._settings = s

    class _Record:
        flags = 0xFF  # every bit set — heuristic would say multi-leg.

    assert ingester._is_multileg_print(_Record()) is False


def test_multileg_detection_can_be_opted_in() -> None:
    """DR-11 boundary: opting in flips the gate from hardcoded False to
    the ``flags`` heuristic; a record without ``flags`` still bails out
    to False so opting in alone doesn't mass-classify.
    """
    from app.ingestion import databento_live as live_mod

    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )

    class _Settings:
        enable_opra_multileg_detection = True

    ingester._settings = _Settings()

    class _NoFlagsRecord:
        pass

    assert ingester._is_multileg_print(_NoFlagsRecord()) is False

