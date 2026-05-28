"""Generic batched writers for Phase-2 tables.

Mirrors :class:`app.ingestion.writer.OptionsChainWriter` but
parameterised by ORM model + conflict-key set so we can reuse the same
backpressure / batching machinery for ``futures_ticks``,
``options_trades``, ``flow_events``, and ``liquidity_snapshots``.

Each writer is created lazily on first use and shared as a process-wide
singleton.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import (
    FlowEvent,
    FuturesTick,
    LiquiditySnapshot,
    OptionsTrade,
)
from app.db.session import get_session_factory
from app.ingestion.dlq import record_dlq

logger = get_logger(__name__)


class BulkUpsertWriter:
    """Batched insert / upsert writer parameterised by ORM model.

    The ``conflict_keys`` set must match the primary key (or a unique
    index) on the table. Set to ``None`` to disable conflict handling
    (plain ``INSERT … ON CONFLICT DO NOTHING`` is used in that case).
    """

    def __init__(
        self,
        model: Any,
        *,
        conflict_keys: Sequence[str] | None,
        batch_size: int | None = None,
        flush_interval_s: float = 2.0,
        on_conflict: str = "update",
        max_pending_rows: int | None = None,
        dlq_source: str = "ingestion",
    ) -> None:
        settings = get_settings()
        self.model = model
        self.conflict_keys = list(conflict_keys) if conflict_keys else None
        self._batch_size = batch_size or settings.upsert_batch_size
        self._flush_interval_s = flush_interval_s
        self._on_conflict = on_conflict
        self._max_pending_rows = (
            max_pending_rows or settings.ingestion_max_pending_rows
        )
        self._dlq_source = dlq_source
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        # Separate flush lock — held for the entire SQL roundtrip so two
        # concurrent flushes can't issue overlapping upserts.
        self._flush_lock = asyncio.Lock()
        self._flushing: bool = False
        # Keep strong references to background flush tasks to prevent garbage collection
        self._background_tasks: set[asyncio.Task] = set()
        self._last_flush_ts: datetime = datetime.now(UTC)
        self._shed_rows = 0

    @property
    def pending(self) -> int:
        return len(self._buffer)

    @property
    def shed_rows(self) -> int:
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
            should_flush = len(self._buffer) >= self._batch_size
            should_kick_flush = overflow and not self._flushing
        if overflow and should_kick_flush:
            # Backpressure: kick a flush before shedding so the buffer
            # drains and subsequent ``add()`` calls have a chance to land.
            task = asyncio.create_task(self.flush())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        if shed:
            await record_dlq(
                source=self._dlq_source,
                reason="backpressure_overflow",
                payload={k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()},
            )
            return
        if should_flush:
            # REV8 OPS-9: soft batch-size threshold flush runs in the
            # background so a slow DB roundtrip cannot stall the live
            # ingester's record loop. The hard ``max_pending_rows`` cap
            # above is the synchronous backpressure point. We dedupe the
            # flush task so a hot stream cannot queue 100 of them.
            if not self._flushing:
                task = asyncio.create_task(self.flush())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

    async def add_many(self, rows: Iterable[dict[str, Any]]) -> None:
        # Materialise the iterable so we can do a single buffer-extend under
        # the lock — re-using ``add()`` per row would acquire ``_lock`` 10k
        # times on a sweep-sized batch.
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
                else:
                    self._buffer.append(row)
            should_flush = len(self._buffer) >= self._batch_size
            if shed_payloads and not self._flushing:
                kicked_flush = True
        if kicked_flush:
            # Backpressure: drain in the background so subsequent ``add``
            # calls have a chance to land before more rows are shed.
            task = asyncio.create_task(self.flush())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
        for row in shed_payloads:
            await record_dlq(
                source=self._dlq_source,
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
        async with self._flush_lock:
            self._flushing = True
            try:
                async with self._lock:
                    if not self._buffer:
                        return 0
                    batch = self._buffer
                    self._buffer = []
                    self._last_flush_ts = datetime.now(UTC)

                if self.conflict_keys:
                    batch = self._dedupe_by(batch, self.conflict_keys)

                factory = get_session_factory()
                try:
                    async with factory() as session:
                        try:
                            await self._do_insert(session, batch)
                        except Exception:
                            await session.rollback()
                            raise
                    return len(batch)
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "bulk_writer_flush_failed",
                        table=self.model.__tablename__,
                        rows=len(batch),
                    )
                    serializable_batch = [
                        {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}
                        for row in batch
                    ]
                    await record_dlq(
                        source=self._dlq_source,
                        reason=f"flush_failed:{self.model.__tablename__}:{type(exc).__name__}",
                        payload={"row_count": len(batch), "error": str(exc)[:500], "batch": serializable_batch},
                    )
                    return 0
            finally:
                self._flushing = False

    async def _do_insert(self, session: AsyncSession, batch: list[dict]) -> None:
        if not batch:
            return
        stmt = insert(self.model).values(batch)
        if self.conflict_keys:
            update_cols = {
                c.name: stmt.excluded[c.name]
                for c in self.model.__table__.columns
                if c.name not in self.conflict_keys
            }
            if self._on_conflict == "update" and update_cols:
                stmt = stmt.on_conflict_do_update(
                    index_elements=self.conflict_keys, set_=update_cols
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=self.conflict_keys)
        await session.execute(stmt)
        await session.commit()

    @staticmethod
    def _dedupe_by(batch: list[dict], keys: Sequence[str]) -> list[dict]:
        deduped: dict[tuple, dict] = {}
        for row in batch:
            k = tuple(row.get(field) for field in keys)
            existing = deduped.get(k)
            if existing is None:
                deduped[k] = row
                continue
            for field, value in row.items():
                if value is not None:
                    existing[field] = value
        return list(deduped.values())

    async def periodic_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001
                logger.exception(
                    "bulk_writer_periodic_flush_error",
                    table=self.model.__tablename__,
                )


# ── Module-level singletons (lazy) ─────────────────────────────────────────

_futures_writer: BulkUpsertWriter | None = None
_options_trade_writer: BulkUpsertWriter | None = None
_flow_event_writer: BulkUpsertWriter | None = None
_liquidity_writer: BulkUpsertWriter | None = None


def get_futures_tick_writer() -> BulkUpsertWriter:
    global _futures_writer
    if _futures_writer is None:
        _futures_writer = BulkUpsertWriter(
            FuturesTick,
            conflict_keys=("ts", "symbol", "seq"),
            dlq_source="globex_live",
        )
    return _futures_writer


def get_options_trade_writer() -> BulkUpsertWriter:
    global _options_trade_writer
    if _options_trade_writer is None:
        _options_trade_writer = BulkUpsertWriter(
            OptionsTrade,
            conflict_keys=("ts", "symbol", "expiration", "strike", "option_type", "seq"),
            dlq_source="opra_live",
        )
    return _options_trade_writer


def get_flow_event_writer() -> BulkUpsertWriter:
    global _flow_event_writer
    if _flow_event_writer is None:
        _flow_event_writer = BulkUpsertWriter(
            FlowEvent,
            conflict_keys=None,  # event rows are append-only
            dlq_source="pipeline",
        )
    return _flow_event_writer


def get_liquidity_snapshot_writer() -> BulkUpsertWriter:
    global _liquidity_writer
    if _liquidity_writer is None:
        _liquidity_writer = BulkUpsertWriter(
            LiquiditySnapshot,
            conflict_keys=("ts", "symbol"),
            dlq_source="globex_live",
        )
    return _liquidity_writer
