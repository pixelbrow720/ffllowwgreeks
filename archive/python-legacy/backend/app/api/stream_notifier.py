"""In-process pub/sub used by the streaming API (Agent 5).

The processing pipeline calls :func:`publish` at the end of every successful
chain-pipeline tick. WebSocket and SSE subscribers receive each published
payload via their own ``asyncio.Queue`` so a slow subscriber never blocks the
pipeline or other subscribers.

This module is deliberately dependency-free (only ``asyncio``) and contains no
HTTP / FastAPI logic — those concerns live in ``endpoints/stream.py``.

Backpressure policy: each subscriber queue is bounded. When a subscriber falls
behind we **drop the oldest** queued payload to make room for the newest — for
real-time market data, freshness beats completeness.

Publish-once-shared (Rev 9 PF-4): the publisher is responsible for
JSON-serialising the WS frame *once* per tick and handing the string to
:meth:`StreamNotifier.publish`. The notifier fans the *same string* out to
every subscriber queue, so 100 subscribers no longer mean 100 redundant
``json.dumps`` calls per frame.

Numeric serialisation contract (Rev 12 BC-16). The publisher uses
``orjson.dumps(..., default=str)`` (see ``pipeline._publish_streaming_snapshot``).
``orjson`` 3.x serialises ``float('nan')``, ``float('inf')`` and
``float('-inf')`` as JSON ``null`` by default, matching FastAPI's
``jsonable_encoder`` on the REST surface. The contract is therefore:
**NaN, +Infinity, -Infinity render as ``null`` in BOTH REST and WS
transports.** Calculators that have a meaningful "not computed" sentinel
should prefer ``None`` over NaN at the source so the contract intent is
explicit. See ``API_POLICY.md`` § 7 for the full statement and the
consumer-side guidance.

Sequence numbers and ring buffer (Rev 13 FE-1). Every published frame is
tagged with a monotonic per-symbol ``seq`` number, and the publisher's
serialised string is appended to a small per-symbol ring buffer
(``_FRAME_BUFFER``, default ``maxlen=60``). On WS reconnect a client may
ask the server to replay frames since a known ``seq`` via the
``?since_seq=`` query param. The ring buffer is only populated when
there is at least one subscriber for the symbol — replay only matters
to clients that were already connected, so we save the memory in the
zero-subscriber case.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


# Per-subscriber queue depth. Sized to absorb a small burst (~30 s of 1 Hz
# updates) while still applying backpressure on truly stuck consumers.
DEFAULT_QUEUE_MAXSIZE: int = 32

# Rev 13 FE-1: per-symbol replay ring buffer depth. Sized to ~60 frames
# which at the pipeline's 1 Hz tick is one minute of history — long
# enough to cover transient WS drops on a flaky cellular link, short
# enough that the memory footprint is negligible (~60 × 150KB on SPX).
DEFAULT_FRAME_BUFFER_MAXLEN: int = 60


class StreamNotifier:
    """In-process fan-out broker keyed by ``symbol``.

    Methods are coroutine-safe under a single event loop; instances are *not*
    safe to share across loops. The module-level singleton returned by
    :func:`get_stream_notifier` is bound to whichever loop owns it at first
    use.

    The notifier is deliberately payload-agnostic: it fans out whatever the
    publisher hands it. Production callers pass an already-serialised JSON
    string (Rev 9 PF-4); tests may pass dicts. Either way every subscriber
    queue receives the same reference, so 100 subscribers do not multiply
    serialisation cost.
    """

    def __init__(
        self,
        queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
        frame_buffer_maxlen: int = DEFAULT_FRAME_BUFFER_MAXLEN,
    ) -> None:
        self._queue_maxsize = queue_maxsize
        self._frame_buffer_maxlen = frame_buffer_maxlen
        self._subscribers: dict[str, set[asyncio.Queue[Any]]] = defaultdict(set)
        self._lock = asyncio.Lock()
        # Rev 13 FE-1: per-symbol monotonic sequence counter and ring
        # buffer of the last ``frame_buffer_maxlen`` published frames.
        # Each entry is ``(seq, frame)`` where ``frame`` is whatever the
        # publisher handed to :meth:`publish` (usually a JSON string).
        # Buffer access is guarded by ``_buffer_lock`` so concurrent
        # publishers + replay readers cannot race.
        self._frame_seq_per_symbol: dict[str, int] = defaultdict(int)
        self._frame_buffer: dict[str, deque[tuple[int, Any]]] = {}
        self._buffer_lock = asyncio.Lock()

    def subscribe(self, symbol: str) -> asyncio.Queue[Any]:
        """Register a new subscriber for ``symbol`` and return its queue.

        The returned queue is bounded; callers must consume promptly or
        accept ``publish`` dropping the oldest frame on overflow.
        """
        sym = symbol.upper()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._queue_maxsize)
        self._subscribers[sym].add(queue)
        logger.debug("stream_subscribe", symbol=sym, total=len(self._subscribers[sym]))
        return queue

    def unsubscribe(self, symbol: str, queue: asyncio.Queue[Any]) -> None:
        """Detach a subscriber. Safe to call multiple times for the same queue."""
        sym = symbol.upper()
        bucket = self._subscribers.get(sym)
        if bucket is None:
            return
        bucket.discard(queue)
        if not bucket:
            self._subscribers.pop(sym, None)
        logger.debug("stream_unsubscribe", symbol=sym, remaining=len(bucket))

    def subscriber_count(self, symbol: str | None = None) -> int:
        """Return the number of subscribers for ``symbol`` (or total)."""
        if symbol is None:
            return sum(len(s) for s in self._subscribers.values())
        return len(self._subscribers.get(symbol.upper(), set()))

    async def publish(
        self, symbol: str, frame: Any, *, seq: int | None = None
    ) -> int:
        """Broadcast ``frame`` to every subscriber of ``symbol``.

        ``frame`` is opaque: production passes an already-serialised JSON
        string (single ``orjson.dumps`` call per tick — Rev 9 PF-4); tests
        may pass dicts. Every subscriber queue receives the same reference,
        so N subscribers do not multiply allocation.

        Returns the number of subscribers that successfully received the
        payload. Slow subscribers have their oldest queued frame discarded so
        the freshest one can land — we never block the publisher.

        Rev 13 FE-1: every published frame is appended to a per-symbol
        ring buffer for resume-token replay on reconnect. The buffer
        only retains frames when there is at least one subscriber at
        publish time — frames nobody can ever ask to replay are not
        worth the memory.

        ``seq`` may be passed when the publisher already reserved a seq
        via :meth:`next_seq` and embedded it into the wire frame. In
        that case we do NOT re-advance the counter; we just file the
        (seq, frame) pair so a later replay sees the same seq the
        consumer saw on the wire. When ``seq`` is None (the test path
        where dicts are published without pre-stamping) we allocate one
        on the fly.
        """
        sym = symbol.upper()
        bucket = self._subscribers.get(sym)
        if not bucket:
            # No subscribers means there is nothing to replay to either —
            # skip the buffer append + counter advance. This keeps the
            # buffer cold for inactive symbols.
            return 0

        # Append to the replay ring buffer first so a connecting client
        # whose ``since_seq`` lookup is racing this publish either sees
        # the frame in the buffer or gets it through the live queue.
        # Either way it lands exactly once.
        async with self._buffer_lock:
            if seq is None:
                seq = self._frame_seq_per_symbol[sym] + 1
                self._frame_seq_per_symbol[sym] = seq
            else:
                # Caller pre-reserved via :meth:`next_seq`; just record
                # it as the latest seen (defensive — the counter is
                # already at this value, but ``max`` keeps us correct
                # if a stale call arrives out of order).
                self._frame_seq_per_symbol[sym] = max(
                    self._frame_seq_per_symbol.get(sym, 0), seq
                )
            buf = self._frame_buffer.get(sym)
            if buf is None:
                buf = deque(maxlen=self._frame_buffer_maxlen)
                self._frame_buffer[sym] = buf
            buf.append((seq, frame))

        delivered = 0
        # Snapshot the set so concurrent unsubscribes during iteration are safe.
        for queue in list(bucket):
            try:
                queue.put_nowait(frame)
                delivered += 1
            except asyncio.QueueFull:
                # Drop oldest, then enqueue the latest. Best-effort: another
                # consumer may have drained between the get and the put.
                try:
                    _ = queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(frame)
                    delivered += 1
                except asyncio.QueueFull:
                    logger.warning(
                        "stream_publish_drop",
                        symbol=sym,
                        queue_maxsize=self._queue_maxsize,
                    )
        return delivered

    async def next_seq(self, symbol: str) -> int:
        """Reserve and return the next per-symbol monotonic seq.

        Pair with ``publish(symbol, frame, seq=seq)`` so the seq embedded
        in the JSON envelope matches the seq under which the frame is
        filed in the replay ring buffer.
        """
        sym = symbol.upper()
        async with self._buffer_lock:
            seq = self._frame_seq_per_symbol[sym] + 1
            self._frame_seq_per_symbol[sym] = seq
            return seq

    async def replay_since(
        self, symbol: str, since_seq: int
    ) -> tuple[list[Any], int]:
        """Return frames buffered with seq > ``since_seq`` for ``symbol``.

        Returns ``(frames, latest_seq)`` where ``frames`` are in
        publish-order (oldest first) and ``latest_seq`` is the most
        recently published seq (independent of whether the caller's
        ``since_seq`` was in-range). Callers can use ``latest_seq`` to
        resume their local sequence pointer.

        Behaviour notes:

        * If ``since_seq >= latest_seq`` the caller is already up-to-date
          and ``frames`` is empty.
        * If ``since_seq < (latest_seq - len(buffer))`` the caller has
          fallen further behind than the buffer can replay — they get
          the entire buffer (which is what the WS handler will then
          interpret as "best we can do; pretend you just connected").
        * If the buffer is empty (no frames have ever been published
          for the symbol) returns ``([], 0)``.
        """
        sym = symbol.upper()
        async with self._buffer_lock:
            latest_seq = self._frame_seq_per_symbol.get(sym, 0)
            buf = self._frame_buffer.get(sym)
            if buf is None or not buf:
                return ([], latest_seq)
            frames = [frame for seq, frame in buf if seq > since_seq]
            return (frames, latest_seq)

    def latest_seq(self, symbol: str) -> int:
        """Return the most recent seq for ``symbol`` (0 if none)."""
        return self._frame_seq_per_symbol.get(symbol.upper(), 0)

    @property
    def frame_buffer_maxlen(self) -> int:
        """Public read-only view of the per-symbol replay buffer depth."""
        return self._frame_buffer_maxlen

    def reset_replay_state_for_tests(self) -> None:
        """Clear seq counters + ring buffer (test-only)."""
        self._frame_seq_per_symbol.clear()
        self._frame_buffer.clear()


_singleton: StreamNotifier | None = None


def get_stream_notifier() -> StreamNotifier:
    """Return the process-wide :class:`StreamNotifier` (lazy)."""
    global _singleton
    if _singleton is None:
        _singleton = StreamNotifier()
    return _singleton


def reset_stream_notifier_for_tests() -> None:
    """Drop the cached singleton so tests can start with a clean broker."""
    global _singleton
    _singleton = None
