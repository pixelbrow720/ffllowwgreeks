"""Central source of truth for trading-session state.

Rev 4 — Agent 1: every other module that needs to know "are we in RTH
right now?" or "what is τ for a 0DTE contract right now?" imports from
this module. No one else computes RTH boundaries — that keeps the policy
in one place and removes the risk that the scheduler, the metrics, and
the API drift out of sync.

The default session is the US equity-index options session:

* **Open**:  09:30 America/New_York (inclusive)
* **Close**: 16:15 America/New_York (inclusive)

Options on SPX / NDX actually stop trading at 16:00 ET; the 15-minute
buffer is intentional so we still emit one or two "post-bell" frames so
end-of-day metrics (charm rate at τ → 0, final HIRO bucket) land cleanly
before the scheduler tells everyone the session is over.

Configuration via env vars (see :class:`app.config.Settings`):

* ``RTH_OPEN_TIME``  — ``HH:MM`` (default ``09:30``)
* ``RTH_CLOSE_TIME`` — ``HH:MM`` (default ``16:15``)

Holidays are sourced from the ``holidays`` package (pure-Python, no
native deps) using the ``NYSE`` calendar where available, falling back
to the US federal calendar. Half-day early-close dates **are** treated
as full holidays for now — the metrics for a half day are typically
noise on a normal trading day so we conservatively skip computation
rather than ship partial figures.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Final
from zoneinfo import ZoneInfo

import holidays  # type: ignore[import-untyped]
import numpy as np
import pandas as pd

from app.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


RTH_TZ: Final[ZoneInfo] = ZoneInfo("America/New_York")

# Days that SPXW / NDXP traditionally settle on. CBOE listed Tue weekly
# in 2022 and Thu weekly in 2022; both are active. The static M/W/F set
# is only a fallback for callers that haven't supplied the actual chain
# expirations via ``set_available_expirations`` /
# ``is_expiration_day(..., available_expirations=...)``.
_DEFAULT_EXPIRY_WEEKDAYS: Final[frozenset[int]] = frozenset({0, 2, 4})  # Mon, Wed, Fri

# Per-symbol cache of expirations actually listed on the most recent
# chain snapshot, populated by the pipeline loader. Keyed on uppercase
# symbol; values are frozensets of ``date``. Empty cache → static
# weekday fallback.
_AVAILABLE_EXPIRATIONS: dict[str, frozenset[date]] = {}

# Minimum tau (in years) used to floor Greeks against blow-ups in the
# final minutes before expiry. 15 minutes ≈ 2.85e-5 years. Below this,
# d1 / d2 send norm.pdf to ~0 while the 1/(σ√τ) term explodes, producing
# numerically unstable per-strike values. Single source of truth for
# vanna_charm, pin_probability, and any other Greek-pricing module.
TAU_FLOOR_YEARS: Final[float] = 15.0 / (365.0 * 24.0 * 60.0)


# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _parse_hhmm(value: str, default: time) -> time:
    """Parse ``HH:MM`` into a ``time``; fall back to ``default`` on error."""
    try:
        hh, mm = value.split(":", 1)
        return time(int(hh), int(mm))
    except Exception:  # noqa: BLE001
        logger.warning(
            "session.bad_rth_time_format",
            value=value,
            fallback=default.strftime("%H:%M"),
        )
        return default


def _rth_window() -> tuple[time, time]:
    """Return the configured (open, close) times as :class:`datetime.time` pairs."""
    settings = get_settings()
    raw_open = getattr(settings, "rth_open_time", "09:30")
    raw_close = getattr(settings, "rth_close_time", "16:15")
    return (
        _parse_hhmm(raw_open, time(9, 30)),
        _parse_hhmm(raw_close, time(16, 15)),
    )


# NYSE-observed full-day closures, hardcoded for 2025-2028. We bake this
# in because the public ``holidays`` package falls back to the US federal
# calendar — which is **not** a strict superset of NYSE (Columbus Day and
# Veterans Day are federal holidays but NYSE trades them). Using the
# federal list as a fallback over-closes the pipeline. Half-day early
# closes are intentionally not treated as full holidays here; we keep
# the session open and accept that the last hour of a half day is
# liquidity-thin.
_NYSE_HOLIDAYS_HARDCODED: Final[frozenset[date]] = frozenset(
    {
        # 2025
        date(2025, 1, 1),    # New Year's Day
        date(2025, 1, 20),   # MLK Day
        date(2025, 2, 17),   # Washington's Birthday
        date(2025, 4, 18),   # Good Friday
        date(2025, 5, 26),   # Memorial Day
        date(2025, 6, 19),   # Juneteenth
        date(2025, 7, 4),    # Independence Day
        date(2025, 9, 1),    # Labor Day
        date(2025, 11, 27),  # Thanksgiving
        date(2025, 12, 25),  # Christmas
        # 2026
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),    # observed
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
        # 2027
        date(2027, 1, 1),
        date(2027, 1, 18),
        date(2027, 2, 15),
        date(2027, 3, 26),
        date(2027, 5, 31),
        date(2027, 6, 18),   # observed (19th = Sat)
        date(2027, 7, 5),    # observed (4th = Sun)
        date(2027, 9, 6),
        date(2027, 11, 25),
        date(2027, 12, 24),  # observed (25th = Sat)
        # 2028
        date(2028, 1, 17),   # MLK Day (Jan 1 is Sat, no NYE-Mon obs)
        date(2028, 2, 21),
        date(2028, 4, 14),
        date(2028, 5, 29),
        date(2028, 6, 19),
        date(2028, 7, 4),
        date(2028, 9, 4),
        date(2028, 11, 23),
        date(2028, 12, 25),
    }
)


# REV8 OPS-2: NYSE half-day early-close calendar.
# Close at 13:00 ET on these dates. Hardcoded 2024-2030 from the public
# NYSE schedule — refresh annually. Three rules drive the list:
#   * Day after Thanksgiving (the Friday after the 4th Thursday in November)
#   * Christmas Eve (Dec 24) when it falls on a regular trading day and is
#     not already an observed full holiday
#   * July 3 when July 4 falls on a regular trading day (Tue-Fri)
# Dates that overlap the full-holiday set above are excluded — the
# full-holiday close wins. Half-day list is intentionally explicit; do
# NOT make this dynamic. The static list survives a missing ``holidays``
# package and matches what NYSE publishes.
_NYSE_HALF_DAYS_HARDCODED: Final[frozenset[date]] = frozenset(
    {
        # 2024
        date(2024, 7, 3),    # July 4 is Thursday
        date(2024, 11, 29),  # Day after Thanksgiving (Nov 28)
        date(2024, 12, 24),  # Christmas Eve (Tue)
        # 2025
        date(2025, 7, 3),    # July 4 is Friday
        date(2025, 11, 28),  # Day after Thanksgiving (Nov 27)
        date(2025, 12, 24),  # Christmas Eve (Wed)
        # 2026 — July 3 is the observed July 4 (Sat) so it is a full
        # holiday, not a half-day. Dec 24 (Thu) is a half-day.
        date(2026, 11, 27),  # Day after Thanksgiving (Nov 26)
        date(2026, 12, 24),  # Christmas Eve (Thu)
        # 2027 — July 3 is a Saturday; July 5 (Mon) is the observed full
        # holiday. Dec 24 (Fri) is itself the observed Christmas full
        # holiday. Only Black Friday remains.
        date(2027, 11, 26),  # Day after Thanksgiving (Nov 25)
        # 2028 — Dec 24 is a Sunday so no half-day there.
        date(2028, 7, 3),    # July 4 is Tuesday
        date(2028, 11, 24),  # Day after Thanksgiving (Nov 23)
        # 2029
        date(2029, 7, 3),    # July 4 is Wednesday
        date(2029, 11, 23),  # Day after Thanksgiving (Nov 22)
        date(2029, 12, 24),  # Christmas Eve (Mon)
        # 2030
        date(2030, 7, 3),    # July 4 is Thursday
        date(2030, 11, 29),  # Day after Thanksgiving (Nov 28)
        date(2030, 12, 24),  # Christmas Eve (Tue)
    }
)

# Canonical early-close time on a half-day session: 13:00 America/New_York.
_HALF_DAY_CLOSE_TIME: Final[time] = time(13, 0)


@lru_cache(maxsize=1)
def _us_market_holidays() -> holidays.HolidayBase | frozenset[date]:
    """Return a cached US market holiday calendar.

    We prefer the dedicated NYSE / CBOE calendar exposed by the
    ``holidays`` package when the installed version provides it. When it
    doesn't (older packages don't expose ``holidays.NYSE``), we fall
    back to a hardcoded 2025-2028 NYSE table rather than the federal
    ``holidays.UnitedStates`` calendar — the federal list incorrectly
    closes Columbus Day and Veterans Day, which would over-close the
    pipeline on regular trading days.
    """
    nyse = getattr(holidays, "NYSE", None)
    if callable(nyse):
        try:
            return nyse(years=range(date.today().year - 1, date.today().year + 3))
        except Exception:  # noqa: BLE001
            pass
    return _NYSE_HOLIDAYS_HARDCODED


def _is_business_day(d: date) -> bool:
    if d.weekday() >= 5:  # Sat / Sun
        return False
    return d not in _us_market_holidays()


# ──────────────────────────────────────────────────────────────────────────
# Half-day calendar (REV8 OPS-2)
# ──────────────────────────────────────────────────────────────────────────


def early_close_at_eastern(today: date) -> time | None:
    """Return the early-close ``time`` (America/New_York) on a half-day,
    else ``None``.

    Resolution order (Rev 12 SRE-25 / DR-26):

    1. Operator override via ``OPERATOR_OVERRIDE_HALF_DAYS`` env var (read
       fresh on each call so the operator can flip mid-process via a
       process-level signal restart). The env var is a comma-separated
       list of ``YYYY-MM-DD`` dates; bad entries are silently dropped.
       This covers unexpected NYSE early-close announcements (extreme
       weather, exchange technical issues, presidential proclamation)
       between annual calendar refreshes.
    2. Hardcoded 2024-2030 half-day list — refresh annually.

    Returns ``None`` for full-holiday dates (those are handled by
    :func:`_is_business_day`) and for any date outside both sources.
    """
    if today in _operator_override_half_days():
        return _HALF_DAY_CLOSE_TIME
    if today in _NYSE_HALF_DAYS_HARDCODED:
        return _HALF_DAY_CLOSE_TIME
    return None


def is_half_day(today: date) -> bool:
    """``True`` when ``today`` is a US market half-day session.

    Honours the ``OPERATOR_OVERRIDE_HALF_DAYS`` env var first, then the
    hardcoded calendar (Rev 12 SRE-25 / DR-26).
    """
    if today in _operator_override_half_days():
        return True
    return today in _NYSE_HALF_DAYS_HARDCODED


def _operator_override_half_days() -> set[date]:
    """Parse ``OPERATOR_OVERRIDE_HALF_DAYS`` into a set of dates.

    Format: comma-separated ``YYYY-MM-DD`` entries. Whitespace is
    trimmed; malformed entries are silently dropped so a typo in one
    date doesn't disable the rest. Read fresh on every call (no cache)
    so an operator can update the env via a sidecar restart and the
    next pipeline tick picks up the new value without a full container
    restart. The cardinality is tiny (≤ a handful per year) so the
    parse cost is negligible.
    """
    raw = os.environ.get("OPERATOR_OVERRIDE_HALF_DAYS", "")
    if not raw:
        return set()
    out: set[date] = set()
    for part in raw.split(","):
        candidate = part.strip()
        if not candidate:
            continue
        try:
            out.add(date.fromisoformat(candidate))
        except ValueError:
            continue
    return out


def effective_rth_close(today: date) -> time:
    """Return the RTH close time effective for ``today``.

    On a half-day session this is :data:`_HALF_DAY_CLOSE_TIME` (13:00 ET);
    otherwise it is the configured ``RTH_CLOSE_TIME``. Callers in the
    pipeline / scheduler should prefer this over the raw config.
    """
    early = early_close_at_eastern(today)
    if early is not None:
        return early
    _, close_t = _rth_window()
    return close_t


def _now_eastern() -> datetime:
    """Current wall-clock in America/New_York (DST-aware)."""
    return datetime.now(tz=RTH_TZ)


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────


def is_rth_now(*, now: datetime | None = None) -> bool:
    """True if the current wall-clock time is inside RTH on a US business day.

    The optional ``now`` argument is for tests — passing a tz-aware datetime
    overrides the system clock. Naive datetimes are interpreted as already
    being in :data:`RTH_TZ`. On half-day sessions the close is 13:00 ET
    via :func:`effective_rth_close`.
    """
    moment = now if now is not None else _now_eastern()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=RTH_TZ)
    else:
        moment = moment.astimezone(RTH_TZ)

    if not _is_business_day(moment.date()):
        return False

    open_t, _ = _rth_window()
    close_t = effective_rth_close(moment.date())
    current = moment.time()
    return open_t <= current <= close_t


def session_open_today(*, now: datetime | None = None) -> datetime:
    """Today's RTH open as a tz-aware ``datetime`` in America/New_York."""
    moment = now if now is not None else _now_eastern()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=RTH_TZ)
    else:
        moment = moment.astimezone(RTH_TZ)
    open_t, _ = _rth_window()
    return moment.replace(hour=open_t.hour, minute=open_t.minute, second=0, microsecond=0)


def session_close_today(*, now: datetime | None = None) -> datetime:
    """Today's RTH close as a tz-aware ``datetime`` in America/New_York.

    Honours the half-day calendar — on a half-day session the returned
    datetime is the 13:00 ET early close instead of the configured
    ``RTH_CLOSE_TIME``.
    """
    moment = now if now is not None else _now_eastern()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=RTH_TZ)
    else:
        moment = moment.astimezone(RTH_TZ)
    close_t = effective_rth_close(moment.date())
    return moment.replace(hour=close_t.hour, minute=close_t.minute, second=0, microsecond=0)


def minutes_to_close(*, now: datetime | None = None) -> float:
    """Minutes remaining until session close. Negative if already after close."""
    moment = now if now is not None else _now_eastern()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=RTH_TZ)
    else:
        moment = moment.astimezone(RTH_TZ)
    close = session_close_today(now=moment)
    return (close - moment).total_seconds() / 60.0


# 1 year ≈ 365 days × 24 h × 60 min × 60 s = 31_536_000 s. Unified with the
# 365.0 calendar convention used by ``calendar_tau_years`` (Rev 9 DT-9) so
# both intraday seconds-to-close and per-day τ live on the same axis;
# downstream Greek pricing modules then see a single year-fraction unit.
SECONDS_PER_YEAR: Final[float] = 365.0 * 24 * 60 * 60


def time_to_expiry_0dte_years(*, now: datetime | None = None) -> float:
    """τ (years) for an option that expires at today's session close.

    * During RTH → ``(close - now) / SECONDS_PER_YEAR`` (always > 0)
    * After close → ``0.0`` (expired)
    * Before open → ``(close - open) / SECONDS_PER_YEAR`` (full session)
    * On weekends / holidays → ``0.0`` (no 0DTE today)
    """
    moment = now if now is not None else _now_eastern()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=RTH_TZ)
    else:
        moment = moment.astimezone(RTH_TZ)

    if not _is_business_day(moment.date()):
        return 0.0

    open_dt = session_open_today(now=moment)
    close_dt = session_close_today(now=moment)

    if moment >= close_dt:
        return 0.0
    if moment < open_dt:
        return max(0.0, (close_dt - open_dt).total_seconds() / SECONDS_PER_YEAR)
    return max(0.0, (close_dt - moment).total_seconds() / SECONDS_PER_YEAR)


# ── Vectorised calendar-day τ helper ─────────────────────────────────────────


def calendar_tau_years(
    expirations: pd.Series,
    *,
    today: pd.Timestamp | date | None = None,
    floor_days: int = 1,
) -> pd.Series:
    """Vectorised calendar-day τ-in-years for a Series of expiration dates.

    Replaces per-row ``df.apply(lambda exp: max(1, (d - today).days)/365)``
    patterns scattered across the processing modules. Single source of truth
    for the calendar convention (365.0 day-count, ``floor_days``-day floor).

    Returns a float Series aligned with ``expirations.index``. Unparseable
    rows return 0.0 so downstream callers can filter on ``tau > 0``.
    """
    if today is None:
        today_d = _now_eastern().date()
    elif isinstance(today, pd.Timestamp):
        today_d = today.date()
    elif isinstance(today, datetime):
        today_d = today.date()
    else:
        today_d = today

    parsed = pd.to_datetime(expirations, errors="coerce")
    days = (parsed.dt.normalize() - pd.Timestamp(today_d)).dt.days
    days = days.fillna(-1)
    days = days.where(days >= 0, -1)
    floored = np.maximum(days.to_numpy(dtype=float), float(floor_days))
    out = floored / 365.0
    out = np.where(days.to_numpy() < 0, 0.0, out)
    return pd.Series(out, index=expirations.index, dtype=float)



def set_available_expirations(symbol: str, expirations: Iterable[date]) -> None:
    """Register the set of expirations currently listed for ``symbol``.

    Called by the pipeline loader once per tick with the chain's actual
    expiration dates. Subsequent calls to :func:`is_expiration_day` for
    the same symbol consult this cache before falling back to the static
    weekday set, so Tue/Thu SPXW expirations are recognised when CBOE
    actually lists them.
    """
    parsed: set[date] = set()
    for exp in expirations:
        if isinstance(exp, datetime):
            parsed.add(exp.date())
        elif isinstance(exp, date):
            parsed.add(exp)
    _AVAILABLE_EXPIRATIONS[symbol.upper()] = frozenset(parsed)


def clear_available_expirations(symbol: str | None = None) -> None:
    """Clear the cached expirations for ``symbol`` (or all symbols)."""
    if symbol is None:
        _AVAILABLE_EXPIRATIONS.clear()
    else:
        _AVAILABLE_EXPIRATIONS.pop(symbol.upper(), None)


def is_expiration_day(
    symbol: str,
    *,
    today: date | None = None,
    available_expirations: Iterable[date] | None = None,
) -> bool:
    """True if today is a regularly-scheduled expiration day for ``symbol``.

    Resolution order:

    1. If ``available_expirations`` is provided, ``today`` must appear in
       that set. This is the preferred path — pass the chain's listed
       expirations directly from the pipeline loader.
    2. Otherwise, consult the per-symbol cache populated via
       :func:`set_available_expirations` (also pipeline-driven). When a
       cache entry exists for ``symbol`` it is authoritative.
    3. Fallback: the static M/W/F weekday set. Kept conservative; misses
       Tue/Thu SPXW expirations that CBOE listed in 2022. The pipeline
       should populate the cache to make this branch cold.
    """
    today_d = today if today is not None else _now_eastern().date()
    if not _is_business_day(today_d):
        return False
    sym = symbol.upper()
    if sym not in {"SPX", "SPXW", "NDX", "NDXP"}:
        return False

    if available_expirations is not None:
        for exp in available_expirations:
            if isinstance(exp, datetime):
                if exp.date() == today_d:
                    return True
            elif isinstance(exp, date):
                if exp == today_d:
                    return True
        return False

    cached = _AVAILABLE_EXPIRATIONS.get(sym)
    if cached is not None:
        return today_d in cached

    return today_d.weekday() in _DEFAULT_EXPIRY_WEEKDAYS


def next_business_day(from_day: date | None = None) -> date:
    """Return the next US business day strictly after ``from_day``."""
    cur = (from_day or _now_eastern().date()) + timedelta(days=1)
    while not _is_business_day(cur):
        cur = cur + timedelta(days=1)
    return cur


def session_snapshot(*, now: datetime | None = None, symbol: str | None = None) -> dict[str, object]:
    """Build the ``session_state`` dict embedded in WebSocket frames.

    A single dict so the API surface stays stable; consumers (live
    dashboards, plugins) pick the fields they need.
    """
    moment = now if now is not None else _now_eastern()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=RTH_TZ)
    else:
        moment = moment.astimezone(RTH_TZ)

    payload: dict[str, object] = {
        "is_rth": is_rth_now(now=moment),
        "minutes_to_close": round(minutes_to_close(now=moment), 3),
        "time_to_expiry_0dte_years": time_to_expiry_0dte_years(now=moment),
        "session_open": session_open_today(now=moment).isoformat(),
        "session_close": session_close_today(now=moment).isoformat(),
        "now_eastern": moment.isoformat(),
    }
    if symbol is not None:
        payload["is_expiration_day"] = is_expiration_day(symbol, today=moment.date())
    return payload
