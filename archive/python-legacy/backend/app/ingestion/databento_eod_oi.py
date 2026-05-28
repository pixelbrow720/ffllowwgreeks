"""End-of-day Open Interest ingestion from Databento OPRA Pillar.

OPRA's ``statistics`` schema publishes ``open_interest`` records once per
trading day. This module pulls them at startup (and again daily, scheduled
by ``app.processing.scheduler``) and upserts them into ``eod_open_interest``.

The compute pipeline reads from this table to back-fill missing live OI when
producing GEX-by-OI and OI-walls — see ``processing.loader``.

This task is **best-effort**:
* If neither ``DATABENTO_API_KEY_OPRA`` nor ``DATABENTO_API_KEY`` is set,
  the function logs a warning and
  returns ``0``.
* If the Databento subscription does not include statistics on OPRA Pillar,
  the API will respond with a 422 / 404; we log the warning and return ``0``.
* Any other transient error is logged and swallowed — the rest of the
  application stays online.

Diagnostics (REV5):
- Raw row counts before/after ``stat_type`` filter are logged so we can
  tell apart "Databento returned nothing" from "filter dropped everything".
- We try multiple ``stat_type`` codes (the enum has shifted slightly
  across Databento revisions) and accept any of them.
- If ``parent`` symbology returns nothing we retry with ``raw_symbol``.
- A wider 7-day lookback is used by default to survive long weekends /
  holidays where the most recent OI snapshot may be several days old.
- As a last resort we sniff ``definition`` schema rows for an inline
  ``open_interest`` field — some Databento product variants surface OI
  there instead of in the statistics stream.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import EodOpenInterest
from app.db.session import get_session_factory
from app.ingestion.key_pool import (
    KeyCandidate,
    iter_keys,
    record_key_error,
    record_key_success,
)

try:
    from app.processing.session import _is_business_day as _session_is_business_day
    from app.processing.session import _now_eastern as _eastern_now
except ImportError:  # pragma: no cover — defensive: session module restructured
    _eastern_now = None  # type: ignore[assignment]
    _session_is_business_day = None  # type: ignore[assignment]

logger = get_logger(__name__)


DATASET = "OPRA.PILLAR"
PARENT_SUFFIX = ".OPT"

# Databento STAT_TYPE codes.
# https://databento.com/docs/standards-and-conventions/common-fields-enums-types#stat-type
# The "open_interest" code has been documented as 9 historically; some
# dataset revisions have used 10. We try both, plus we sniff any rows
# whose ``stat_type`` happens to map to a string that mentions "open_interest".
STAT_TYPE_OPEN_INTEREST_PRIMARY = 9
STAT_TYPE_OPEN_INTEREST_ALT = 10
CANDIDATE_OI_STAT_TYPES: tuple[int, ...] = (
    STAT_TYPE_OPEN_INTEREST_PRIMARY,
    STAT_TYPE_OPEN_INTEREST_ALT,
)


def _parent_symbol(underlying: str) -> str:
    return f"{underlying.upper()}{PARENT_SUFFIX}"


def _normalize_oi_row(
    underlying: str, raw: pd.Series, *, quantity_col: str = "quantity"
) -> dict | None:
    """Best-effort conversion of a Databento statistics row into our shape."""
    expiry = raw.get("expiration") or raw.get("expiration_date")
    strike_raw = raw.get("strike_price")
    option_type = raw.get("instrument_class") or raw.get("option_type")
    quantity = raw.get(quantity_col)
    if quantity is None:
        # Some schemas put the value under ``open_interest`` directly.
        quantity = raw.get("open_interest")
    if (
        expiry is None
        or strike_raw is None
        or option_type is None
        or quantity is None
        or pd.isna(quantity)
    ):
        return None

    try:
        strike = float(strike_raw)
        if strike > 1e6:
            strike /= 1e9
    except (TypeError, ValueError):
        return None

    opt = str(option_type).upper()
    opt_char = "C" if opt in ("C", "CALL") else "P" if opt in ("P", "PUT") else None
    if opt_char is None:
        return None

    try:
        expiry_dt = pd.Timestamp(expiry).date()
    except Exception:  # noqa: BLE001
        return None

    try:
        oi_value = int(float(quantity))
    except (TypeError, ValueError):
        return None
    if oi_value < 0:
        return None

    # REV8 OPS-3: derive ``oi_date`` from the source row's ``ts_event`` so
    # holiday cron firings stamp yesterday's snapshot with yesterday's date,
    # not today's. Falls back to ``today_eastern`` when the source row has
    # no usable ts (degenerate path; the caller already gates on a business
    # day so the stamp is at worst the running-day's date).
    oi_date_value: date | None = None
    ts_event = raw.get("ts_event")
    if ts_event is not None:
        try:
            ts_event_ts = pd.Timestamp(ts_event)
            if ts_event_ts.tzinfo is None:
                ts_event_ts = ts_event_ts.tz_localize("UTC")
            # OPRA EOD OI snapshots fire after the regular session close —
            # convert to ET so the stamped date matches the trading day.
            oi_date_value = ts_event_ts.tz_convert("America/New_York").date()
        except Exception:  # noqa: BLE001
            oi_date_value = None
    if oi_date_value is None:
        oi_date_value = _today_eastern()

    return {
        "symbol": underlying.upper(),
        "expiration": expiry_dt,
        "strike": strike,
        "option_type": opt_char,
        "oi_date": oi_date_value,
        "open_interest": oi_value,
        "updated_at": datetime.now(UTC),
    }


def _filter_oi_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Filter ``df`` to rows that look like open-interest records.

    Tries the documented ``stat_type`` codes first. If none of those match,
    falls back to a substring search on any string-typed ``stat_type`` /
    ``stat_name`` column for "open_interest".
    """
    if df is None or df.empty or "stat_type" not in df.columns:
        return df if df is not None else pd.DataFrame()

    # Numeric path — primary + alt code.
    coerced = pd.to_numeric(df["stat_type"], errors="coerce")
    matched = df[coerced.isin(CANDIDATE_OI_STAT_TYPES)]
    if not matched.empty:
        return matched

    # String path — some revisions return labelled stat types.
    as_str = df["stat_type"].astype(str).str.lower()
    matched = df[as_str.str.contains("open_interest", na=False)]
    if not matched.empty:
        return matched

    # Last resort — if the dataframe carries a separate ``stat_name``
    # column, scan it.
    if "stat_name" in df.columns:
        as_str_name = df["stat_name"].astype(str).str.lower()
        matched = df[as_str_name.str.contains("open_interest", na=False)]
        if not matched.empty:
            return matched

    return pd.DataFrame()


async def _fetch_statistics(
    client, *, parent: str, start: datetime, end: datetime, stype_in: str
) -> pd.DataFrame | None:
    """Fetch the statistics schema for ``parent``, returning a DF or None on error.

    Failures (auth, schema not authorised, etc.) are logged and translated
    into ``None`` so the caller can move to the next strategy.
    """
    try:
        data = await asyncio.to_thread(
            client.timeseries.get_range,
            dataset=DATASET,
            schema="statistics",
            symbols=[parent],
            stype_in=stype_in,
            start=start,
            end=end,
        )
        df = await asyncio.to_thread(data.to_df)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "eod_oi_statistics_fetch_failed",
            parent=parent,
            stype_in=stype_in,
            error=str(exc)[:200],
        )
        return None
    return df


async def _fetch_definition_oi(
    client, *, parent: str, start: datetime, end: datetime, stype_in: str
) -> pd.DataFrame | None:
    """Fetch the definition schema and look for an inline ``open_interest`` column."""
    try:
        data = await asyncio.to_thread(
            client.timeseries.get_range,
            dataset=DATASET,
            schema="definition",
            symbols=[parent],
            stype_in=stype_in,
            start=start,
            end=end,
        )
        df = await asyncio.to_thread(data.to_df)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "eod_oi_definition_fetch_failed",
            parent=parent,
            stype_in=stype_in,
            error=str(exc)[:200],
        )
        return None

    if df is None or df.empty:
        return None
    if "open_interest" not in df.columns:
        logger.info(
            "eod_oi_definition_no_oi_column",
            parent=parent,
            columns=list(df.columns)[:30],
        )
        return None
    # Keep rows where the inline OI column carries a usable value.
    coerced = pd.to_numeric(df["open_interest"], errors="coerce")
    df = df[coerced.notna() & (coerced > 0)].copy()
    if df.empty:
        return None
    return df


async def fetch_eod_oi_from_databento(
    underlying: str, *, lookback_days: int = 7
) -> list[dict]:
    """Fetch the latest EOD OI rows for ``underlying``. Returns possibly empty list."""
    try:
        import databento as db
    except ImportError:
        logger.warning("databento_import_failed_for_eod_oi")
        return []

    factory = get_session_factory()
    try:
        async with factory() as session:
            candidates = await iter_keys(session, DATASET)
    except Exception:  # noqa: BLE001 — degrade gracefully on DB failure
        logger.exception("eod_oi_key_pool_resolve_failed")
        candidates = []
    if not candidates:
        logger.warning("eod_oi_skipped_no_api_key", symbol=underlying)
        return []

    end = datetime.now(UTC) - timedelta(minutes=30)
    start = end - timedelta(days=lookback_days)
    parent = _parent_symbol(underlying)

    last_error: str | None = None
    for candidate in candidates:
        try:
            client = db.Historical(key=candidate.api_key)
            rows = await _fetch_with_client(
                client,
                underlying=underlying,
                parent=parent,
                start=start,
                end=end,
                lookback_days=lookback_days,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning(
                "eod_oi_candidate_failed",
                symbol=underlying,
                key_label=candidate.label,
                error=last_error[:200],
            )
            await _record_candidate_error(candidate, last_error)
            continue

        # Treat *any* successful round-trip (even an empty result) as a key
        # health signal. The empty-result path is logged separately by the
        # inner helper.
        await _record_candidate_success(candidate)
        return rows

    if last_error is not None:
        logger.warning(
            "eod_oi_all_candidates_failed",
            symbol=underlying,
            error=last_error[:200],
        )
    return []


async def _record_candidate_success(candidate: KeyCandidate) -> None:
    if candidate.source != "db":
        return
    try:
        factory = get_session_factory()
        async with factory() as session:
            await record_key_success(session, candidate)
    except Exception:  # noqa: BLE001
        logger.exception("eod_oi_record_key_success_failed", key_label=candidate.label)


async def _record_candidate_error(candidate: KeyCandidate, error_msg: str) -> None:
    if candidate.source != "db":
        return
    try:
        factory = get_session_factory()
        async with factory() as session:
            await record_key_error(session, candidate, error_msg=error_msg)
    except Exception:  # noqa: BLE001
        logger.exception("eod_oi_record_key_error_failed", key_label=candidate.label)


async def _fetch_with_client(
    client,
    *,
    underlying: str,
    parent: str,
    start: datetime,
    end: datetime,
    lookback_days: int,
) -> list[dict]:
    """Run the three-strategy fetch against an already-keyed client."""
    # ── Strategy 1: statistics via parent symbology ──────────────────────
    df = await _fetch_statistics(
        client, parent=parent, start=start, end=end, stype_in="parent"
    )
    raw_count = 0 if df is None else int(len(df))
    logger.info(
        "eod_oi_statistics_raw_rows",
        symbol=underlying,
        rows=raw_count,
        lookback_days=lookback_days,
        stype_in="parent",
    )

    filtered = pd.DataFrame() if df is None else _filter_oi_rows(df)
    filtered_count = 0 if filtered is None else int(len(filtered))
    if df is not None and not df.empty:
        # Distribution diagnostic — helps pinpoint which stat_type values
        # *are* present when none of our candidates match.
        try:
            distribution = (
                df["stat_type"]
                .value_counts(dropna=False)
                .head(20)
                .to_dict()
            )
        except Exception:  # noqa: BLE001
            distribution = {}
        logger.info(
            "eod_oi_statistics_filtered_rows",
            symbol=underlying,
            rows=filtered_count,
            stat_type_distribution={str(k): int(v) for k, v in distribution.items()},
        )

    # ── Strategy 2: statistics via raw_symbol if parent yielded nothing ──
    if filtered is None or filtered.empty:
        df_raw = await _fetch_statistics(
            client, parent=parent, start=start, end=end, stype_in="raw_symbol"
        )
        raw_count2 = 0 if df_raw is None else int(len(df_raw))
        logger.info(
            "eod_oi_statistics_raw_rows",
            symbol=underlying,
            rows=raw_count2,
            lookback_days=lookback_days,
            stype_in="raw_symbol",
        )
        filtered = pd.DataFrame() if df_raw is None else _filter_oi_rows(df_raw)
        filtered_count = 0 if filtered is None else int(len(filtered))
        logger.info(
            "eod_oi_statistics_filtered_rows",
            symbol=underlying,
            rows=filtered_count,
            stype_in="raw_symbol",
        )

    quantity_col = "quantity"

    # ── Strategy 3: definition schema with inline open_interest ──────────
    if filtered is None or filtered.empty:
        df_def = await _fetch_definition_oi(
            client, parent=parent, start=start, end=end, stype_in="parent"
        )
        if df_def is not None and not df_def.empty:
            logger.info(
                "eod_oi_definition_fallback_rows",
                symbol=underlying,
                rows=int(len(df_def)),
            )
            filtered = df_def
            quantity_col = "open_interest"

    if filtered is None or filtered.empty:
        logger.warning(
            "eod_oi_no_rows_found",
            symbol=underlying,
            hint=(
                "No open_interest rows from statistics or definition. Either "
                "the OPRA subscription does not include statistics, or "
                "Databento has not published OI for this lookback window. "
                "Walls/GEX will fall back to volume weights in pipeline."
            ),
        )
        return []

    out: list[dict] = []
    seen: set[tuple[str, date, float, str]] = set()
    sort_col = (
        "ts_event" if "ts_event" in filtered.columns else filtered.columns[0]
    )
    filtered = filtered.sort_values(sort_col, ascending=False)
    for _, raw in filtered.iterrows():
        normalized = _normalize_oi_row(
            underlying, raw, quantity_col=quantity_col
        )
        if normalized is None:
            continue
        key = (
            normalized["symbol"],
            normalized["expiration"],
            normalized["strike"],
            normalized["option_type"],
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(normalized)

    logger.info(
        "eod_oi_normalized_rows",
        symbol=underlying,
        rows=len(out),
        quantity_col=quantity_col,
    )
    return out


async def upsert_eod_oi(rows: list[dict]) -> int:
    if not rows:
        return 0
    factory = get_session_factory()
    async with factory() as session:
        stmt = insert(EodOpenInterest).values(rows)
        # TODO(schema): EOD OI table PK is (symbol, expiration, strike,
        # option_type) — ``oi_date`` is NOT part of the conflict key, so
        # every daily run overwrites the previous day's row and we lose
        # OI history. Fixing this requires adding ``oi_date`` to the PK
        # (a migration), which is out of scope here. Until then, callers
        # MUST guard against re-running for an ``oi_date`` that's already
        # present (see :func:`run_eod_oi_ingestion`'s same-day check), and
        # we refuse to overwrite a row whose stored ``oi_date`` is newer
        # than the row we're about to write.
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "expiration", "strike", "option_type"],
            set_={
                "oi_date": stmt.excluded.oi_date,
                "open_interest": stmt.excluded.open_interest,
                "updated_at": stmt.excluded.updated_at,
            },
            # Refuse to overwrite a newer oi_date with an older one. This
            # protects against an out-of-order replay (e.g. a startup pull
            # racing the scheduled one) clobbering today's row with
            # yesterday's data.
            where=(EodOpenInterest.oi_date <= stmt.excluded.oi_date),
        )
        await session.execute(stmt)
        await session.commit()
    return len(rows)


def _today_eastern() -> date:
    """Today's date in America/New_York. Falls back to UTC if helper missing."""
    if _eastern_now is not None:
        return _eastern_now().date()
    return datetime.now(UTC).date()


async def run_eod_oi_ingestion() -> int:
    """Pull EOD OI for every supported symbol. Returns total rows upserted."""
    settings = get_settings()

    today = _today_eastern()

    # REV8 OPS-3: gate on business-day calendar at entry. Holiday cron
    # firings would otherwise pull yesterday's snapshot and (pre-fix)
    # mis-stamp it with today's date — corrupting the EOD-OI fallback
    # used by walls / GEX. Half-days are still valid trading sessions
    # so we only skip on full holidays.
    if _session_is_business_day is not None and not _session_is_business_day(today):
        logger.info(
            "eod_oi_skip_non_business_day",
            today=str(today),
            hint=(
                "non-business day (holiday or weekend); EOD OI ingestion "
                "is a no-op"
            ),
        )
        return 0

    # Idempotency guard: if today's oi_date is already present we skip the
    # whole pull. The PK doesn't include oi_date so the upsert would happily
    # rewrite the same data — this saves a Databento round-trip and a noisy
    # write storm on startup + scheduled-job overlap.
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(EodOpenInterest.oi_date)
                .order_by(EodOpenInterest.oi_date.desc())
                .limit(1)
            )
            latest = result.scalar_one_or_none()
        if latest is not None and latest >= today:
            logger.info(
                "eod_oi_skip_already_pulled_today",
                latest=str(latest),
                today=str(today),
            )
            return 0
    except Exception:  # noqa: BLE001 — fail open, run the pull regardless
        logger.exception("eod_oi_idempotency_check_failed")

    total = 0
    for symbol in settings.supported_symbols:
        try:
            rows = await fetch_eod_oi_from_databento(symbol)
            inserted = await upsert_eod_oi(rows)
            total += inserted
            logger.info("eod_oi_ingested", symbol=symbol, rows=inserted)
        except Exception:  # noqa: BLE001
            logger.exception("eod_oi_ingestion_error", symbol=symbol)
    return total
