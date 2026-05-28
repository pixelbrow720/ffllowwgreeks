"""Historical backfill of options chain data from Databento.

Two phases run in sequence at startup:

1. ``run_historical_backfill()`` pulls ``schema="definition"`` for the last
   ``HISTORICAL_BACKFILL_DAYS`` days and writes one stub row per contract
   (strike / expiry / option_type, no quotes). It also returns an
   *instrument_id → contract metadata* registry for phase 2.

2. ``run_historical_quotes_backfill(registry)`` pulls ``schema="cmbp-1"``
   (Consolidated MBP-1 = NBBO top-of-book) for the **last 15 minutes of
   the most recent RTH session**, groups by ``instrument_id``, takes the
   final snapshot per contract, and writes one row each carrying real
   ``bid`` / ``ask``. This gives the pipeline enough quote coverage to
   compute IV/Greeks and unblocks ``/last-close`` when the market is shut.

Both phases fail gracefully when the Databento API key is missing, the
schemas are not authorised, or the cost guardrail trips.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import BackfillCheckpoint
from app.db.session import get_session_factory
from app.ingestion.key_pool import (
    KeyCandidate,
    iter_keys,
    record_key_error,
    record_key_success,
)
from app.ingestion.writer import OptionsChainWriter, get_writer
from app.processing.session import RTH_TZ, _is_business_day, _now_eastern, session_close_today

logger = get_logger(__name__)


# Databento parent symbology suffix for OPRA options.
PARENT_SUFFIX = ".OPT"
DATASET = "OPRA.PILLAR"

# Window around the most recent RTH close for the cmbp-1 quote pull.
QUOTES_PRE_CLOSE_MINUTES = 30
QUOTES_POST_CLOSE_MINUTES = 0
# Soft cap (USD) above which we skip the cmbp-1 pull rather than risk a
# surprise bill. The Databento metadata.get_cost endpoint is cheap and
# tells us before we commit. 1.0 is plenty for a 15-minute window per
# parent symbol on OPRA — bumping it later is a one-line change.
QUOTES_COST_LIMIT_USD = 1.0

_AVAILABLE_END_RE = re.compile(
    r"available up to '([0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.+\-]+)'"
)


def _parent_symbol(underlying: str) -> str:
    return f"{underlying.upper()}{PARENT_SUFFIX}"


def _parse_available_end(message: str) -> datetime | None:
    """Best-effort parse of ``available up to '<iso>'`` from a Databento 422 error."""
    m = _AVAILABLE_END_RE.search(message)
    if not m:
        return None
    raw = m.group(1).replace(" ", "T")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _scale_price(value: object) -> float | None:
    """Mirror of the live ingester's price scaler.

    Databento commonly transports prices as int64 fixed-point at 1e9.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    if abs(f) > 1e6:
        f /= 1e9
    return f


def _coerce_expiry(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return pd.Timestamp(value).date()
    except Exception:  # noqa: BLE001
        try:
            return datetime.fromtimestamp(int(value) / 1e9, tz=UTC).date()
        except (TypeError, ValueError):
            return None


def _normalize_definition_row(
    underlying: str, raw: pd.Series, ts: datetime
) -> tuple[dict | None, int | None, dict | None]:
    """Convert a Databento ``definition`` record into an ``options_chain`` row.

    Returns ``(row, instrument_id, registry_entry)``. The registry entry is
    fed to the cmbp-1 phase so it can map ``instrument_id`` → contract.
    """
    expiry = raw.get("expiration") or raw.get("expiration_date")
    strike_raw = raw.get("strike_price")
    option_type = raw.get("instrument_class") or raw.get("option_type")
    if expiry is None or strike_raw is None or option_type is None:
        return None, None, None

    strike = _scale_price(strike_raw)
    if strike is None:
        return None, None, None

    opt = str(option_type).upper()
    if opt in ("C", "CALL"):
        opt_char = "C"
    elif opt in ("P", "PUT"):
        opt_char = "P"
    else:
        return None, None, None

    expiry_dt = _coerce_expiry(expiry)
    if expiry_dt is None:
        return None, None, None

    instrument_id_raw = raw.get("instrument_id")
    instrument_id: int | None = None
    if instrument_id_raw is not None:
        try:
            instrument_id = int(instrument_id_raw)
        except (TypeError, ValueError):
            instrument_id = None

    row = {
        "ts": ts,
        "symbol": underlying.upper(),
        "expiration": expiry_dt,
        "strike": strike,
        "option_type": opt_char,
        "oi": None,
        "volume": None,
        "iv": None,
        "delta": None,
        "gamma": None,
        "last_price": None,
        "bid": None,
        "ask": None,
        "underlying_price": None,
    }
    registry_entry = {
        "symbol": underlying.upper(),
        "expiration": expiry_dt,
        "strike": strike,
        "option_type": opt_char,
    }
    return row, instrument_id, registry_entry


async def _resolve_candidates() -> list[KeyCandidate]:
    """Resolve OPRA key candidates (env first, then DB pool). Empty on failure."""
    try:
        factory = get_session_factory()
        async with factory() as session:
            return await iter_keys(session, DATASET)
    except Exception:  # noqa: BLE001 — degrade gracefully on DB failure
        logger.exception("historical_backfill_key_pool_resolve_failed")
        return []


async def _record_candidate_success(candidate: KeyCandidate) -> None:
    if candidate.source != "db":
        return
    try:
        factory = get_session_factory()
        async with factory() as session:
            await record_key_success(session, candidate)
    except Exception:  # noqa: BLE001
        logger.exception(
            "historical_backfill_record_key_success_failed",
            key_label=candidate.label,
        )


async def _record_candidate_error(candidate: KeyCandidate, error_msg: str) -> None:
    if candidate.source != "db":
        return
    try:
        factory = get_session_factory()
        async with factory() as session:
            await record_key_error(session, candidate, error_msg=error_msg)
    except Exception:  # noqa: BLE001
        logger.exception(
            "historical_backfill_record_key_error_failed",
            key_label=candidate.label,
        )


async def run_historical_backfill(
    writer: OptionsChainWriter | None = None,
) -> dict[int, dict]:
    """Best-effort historical definition backfill.

    Returns the *instrument_id → {symbol, expiration, strike, option_type}*
    registry built while iterating the rows. The registry is consumed by
    :func:`run_historical_quotes_backfill` to attach NBBO quotes to the
    same contracts. An empty dict is returned on any failure so callers
    can chain phases without an extra ``None`` check.
    """
    registry: dict[int, dict] = {}
    settings = get_settings()
    if settings.disable_historical_backfill:
        logger.info("historical_backfill_disabled")
        return registry

    # Query completed checkpoints to prevent redundant API calls
    completed_symbols: set[str] = set()
    try:
        factory = get_session_factory()
        async with factory() as session:
            stmt = select(BackfillCheckpoint.symbol).where(BackfillCheckpoint.dataset == DATASET)
            res = await session.execute(stmt)
            completed_symbols = {s.upper() for s in res.scalars().all()}
    except Exception:
        logger.exception("historical_backfill_checkpoints_query_failed_degrading_gracefully")

    try:
        import databento as db
    except ImportError:
        logger.warning("databento_import_failed_for_backfill")
        return registry

    candidates = await _resolve_candidates()
    if not candidates:
        logger.warning("historical_backfill_skipped_no_api_key")
        return registry

    writer = writer or get_writer()
    # Use a generous buffer below "now" because Databento publishes historical
    # data with a ~15 minute lag. Without the buffer we routinely get
    # ``422 data_end_after_available_end``.
    end = datetime.now(UTC) - timedelta(minutes=30)
    start = end - timedelta(days=settings.historical_backfill_days)

    last_error: str | None = None
    for candidate in candidates:
        client = db.Historical(key=candidate.api_key)
        try:
            total_rows = await _run_definition_phase(
                client,
                writer=writer,
                supported_symbols=settings.supported_symbols,
                completed_symbols=completed_symbols,
                start=start,
                end=end,
                registry=registry,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning(
                "historical_backfill_candidate_failed",
                key_label=candidate.label,
                error=last_error[:200],
            )
            await _record_candidate_error(candidate, last_error)
            continue

        await _record_candidate_success(candidate)
        await writer.flush()
        logger.info(
            "historical_backfill_complete",
            rows=total_rows,
            registry_size=len(registry),
            key_label=candidate.label,
        )
        return registry

    if last_error is not None:
        logger.warning(
            "historical_backfill_all_candidates_failed",
            error=last_error[:200],
        )
    return registry


async def _run_definition_phase(
    client,
    *,
    writer: OptionsChainWriter,
    supported_symbols: list[str],
    completed_symbols: set[str],
    start: datetime,
    end: datetime,
    registry: dict[int, dict],
) -> int:
    """Pull definition rows for every parent symbol against an already-keyed client."""
    total_rows = 0
    for underlying in supported_symbols:
        if underlying.upper() in completed_symbols:
            logger.info("historical_backfill_skipped_by_checkpoint", symbol=underlying)
            continue
        parent = _parent_symbol(underlying)
        df = None
        symbol_end = end
        # Retry once if Databento rejects the end as too recent: parse the
        # available_end from the error and replay with that as the cutoff.
        for retry in range(2):
            try:
                data = await asyncio.to_thread(
                    client.timeseries.get_range,
                    dataset=DATASET,
                    schema="definition",
                    symbols=[parent],
                    stype_in="parent",
                    start=start,
                    end=symbol_end,
                )
                df = await asyncio.to_thread(data.to_df)
                break
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                if retry == 0 and "data_end_after_available_end" in msg:
                    parsed = _parse_available_end(msg)
                    if parsed is not None and parsed > start:
                        logger.info(
                            "historical_backfill_retry_with_available_end",
                            symbol=underlying,
                            available_end=parsed.isoformat(),
                        )
                        symbol_end = parsed
                        continue
                logger.warning(
                    "historical_backfill_symbol_failed",
                    symbol=underlying,
                    error=msg,
                )
                df = None
                break
        if df is None:
            continue
        if df.empty:
            logger.info("historical_backfill_empty", symbol=underlying)
            continue

        ts = end
        for _, raw in df.iterrows():
            row, instrument_id, reg_entry = _normalize_definition_row(
                underlying, raw, ts
            )
            if row is None:
                continue
            await writer.add(row)
            total_rows += 1
            if instrument_id is not None and reg_entry is not None:
                registry[instrument_id] = reg_entry

    return total_rows


# ──────────────────────────────────────────────────────────────────────────
# Phase 2 — cmbp-1 NBBO snapshot for the most recent close
# ──────────────────────────────────────────────────────────────────────────


def _last_close_window(
    *,
    pre_minutes: int = QUOTES_PRE_CLOSE_MINUTES,
    post_minutes: int = QUOTES_POST_CLOSE_MINUTES,
) -> tuple[datetime, datetime] | None:
    """Compute the (start, end) UTC datetimes of the last RTH close window.

    If today is a business day and we're already past the close, use today's
    close. Otherwise walk backwards day-by-day until we hit a business day
    and use that day's close. ``None`` is returned only in the unreachable
    case where the calendar somehow has no recent business day (a
    permanent holiday year, etc.) — defensive.
    """
    now_et = _now_eastern()
    candidate_close: datetime | None = None
    if _is_business_day(now_et.date()):
        today_close = session_close_today(now=now_et)
        if now_et >= today_close:
            candidate_close = today_close

    if candidate_close is None:
        # Walk backwards (yesterday → ...) until we hit a business day.
        d = now_et.date() - timedelta(days=1)
        for _ in range(10):  # bounded — we'll find one inside 10 days
            if _is_business_day(d):
                # Reuse session_close_today by anchoring "now" on that date.
                anchor = datetime.combine(d, dtime(12, 0), tzinfo=RTH_TZ)
                candidate_close = session_close_today(now=anchor)
                break
            d -= timedelta(days=1)

    if candidate_close is None:
        return None

    start_et = candidate_close - timedelta(minutes=pre_minutes)
    end_et = candidate_close + timedelta(minutes=post_minutes)
    return start_et.astimezone(UTC), end_et.astimezone(UTC)


def _extract_top_of_book(raw: pd.Series) -> tuple[float | None, float | None]:
    """Pull bid/ask from a Databento cmbp-1 row.

    The SDK's ``to_df()`` flattens ``levels[0]`` into either the legacy
    ``bid_px_00`` / ``ask_px_00`` columns or the newer ``bid_px_01`` /
    ``ask_px_01`` style. We try both, mirroring the live ingester.
    """
    bid = _scale_price(raw.get("bid_px_00"))
    ask = _scale_price(raw.get("ask_px_00"))
    if bid is None:
        bid = _scale_price(raw.get("bid_px"))
    if ask is None:
        ask = _scale_price(raw.get("ask_px"))
    return bid, ask


def _ts_event_to_dt(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        return pd.Timestamp(value).to_pydatetime().astimezone(UTC)
    except Exception:  # noqa: BLE001
        try:
            return datetime.fromtimestamp(int(value) / 1e9, tz=UTC)
        except (TypeError, ValueError, OverflowError):
            return None


def _reduce_last_per_instrument(records_iterable) -> tuple[list, dict[str, int]]:
    """Stream through Databento's record iterable, keeping ONE record per
    ``instrument_id`` (the latest — by iteration order — wins).

    The Phase-2 backfill only needs the closing snapshot per contract, so
    materialising the full window via ``list(data)`` is wasteful: a 30-min
    cbbo-1m pull across an OPRA parent fans out to millions of records and
    OOM's the worker. This streamed reduction keeps memory bounded to one
    entry per instrument.

    Returns the reduced list plus a counters dict so callers can log how
    many records were skipped without re-iterating.
    """
    latest: dict[int, object] = {}
    counters = {"total_seen": 0, "skipped_no_iid": 0, "skipped_no_levels": 0}
    for rec in records_iterable:
        counters["total_seen"] += 1
        iid_raw = getattr(rec, "instrument_id", None)
        if iid_raw is None:
            counters["skipped_no_iid"] += 1
            continue
        try:
            iid = int(iid_raw)
        except (TypeError, ValueError):
            counters["skipped_no_iid"] += 1
            continue
        # Filter to BBO-like records (skip SymbolMappingMsg, ErrorMsg, etc.).
        if not (hasattr(rec, "levels") or hasattr(rec, "bid_px")):
            counters["skipped_no_levels"] += 1
            continue
        latest[iid] = rec
    return list(latest.values()), counters


async def _write_backfill_checkpoint(
    *, dataset: str, symbol: str
) -> None:
    """Mark a per-parent backfill as complete. Best-effort: never raises."""
    try:
        factory = get_session_factory()
        now = datetime.now(UTC)
        async with factory() as session:
            stmt = pg_insert(BackfillCheckpoint).values(
                dataset=dataset,
                symbol=symbol,
                last_completed_at=now,
                updated_at=now,
            ).on_conflict_do_update(
                index_elements=["dataset", "symbol"],
                set_={
                    "last_completed_at": now,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)
            await session.commit()
    except Exception:  # noqa: BLE001 — checkpoints must never break backfill
        logger.exception(
            "backfill_checkpoint_write_failed",
            dataset=dataset,
            symbol=symbol,
        )


async def run_historical_quotes_backfill(
    registry: dict[int, dict],
    writer: OptionsChainWriter | None = None,
) -> int:
    """Pull cmbp-1 NBBO for the last 15 minutes of the most recent RTH session.

    For each ``parent`` symbol we ask Databento for the consolidated MBP-1
    quote stream over the close window, group by ``instrument_id``, take
    the *latest* row per contract, and write one ``options_chain`` row
    carrying the closing NBBO. The writer's ON CONFLICT DO UPDATE handles
    idempotency, so it's safe to run on every restart.

    Returns the total rows written across symbols.
    """
    settings = get_settings()
    if settings.disable_historical_backfill:
        logger.info("historical_quotes_backfill_disabled")
        return 0
    if not registry:
        logger.warning("historical_quotes_backfill_skipped_empty_registry")
        return 0

    try:
        import databento as db
    except ImportError:
        logger.warning("databento_import_failed_for_quotes_backfill")
        return 0

    candidates = await _resolve_candidates()
    if not candidates:
        logger.warning("historical_quotes_backfill_skipped_no_api_key")
        return 0

    window = _last_close_window()
    if window is None:
        logger.warning("historical_quotes_backfill_no_window")
        return 0
    window_start, window_end = window
    # Cap the end at Databento's available_end if needed (similar 15-min lag).
    safe_end = datetime.now(UTC) - timedelta(minutes=15)
    if window_end > safe_end:
        window_end = safe_end
    if window_start >= window_end:
        logger.info(
            "historical_quotes_backfill_window_in_future",
            start=window_start.isoformat(),
            end=window_end.isoformat(),
        )
        return 0

    writer = writer or get_writer()

    logger.info(
        "historical_quotes_backfill_start",
        start=window_start.isoformat(),
        end=window_end.isoformat(),
    )

    last_error: str | None = None
    for candidate in candidates:
        client = db.Historical(key=candidate.api_key)
        try:
            total_rows = await _run_quotes_phase(
                client,
                writer=writer,
                registry=registry,
                supported_symbols=settings.supported_symbols,
                window_start=window_start,
                window_end=window_end,
            )
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            logger.warning(
                "historical_quotes_backfill_candidate_failed",
                key_label=candidate.label,
                error=last_error[:200],
            )
            await _record_candidate_error(candidate, last_error)
            continue

        await _record_candidate_success(candidate)
        await writer.flush()
        logger.info(
            "historical_quotes_backfill_complete",
            rows=total_rows,
            key_label=candidate.label,
        )
        return total_rows

    if last_error is not None:
        logger.warning(
            "historical_quotes_backfill_all_candidates_failed",
            error=last_error[:200],
        )
    return 0


async def _run_quotes_phase(
    client,
    *,
    writer: OptionsChainWriter,
    registry: dict[int, dict],
    supported_symbols: list[str],
    window_start: datetime,
    window_end: datetime,
) -> int:
    """Pull cmbp-1 / cbbo-1m / tcbbo NBBO snapshots against an already-keyed client."""
    total_rows = 0
    for underlying in supported_symbols:
        parent = _parent_symbol(underlying)

        # Cost guardrail. cmbp-1 over OPRA can be heavy; bail if it would
        # blow past the threshold rather than surprise-bill the user.
        try:
            cost = await asyncio.to_thread(
                client.metadata.get_cost,
                dataset=DATASET,
                schema="cbbo-1m",
                symbols=[parent],
                stype_in="parent",
                start=window_start,
                end=window_end,
            )
            cost_usd = float(cost) if cost is not None else 0.0
        except Exception as exc:  # noqa: BLE001
            # Cost endpoint failures are non-fatal — we proceed and let the
            # actual fetch surface auth / billing problems.
            logger.info(
                "historical_quotes_backfill_cost_check_failed",
                symbol=underlying,
                error=str(exc)[:200],
            )
            cost_usd = 0.0

        if cost_usd > QUOTES_COST_LIMIT_USD:
            logger.warning(
                "historical_quotes_backfill_cost_exceeded",
                symbol=underlying,
                cost_usd=cost_usd,
                limit_usd=QUOTES_COST_LIMIT_USD,
            )
            continue

        # Try schemas in priority order. ``cbbo-1m`` is our preferred
        # 1-minute consolidated BBO; ``tcbbo`` (trade-tagged consolidated
        # BBO) is a smaller / often more available alternative we fall
        # back to when cbbo-1m is empty or unauthorized.
        last_by_instrument: dict[int, object] = {}
        reduce_counters: dict[str, int] = {
            "total_seen": 0,
            "skipped_no_iid": 0,
            "skipped_no_levels": 0,
        }
        last_error: str | None = None
        used_schema: str | None = None
        for schema_candidate in ("cbbo-1m", "tcbbo"):
            symbol_end = window_end
            attempt_last: dict[int, object] = {}
            attempt_counters: dict[str, int] = {
                "total_seen": 0,
                "skipped_no_iid": 0,
                "skipped_no_levels": 0,
            }
            schema_failed = False
            for retry in range(2):
                try:
                    data = await asyncio.to_thread(
                        client.timeseries.get_range,
                        dataset=DATASET,
                        schema=schema_candidate,
                        symbols=[parent],
                        stype_in="parent",
                        start=window_start,
                        end=symbol_end,
                    )
                    # Stream the iterator and reduce to one record per
                    # instrument_id inline — avoids materialising the full
                    # multi-million-record window (which OOM'd the worker).
                    reduced, attempt_counters = await asyncio.to_thread(
                        _reduce_last_per_instrument, data
                    )
                    attempt_last = {
                        int(r.instrument_id): r for r in reduced
                    }
                    break
                except Exception as exc:  # noqa: BLE001
                    msg = str(exc)
                    last_error = msg[:200]
                    lowered = msg.lower()
                    if (
                        "not authorized" in lowered
                        or "not supported" in lowered
                        or "unauthorized" in lowered
                        or "403" in lowered
                    ):
                        logger.warning(
                            "historical_quotes_backfill_unauthorized",
                            symbol=underlying,
                            schema=schema_candidate,
                            error=msg[:200],
                        )
                        attempt_last = {}
                        schema_failed = True
                        break
                    if (
                        retry == 0
                        and "data_end_after_available_end" in msg
                    ):
                        parsed = _parse_available_end(msg)
                        if parsed is not None and parsed > window_start:
                            logger.info(
                                "historical_quotes_backfill_retry_available_end",
                                symbol=underlying,
                                schema=schema_candidate,
                                available_end=parsed.isoformat(),
                            )
                            symbol_end = parsed
                            continue
                    logger.warning(
                        "historical_quotes_backfill_symbol_failed",
                        symbol=underlying,
                        schema=schema_candidate,
                        error=msg[:200],
                    )
                    attempt_last = {}
                    schema_failed = True
                    break

            logger.info(
                "historical_quotes_backfill_schema_attempt",
                symbol=underlying,
                schema=schema_candidate,
                contracts=len(attempt_last),
                total_seen=attempt_counters.get("total_seen", 0),
                failed=schema_failed,
            )
            if attempt_last:
                last_by_instrument = attempt_last
                reduce_counters = attempt_counters
                used_schema = schema_candidate
                break

        if not last_by_instrument:
            logger.info(
                "historical_quotes_backfill_empty",
                symbol=underlying,
                last_error=last_error,
                hint=(
                    "Both cbbo-1m and tcbbo returned no records. Live "
                    "ingestion will populate quotes when the market opens."
                ),
            )
            continue

        logger.info(
            "historical_quotes_backfill_records_reduced",
            symbol=underlying,
            schema=used_schema,
            unique_contracts=len(last_by_instrument),
            total_seen=reduce_counters.get("total_seen", 0),
            skipped_no_iid=reduce_counters.get("skipped_no_iid", 0),
            skipped_no_levels=reduce_counters.get("skipped_no_levels", 0),
            registry_size=len(registry),
        )

        symbol_rows = 0
        unknown_in_registry = 0
        no_bid_ask = 0
        for instrument_id, rec in last_by_instrument.items():
            contract = registry.get(instrument_id)
            if contract is None:
                unknown_in_registry += 1
                continue

            # Try levels[0] first (cbbo-1m / mbp-1 style)
            bid: float | None = None
            ask: float | None = None
            levels = getattr(rec, "levels", None)
            if levels and len(levels) > 0:
                lvl0 = levels[0]
                bid = _scale_price(getattr(lvl0, "bid_px", None))
                ask = _scale_price(getattr(lvl0, "ask_px", None))
            # Fallback to flat bid_px/ask_px
            if bid is None:
                bid = _scale_price(getattr(rec, "bid_px", None))
            if ask is None:
                ask = _scale_price(getattr(rec, "ask_px", None))

            if bid is None and ask is None:
                no_bid_ask += 1
                continue

            ts_event = getattr(rec, "ts_event", None)
            ts = _ts_event_to_dt(ts_event) or window_end
            row = {
                "ts": ts,
                "symbol": contract["symbol"],
                "expiration": contract["expiration"],
                "strike": contract["strike"],
                "option_type": contract["option_type"],
                "oi": None,
                "volume": None,
                "iv": None,
                "delta": None,
                "gamma": None,
                "last_price": None,
                "bid": bid,
                "ask": ask,
                "underlying_price": None,
            }
            await writer.add(row)
            symbol_rows += 1

        # Flush this parent's rows before recording the checkpoint so a
        # crash between flush and checkpoint never claims completion for
        # rows that weren't persisted.
        await writer.flush()
        await _write_backfill_checkpoint(dataset=DATASET, symbol=underlying)

        total_rows += symbol_rows
        logger.info(
            "historical_quotes_backfill_symbol_complete",
            symbol=underlying,
            rows=symbol_rows,
            unknown_in_registry=unknown_in_registry,
            no_bid_ask=no_bid_ask,
        )

    return total_rows
