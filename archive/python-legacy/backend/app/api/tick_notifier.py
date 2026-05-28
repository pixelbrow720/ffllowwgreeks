"""In-process pub/sub for sub-second futures price ticks.

Mirrors the structure of :mod:`app.api.stream_notifier` but is purpose-built
for a high-frequency channel: every futures trade tick (potentially many per
second per contract) is published to a tiny per-symbol fan-out so the public
dashboard can render live spot/futures prices without waiting for the next
30-second pipeline snapshot.

Key differences vs. ``StreamNotifier``:

* :meth:`TickNotifier.publish` is **synchronous and non-blocking**. The
  ingester's ``_handle_trade`` hot path must not be slowed by awaitable I/O
  on every print, so we do a straight ``put_nowait`` and drop the oldest
  frame on overflow.
* Per-subscriber queue depth is much larger (``500`` vs. ``32``) — a
  short network stall on a subscriber would otherwise discard hundreds of
  ticks in a single bursty second.

Backpressure policy is identical to the snapshot stream: drop oldest, keep
newest. For a live tape, freshness beats completeness.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


# Per-subscriber queue depth. Sized for the bursty trade tape: ES alone can
# print several hundred ticks in a single active second, and a brief network
# stall on the subscriber side should not nuke the entire window.
DEFAULT_QUEUE_MAXSIZE: int = 500


class TickNotifier:
    """In-process fan-out broker for real-time price ticks, keyed by cash symbol.

    Methods are coroutine-safe under a single event loop; instances are not
    safe to share across loops. The module-level singleton returned by
    :func:`get_tick_notifier` is bound to whichever loop owns it at first
    use.
    """

    def __init__(self, queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE) -> None:
        self._queue_maxsize = queue_maxsize
        self._subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, symbol: str) -> asyncio.Queue[dict[str, Any]]:
        """Register a new subscriber for ``symbol`` and return its queue.

        The returned queue is bounded; callers must consume promptly or
        accept :meth:`publish` dropping the oldest frame on overflow.
        """
        sym = symbol.upper()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers[sym].add(queue)
        logger.debug("tick_subscribe", symbol=sym, total=len(self._subscribers[sym]))
        return queue

    def unsubscribe(self, symbol: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """Detach a subscriber. Safe to call multiple times for the same queue."""
        sym = symbol.upper()
        bucket = self._subscribers.get(sym)
        if bucket is None:
            return
        bucket.discard(queue)
        if not bucket:
            self._subscribers.pop(sym, None)
        logger.debug("tick_unsubscribe", symbol=sym, remaining=len(bucket) if bucket else 0)

    def subscriber_count(self, symbol: str | None = None) -> int:
        """Return the number of subscribers for ``symbol`` (or total)."""
        if symbol is None:
            return sum(len(s) for s in self._subscribers.values())
        return len(self._subscribers.get(symbol.upper(), set()))

    def publish(self, symbol: str, payload: dict[str, Any]) -> int:
        """Broadcast ``payload`` to every subscriber of ``symbol``.

        **Synchronous and non-blocking.** Designed to be called directly
        from the futures ingester's hot path (``_handle_trade``) without
        ``await``. Slow subscribers have their oldest queued frame
        discarded so the freshest one can land — we never block the
        publisher.

        Returns the number of subscribers that successfully received the
        payload.
        """
        sym = symbol.upper()
        bucket = self._subscribers.get(sym)
        if not bucket:
            return 0

        delivered = 0
        # Snapshot the set so concurrent unsubscribes during iteration are safe.
        for queue in list(bucket):
            try:
                queue.put_nowait(payload)
                delivered += 1
            except asyncio.QueueFull:
                # Drop oldest, then enqueue the latest. Best-effort: another
                # consumer may have drained between the get and the put.
                try:
                    _ = queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(payload)
                    delivered += 1
                except asyncio.QueueFull:
                    logger.warning(
                        "tick_publish_drop",
                        symbol=sym,
                        queue_maxsize=self._queue_maxsize,
                    )
        return delivered


_singleton: TickNotifier | None = None


def get_tick_notifier() -> TickNotifier:
    """Return the process-wide :class:`TickNotifier` (lazy)."""
    global _singleton
    if _singleton is None:
        _singleton = TickNotifier()
    return _singleton


def reset_tick_notifier_for_tests() -> None:
    """Drop the cached singleton so tests can start with a clean broker."""
    global _singleton
    _singleton = None
