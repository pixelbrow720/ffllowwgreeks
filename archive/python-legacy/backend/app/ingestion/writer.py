"""Buffered writer for ``options_chain`` rows.

The writer accepts rows from the live OPRA ingester, batches them by
``ts/symbol/expiration/strike/option_type`` primary key, deduplicates
intra-batch collisions (so an ``ON CONFLICT DO UPDATE`` cannot violate
the per-statement single-row constraint), and upserts.

Rev 3 hardening:
* Batch size defaults to ``Settings.upsert_batch_size`` (was hard-coded).
* Maximum pending rows enforced via ``Settings.ingestion_max_pending_rows``
  — anything beyond is shed to the dead-letter queue with reason
  ``backpressure_overflow``.
* Flush failures route the offending batch through the DLQ instead of
  silently dropping it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import OptionsChain
from app.db.session import get_session_factory
from app.ingestion.dlq import record_dlq

logger = get_logger(__name__)


class OptionsChainWriter:
    """Batches rows and flushes them to TimescaleDB on a size or time trigger."""

    def __init__(
        self,
        *,
        batch_size: int | None = None,
        flush_interval_s: float = 2.0,
        max_pending_rows: int | None = None,
    ) -> None:
        settings = get_settings()
        self._buffer: list[dict[str, Any]] = []
        self._batch_size = batch_size or settings.upsert_batch_size
        self._flush_interval_s = flush_interval_s
        self._max_pending_rows = (
            max_pending_rows or settings.ingestion_max_pending_rows
        )
        self._lock = asyncio.Lock()
        # Separate flush lock — held for the duration of a flush operation
        # so concurrent ``add()`` calls can append to the buffer while a
        # flush is mid-roundtrip, without two flushes racing each other.
        self._flush_lock = asyncio.Lock()
        self._flushing: bool = False
        # Keep strong references to background flush tasks to prevent garbage collection
        self._background_tasks: set[asyncio.Task] = set()
        self._last_flush_ts = datetime.now(UTC)
        self._last_event_ts: datetime | None = None
        self._row_counts: dict[str, int] = {}
        self._shed_rows = 0

    @property
    def last_event_ts(self) -> datetime | None:
        return self._last_event_ts

    @property
    def row_counts(self) -> dict[str, int]:
        return dict(self._row_counts)

    @property
    def pending(self) -> int:
        return len(self._buffer)

    @property
    def shed_rows(self) -> int:
        """Total rows shed to DLQ due to backpressure since process start."""
        return self._shed_rows

    async def add(self, row: dict[str, Any]) -> None:
        async with self._lock:
            overflow = len(self._buffer) >= self._max_pending_rows
            if overflow:
                self._shed_rows += 1
                shed = True
            else:
                shed = False
                self._buffer.append(row)
                self._last_event_ts = row.get("ts") or datetime.now(UTC)
                symbol = row.get("symbol")
                if symbol:
                    self._row_counts[symbol] = self._row_counts.get(symbol, 0) + 1
            should_flush = len(self._buffer) >= self._batch_size
            should_kick_flush = overflow and not self._flushing
        if overflow and should_kick_flush:
            # Backpressure: kick a flush in the background BEFORE shedding so
            # the buffer drains and the next ``add()`` has a chance to land.
            # Guarded by ``_flushing`` to avoid stampede.
            task = asyncio.create_task(self.flush())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        if shed:
            await record_dlq(
                source="opra_live",
                reason="backpressure_overflow",
                payload={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()},
            )
            return
        if should_flush:
            # REV8 OPS-9: soft batch-size threshold flush runs in the
            # background so a slow DB roundtrip cannot stall the OPRA
            # ingester's record loop. The hard ``max_pending_rows`` cap
            # above remains synchronous for backpressure correctness.
            if not self._flushing:
                task = asyncio.create_task(self.flush())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

    async def add_many(self, rows: Iterable[dict[str, Any]]) -> None:
        # Single-lock buffer extend so a sweep-sized batch doesn't re-enter
        # ``add()`` (and ``self._lock``) per row. Per-row event-ts and
        # row_counts bookkeeping is preserved so downstream telemetry stays
        # consistent with the per-row ``add()`` path.
        new_rows = list(rows)
        if not new_rows:
            return
        shed_payloads: list[dict[str, Any]] = []
        kicked_flush = False
        async with self._lock:
            for row in new_rows:
                if len(self._buffer) >= self._max_pending_rows:
                    self._shed_rows += 1
                    shed_payloads.append(row)
                    continue
                self._buffer.append(row)
                self._last_event_ts = row.get("ts") or datetime.now(UTC)
                symbol = row.get("symbol")
                if symbol:
                    self._row_counts[symbol] = self._row_counts.get(symbol, 0) + 1
            should_flush = len(self._buffer) >= self._batch_size
            if shed_payloads and not self._flushing:
                kicked_flush = True
        if kicked_flush:
            task = asyncio.create_task(self.flush())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        for row in shed_payloads:
            await record_dlq(
                source="opra_live",
                reason="backpressure_overflow",
                payload={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()},
            )
        if should_flush:
            # REV8 OPS-9: soft-threshold flush goes background — see ``add``.
            if not self._flushing:
                task = asyncio.create_task(self.flush())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

    async def flush(self) -> int:
        # Hold the flush_lock for the entire SQL roundtrip so two concurrent
        # callers can't issue overlapping upsert batches against the same
        # rows. The buffer-swap is still done under ``_lock`` so ``add()``
        # remains non-blocking during the SQL roundtrip.
        async with self._flush_lock:
            self._flushing = True
            try:
                async with self._lock:
                    if not self._buffer:
                        return 0
                    batch = self._buffer
                    self._buffer = []
                    self._last_flush_ts = datetime.now(UTC)

                # Deduplicate by primary-key tuple. Live trades on the same contract
                # frequently share a microsecond timestamp, which would cause Postgres
                # to raise ``ON CONFLICT DO UPDATE command cannot affect row a second
                # time`` (CardinalityViolationError). Last write wins per key.
                deduped: dict[tuple, dict[str, Any]] = {}
                for row in batch:
                    key = (
                        row.get("ts"),
                        row.get("symbol"),
                        row.get("expiration"),
                        row.get("strike"),
                        row.get("option_type"),
                    )
                    existing = deduped.get(key)
                    if existing is None:
                        deduped[key] = row
                        continue
                    # Merge: prefer non-null values from the newer row.
                    for k, v in row.items():
                        if v is not None:
                            existing[k] = v
                batch = list(deduped.values())

                try:
                    factory = get_session_factory()
                    async with factory() as session:
                        stmt = insert(OptionsChain).values(batch)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["ts", "symbol", "expiration", "strike", "option_type"],
                            set_={
                                "oi": stmt.excluded.oi,
                                "volume": stmt.excluded.volume,
                                "iv": stmt.excluded.iv,
                                "delta": stmt.excluded.delta,
                                "gamma": stmt.excluded.gamma,
                                "last_price": stmt.excluded.last_price,
                                "bid": stmt.excluded.bid,
                                "ask": stmt.excluded.ask,
                                "underlying_price": stmt.excluded.underlying_price,
                            },
                        )
                        try:
                            await session.execute(stmt)
                            await session.commit()
                        except Exception:
                            await session.rollback()
                            raise
                except Exception as exc:  # noqa: BLE001
                    logger.exception("options_chain_write_failed", rows=len(batch))
                    serializable_batch = [
                        {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
                        for row in batch
                    ]
                    await record_dlq(
                        source="opra_live",
                        reason=f"flush_failed: {type(exc).__name__}",
                        payload={"row_count": len(batch), "error": str(exc)[:500], "batch": serializable_batch},
                    )
                    return 0

                logger.info("options_chain_flushed", rows=len(batch))
                async with self._lock:
                    self._row_counts.clear()
                return len(batch)
            finally:
                self._flushing = False

    async def periodic_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001
                logger.exception("periodic_flush_error")


_writer: OptionsChainWriter | None = None


def get_writer() -> OptionsChainWriter:
    global _writer
    if _writer is None:
        _writer = OptionsChainWriter()
    return _writer
