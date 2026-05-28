"""Tests for the Rev 4 RTH session lifecycle helpers."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.processing.session import (
    SECONDS_PER_YEAR,
    clear_available_expirations,
    early_close_at_eastern,
    effective_rth_close,
    is_expiration_day,
    is_half_day,
    is_rth_now,
    minutes_to_close,
    next_business_day,
    session_close_today,
    session_open_today,
    session_snapshot,
    set_available_expirations,
    time_to_expiry_0dte_years,
)

ET = ZoneInfo("America/New_York")


# Pick a known Monday (2026-05-18) and verify every helper.
MONDAY_11_00 = datetime(2026, 5, 18, 11, 0, tzinfo=ET)
MONDAY_09_29 = datetime(2026, 5, 18, 9, 29, tzinfo=ET)
MONDAY_09_30 = datetime(2026, 5, 18, 9, 30, tzinfo=ET)
MONDAY_16_15 = datetime(2026, 5, 18, 16, 15, tzinfo=ET)
MONDAY_17_00 = datetime(2026, 5, 18, 17, 0, tzinfo=ET)
SATURDAY_11_00 = datetime(2026, 5, 16, 11, 0, tzinfo=ET)
MEMORIAL_DAY = datetime(2026, 5, 25, 11, 0, tzinfo=ET)  # Mon holiday


# ──────────────────────────────────────────────────────────────────────────


def test_is_rth_now_inside_window() -> None:
    assert is_rth_now(now=MONDAY_11_00) is True


def test_is_rth_now_before_open() -> None:
    assert is_rth_now(now=MONDAY_09_29) is False


def test_is_rth_now_at_open_boundary_inclusive() -> None:
    assert is_rth_now(now=MONDAY_09_30) is True


def test_is_rth_now_at_close_boundary_inclusive() -> None:
    assert is_rth_now(now=MONDAY_16_15) is True


def test_is_rth_now_after_close() -> None:
    assert is_rth_now(now=MONDAY_17_00) is False


def test_is_rth_now_weekend() -> None:
    assert is_rth_now(now=SATURDAY_11_00) is False


def test_is_rth_now_holiday() -> None:
    # Memorial Day 2026 (May 25) is a Monday US federal holiday.
    assert is_rth_now(now=MEMORIAL_DAY) is False


def test_session_open_close_today_returns_aware_datetimes() -> None:
    open_dt = session_open_today(now=MONDAY_11_00)
    close_dt = session_close_today(now=MONDAY_11_00)
    assert open_dt.tzinfo is not None
    assert close_dt.tzinfo is not None
    assert open_dt.time() == time(9, 30)
    assert close_dt.time() == time(16, 15)


def test_minutes_to_close_basic() -> None:
    # 11:00 to 16:15 = 5h 15m = 315 minutes.
    assert minutes_to_close(now=MONDAY_11_00) == pytest.approx(315.0, abs=0.01)


def test_minutes_to_close_negative_after_close() -> None:
    assert minutes_to_close(now=MONDAY_17_00) < 0


# ──────────────────────────────────────────────────────────────────────────
# time_to_expiry_0dte_years
# ──────────────────────────────────────────────────────────────────────────


def test_time_to_expiry_0dte_at_open_equals_session_length() -> None:
    """At open the τ equals the entire session length expressed in years."""
    tau = time_to_expiry_0dte_years(now=MONDAY_09_30)
    expected_seconds = (16 * 3600 + 15 * 60) - (9 * 3600 + 30 * 60)
    assert tau == pytest.approx(expected_seconds / SECONDS_PER_YEAR, abs=1e-9)


def test_time_to_expiry_0dte_at_close_is_zero() -> None:
    assert time_to_expiry_0dte_years(now=MONDAY_16_15) == 0.0


def test_time_to_expiry_0dte_after_close_is_zero() -> None:
    assert time_to_expiry_0dte_years(now=MONDAY_17_00) == 0.0


def test_time_to_expiry_0dte_weekend_is_zero() -> None:
    assert time_to_expiry_0dte_years(now=SATURDAY_11_00) == 0.0


def test_time_to_expiry_0dte_before_open_is_full_session() -> None:
    """A snapshot taken at 08:00 ET should still return a full-session τ."""
    pre_open = datetime(2026, 5, 18, 8, 0, tzinfo=ET)
    full = time_to_expiry_0dte_years(now=pre_open)
    expected_seconds = (16 * 3600 + 15 * 60) - (9 * 3600 + 30 * 60)
    assert full == pytest.approx(expected_seconds / SECONDS_PER_YEAR, abs=1e-9)


def test_time_to_expiry_0dte_monotonic_throughout_session() -> None:
    """τ must monotonically decrease as the session progresses."""
    samples = [
        time_to_expiry_0dte_years(now=datetime(2026, 5, 18, h, 0, tzinfo=ET))
        for h in range(10, 17)
    ]
    for prev, cur in zip(samples, samples[1:], strict=False):
        assert cur < prev


# ──────────────────────────────────────────────────────────────────────────
# is_expiration_day
# ──────────────────────────────────────────────────────────────────────────


def test_is_expiration_day_monday_spxw() -> None:
    assert is_expiration_day("SPXW", today=date(2026, 5, 18)) is True


def test_is_expiration_day_tuesday_returns_false() -> None:
    # Mon/Wed/Fri schedule by default.
    assert is_expiration_day("SPXW", today=date(2026, 5, 19)) is False


def test_is_expiration_day_holiday_returns_false() -> None:
    assert is_expiration_day("SPXW", today=date(2026, 5, 25)) is False


def test_is_expiration_day_unknown_symbol_returns_false() -> None:
    assert is_expiration_day("AAPL", today=date(2026, 5, 18)) is False


def test_is_expiration_day_ndxp_friday() -> None:
    assert is_expiration_day("NDXP", today=date(2026, 5, 22)) is True


# ──────────────────────────────────────────────────────────────────────────


def test_next_business_day_skips_weekend() -> None:
    # Friday → next is Monday.
    fri = date(2026, 5, 22)
    assert next_business_day(fri) == date(2026, 5, 25) or next_business_day(fri) == date(2026, 5, 26)


def test_session_snapshot_includes_required_fields() -> None:
    snap = session_snapshot(now=MONDAY_11_00, symbol="SPXW")
    for key in (
        "is_rth",
        "minutes_to_close",
        "time_to_expiry_0dte_years",
        "session_open",
        "session_close",
        "now_eastern",
        "is_expiration_day",
    ):
        assert key in snap
    assert snap["is_rth"] is True
    assert snap["is_expiration_day"] is True


# ──────────────────────────────────────────────────────────────────────────
# Lane B — Tue/Thu expiration via set_available_expirations / argument
# ──────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_available_expirations_cache():
    clear_available_expirations()
    yield
    clear_available_expirations()


def test_is_expiration_day_tuesday_with_chain_argument() -> None:
    """Tuesday SPXW expiry today → True when today appears in
    ``available_expirations``."""
    today = date(2026, 5, 19)  # Tuesday
    assert is_expiration_day("SPXW", today=today, available_expirations=[today]) is True


def test_is_expiration_day_thursday_with_chain_argument() -> None:
    today = date(2026, 5, 21)  # Thursday
    assert is_expiration_day("SPXW", today=today, available_expirations=[today]) is True


def test_is_expiration_day_tuesday_via_module_cache() -> None:
    today = date(2026, 5, 19)  # Tuesday
    set_available_expirations("SPXW", [today])
    assert is_expiration_day("SPXW", today=today) is True


def test_is_expiration_day_thursday_via_module_cache() -> None:
    today = date(2026, 5, 21)  # Thursday
    set_available_expirations("SPXW", [today])
    assert is_expiration_day("SPXW", today=today) is True


def test_is_expiration_day_chain_arg_overrides_static_fallback() -> None:
    """Argument-supplied expirations take precedence over the static
    M/W/F default — Monday with empty chain returns False."""
    monday = date(2026, 5, 18)
    assert is_expiration_day("SPXW", today=monday, available_expirations=[]) is False


def test_is_expiration_day_cache_falls_back_to_static_set_when_empty() -> None:
    """No cache + no argument → static M/W/F fallback applies."""
    monday = date(2026, 5, 18)
    tuesday = date(2026, 5, 19)
    assert is_expiration_day("SPXW", today=monday) is True
    assert is_expiration_day("SPXW", today=tuesday) is False


def test_clear_available_expirations_per_symbol_isolation() -> None:
    today = date(2026, 5, 19)
    set_available_expirations("SPXW", [today])
    set_available_expirations("NDXP", [today])

    clear_available_expirations("SPXW")

    assert is_expiration_day("SPXW", today=today) is False
    assert is_expiration_day("NDXP", today=today) is True


def test_clear_available_expirations_clears_all_when_no_symbol() -> None:
    today = date(2026, 5, 19)
    set_available_expirations("SPXW", [today])
    set_available_expirations("NDXP", [today])

    clear_available_expirations()

    assert is_expiration_day("SPXW", today=today) is False
    assert is_expiration_day("NDXP", today=today) is False


# ──────────────────────────────────────────────────────────────────────────
# REV8 OPS-2 — Half-day calendar primitives
# ──────────────────────────────────────────────────────────────────────────


def test_early_close_at_eastern_black_friday_2026() -> None:
    """Day after Thanksgiving 2026 (Nov 27) is a half-day → 13:00 ET."""
    assert early_close_at_eastern(date(2026, 11, 27)) == time(13, 0)


def test_early_close_at_eastern_christmas_eve_2026() -> None:
    """Christmas Eve 2026 (Thu) is a half-day → 13:00 ET."""
    assert early_close_at_eastern(date(2026, 12, 24)) == time(13, 0)


def test_early_close_at_eastern_full_holiday_returns_none() -> None:
    """Christmas Day is a full holiday handled elsewhere — not a half-day."""
    assert early_close_at_eastern(date(2026, 12, 25)) is None


def test_early_close_at_eastern_regular_session_returns_none() -> None:
    """An ordinary trading day has no early close."""
    assert early_close_at_eastern(date(2026, 5, 18)) is None


def test_is_half_day_matches_calendar() -> None:
    """``is_half_day`` round-trips with ``early_close_at_eastern``."""
    assert is_half_day(date(2026, 11, 27)) is True
    assert is_half_day(date(2026, 12, 24)) is True
    assert is_half_day(date(2026, 5, 18)) is False
    assert is_half_day(date(2026, 12, 25)) is False


def test_effective_rth_close_returns_early_on_half_day() -> None:
    assert effective_rth_close(date(2026, 11, 27)) == time(13, 0)


def test_effective_rth_close_returns_configured_close_on_regular_day() -> None:
    """Default config close is 16:15 ET on a regular session."""
    assert effective_rth_close(date(2026, 5, 18)) == time(16, 15)


def test_is_rth_now_after_half_day_close_returns_false() -> None:
    """14:00 ET on Black Friday 2026 is *after* the 13:00 ET early close."""
    moment = datetime(2026, 11, 27, 14, 0, tzinfo=ET)
    assert is_rth_now(now=moment) is False


def test_is_rth_now_before_half_day_close_returns_true() -> None:
    """12:30 ET on a half-day is still inside RTH."""
    moment = datetime(2026, 11, 27, 12, 30, tzinfo=ET)
    assert is_rth_now(now=moment) is True


def test_session_close_today_honours_half_day() -> None:
    """``session_close_today`` returns the early-close datetime on a half-day."""
    moment = datetime(2026, 11, 27, 11, 0, tzinfo=ET)
    close = session_close_today(now=moment)
    assert close.time() == time(13, 0)


def test_session_close_today_unchanged_on_regular_day() -> None:
    moment = datetime(2026, 5, 18, 11, 0, tzinfo=ET)
    close = session_close_today(now=moment)
    assert close.time() == time(16, 15)


def test_minutes_to_close_uses_half_day() -> None:
    """At 11:00 ET on a half-day, ``minutes_to_close`` ≈ 120 min, not 315."""
    moment = datetime(2026, 11, 27, 11, 0, tzinfo=ET)
    assert minutes_to_close(now=moment) == pytest.approx(120.0, abs=0.01)


# ──────────────────────────────────────────────────────────────────────────
# Rev 8 ARCH-1 — pipeline-driven cache integration (Lane B)
# ──────────────────────────────────────────────────────────────────────────


def test_arch1_set_available_expirations_flips_session_snapshot_for_tuesday() -> None:
    """Rev 8 ARCH-1 integration: pipeline call to ``set_available_expirations``
    must flip ``session_snapshot()['is_expiration_day']`` for a Tuesday SPXW
    when the chain lists today (the static fallback would return False).
    """
    tuesday = date(2026, 5, 19)
    set_available_expirations("SPXW", [tuesday])

    moment = datetime(2026, 5, 19, 11, 0, tzinfo=ET)
    snap = session_snapshot(now=moment, symbol="SPXW")
    assert snap["is_expiration_day"] is True


def test_arch1_set_available_expirations_per_symbol_isolated() -> None:
    tuesday = date(2026, 5, 19)
    set_available_expirations("SPXW", [tuesday])
    moment = datetime(2026, 5, 19, 11, 0, tzinfo=ET)
    spxw = session_snapshot(now=moment, symbol="SPXW")
    ndxp = session_snapshot(now=moment, symbol="NDXP")
    assert spxw["is_expiration_day"] is True
    assert ndxp["is_expiration_day"] is False


def test_arch1_set_available_expirations_overwrites_prior_cache() -> None:
    """Subsequent calls replace the cached set instead of unioning."""
    tuesday = date(2026, 5, 19)
    wednesday = date(2026, 5, 20)
    set_available_expirations("SPXW", [tuesday])
    set_available_expirations("SPXW", [wednesday])
    assert is_expiration_day("SPXW", today=tuesday) is False
    assert is_expiration_day("SPXW", today=wednesday) is True
