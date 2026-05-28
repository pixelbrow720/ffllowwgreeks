"""REV8 OPS-2 / OPS-3 — half-day calendar + EOD OI business-day gate.

Pure-function tests; no DB. The half-day primitive lives in
``app.processing.session`` and is exercised end-to-end here against the
hardcoded 2024-2030 list. The EOD OI gate is exercised by patching the
business-day predicate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pandas as pd
import pytest

from app.ingestion import databento_eod_oi as eod_oi
from app.ingestion.databento_eod_oi import _normalize_oi_row, run_eod_oi_ingestion
from app.processing.session import (
    early_close_at_eastern,
    effective_rth_close,
    is_half_day,
)

# ── Half-day calendar (2024-2030 hardcoded) ─────────────────────────────────


@pytest.mark.parametrize(
    "today",
    [
        date(2024, 7, 3),     # July 4 Thu → July 3 Wed half-day
        date(2024, 11, 29),   # Black Friday 2024
        date(2024, 12, 24),   # Christmas Eve 2024 (Tue)
        date(2025, 7, 3),     # July 4 Fri → July 3 Thu half-day
        date(2025, 11, 28),   # Black Friday 2025
        date(2025, 12, 24),   # Christmas Eve 2025 (Wed)
        date(2026, 11, 27),   # Black Friday 2026
        date(2026, 12, 24),   # Christmas Eve 2026 (Thu)
        date(2027, 11, 26),   # Black Friday 2027
        date(2028, 7, 3),     # July 4 Tue → July 3 Mon half-day
        date(2028, 11, 24),   # Black Friday 2028
        date(2029, 7, 3),     # July 4 Wed → July 3 Tue half-day
        date(2029, 11, 23),   # Black Friday 2029
        date(2029, 12, 24),   # Christmas Eve 2029 (Mon)
        date(2030, 7, 3),     # July 4 Thu → July 3 Wed half-day
        date(2030, 11, 29),   # Black Friday 2030
        date(2030, 12, 24),   # Christmas Eve 2030 (Tue)
    ],
)
def test_half_day_dates_recognised(today: date) -> None:
    assert is_half_day(today)
    assert early_close_at_eastern(today) == time(13, 0)
    assert effective_rth_close(today) == time(13, 0)


@pytest.mark.parametrize(
    "today",
    [
        date(2026, 1, 5),     # ordinary Monday
        date(2026, 5, 18),    # ordinary Monday
        date(2026, 12, 25),   # full holiday — not half-day
        date(2027, 7, 5),     # observed Independence Day full-holiday
        date(2027, 12, 24),   # observed Christmas full-holiday
    ],
)
def test_non_half_day_dates_return_none(today: date) -> None:
    assert is_half_day(today) is False
    assert early_close_at_eastern(today) is None


# ── REV8 OPS-3 — EOD OI business-day gate ──────────────────────────────────


@pytest.mark.asyncio
async def test_run_eod_oi_skips_non_business_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a US market holiday, ``run_eod_oi_ingestion`` returns 0 and never
    calls the fetch helper — preventing yesterday's snapshot from being
    mis-stamped with today's date."""
    holiday = date(2026, 12, 25)  # Christmas Day 2026 (Friday)

    monkeypatch.setattr(eod_oi, "_today_eastern", lambda: holiday)
    # Defensive: even if the fetch were called we'd notice — but the
    # business-day gate must short-circuit before the fetch runs.
    fetch_calls: list[str] = []

    async def fake_fetch(*_args: object, **_kwargs: object) -> list[dict]:
        fetch_calls.append("called")
        return []

    monkeypatch.setattr(eod_oi, "fetch_eod_oi_from_databento", fake_fetch)

    result = await run_eod_oi_ingestion()
    assert result == 0
    assert fetch_calls == []


@pytest.mark.asyncio
async def test_run_eod_oi_skips_weekend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saturday = date(2026, 5, 16)
    monkeypatch.setattr(eod_oi, "_today_eastern", lambda: saturday)

    async def fake_fetch(*_args: object, **_kwargs: object) -> list[dict]:
        raise AssertionError("fetch should not run on a weekend")

    monkeypatch.setattr(eod_oi, "fetch_eod_oi_from_databento", fake_fetch)
    result = await run_eod_oi_ingestion()
    assert result == 0


def test_normalize_oi_row_uses_ts_event_for_oi_date() -> None:
    """OPS-3: ``oi_date`` is derived from the source row's ``ts_event``
    in ET, not from ``datetime.now(UTC).date()``."""
    # OPRA EOD OI snapshots typically fire ~22:30 UTC, which is the same
    # trading day in ET. Confirm the conversion lands on the correct date.
    raw = pd.Series(
        {
            "ts_event": pd.Timestamp("2026-11-25 22:30:00", tz="UTC"),
            "expiration": pd.Timestamp("2026-12-18"),
            "strike_price": 4500.0,
            "instrument_class": "C",
            "quantity": 1234,
        }
    )
    out = _normalize_oi_row("SPXW", raw)
    assert out is not None
    assert out["oi_date"] == date(2026, 11, 25)
    assert out["open_interest"] == 1234


def test_normalize_oi_row_falls_back_to_today_when_ts_event_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the source row lacks ts_event, ``oi_date`` falls back to today
    in ET. Only matters for degenerate rows; the run_eod_oi_ingestion
    business-day gate stops the holiday-mis-date case at the entry."""
    monkeypatch.setattr(eod_oi, "_today_eastern", lambda: date(2026, 5, 18))
    raw = pd.Series(
        {
            "expiration": pd.Timestamp("2026-12-18"),
            "strike_price": 4500.0,
            "instrument_class": "P",
            "quantity": 99,
        }
    )
    out = _normalize_oi_row("SPXW", raw)
    assert out is not None
    assert out["oi_date"] == date(2026, 5, 18)


# ── REV8 OPS-10 — DLQ retention primitive ──────────────────────────────────


@pytest.mark.asyncio
async def test_cleanup_dlq_older_than_zero_is_noop() -> None:
    """A misconfigured retention of 0 must not delete every DLQ row."""
    from app.ingestion.dlq import cleanup_dlq_older_than

    deleted = await cleanup_dlq_older_than(0)
    assert deleted == 0


@pytest.mark.asyncio
async def test_cleanup_dlq_older_than_negative_is_noop() -> None:
    from app.ingestion.dlq import cleanup_dlq_older_than

    deleted = await cleanup_dlq_older_than(-1)
    assert deleted == 0


@pytest.mark.asyncio
async def test_cleanup_dlq_older_than_swallows_db_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient DB outage must NOT propagate into the cleanup caller."""
    from app.ingestion import dlq as dlq_mod

    class _BoomSession:
        async def __aenter__(self) -> _BoomSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("db down")

        async def commit(self) -> None:  # pragma: no cover — never reached
            return None

    def fake_factory():
        return _BoomSession()

    monkeypatch.setattr(dlq_mod, "get_session_factory", lambda: fake_factory)
    deleted = await dlq_mod.cleanup_dlq_older_than(14)
    assert deleted == 0


# ── REV8 OPS-11 — DLQ eviction counter exposed ─────────────────────────────


@pytest.mark.asyncio
async def test_dlq_evicted_count_increments_when_buffer_full() -> None:
    """Appends past ``maxlen`` increment ``evicted_count`` exactly once
    per drop — that is what the Prometheus counter exports."""
    from app.ingestion.dlq import DeadLetterQueue

    queue = DeadLetterQueue(max_size=2)
    assert queue.evicted_count() == 0
    for i in range(5):
        await queue.add(source="t", reason=f"r{i}")
    # 5 appends - 2 retained = 3 evictions.
    assert queue.evicted_count() == 3


def test_normalize_oi_row_naive_ts_event_localised_to_utc_then_et() -> None:
    """Naive ts_event values are interpreted as UTC then converted to ET
    so the stamped date matches the trading session."""
    raw = pd.Series(
        {
            "ts_event": datetime(2026, 11, 26, 4, 30),  # 04:30 UTC = 23:30 ET prev day
            "expiration": pd.Timestamp("2026-12-18"),
            "strike_price": 4500.0,
            "instrument_class": "C",
            "quantity": 50,
        }
    )
    out = _normalize_oi_row("SPXW", raw)
    assert out is not None
    # 04:30 UTC on Nov 26 == 23:30 ET on Nov 25.
    assert out["oi_date"] == date(2026, 11, 25)


def test_normalize_oi_row_returns_none_on_missing_quantity() -> None:
    raw = pd.Series(
        {
            "ts_event": pd.Timestamp("2026-05-18 22:30:00", tz=UTC),
            "expiration": pd.Timestamp("2026-06-18"),
            "strike_price": 4500.0,
            "instrument_class": "C",
        }
    )
    assert _normalize_oi_row("SPXW", raw) is None


# ── REV12 SRE-25 / DR-26 — operator override env var ───────────────────────


def test_operator_override_inserts_extra_half_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A date in OPERATOR_OVERRIDE_HALF_DAYS is treated as a half-day
    even when it is not in the hardcoded calendar."""
    extra = date(2026, 9, 15)
    assert is_half_day(extra) is False
    monkeypatch.setenv("OPERATOR_OVERRIDE_HALF_DAYS", "2026-09-15")
    assert is_half_day(extra) is True
    assert early_close_at_eastern(extra) == time(13, 0)
    assert effective_rth_close(extra) == time(13, 0)


def test_operator_override_handles_multiple_dates_and_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "OPERATOR_OVERRIDE_HALF_DAYS", " 2026-09-15 ,2026-10-12,, 2026-12-29 "
    )
    assert is_half_day(date(2026, 9, 15)) is True
    assert is_half_day(date(2026, 10, 12)) is True
    assert is_half_day(date(2026, 12, 29)) is True
    # Untouched dates stay non-half-day.
    assert is_half_day(date(2026, 9, 16)) is False


def test_operator_override_silently_drops_malformed_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo on one date does not disable the others."""
    monkeypatch.setenv(
        "OPERATOR_OVERRIDE_HALF_DAYS", "not-a-date,2026-09-15,also-bad"
    )
    assert is_half_day(date(2026, 9, 15)) is True


def test_operator_override_preserves_hardcoded_dates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting the override does NOT shadow the hardcoded list."""
    monkeypatch.setenv("OPERATOR_OVERRIDE_HALF_DAYS", "2026-09-15")
    # 2026 Black Friday remains a half-day.
    assert is_half_day(date(2026, 11, 27)) is True


def test_operator_override_unset_means_only_hardcoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPERATOR_OVERRIDE_HALF_DAYS", raising=False)
    assert is_half_day(date(2026, 9, 15)) is False
