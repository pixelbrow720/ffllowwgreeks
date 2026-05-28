"""Dead-letter queue (DLQ) writer for ingestion failures.

Any ingester that drops a record because it cannot be parsed, has invalid
fields, or repeatedly fails to write should call :func:`record_dlq` so
operators can inspect the offending payload via `/admin/inspector/dlq`.

The DLQ writer is intentionally **best-effort**: a failure to persist to
the DLQ table is logged but never re-raised, so DLQ failures cannot
themselves crash the ingester. We also cap the in-memory queue depth
at :attr:`Settings.ingestion_dlq_max_size` so a runaway feed can't OOM
the process before the periodic flush lands.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert

from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import DeadLetterEntry
from app.db.session import get_session_factory

logger = get_logger(__name__)


class DeadLetterQueue:
    """In-memory ring buffer that periodically drains to ``dead_letter_queue``.

    Designed to be a process-wide singleton — see :func:`get_dlq`.
    """

    def __init__(self, *, max_size: int | None = None) -> None:
        settings = get_settings()
        capacity = max_size or settings.ingestion_dlq_max_size
        self._max_capacity = capacity
        self._buffer: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = asyncio.Lock()
        self._flush_interval_s = 5.0
        # Counter incremented every time an append evicts the oldest entry
        # because the buffer was already at ``maxlen``. Surfaced via
        # :meth:`evicted_count` so operators can spot DLQ saturation.
        self._evicted_count: int = 0
        # Holding slot for a batch whose DB-flush failed. Re-prepending into
        # the deque under load loses entries to ``maxlen``; instead we keep
        # the failed batch here and replay on the next flush tick.
        self._pending_retry: list[dict[str, Any]] = []

    @property
    def pending(self) -> int:
        return len(self._buffer) + len(self._pending_retry)

    def evicted_count(self) -> int:
        """Total entries dropped because the in-memory buffer was full."""
        return self._evicted_count

    async def add(
        self, *, source: str, reason: str, payload: dict | None = None
    ) -> None:
        async with self._lock:
            # Detect whether this append will evict the oldest entry — the
            # deque silently drops it under ``maxlen`` so we have to check
            # before mutating to keep the metric accurate.
            if (
                self._buffer.maxlen is not None
                and len(self._buffer) == self._buffer.maxlen
            ):
                self._evicted_count += 1
            self._buffer.append(
                {
                    "ts": datetime.now(UTC),
                    "source": source,
                    "reason": reason,
                    "payload": payload,
                }
            )
        logger.warning(
            "dlq_recorded",
            source=source,
            reason=reason,
            pending=self.pending,
            evicted_total=self._evicted_count,
        )

    async def flush(self) -> int:
        async with self._lock:
            # Drain pending_retry first so the oldest failed batch goes out
            # ahead of newer entries.
            batch: list[dict[str, Any]] = list(self._pending_retry)
            self._pending_retry = []
            if self._buffer:
                batch.extend(self._buffer)
                self._buffer.clear()
            if not batch:
                return 0

        try:
            factory = get_session_factory()
            async with factory() as session:
                stmt = insert(DeadLetterEntry).values(batch)
                await session.execute(stmt)
                await session.commit()
        except Exception:  # noqa: BLE001 — DLQ failure must never propagate
            logger.exception("dlq_flush_failed", rows=len(batch))
            # Park the failed batch in the retry slot rather than re-prepending
            # into the bounded deque (which would lose entries to ``maxlen``
            # under sustained backpressure). The next flush tick replays it.
            async with self._lock:
                self._pending_retry = batch + self._pending_retry
                # Enforce OOM protection on retry buffer during sustained outages
                if len(self._pending_retry) > self._max_capacity:
                    excess = len(self._pending_retry) - self._max_capacity
                    self._pending_retry = self._pending_retry[excess:]
                    self._evicted_count += excess
            return 0
        return len(batch)

    async def periodic_flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval_s)
            try:
                await self.flush()
            except Exception:  # noqa: BLE001
                logger.exception("dlq_periodic_flush_error")


_dlq: DeadLetterQueue | None = None


def get_dlq() -> DeadLetterQueue:
    global _dlq
    if _dlq is None:
        _dlq = DeadLetterQueue()
    return _dlq


async def record_dlq(
    *, source: str, reason: str, payload: dict | None = None
) -> None:
    """Convenience wrapper used by ingesters / pipelines.

    Falls back to a log statement if a DLQ instance is somehow not yet
    constructed (defensive — should never happen in practice).
    """
    await get_dlq().add(source=source, reason=reason, payload=payload)


# REV8 OPS-10 — DLQ retention.
# The lifespan-side scheduling is left to Lane A; this module just
# exposes the cleanup primitive so operators / a future scheduler tick
# can call it. Default retention is operator-configurable via
# ``Settings.ingestion_dlq_retention_days`` (REV8 default: 14 days).
async def cleanup_dlq_older_than(retention_days: int) -> int:
    """Delete ``dead_letter_queue`` rows older than ``retention_days``.

    Returns the number of rows deleted (best-effort — exceptions are
    swallowed and logged so a transient DB failure can never propagate
    into ingestion). ``retention_days`` must be a positive integer; a
    zero or negative value is treated as a no-op so a misconfigured env
    cannot accidentally truncate the entire table.

    The complementary in-memory ring buffer is bounded separately by
    ``Settings.ingestion_dlq_max_size`` and does not need cleanup.
    """
    if retention_days <= 0:
        logger.info(
            "dlq_cleanup_skipped_non_positive_retention",
            retention_days=retention_days,
        )
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    try:
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                delete(DeadLetterEntry).where(DeadLetterEntry.ts < cutoff)
            )
            await session.commit()
        deleted = int(result.rowcount or 0)
    except Exception:  # noqa: BLE001
        logger.exception("dlq_cleanup_failed", retention_days=retention_days)
        return 0
    if deleted:
        logger.info(
            "dlq_cleanup_complete",
            deleted=deleted,
            cutoff=cutoff.isoformat(),
            retention_days=retention_days,
        )
    return deleted


# Type alias for callers that prefer dependency injection.
DlqRecorder = Callable[..., Awaitable[None]]
