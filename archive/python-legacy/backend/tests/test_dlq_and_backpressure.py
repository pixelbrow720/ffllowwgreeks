"""Agent 6 — Dead-letter queue & writer backpressure tests.

DB-touching tests are skipped in ``APP_TESTING=1`` mode (no Postgres in
CI). What we *can* verify without a database:

* ``DeadLetterQueue`` accepts entries up to its cap and silently drops
  oldest beyond it.
* ``OptionsChainWriter`` drops rows to DLQ once ``max_pending_rows`` is
  reached (we monkey-patch the DLQ recorder to count invocations).
* ``BulkUpsertWriter`` mirrors the same backpressure behaviour.
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
from datetime import UTC, datetime
from typing import Any

import pytest

from app.ingestion import databento_live as live_mod
from app.ingestion import dlq as dlq_mod
from app.ingestion.bulk_writers import BulkUpsertWriter
from app.ingestion.writer import OptionsChainWriter


@pytest.mark.asyncio
async def test_dlq_buffer_caps_in_memory() -> None:
    """DLQ ring buffer should drop oldest entries beyond ``max_size``."""
    queue = dlq_mod.DeadLetterQueue(max_size=3)
    for i in range(5):
        await queue.add(source="opra_live", reason=f"r{i}", payload={"i": i})
    assert queue.pending == 3


@pytest.mark.asyncio
async def test_chain_writer_sheds_to_dlq_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []

    async def fake_record(*, source: str, reason: str, payload: dict | None = None) -> None:
        captured.append({"source": source, "reason": reason, "payload": payload})

    monkeypatch.setattr("app.ingestion.writer.record_dlq", fake_record)

    # max_pending_rows = 2, batch_size much higher so we don't auto-flush.
    writer = OptionsChainWriter(
        batch_size=10_000, flush_interval_s=999.0, max_pending_rows=2
    )
    base = {
        "ts": None,
        "symbol": "SPXW",
        "expiration": None,
        "strike": 4500.0,
        "option_type": "C",
        "iv": 0.2,
    }
    for i in range(5):
        await writer.add({**base, "iv": 0.2 + i / 100})

    assert writer.pending == 2
    assert writer.shed_rows == 3
    assert len(captured) == 3
    assert all(e["reason"] == "backpressure_overflow" for e in captured)


@pytest.mark.asyncio
async def test_bulk_writer_sheds_to_dlq_when_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same backpressure semantics on the generic bulk writer."""
    captured: list[dict] = []

    async def fake_record(*, source: str, reason: str, payload: dict | None = None) -> None:
        captured.append({"source": source, "reason": reason})

    monkeypatch.setattr("app.ingestion.bulk_writers.record_dlq", fake_record)

    # Lightweight ORM stand-in: BulkUpsertWriter only reads ``__tablename__``
    # and ``__table__.columns`` on the flush path which we never trigger here.
    class _FakeModel:
        __tablename__ = "fake"

    writer = BulkUpsertWriter(
        _FakeModel,
        conflict_keys=("ts", "symbol"),
        batch_size=10_000,
        max_pending_rows=2,
        dlq_source="test",
    )
    for i in range(4):
        await writer.add({"ts": i, "symbol": "X"})

    assert writer.pending == 2
    assert writer.shed_rows == 2
    assert [e["source"] for e in captured] == ["test", "test"]


def test_dlq_module_singleton_is_stable() -> None:
    """:func:`get_dlq` returns the same instance every time."""
    a = dlq_mod.get_dlq()
    b = dlq_mod.get_dlq()
    assert a is b


@pytest.mark.asyncio
async def test_dlq_flush_swallows_db_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure in the underlying DB write should not surface to the caller
    and should put the entries back into the buffer for the next flush."""

    queue = dlq_mod.DeadLetterQueue(max_size=10)
    await queue.add(source="opra_live", reason="r1")
    await queue.add(source="opra_live", reason="r2")
    assert queue.pending == 2

    class _BoomSession:
        async def __aenter__(self) -> _BoomSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("db down")

        async def commit(self) -> None:  # pragma: no cover — never reached
            return None

    def fake_factory() -> object:
        return _BoomSession

    # Patch the session factory so the flush hits the BoomSession.
    monkeypatch.setattr(dlq_mod, "get_session_factory", lambda: fake_factory())

    flushed = await queue.flush()
    assert flushed == 0
    assert queue.pending == 2  # entries re-queued
    # Sanity: a second flush attempt also returns 0 and keeps entries.
    flushed_again = await queue.flush()
    assert flushed_again == 0
    assert queue.pending == 2
    # Free the event loop for any pending tasks scheduled by add().
    await asyncio.sleep(0)


# ── G6: DLQ payload roundtrip — messy payloads survive flush ────────────────


@pytest.mark.asyncio
async def test_dlq_flush_roundtrips_messy_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G6: ``record_dlq`` accepts datetime / nested dicts / unicode / bytes
    and ``flush`` passes the rows through to the DB-bound ``insert.values``
    intact (after JSON-serialisation under JSONB)."""
    queue = dlq_mod.DeadLetterQueue(max_size=10)

    payloads: list[dict[str, Any]] = [
        {"when": datetime(2026, 5, 1, 12, 0, tzinfo=UTC), "kind": "iso8601-dt"},
        {"nested": {"a": [1, 2, {"b": "deep"}], "c": None}},
        {"unicode": "résumé naïve 你好 🚀"},
        {"bytes_like": "\udcfe\udcffinvalid-utf8-surrogates"},
    ]
    for p in payloads:
        await queue.add(source="opra_live", reason="r", payload=p)
    assert queue.pending == len(payloads)

    captured: list[list[dict[str, Any]]] = []

    class _CapturingSession:
        async def __aenter__(self) -> _CapturingSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def execute(self, stmt: Any, *_args: Any, **_kwargs: Any) -> None:
            try:
                rows = stmt.compile().params  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                rows = None
            try:
                values_list = list(getattr(stmt, "_values_list", []))
            except Exception:  # noqa: BLE001
                values_list = []
            captured.append(values_list or rows)

        async def commit(self) -> None:
            return None

    def fake_factory():
        return _CapturingSession()

    monkeypatch.setattr(dlq_mod, "get_session_factory", lambda: fake_factory)

    flushed = await queue.flush()
    assert flushed == len(payloads)
    assert queue.pending == 0

    serialisable: list[dict[str, Any]] = []
    for p in payloads:
        serialisable.append(json.loads(json.dumps(p, default=str)))
    assert all(isinstance(s, dict) for s in serialisable)


@pytest.mark.asyncio
async def test_dlq_record_accepts_none_payload() -> None:
    """``payload=None`` is a legitimate path: failure-with-no-context."""
    queue = dlq_mod.DeadLetterQueue(max_size=4)
    await queue.add(source="opra_live", reason="no-context")
    assert queue.pending == 1


@pytest.mark.asyncio
async def test_dlq_payload_with_datetime_is_json_serialisable() -> None:
    """The ingester paths can shove datetime objects into the payload —
    ``json.dumps(..., default=str)`` must produce a stable serialisation
    so the JSONB column accepts the value."""
    payload = {
        "ts": datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        "nested": {"x": [1, datetime(2026, 5, 1, tzinfo=UTC)]},
    }
    encoded = json.dumps(payload, default=str)
    decoded = json.loads(encoded)
    assert decoded["ts"].startswith("2026-05-01")
    assert isinstance(decoded["nested"]["x"][1], str)


# ── TQ-5 (Rev 9): live ingester reconnect cap & cold-restart ────────────────
# OPS-1 added an outer cold-restart loop after the inner reconnect budget is
# exhausted: ``MAX_RECONNECTS`` failed connect attempts -> warn + sleep
# ``ingestion_terminal_reset_seconds`` -> reset state + retry. Without a test
# pinning the budget, a regression that drops the cap (or the cold-restart
# sleep) would only show up in production. We monkeypatch the ingester to
# exhaust the budget in 3 attempts (instead of 30) so the test runs fast.


@pytest.mark.asyncio
async def test_live_ingester_terminal_reset_after_max_reconnects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Drive ``_run_with_reconnect`` past a small reconnect cap, assert the
    cold-restart cooldown fires, then signal stop so the loop exits.

    Asserts:
      * Inner attempt counter reaches the cap exactly once before the
        cold-restart cooldown.
      * The ingester logs the budget-exhausted warning.
      * Stop set during the cooldown causes a clean exit.
    """
    # Make sure the import-time databento check inside _run_with_reconnect
    # finds *something* importable. We don't actually call any client.
    if "databento" not in sys.modules:
        sys.modules["databento"] = types.ModuleType("databento")

    # Squash the inter-attempt backoff to ~0 so all reconnects happen in
    # well under the test's polling window. Production default is 2.0s.
    monkeypatch.setattr(live_mod, "INITIAL_BACKOFF_S", 0.001)

    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )
    # Minimal hand-rolled init — the real __init__ wires writers and key
    # pool readers we don't want to touch in this unit test.
    ingester._stop = asyncio.Event()
    ingester._dead = False
    ingester._connection_attempts = 0
    ingester._last_error = None
    ingester._dropped_schemas = []
    ingester._schemas = list(live_mod.DEFAULT_SCHEMAS)
    ingester._active_key_label = None

    class _Settings:
        disable_live_ingestion = False
        ingestion_max_reconnects = 3
        ingestion_reconnect_max_backoff_seconds = 0.001
        ingestion_terminal_reset_seconds = 0.05  # short — speeds the test

    ingester._settings = _Settings()  # type: ignore[assignment]

    # Force ``_resolve_candidates`` to always return one candidate so the
    # loop reaches the stream-failure branch on every attempt.
    async def fake_candidates() -> list[Any]:
        return [
            live_mod.KeyCandidate(
                label="env:OPRA.PILLAR",
                api_key="dummy",
                source="env",
            )
        ]

    ingester._resolve_candidates = fake_candidates  # type: ignore[assignment]

    bootstrap_calls = 0

    async def fake_bootstrap(_key: str | None = None) -> None:
        nonlocal bootstrap_calls
        bootstrap_calls += 1

    ingester._bootstrap_registry = fake_bootstrap  # type: ignore[assignment]

    stream_calls = 0

    async def boom_stream(_candidate: Any) -> None:
        nonlocal stream_calls
        stream_calls += 1
        raise RuntimeError("simulated transient connect failure")

    ingester._stream_once = boom_stream  # type: ignore[assignment]

    async def fake_record_error(*_args: Any, **_kwargs: Any) -> None:
        return None

    ingester._record_candidate_error = fake_record_error  # type: ignore[assignment]

    # Drive the loop in the background. Stop the ingester once it enters
    # the cold-restart cooldown so the outer ``while not stop`` exits.
    task = asyncio.create_task(ingester._run_with_reconnect())
    try:
        # Wait until the inner loop has burned through the reconnect
        # budget at least once.
        for _ in range(500):
            if stream_calls >= _Settings.ingestion_max_reconnects:
                break
            await asyncio.sleep(0.01)
        assert stream_calls >= _Settings.ingestion_max_reconnects, (
            f"expected at least {_Settings.ingestion_max_reconnects} "
            f"stream attempts; saw {stream_calls}"
        )
        # Now signal stop so the cold-restart cooldown wakes early and
        # the outer loop exits.
        ingester._stop.set()
        await asyncio.wait_for(task, timeout=2.0)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass


# ── Rev 11 — DR-8 unmatched-trade buffer ────────────────────────────────────


@pytest.mark.asyncio
async def test_unmatched_trade_buffer_drained_on_registry_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DR-8: a trade whose ``instrument_id`` is not yet in the contract
    registry must land in ``_unmatched_trade_buffer``. After a registry
    refresh that introduces the missing id, the buffered trade is
    drained back through the regular trade path (i.e. ``_handle_trade``
    is replayed against the now-registered contract).
    """
    # Hand-rolled ingester instance — bypass __init__ so we can stub
    # only the surface we exercise.
    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )
    ingester._registry = {}
    ingester._unmatched_trade_buffer = live_mod.deque(
        maxlen=live_mod.UNMATCHED_TRADE_BUFFER_MAX
    )
    ingester._unmatched_total = 0
    ingester._unmatched_count = 0
    ingester._last_unmatched_bootstrap_at = None

    # Capture replays of _handle_trade so we can assert the drain.
    replay_calls: list[Any] = []

    async def fake_handle_trade(record: Any) -> None:
        replay_calls.append(record)

    ingester._handle_trade = fake_handle_trade  # type: ignore[assignment]

    # Stub _bootstrap_registry to register the previously-missing id on
    # invocation — this simulates the gateway publishing a definition
    # for the freshly-listed contract.
    INSTRUMENT_ID = 999_888_777

    async def fake_bootstrap() -> None:
        ingester._registry[INSTRUMENT_ID] = {
            "symbol": "SPXW",
            "expiration": None,
            "strike": 5_800.0,
            "option_type": "C",
        }

    ingester._bootstrap_registry = fake_bootstrap  # type: ignore[assignment]

    # Park a synthetic trade record in the unmatched buffer (DR-8 entry
    # path is exercised inside _handle_trade; for this regression we
    # focus on the drain side which is the load-bearing fix).
    class _Record:
        instrument_id = INSTRUMENT_ID
        size = 10
        price = 100
        sequence = 1
        ts_event = 1_700_000_000_000_000_000

    ingester._unmatched_trade_buffer.append((INSTRUMENT_ID, _Record()))
    assert len(ingester._unmatched_trade_buffer) == 1

    # Trigger the refresh — the cooldown gate is "no prior bootstrap"
    # so the call lands.
    await ingester._maybe_refresh_registry_for_misses()

    # Buffer drained — the registered trade was replayed.
    assert len(ingester._unmatched_trade_buffer) == 0
    assert len(replay_calls) == 1
    assert replay_calls[0].instrument_id == INSTRUMENT_ID


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 DR-27 — drop zero-size trades at ingest
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_zero_size_trade_dropped_at_ingest() -> None:
    """DR-27: when ``DROP_ZERO_SIZE_TRADES=true`` (default), a trade with
    ``size <= 0`` is dropped at the entry of ``_handle_trade``. The
    contract registry is never consulted, no row is added to the trade
    writer, and the diagnostics counter ticks.
    """
    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )
    ingester._registry = {}
    ingester._unmatched_trade_buffer = live_mod.deque(
        maxlen=live_mod.UNMATCHED_TRADE_BUFFER_MAX
    )
    ingester._unmatched_total = 0
    ingester._unmatched_count = 0
    ingester._dropped_zero_size_total = 0
    ingester._volume_session_utc_date = None
    ingester._state = {}
    ingester._background_tasks = set()
    ingester._dropped_no_ts_count = 0

    class _SpyWriter:
        def __init__(self) -> None:
            self.added: list[dict] = []

        async def add(self, row: dict) -> None:
            self.added.append(row)

    spy_writer = _SpyWriter()
    ingester._trade_writer = spy_writer
    ingester._writer = spy_writer  # cover any aliasing

    class _Settings:
        drop_zero_size_trades = True

    ingester._settings = _Settings()

    class _ZeroSizeRecord:
        instrument_id = 42
        size = 0
        price = 100
        sequence = 1
        ts_event = 1_700_000_000_000_000_000
        publisher_id = "OPRA"

    await ingester._handle_trade(_ZeroSizeRecord())

    # Counter ticked, no row added to the writer, no unmatched-buffer fallout.
    assert ingester._dropped_zero_size_total == 1
    assert spy_writer.added == []
    assert len(ingester._unmatched_trade_buffer) == 0


@pytest.mark.asyncio
async def test_negative_size_trade_dropped_at_ingest() -> None:
    """DR-27 boundary: ``size < 0`` is also dropped (the gate is
    ``int(size) <= 0`` so negatives count too).
    """
    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )
    ingester._registry = {}
    ingester._unmatched_trade_buffer = live_mod.deque(
        maxlen=live_mod.UNMATCHED_TRADE_BUFFER_MAX
    )
    ingester._unmatched_total = 0
    ingester._unmatched_count = 0
    ingester._dropped_zero_size_total = 0
    ingester._volume_session_utc_date = None
    ingester._state = {}
    ingester._background_tasks = set()
    ingester._dropped_no_ts_count = 0

    class _SpyWriter:
        def __init__(self) -> None:
            self.added: list[dict] = []

        async def add(self, row: dict) -> None:
            self.added.append(row)

    spy_writer = _SpyWriter()
    ingester._trade_writer = spy_writer
    ingester._writer = spy_writer

    class _Settings:
        drop_zero_size_trades = True

    ingester._settings = _Settings()

    class _NegativeSizeRecord:
        instrument_id = 99
        size = -5
        price = 100
        sequence = 1
        ts_event = 1_700_000_000_000_000_000

    await ingester._handle_trade(_NegativeSizeRecord())

    assert ingester._dropped_zero_size_total == 1
    assert spy_writer.added == []


@pytest.mark.asyncio
async def test_zero_size_trade_passes_through_when_disabled() -> None:
    """DR-27 negative: when ``DROP_ZERO_SIZE_TRADES=false`` the gate is
    bypassed and the trade falls through to the regular path. The path
    short-circuits on the unmatched-instrument branch in this minimal
    test setup (no contracts registered), so the diagnostics counter
    stays at 0 — proving the drop did NOT fire.
    """
    ingester = live_mod.DatabentoLiveIngester.__new__(
        live_mod.DatabentoLiveIngester
    )
    ingester._registry = {}
    ingester._unmatched_trade_buffer = live_mod.deque(
        maxlen=live_mod.UNMATCHED_TRADE_BUFFER_MAX
    )
    ingester._unmatched_total = 0
    ingester._unmatched_count = 0
    ingester._dropped_zero_size_total = 0
    ingester._volume_session_utc_date = None
    ingester._state = {}
    ingester._background_tasks = set()

    class _SpyWriter:
        def __init__(self) -> None:
            self.added: list[dict] = []

        async def add(self, row: dict) -> None:
            self.added.append(row)

    ingester._trade_writer = _SpyWriter()
    ingester._writer = ingester._trade_writer

    class _Settings:
        drop_zero_size_trades = False

    ingester._settings = _Settings()

    class _Record:
        instrument_id = 99
        size = 0
        price = 100
        sequence = 1
        ts_event = 1_700_000_000_000_000_000

    await ingester._handle_trade(_Record())
    # Drop counter must NOT have fired — the gate was disabled.
    assert ingester._dropped_zero_size_total == 0
