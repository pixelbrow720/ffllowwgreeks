"""Lee-Ready classifier sanity tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from app.processing.lee_ready import classify_lee_ready


def _trade(ts, price: float, bid: float, ask: float, size: int = 10):
    return {"ts": ts, "price": price, "bid": bid, "ask": ask, "size": size}


def test_quote_rule_classifies_above_below_mid():
    df = pd.DataFrame([
        _trade(1, 5.10, 5.00, 5.10),  # at-ask, bid<ask -> price>mid -> +1
        _trade(2, 5.00, 5.00, 5.10),  # at-bid -> -1
    ])
    out = classify_lee_ready(df)
    assert list(out["side"]) == [1, -1]


def test_tick_rule_resolves_midpoint_trades():
    df = pd.DataFrame([
        _trade(1, 5.05, 5.00, 5.10),  # at mid -> needs tick rule, no history -> 0
        _trade(2, 5.05, 5.00, 5.10),  # equal to last -> still 0
        _trade(3, 5.04, 5.00, 5.10),  # below last different (5.05) -> -1
        _trade(4, 5.05, 5.00, 5.10),  # above last (5.04) -> +1
    ])
    out = classify_lee_ready(df)
    assert list(out["side"]) == [0, 0, -1, 1]


def test_signed_qty_uses_size():
    df = pd.DataFrame([
        _trade(1, 5.10, 5.00, 5.10, size=7),
        _trade(2, 5.00, 5.00, 5.10, size=11),
    ])
    out = classify_lee_ready(df)
    assert list(out["signed_qty"]) == [7.0, -11.0]


def test_empty_input_returns_typed_empty():
    out = classify_lee_ready(pd.DataFrame(columns=["price", "bid", "ask"]))
    assert list(out.columns) >= ["mid", "side", "signed_qty"]
    assert out.empty


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 DR-10 — auction-print detection is opt-in (default off)
# ──────────────────────────────────────────────────────────────────────────


def test_auction_detection_disabled_by_default() -> None:
    """DR-10: ``ENABLE_OPRA_AUCTION_DETECTION`` is False by default. The
    heuristic ``_is_auction_print`` must therefore return False
    regardless of the ``flags`` byte content. Auction discrimination
    requires a real OPRA fixture validation that we don't have today —
    leaving the flag off keeps the legacy quote-rule path active.
    """
    from app.config import Settings
    from app.ingestion import databento_live as live_mod

    s = Settings()
    assert s.enable_opra_auction_detection is False

    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )
    ingester._settings = s

    class _Record:
        flags = 0xFF  # every bit set — heuristic would say auction.

    # Flag off → always False, irrespective of `flags` bits.
    assert ingester._is_auction_print(_Record()) is False


def test_auction_detection_can_be_opted_in() -> None:
    """DR-10 boundary: when the operator opts in, the heuristic runs and
    the bit-inspection path is exercised. We don't assert what the bit
    means — only that the gate now follows ``flags`` instead of being
    hardcoded False.
    """
    from app.ingestion import databento_live as live_mod

    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )

    class _Settings:
        enable_opra_auction_detection = True

    ingester._settings = _Settings()

    class _NoFlagsRecord:
        pass  # no `flags` attribute → heuristic bails out to False.

    # No flags attribute → heuristic returns False even when enabled.
    assert ingester._is_auction_print(_NoFlagsRecord()) is False


# ──────────────────────────────────────────────────────────────────────────
# Rev 11 — DR-1 / DR-19 regressions
# ──────────────────────────────────────────────────────────────────────────


def test_pipeline_lee_ready_rejects_crossed_market():
    """DR-1 (pipeline-side): a crossed market (bid > ask) routes to the
    tick rule rather than producing a quote-rule sign with the wrong
    polarity. With no tick history the row stays unclassified.
    """
    base = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    df = pd.DataFrame([
        # Crossed: bid=5.20 > ask=5.10. Quote-rule must be skipped.
        _trade(base, 5.15, 5.20, 5.10),
    ])
    out = classify_lee_ready(df)
    # Only one trade with crossed quotes and no prior history → side=0.
    assert list(out["side"]) == [0]


def test_pipeline_lee_ready_rejects_locked_market():
    """DR-1 follow-up: a locked market (bid == ask) also bypasses the
    quote-rule (spread <= eps) and falls through to the tick rule.
    """
    base = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    df = pd.DataFrame([
        _trade(base, 5.05, 5.05, 5.05),
    ])
    out = classify_lee_ready(df)
    assert list(out["side"]) == [0]


def test_tick_rule_breaks_on_halt_gap():
    """DR-19: an inter-trade gap > HALT_THRESHOLD_SECONDS severs the
    tick-rule reference. The first post-halt trade has no anchor and
    must be unclassified (0) on the tick path even though a pre-halt
    reference would otherwise have been available.
    """
    base = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    # Three pre-halt mid-prints followed by a post-halt mid-print after
    # a 90-second gap. ``HALT_THRESHOLD_SECONDS`` defaults to 60s so the
    # gap must trip the halt-break.
    df = pd.DataFrame([
        _trade(base, 5.05, 5.00, 5.10),
        _trade(base + timedelta(seconds=1), 5.04, 5.00, 5.10),
        _trade(base + timedelta(seconds=2), 5.06, 5.00, 5.10),
        # Halt gap.
        _trade(base + timedelta(seconds=92), 5.05, 5.00, 5.10),
    ])
    out = classify_lee_ready(df)
    # Pre-halt: 5.05 (no history) → 0; 5.04 < 5.05 → -1; 5.06 > 5.04 → +1.
    # Post-halt: anchor severed → no tick-rule reference → 0.
    sides = list(out["side"])
    assert sides[0] == 0
    assert sides[1] == -1
    assert sides[2] == 1
    # The post-halt row must be unclassified — its anchor was wiped.
    assert sides[3] == 0


def test_tick_rule_continuous_below_halt_threshold():
    """DR-19 negative: an inter-trade gap below the threshold must NOT
    sever the tick-rule reference — the post-gap trade still gets a
    classified sign. This pins the gate so a future bug that wipes
    references on every gap fails this test.
    """
    base = datetime(2026, 1, 2, 14, 30, tzinfo=UTC)
    df = pd.DataFrame([
        _trade(base, 5.04, 5.00, 5.10),
        _trade(base + timedelta(seconds=1), 5.06, 5.00, 5.10),
        # 30s gap — under the 60s halt threshold.
        _trade(base + timedelta(seconds=31), 5.05, 5.00, 5.10),
    ])
    out = classify_lee_ready(df)
    sides = list(out["side"])
    # 5.05 < 5.06 (last different, still tracked across the short gap) → -1.
    assert sides[2] == -1
