"""Globex MDP 3.0 (CME futures) live ingester.

Subscribes to GLBX.MDP3 streams for the configured futures parent symbols
(default ES + NQ front-month) and writes:

* every trade into ``futures_ticks``;
* every 1-second order-book snapshot into ``liquidity_snapshots``.

Behaviour mirrors :class:`DatabentoLiveIngester` for OPRA Pillar:
graceful degradation when the API key / package / schemas are missing,
exponential backoff on disconnects, and runtime telemetry.

Dataset: ``GLBX.MDP3`` (CME Globex MDP 3.0 — official feed for ES/NQ/etc.).

Schemas used:

* ``trades``  — per-trade events (price, size, aggressor side).
* ``mbp-10``  — top-10 levels of the order book; we throttle to 1 Hz.
* ``definition`` — instrument metadata, used to map ``instrument_id`` to
  user-friendly ``symbol`` + expiration.

If the ``mbp-10`` schema is dropped at connect time (insufficient
subscription tier) the ingester continues with trade tape only.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from app.api.tick_notifier import get_tick_notifier
from app.config import get_settings
from app.core.logging import get_logger
from app.db.session import get_session_factory
from app.ingestion.bulk_writers import (
    BulkUpsertWriter,
    get_futures_tick_writer,
    get_liquidity_snapshot_writer,
)
from app.ingestion.key_pool import (
    KeyCandidate,
    iter_keys,
    record_key_error,
    record_key_success,
)
from app.processing.spot import get_basis

# Map a futures parent root (the alpha prefix of e.g. ``ESM6``) to the cash
# index symbol our public API uses. Anything not in this map is silently
# skipped on the tick fast-path — we only stream cash-index price ticks.
_FUTURES_ROOT_TO_CASH_SYMBOL: dict[str, str] = {
    "ES": "SPXW",
    "NQ": "NDXP",
}

logger = get_logger(__name__)

DATASET = "GLBX.MDP3"
DEFAULT_SCHEMAS = ("definition", "trades", "mbp-10")
# Bootstrap window for historical definition snapshots. Two days is enough
# to capture the active instrument curve while staying inside the
# Databento publication window.
_BOOTSTRAP_DAYS = 2
# Databento parent symbology requires a ROOT.[FUT|OPT|SPOT] suffix; for CME
# futures we use ``.FUT`` (e.g. ``ES.FUT`` resolves to the entire ES futures
# curve). The ``_parent_root`` helper strips the suffix back to ``ES`` for
# downstream filtering.
DEFAULT_PARENTS = ("ES.FUT", "NQ.FUT")
SNAPSHOT_INTERVAL_S = 1.0
# Legacy default — overridden at runtime by ``Settings.ingestion_max_reconnects``
# (REV8 OPS-1).
MAX_RECONNECTS = 30
INITIAL_BACKOFF_S = 2.0

# Allowlist for ``sample_record_attrs`` diagnostics — see databento_live.py.
_DIAGNOSTICS_ATTR_ALLOWLIST: frozenset[str] = frozenset({
    "bid_px",
    "ask_px",
    "bid_sz",
    "ask_sz",
    "instrument_id",
    "raw_symbol",
    "ts_event",
    "ts_recv",
    "stat_type",
    "quantity",
    "value",
    "price",
    "size",
    "aggressor_side",
    "publisher_id",
    "expiration",
    "expiration_date",
    "strike_price",
    "instrument_class",
    "option_type",
    "msg",
    "err",
})

# Auth-style error fragments that imply a schema-drop event but did not
# match one of the explicit ``"<schema> not authorized"`` substrings.
_AUTH_ERROR_FRAGMENTS: tuple[str, ...] = (
    "unauthorized",
    "forbidden",
    "not authorized",
    "not supported",
)


def _scale_price(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if abs(f) > 1e6:  # int64 1e9 fixed-point detection
        f /= 1e9
    return f


def _record_ts(record: Any) -> datetime | None:
    """Convert ``record.ts_event`` (ns int) to UTC datetime, or ``None`` on fail.

    Fabricating ``datetime.now(UTC)`` would create batch-wide PK collisions
    under load — callers must skip + count drops on ``None``.
    """
    ts_event = getattr(record, "ts_event", None)
    if ts_event is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts_event) / 1e9, tz=UTC)
    except (TypeError, ValueError, OverflowError):
        return None


class GlobexLiveIngester:
    """Live CME Globex MDP 3.0 trade-tape + book-depth ingester."""

    def __init__(
        self,
        *,
        parents: tuple[str, ...] = DEFAULT_PARENTS,
        tick_writer: BulkUpsertWriter | None = None,
        liquidity_writer: BulkUpsertWriter | None = None,
    ) -> None:
        self._settings = get_settings()
        self._parents = parents
        self._tick_writer = tick_writer or get_futures_tick_writer()
        self._liquidity_writer = liquidity_writer or get_liquidity_snapshot_writer()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._schemas: list[str] = list(DEFAULT_SCHEMAS)
        # registry: instrument_id -> {symbol, raw_symbol, expiration}
        self._registry: dict[int, dict[str, Any]] = {}
        # latest book per instrument_id (snapshot pumped at 1 Hz)
        self._book: dict[int, dict[str, Any]] = {}
        self._snapshot_task: asyncio.Task | None = None
        self._record_counts: dict[str, int] = {}
        # Persistent diagnostics surfaced to the admin Data Inspector.
        self._cumulative_record_counts: dict[str, int] = {}
        self._dropped_schemas: list[str] = []
        self._connection_attempts: int = 0
        self._last_error: str | None = None
        self._first_record_at: datetime | None = None
        self._last_record_at: datetime | None = None
        # Captured gateway frames (SystemMsg / ErrorMsg). Most recent first.
        self._system_messages: list[str] = []
        self._error_messages: list[str] = []
        # Sample record attrs (allowlisted) for first record of each type.
        self._sample_record_attrs: dict[str, dict[str, Any]] = {}
        # Terminal-failure flag — set after MAX_RECONNECTS or no schemas left.
        self._dead: bool = False
        # Counter for trades dropped due to unparseable timestamp.
        self._dropped_no_ts_count: int = 0
        # Counter for trades whose instrument_id was not in the registry.
        self._unmatched_total: int = 0
        # Most recent KeyCandidate label that successfully connected.
        self._active_key_label: str | None = None

    def diagnostics(self) -> dict[str, Any]:
        max_reconnects = int(
            getattr(self._settings, "ingestion_max_reconnects", MAX_RECONNECTS)
        )
        return {
            "parents": list(self._parents),
            "registry_size": len(self._registry),
            "book_size": len(self._book),
            "schemas_active": list(self._schemas),
            "schemas_dropped": list(self._dropped_schemas),
            "record_counts": dict(self._cumulative_record_counts),
            "sample_record_attrs": dict(self._sample_record_attrs),
            "first_record_at": (
                self._first_record_at.isoformat() if self._first_record_at else None
            ),
            "last_record_at": (
                self._last_record_at.isoformat() if self._last_record_at else None
            ),
            "connection_attempts": self._connection_attempts,
            "last_error": self._last_error,
            "system_messages": list(self._system_messages),
            "error_messages": list(self._error_messages),
            "terminated": self._dead,
            "attempts_remaining_until_terminal_reset": (
                0 if self._dead
                else max(0, max_reconnects - self._connection_attempts)
            ),
            "dropped_no_ts_count": self._dropped_no_ts_count,
            "unmatched_total": self._unmatched_total,
            "active_key_label": self._active_key_label,
        }

    def reset_after_terminal(self) -> None:
        """Clear the terminal-failure flag so :meth:`start` can be called again."""
        self._dead = False
        self._connection_attempts = 0
        self._last_error = None

    # ── Public API ─────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._task is not None:
            return
        if self._dead:
            logger.error(
                "globex_live_start_blocked_terminal_state",
                hint="call reset_after_terminal() before retrying",
            )
            return
        self._task = asyncio.create_task(self._run_with_reconnect(),
                                         name="databento_globex_live")
        self._snapshot_task = asyncio.create_task(self._book_snapshot_loop(),
                                                  name="globex_book_snapshot_loop")

    async def stop(self) -> None:
        # Idempotent — repeated stop() calls are a no-op.
        if self._task is None:
            return
        self._stop.set()
        for task in (self._task, self._snapshot_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._task = None
        self._snapshot_task = None
        # Final flush so buffered rows are not lost on shutdown.
        try:
            await self._tick_writer.flush()
            await self._liquidity_writer.flush()
        except Exception:  # noqa: BLE001
            logger.exception("globex_ingester_shutdown_flush_failed")

    # ── Internals ──────────────────────────────────────────────────────────
    async def _run_with_reconnect(self) -> None:
        if self._settings.disable_live_ingestion:
            logger.info("globex_live_disabled")
            return
        try:
            import databento as db  # noqa: F401
        except ImportError:
            logger.warning("globex_databento_import_failed")
            return

        max_reconnects = max(
            1,
            int(
                getattr(
                    self._settings, "ingestion_max_reconnects", MAX_RECONNECTS
                )
            ),
        )
        backoff_cap = float(
            getattr(
                self._settings,
                "ingestion_reconnect_max_backoff_seconds",
                60.0,
            )
        )
        reset_seconds = float(
            getattr(
                self._settings, "ingestion_terminal_reset_seconds", 600.0
            )
        )

        # REV8 OPS-1: outer cold-restart loop (mirrors databento_live.py).
        # Routine outages should self-recover after a cooldown rather than
        # going permanently terminal.
        while not self._stop.is_set():
            backoff = INITIAL_BACKOFF_S
            attempt = 0
            terminal_reason: str | None = None
            while attempt < max_reconnects:
                if self._stop.is_set():
                    return
                attempt += 1
                self._connection_attempts = attempt

                # Resolve the candidate list at the start of every attempt
                # so newly-registered DB keys / rotated env vars are picked
                # up without a service restart.
                candidates = await self._resolve_candidates()
                if not candidates:
                    logger.warning("globex_live_skipped_no_api_key")
                    self._dead = True
                    logger.error(
                        "globex_live_terminated_no_keys",
                        hint=(
                            "manual intervention required: register a "
                            "Databento key"
                        ),
                    )
                    terminal_reason = "no_keys"
                    break

                connected = False
                for candidate in candidates:
                    if self._stop.is_set():
                        return
                    try:
                        logger.info(
                            "globex_live_connecting",
                            attempt=attempt,
                            schemas=self._schemas,
                            parents=self._parents,
                            key_label=candidate.label,
                            key_source=candidate.source,
                        )
                        # Bootstrap the registry on every candidate switch
                        # so the in-memory map matches the key we're about
                        # to stream against.
                        await self._bootstrap_registry(candidate.api_key)
                        self._active_key_label = candidate.label
                        await self._stream_once(candidate)
                        backoff = INITIAL_BACKOFF_S
                        connected = True
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        msg = str(exc)
                        self._last_error = msg[:500]
                        dropped = self._drop_unsupported_schema(msg)
                        if dropped:
                            self._dropped_schemas.append(dropped)
                            logger.warning(
                                "globex_live_dropping_schema",
                                dropped=dropped,
                                remaining=self._schemas,
                            )
                            if not self._schemas:
                                self._dead = True
                                logger.error(
                                    "globex_live_no_schemas_left",
                                    hint=(
                                        "register a key with appropriate "
                                        "Databento entitlements"
                                    ),
                                )
                                terminal_reason = "no_schemas"
                                break
                            # Don't burn an attempt for a config-time fix.
                            attempt -= 1
                            connected = True
                            break
                        logger.exception(
                            "globex_live_stream_failed",
                            error=msg,
                            key_label=candidate.label,
                        )
                        await self._record_candidate_error(candidate, msg)
                        # Try the next candidate before consuming a reconnect.
                        continue

                if terminal_reason is not None:
                    break
                if connected:
                    continue

                if attempt >= max_reconnects:
                    logger.warning(
                        "globex_live_reconnect_budget_exhausted",
                        attempts=attempt,
                        cold_restart_in_s=reset_seconds,
                    )
                    break
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, backoff_cap)

            if terminal_reason is not None:
                return
            if self._stop.is_set():
                return

            logger.warning(
                "globex_live_cold_restart_pending",
                sleep_s=reset_seconds,
            )
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=reset_seconds
                )
                return
            except TimeoutError:
                pass
            logger.warning("globex_live_cold_restart_resuming")
            self._dead = False
            self._connection_attempts = 0
            self._last_error = None

    async def _resolve_candidates(self) -> list[KeyCandidate]:
        """Resolve the prioritised candidate list (env first, then DB pool)."""
        try:
            factory = get_session_factory()
            async with factory() as session:
                return await iter_keys(session, DATASET)
        except Exception:  # noqa: BLE001 — degrade to env-only on DB failure
            logger.exception("globex_live_key_pool_resolve_failed")
            env_key = self._settings.globex_api_key
            if env_key:
                return [
                    KeyCandidate(
                        label=f"env:{DATASET}",
                        api_key=env_key,
                        source="env",
                    )
                ]
            return []

    async def _record_candidate_error(
        self, candidate: KeyCandidate, error_msg: str
    ) -> None:
        if candidate.source != "db":
            return
        try:
            factory = get_session_factory()
            async with factory() as session:
                await record_key_error(session, candidate, error_msg=error_msg)
        except Exception:  # noqa: BLE001
            logger.exception(
                "globex_live_record_key_error_failed",
                key_label=candidate.label,
            )

    async def _record_candidate_success(self, candidate: KeyCandidate) -> None:
        if candidate.source != "db":
            return
        try:
            factory = get_session_factory()
            async with factory() as session:
                await record_key_success(session, candidate)
        except Exception:  # noqa: BLE001
            logger.exception(
                "globex_live_record_key_success_failed",
                key_label=candidate.label,
            )

    def _drop_unsupported_schema(self, error_message: str) -> str | None:
        msg = error_message.lower()
        for schema in list(self._schemas):
            s = schema.lower()
            if (
                f"{s} schema not supported" in msg
                or f"not authorized for {s}" in msg
                or f"not authorized for {s} schema" in msg
                or f"unauthorized for {s}" in msg
            ):
                self._schemas.remove(schema)
                return schema
        # Fallback: the gateway changed its error format. Don't drop a
        # schema speculatively but log loudly so operators can update.
        if any(fragment in msg for fragment in _AUTH_ERROR_FRAGMENTS):
            logger.warning(
                "globex_schema_drop_unrecognized_error_format",
                schemas_active=list(self._schemas),
                error=error_message[:300],
            )
        return None

    async def _bootstrap_registry(self, api_key: str | None = None) -> None:
        """Pre-populate ``self._registry`` from the historical definition schema.

        Live ``mbp-10`` / ``trades`` records carry only ``instrument_id``,
        not the human-readable contract symbol. Without a registry every
        live record is dropped because the lookup in ``_handle_trade`` /
        ``_handle_mbp`` returns ``None``. Mirrors the OPRA ingester's
        bootstrap step.
        """
        try:
            import databento as db
        except ImportError:
            return

        key = api_key or self._settings.globex_api_key
        if not key:
            return

        client = db.Historical(key=key)
        end = datetime.now(UTC) - timedelta(minutes=30)
        start = end - timedelta(days=_BOOTSTRAP_DAYS)
        loaded = 0
        for parent in self._parents:
            try:
                data = await asyncio.to_thread(
                    client.timeseries.get_range,
                    dataset=DATASET,
                    schema="definition",
                    symbols=[parent],
                    stype_in="parent",
                    start=start,
                    end=end,
                )
                df = await asyncio.to_thread(data.to_df)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "globex_registry_bootstrap_failed",
                    parent=parent,
                    error=str(exc),
                )
                continue
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                instrument_id = row.get("instrument_id")
                raw_symbol = row.get("raw_symbol") or row.get("symbol")
                if instrument_id is None or raw_symbol is None:
                    continue
                try:
                    instrument_id = int(instrument_id)
                except (TypeError, ValueError):
                    continue
                expiration = row.get("expiration") or row.get("expiration_date")
                self._registry[instrument_id] = {
                    "symbol": str(raw_symbol).upper(),
                    "expiration": expiration,
                    "parent": _parent_from_raw(raw_symbol),
                }
                loaded += 1
        logger.info("globex_registry_bootstrapped", contracts=loaded)

    async def _stream_once(self, candidate: KeyCandidate) -> None:
        import databento as db

        # First time only: pre-populate the registry so live trades on
        # ``mbp-10`` / ``trades`` schemas can be mapped to a contract.
        if not self._registry:
            await self._bootstrap_registry(candidate.api_key)

        client = db.Live(key=candidate.api_key)
        for parent in self._parents:
            for schema in self._schemas:
                try:
                    client.subscribe(
                        dataset=DATASET,
                        schema=schema,
                        symbols=[parent],
                        stype_in="parent",
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "globex_subscribe_failed",
                        schema=schema,
                        parent=parent,
                    )

        first_record_seen = False
        async for record in client:
            if self._stop.is_set():
                break
            if not first_record_seen:
                first_record_seen = True
                await self._record_candidate_success(candidate)
            try:
                await self._handle_record(record)
            except Exception:  # noqa: BLE001
                logger.exception("globex_record_processing_error")

    async def _handle_record(self, record: Any) -> None:
        rtype = type(record).__name__
        self._record_counts[rtype] = self._record_counts.get(rtype, 0) + 1
        self._cumulative_record_counts[rtype] = (
            self._cumulative_record_counts.get(rtype, 0) + 1
        )
        if self._first_record_at is None:
            self._first_record_at = datetime.now(UTC)
        self._last_record_at = datetime.now(UTC)
        # Capture a snapshot of the first record of each type — invaluable for
        # diagnostics. Use an explicit allowlist rather than ``dir(record)`` so
        # ErrorMsg / SystemMsg frames don't leak auth tokens via diagnostics.
        if rtype not in self._sample_record_attrs:
            attrs: dict[str, Any] = {}
            for name in _DIAGNOSTICS_ATTR_ALLOWLIST:
                if not hasattr(record, name):
                    continue
                try:
                    val = getattr(record, name)
                except Exception:  # noqa: BLE001
                    continue
                if callable(val):
                    continue
                try:
                    if isinstance(val, str | int | float | bool | type(None)):
                        attrs[name] = val
                    elif isinstance(val, list | tuple):
                        attrs[name] = [str(x)[:200] for x in val[:3]]
                    else:
                        attrs[name] = str(val)[:200]
                except Exception:  # noqa: BLE001
                    attrs[name] = "<unserialisable>"
            self._sample_record_attrs[rtype] = attrs

        if "Definition" in rtype:
            self._handle_definition(record)
        elif "Trade" in rtype:
            await self._handle_trade(record)
        elif "MBP" in rtype or "Mbp" in rtype:
            self._handle_mbp(record)
        elif "Error" in rtype:
            msg = (
                getattr(record, "err", None)
                or getattr(record, "msg", None)
                or str(record)
            )
            text = str(msg)[:300]
            self._last_error = text
            self._error_messages.append(text)
            self._error_messages = self._error_messages[-10:]
            logger.warning("globex_gateway_error", msg=text)
        elif "System" in rtype:
            msg = getattr(record, "msg", None) or str(record)
            text = str(msg)[:300]
            self._system_messages.append(text)
            self._system_messages = self._system_messages[-10:]
            logger.info("globex_gateway_system", msg=text)
        # Other types ignored (symbol-mapping frames, etc.).

    def _handle_definition(self, record: Any) -> None:
        instrument_id = getattr(record, "instrument_id", None)
        raw_symbol = getattr(record, "raw_symbol", None) or getattr(
            record, "symbol", None
        )
        if instrument_id is None or raw_symbol is None:
            return
        try:
            instrument_id = int(instrument_id)
        except (TypeError, ValueError):
            return

        # Best-effort: parent symbol = the leading alpha portion of raw_symbol
        # (e.g. ``ESM5`` -> ``ES``). We keep the full raw_symbol as the
        # ``symbol`` we persist so downstream consumers can disambiguate
        # between contract months.
        expiration = (
            getattr(record, "expiration", None)
            or getattr(record, "expiration_date", None)
        )
        self._registry[instrument_id] = {
            "symbol": str(raw_symbol).upper(),
            "expiration": expiration,
            "parent": _parent_from_raw(raw_symbol),
        }

    async def _handle_trade(self, record: Any) -> None:
        instrument_id = getattr(record, "instrument_id", None)
        contract = self._registry.get(instrument_id) if instrument_id else None
        if contract is None:
            self._unmatched_total += 1
            if self._unmatched_total % 100 == 0:
                logger.warning(
                    "globex_trade_unmatched_rollup",
                    unmatched_total=self._unmatched_total,
                    registry_size=len(self._registry),
                )
            return

        price = _scale_price(getattr(record, "price", None))
        size = getattr(record, "size", None)
        if price is None or size is None:
            return

        try:
            size_int = int(size)
        except (TypeError, ValueError):
            return
        seq = getattr(record, "sequence", None) or getattr(record, "ts_event", 0)
        try:
            seq_int = int(seq)
        except (TypeError, ValueError):
            seq_int = 0

        ts = _record_ts(record)
        if ts is None:
            self._dropped_no_ts_count += 1
            logger.warning(
                "globex_trade_dropped_no_ts",
                instrument_id=instrument_id,
                dropped_total=self._dropped_no_ts_count,
            )
            return

        # Map MDP 3.0 ``aggressor_side`` (1 = buy, 2 = sell, 0 = none) to our
        # +1 / -1 / null convention.
        aggressor_raw = getattr(record, "aggressor_side", None)
        if aggressor_raw == 1 or str(aggressor_raw).upper() in {"B", "BUY"}:
            aggressor: int | None = 1
        elif aggressor_raw == 2 or str(aggressor_raw).upper() in {"S", "SELL"}:
            aggressor = -1
        else:
            aggressor = None

        book = self._book.get(instrument_id, {})
        await self._tick_writer.add({
            "ts": ts,
            "symbol": contract["symbol"],
            "seq": seq_int,
            "price": price,
            "size": size_int,
            "aggressor": aggressor,
            "bid": book.get("bid_top"),
            "ask": book.get("ask_top"),
            "venue": _coerce_str(getattr(record, "publisher_id", None)),
        })

        # ── Real-time tick fan-out ─────────────────────────────────────────
        # Publish a tiny frame to every public subscriber of the underlying
        # cash index. Synchronous, non-blocking, drop-oldest on overflow —
        # the hot path must not be slowed by the live-stream channel.
        parent = contract.get("parent") or _parent_from_raw(contract.get("symbol"))
        cash_symbol = (
            _FUTURES_ROOT_TO_CASH_SYMBOL.get(parent.upper())
            if isinstance(parent, str) and parent
            else None
        )
        if cash_symbol is not None:
            try:
                basis_value: float | None = get_basis(cash_symbol)
                cash_spot: float | None = (
                    float(price) + basis_value if basis_value is not None else None
                )
                tick_payload: dict[str, Any] = {
                    "symbol": cash_symbol,
                    "futures_symbol": contract["symbol"],
                    "futures_price": float(price),
                    "cash_spot": cash_spot,
                    "basis": basis_value,
                    "ts": ts.isoformat().replace("+00:00", "Z"),
                }
                get_tick_notifier().publish(cash_symbol, tick_payload)
            except Exception:  # noqa: BLE001 - never let stream fan-out break ingestion
                logger.exception(
                    "globex_tick_publish_failed",
                    cash_symbol=cash_symbol,
                    contract=contract.get("symbol"),
                )

    def _handle_mbp(self, record: Any) -> None:
        """Cache the top-N book levels for downstream snapshotting."""
        instrument_id = getattr(record, "instrument_id", None)
        if instrument_id is None:
            return
        contract = self._registry.get(instrument_id)
        if contract is None:
            return

        levels = getattr(record, "levels", None) or []
        bids: list[dict] = []
        asks: list[dict] = []
        for lvl in levels:
            bid_px = _scale_price(getattr(lvl, "bid_px", None))
            ask_px = _scale_price(getattr(lvl, "ask_px", None))
            bid_sz = getattr(lvl, "bid_sz", None)
            ask_sz = getattr(lvl, "ask_sz", None)
            bid_ct = getattr(lvl, "bid_ct", None)
            ask_ct = getattr(lvl, "ask_ct", None)
            if bid_px is not None:
                bids.append({"price": bid_px,
                             "size": int(bid_sz) if bid_sz is not None else None,
                             "orders": int(bid_ct) if bid_ct is not None else None})
            if ask_px is not None:
                asks.append({"price": ask_px,
                             "size": int(ask_sz) if ask_sz is not None else None,
                             "orders": int(ask_ct) if ask_ct is not None else None})

        self._book[instrument_id] = {
            "bid_top": bids[0]["price"] if bids else None,
            "ask_top": asks[0]["price"] if asks else None,
            "bids": bids,
            "asks": asks,
            "ts_event": _record_ts(record),
            "symbol": contract["symbol"],
            # Mark dirty so the 1Hz snapshot loop only writes a row when the
            # book actually changed since the last flush. Cleared in
            # ``_flush_book_snapshots`` once a row is enqueued.
            "dirty": True,
        }

    async def _book_snapshot_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(SNAPSHOT_INTERVAL_S)
            try:
                await self._flush_book_snapshots()
            except Exception:  # noqa: BLE001
                logger.exception("globex_snapshot_loop_failed")

    async def _flush_book_snapshots(self) -> None:
        if not self._book:
            return
        rows: list[dict] = []
        flushed_ids: list[int] = []
        now_ts = datetime.now(UTC)
        for instrument_id, state in self._book.items():
            # Skip contracts whose book has not changed since the last flush.
            # Without this gate the snapshot loop writes a row per contract
            # every SNAPSHOT_INTERVAL_S regardless of activity, which churns
            # liquidity_snapshots even on dead off-hours feeds.
            if not state.get("dirty"):
                continue
            ts = state.get("ts_event") or now_ts
            rows.append({
                "ts": ts,
                "symbol": state["symbol"],
                "bids": state.get("bids", []),
                "asks": state.get("asks", []),
                "depth_levels": max(
                    len(state.get("bids") or []),
                    len(state.get("asks") or []),
                ),
            })
            flushed_ids.append(instrument_id)
        if not rows:
            return
        for row in rows:
            await self._liquidity_writer.add(row)
        # Clear the dirty flag only after the rows are enqueued so a racing
        # ``_handle_mbp`` between iteration and clear can re-mark the entry
        # and we'll pick it up on the next tick.
        for instrument_id in flushed_ids:
            entry = self._book.get(instrument_id)
            if entry is not None:
                entry["dirty"] = False


def _parent_from_raw(raw_symbol: str | None) -> str | None:
    if not raw_symbol:
        return None
    out = []
    for ch in raw_symbol:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out).upper() or None


def _coerce_str(value: Any) -> str | None:
    """Coerce gateway-supplied identifiers (often int) to a Text-friendly str.

    asyncpg is strict about types; passing an int into a ``Text`` column
    raises ``InvalidTextRepresentationError`` and silently fails the whole
    batch.
    """
    if value is None:
        return None
    return str(value)


_globex_ingester: GlobexLiveIngester | None = None


def get_globex_live_ingester() -> GlobexLiveIngester:
    global _globex_ingester
    if _globex_ingester is None:
        _globex_ingester = GlobexLiveIngester()
    return _globex_ingester
