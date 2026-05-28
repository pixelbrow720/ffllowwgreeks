"""Realtime streaming endpoints (Agent 5 — streaming API).

Two transports backed by the same in-process :mod:`stream_notifier`:

* ``WS  /v1/{symbol}/stream``      — preferred; bi-directional, low-overhead.
* ``GET /v1/{symbol}/stream/sse``  — Server-Sent Events fallback for
  environments that strip WebSocket upgrades (corporate proxies, etc.).

Both push a JSON frame whose ``data`` field matches the payload returned by
``/v1/{symbol}/snapshot``. Frames land on subscribers within milliseconds of
the chain pipeline calling :func:`stream_notifier.publish` at the end of
``run_pipeline_for_symbol``.

Authentication mirrors the REST API: a valid ``X-API-Key`` (header or
``?key=`` query param for WS clients that cannot set custom headers) bound to
the requested ``symbol`` is required.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Path,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import record_api_key_usage
from app.api.endpoints.snapshot import (
    build_snapshot_payload_single_flight,
    get_cached_snapshot,
)
from app.api.stream_notifier import get_stream_notifier
from app.api.tick_notifier import get_tick_notifier
from app.config import get_settings
from app.core.logging import get_logger
from app.core.security import api_key_lookup_digest, verify_api_key
from app.db.models import ApiKey
from app.db.session import get_session_factory

logger = get_logger(__name__)

router = APIRouter()


_SYMBOL_PATTERN = r"^[A-Z][A-Z0-9]{0,11}$"

# Heartbeat cadence: corporate proxies typically drop idle WS connections
# after 30–60 s. 25 s leaves comfortable margin without flooding.
HEARTBEAT_INTERVAL_SECONDS: float = 25.0


def _revocation_check_interval_seconds() -> float:
    """How often the WS handlers re-poll ``api_keys`` for revocation.

    Sourced from :class:`Settings.ws_revocation_check_interval_seconds`
    (default 5s, was 30s in Rev 7). Lookup at call-time rather than
    module-import so test overrides via env vars + ``get_settings``
    cache invalidation actually apply.
    """
    return float(get_settings().ws_revocation_check_interval_seconds)


# Custom close code emitted when an active connection is severed because
# the underlying credential was revoked mid-stream. RFC 6455 reserves
# 4000–4999 for application use.
WS_REVOKED_CODE: int = 4401

# RFC 6455 status code 1012 — "service restart". Emitted when the
# lifespan teardown closes every live socket so clients see a clean
# protocol-level signal to back off and reconnect after the restart
# rather than an ungraceful TCP drop. (SRE-6)
WS_SERVICE_RESTART_CODE: int = 1012


# ── Rev 10 SRE-6: live WebSocket registry for graceful shutdown ───────────
# Tracks every accepted WS socket so the lifespan teardown can broadcast
# a 1012 close before the engine is disposed. asyncio.Lock guards the
# set even though asyncio is single-threaded — the convention keeps
# concurrent registers/deregisters and the shutdown sweep cleanly
# serialised across awaits.
_LIVE_WEBSOCKETS: set[WebSocket] = set()
_LIVE_WEBSOCKETS_LOCK = asyncio.Lock()


async def _register_live_websocket(websocket: WebSocket) -> None:
    async with _LIVE_WEBSOCKETS_LOCK:
        _LIVE_WEBSOCKETS.add(websocket)


async def _deregister_live_websocket(websocket: WebSocket) -> None:
    async with _LIVE_WEBSOCKETS_LOCK:
        _LIVE_WEBSOCKETS.discard(websocket)


async def shutdown_all_websockets() -> int:
    """Close every live WebSocket with code 1012 (service restart).

    Called from ``app.main`` lifespan teardown before the engine is
    disposed so clients see a clean protocol-level signal to reconnect
    after the restart instead of an ungraceful TCP drop.

    Returns the number of sockets the helper attempted to close. Each
    close is best-effort — already-disconnected sockets, network
    errors, or sockets in an unexpected state are swallowed so one
    bad connection cannot block the rest.
    """
    async with _LIVE_WEBSOCKETS_LOCK:
        sockets = list(_LIVE_WEBSOCKETS)
        _LIVE_WEBSOCKETS.clear()

    closed = 0
    for ws in sockets:
        closed += 1
        try:
            await ws.close(code=WS_SERVICE_RESTART_CODE)
        except (RuntimeError, ConnectionError):
            # Socket already torn down by the peer or stuck in a
            # half-closed state. Either way nothing else to do.
            pass
        except Exception:  # noqa: BLE001
            logger.exception("shutdown_websocket_close_failed")
    if closed:
        logger.info("shutdown_all_websockets_complete", closed=closed)
    return closed


def reset_live_websockets_for_tests() -> None:
    """Test helper: clear the live WS registry between scenarios."""
    _LIVE_WEBSOCKETS.clear()


# ── Rev 8 ARCH-6: process-global revocation watcher ────────────────────────
# Each WS connection used to spin its own ``_revocation_watcher`` task
# that polled ``api_keys`` independently. With K keys × N connections per
# key × 1 query / interval the DB load grows linearly with the connection
# pool. Consolidating to ONE watcher per ``api_key_id`` (regardless of
# how many connections share the key) gives O(K) polls instead of O(K·N).
#
# Each connection registers an :class:`asyncio.Event`; when the global
# watcher decides the key is revoked, every event for that key is set.
# Connections await their event and close the socket on wake.
_KEY_REVOCATION_SUBSCRIBERS: dict[Any, set[asyncio.Event]] = defaultdict(set)
_KEY_REVOCATION_LOCK = asyncio.Lock()
_KEY_REVOCATION_TASK: asyncio.Task | None = None


async def _check_key_revoked(api_key_id: Any) -> bool:
    """Return True when ``api_key_id`` is no longer valid (deactivated/expired)."""
    factory = get_session_factory()
    try:
        async with factory() as session:
            row = await session.get(ApiKey, api_key_id)
    except Exception:  # noqa: BLE001 - DB blip should not kick the client
        logger.exception(
            "ws_revocation_check_failed", api_key_id=str(api_key_id)
        )
        return False
    if row is None or not row.is_active:
        return True
    if getattr(row, "deleted_at", None) is not None:
        return True
    if row.expires_at is not None:
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            return True
    return False


async def _global_revocation_watcher() -> None:
    """Single process-wide poll of ``api_keys`` for every actively-watched id.

    Sleeps for ``_revocation_check_interval_seconds()`` between rounds,
    queries every key id with at least one subscriber, and signals each
    subscriber's event when the key has been revoked. Robust to per-key
    DB errors (handled by ``_check_key_revoked`` which returns False on
    blip) so a flaky DB doesn't tear connections down spuriously.
    """
    while True:
        try:
            await asyncio.sleep(_revocation_check_interval_seconds())
        except asyncio.CancelledError:
            raise
        try:
            async with _KEY_REVOCATION_LOCK:
                # Snapshot the active ids — we mutate the bucket from
                # _register/_deregister, so iterate a copy.
                watched = list(_KEY_REVOCATION_SUBSCRIBERS.keys())
            for key_id in watched:
                try:
                    revoked = await _check_key_revoked(key_id)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    continue
                if revoked:
                    async with _KEY_REVOCATION_LOCK:
                        events = list(
                            _KEY_REVOCATION_SUBSCRIBERS.get(key_id, set())
                        )
                    for ev in events:
                        ev.set()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("global_revocation_watcher_iteration_failed")


async def start_global_revocation_watcher() -> asyncio.Task:
    """Idempotent starter wired from ``app.main.lifespan``.

    Returns the running task so the lifespan tear-down can cancel it
    cleanly. Calling twice in the same process re-uses the existing
    task (the second call is a no-op).
    """
    global _KEY_REVOCATION_TASK
    if _KEY_REVOCATION_TASK is not None and not _KEY_REVOCATION_TASK.done():
        return _KEY_REVOCATION_TASK
    _KEY_REVOCATION_TASK = asyncio.create_task(
        _global_revocation_watcher(), name="ws_global_revocation_watcher"
    )
    return _KEY_REVOCATION_TASK


async def stop_global_revocation_watcher() -> None:
    """Cancel the global watcher (used by tests and lifespan tear-down)."""
    global _KEY_REVOCATION_TASK
    task = _KEY_REVOCATION_TASK
    _KEY_REVOCATION_TASK = None
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


def reset_revocation_subscribers_for_tests() -> None:
    """Test helper: drop every subscriber + the running watcher task."""
    _KEY_REVOCATION_SUBSCRIBERS.clear()
    global _KEY_REVOCATION_TASK
    task = _KEY_REVOCATION_TASK
    _KEY_REVOCATION_TASK = None
    if task is not None and not task.done():
        task.cancel()


async def _register_revocation_subscriber(api_key_id: Any) -> asyncio.Event:
    """Register a per-connection event waiting on revocation of ``api_key_id``."""
    ev = asyncio.Event()
    async with _KEY_REVOCATION_LOCK:
        _KEY_REVOCATION_SUBSCRIBERS[api_key_id].add(ev)
    # Lazily start the global watcher on the first subscription so unit
    # tests that never WS-connect don't have a stray task.
    if _KEY_REVOCATION_TASK is None or _KEY_REVOCATION_TASK.done():
        await start_global_revocation_watcher()
    return ev


async def _deregister_revocation_subscriber(
    api_key_id: Any, ev: asyncio.Event
) -> None:
    async with _KEY_REVOCATION_LOCK:
        bucket = _KEY_REVOCATION_SUBSCRIBERS.get(api_key_id)
        if bucket is None:
            return
        bucket.discard(ev)
        if not bucket:
            _KEY_REVOCATION_SUBSCRIBERS.pop(api_key_id, None)


# ── Per-key WS connection accounting ────────────────────────────────────────


_ws_connections_per_key: dict[str, int] = defaultdict(int)
_ws_lock = asyncio.Lock()


async def _ws_try_register(api_key_id: str) -> bool:
    """Atomically reserve a WS slot for ``api_key_id``.

    Returns ``True`` when the new connection fits under
    ``Settings.max_ws_connections_per_key``, ``False`` otherwise.
    """
    cap = get_settings().max_ws_connections_per_key
    async with _ws_lock:
        if _ws_connections_per_key[api_key_id] >= cap:
            return False
        _ws_connections_per_key[api_key_id] += 1
        return True


async def _ws_release(api_key_id: str) -> None:
    async with _ws_lock:
        current = _ws_connections_per_key.get(api_key_id, 0)
        if current <= 1:
            _ws_connections_per_key.pop(api_key_id, None)
        else:
            _ws_connections_per_key[api_key_id] = current - 1


def ws_connection_count(api_key_id: str) -> int:
    """Test helper: introspect the per-key counter."""
    return _ws_connections_per_key.get(api_key_id, 0)


def reset_ws_state_for_tests() -> None:
    """Test helper: clear all per-key accounting."""
    _ws_connections_per_key.clear()


# ── Authentication helpers ──────────────────────────────────────────────────


async def _authenticate_streaming_key(
    api_key: str | None, symbol: str, session: AsyncSession
) -> ApiKey | None:
    """Validate ``api_key`` against the DB and ``symbol``'s ACL.

    Returns the :class:`ApiKey` row on success, ``None`` on any failure. We
    intentionally return ``None`` rather than raising so the WS handler can
    pick the close code; the SSE handler uses the standard FastAPI dependency
    and raises a 401 automatically.
    """
    if not api_key:
        return None
    # O(1) keyed-BLAKE2b lookup — the legacy NULL-key_lookup prefix-scan
    # fallback was retired in Rev 8 SEC-1 once migration 0012
    # deactivated every row that lacked a digest. A row that still has
    # ``key_lookup IS NULL`` after the migration is one an operator
    # actively re-introduced; auth correctly refuses it via the
    # ``is_active`` check below.
    matched: ApiKey | None = None
    lookup_digest = api_key_lookup_digest(api_key)
    fast = await session.execute(
        select(ApiKey).where(ApiKey.key_lookup == lookup_digest)
    )
    candidate = fast.scalar_one_or_none()
    if candidate is not None and verify_api_key(api_key, candidate.key_hash):
        matched = candidate
    if matched is None or not matched.is_active:
        return None
    if getattr(matched, "deleted_at", None) is not None:
        return None
    if matched.expires_at is not None:
        expires_at = matched.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            return None
    sym_u = symbol.upper()
    if sym_u not in [s.upper() for s in (matched.allowed_symbols or [])]:
        return None
    # Mirror the REST auth path: record one usage event in the deferred
    # buffer so admin telemetry sees WS traffic. The buffer is drained by
    # ``_usage_flush_loop`` in ``app.main.lifespan``.
    await record_api_key_usage(matched.id)
    return matched


# ── Wire format ─────────────────────────────────────────────────────────────


def _frame(
    symbol: str,
    computed_at: datetime | None,
    data: dict[str, Any],
    *,
    seq: int | None = None,
) -> dict[str, Any]:
    """Serialise a snapshot payload as the WS/SSE wire frame.

    Rev 12 BC-9: the frame carries an explicit ``type: "snapshot"``
    discriminator alongside the existing ``data`` field. This is
    additive — pre-Rev-12 clients that branched on ``"data" in frame``
    continue to work because the field is still present; new clients
    can dispatch on ``type`` uniformly across snapshot / tick /
    heartbeat / error frames.

    Rev 13 FE-1: optional ``seq`` is the per-symbol monotonic sequence
    number from :class:`StreamNotifier`. The pipeline-published wire
    frames embed it in :func:`pipeline._publish_streaming_snapshot`;
    the prime frame here uses ``notifier.latest_seq(symbol)`` so a
    client primed from cache knows the seq from which to ask for
    replay if the connection drops mid-stream. When ``seq`` is None
    the field is omitted so v1.1 wire compatibility is preserved.
    """
    out: dict[str, Any] = {
        "type": "snapshot",
        "symbol": symbol.upper(),
        "computed_at": computed_at.isoformat() if computed_at is not None else None,
        "data": data,
    }
    if seq is not None:
        out["seq"] = int(seq)
    return out


def _coerce_wire_frame(sym_u: str, queued: Any) -> str:
    """Normalise a notifier-queued frame into a JSON wire string.

    Production publishes a pre-serialised JSON string (Rev 9 PF-4 — single
    ``orjson.dumps`` per tick fanned out across all subscribers). Tests
    occasionally publish dicts directly into the notifier; in that case we
    fall back to ``json.dumps`` here so the wire format stays stable.

    Rev 12 BC-9: when we synthesize the frame from a dict payload we
    inject ``type: "snapshot"`` so test-driven publishes match the
    production wire format.

    Rev 13 FE-1: when the dict already carries ``seq`` (e.g. tests that
    pre-stamp the seq for replay assertions) we propagate it; otherwise
    the field is omitted so v1.1 wire compatibility is preserved.
    """
    if isinstance(queued, str):
        return queued
    if isinstance(queued, (bytes, bytearray)):
        return bytes(queued).decode("utf-8")
    payload = queued if isinstance(queued, dict) else {"data": queued}
    computed_at = payload.get("computed_at")
    if isinstance(computed_at, datetime):
        computed_at = computed_at.isoformat()
    data = payload.get("data", payload)
    wrapped: dict[str, Any] = {
        "type": "snapshot",
        "symbol": sym_u,
        "computed_at": computed_at,
        "data": data,
    }
    seq_val = payload.get("seq")
    if isinstance(seq_val, int):
        wrapped["seq"] = seq_val
    return json.dumps(wrapped, default=str)


async def _emit_error_frame(
    websocket: WebSocket, *, code: int, message: str
) -> None:
    """Best-effort ``WsErrorFrame`` emit on an already-accepted socket.

    Rev 12 BC-10: the TS contract has shipped ``WsErrorFrame`` since
    v1.1 but the emitter never produced one. We now wire it into
    pre-existing fatal-ish conditions (initial-snapshot prime failure)
    so consumers waiting on ``type === "error"`` actually see the
    frame instead of an opaque close. ``code`` is conventionally an
    HTTP-style status (e.g. 503 = degraded). Errors here are swallowed
    so an already-disconnected peer cannot blow up the handler.
    """
    try:
        await websocket.send_text(
            json.dumps(
                {"type": "error", "code": int(code), "message": str(message)}
            )
        )
    except (RuntimeError, ConnectionError, WebSocketDisconnect):
        # Socket gone or in a half-closed state — nothing to surface.
        pass
    except Exception:  # noqa: BLE001
        logger.exception("ws_emit_error_frame_failed")


def _sse_event(payload: dict[str, Any], event: str | None = None) -> str:
    """Encode ``payload`` as one SSE event."""
    body = json.dumps(payload, default=str)
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {body}\n\n"


def _sse_event_raw(body: str, event: str | None = None) -> str:
    """Encode an already-serialised JSON ``body`` as one SSE event.

    Used by the publish pump where the publisher hands us a pre-serialised
    string (Rev 9 PF-4) — embedding it directly avoids a roundtrip through
    ``json.loads`` + ``json.dumps``.
    """
    prefix = f"event: {event}\n" if event else ""
    return f"{prefix}data: {body}\n\n"


# ── WebSocket endpoint ──────────────────────────────────────────────────────


@router.websocket("/v1/{symbol}/stream")
async def stream_ws(
    websocket: WebSocket,
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    key: str | None = Query(default=None),
    since_seq: int | None = Query(
        default=None,
        ge=0,
        description=(
            "Optional resume token (Rev 13 FE-1). When supplied, the server "
            "replays frames buffered with seq > since_seq from the per-symbol "
            "ring buffer (depth ~60) before subscribing to live updates. "
            "Omit on first connect — the server then primes from the latest "
            "snapshot cache as before."
        ),
    ),
) -> None:
    """Push a JSON frame whenever the pipeline completes a cycle for ``symbol``.

    Auth (in priority order):
      * ``X-API-Key`` header — works for non-browser clients that can
        set custom headers on the upgrade.
      * ``?key=...`` query param — fallback for browser WebSocket clients
        that cannot set custom headers.

    Close codes:
    * ``1008`` (policy violation) — missing / invalid auth, symbol ACL
      miss, or per-key connection cap exceeded.
    * ``4401`` (custom) — auth was valid at connect but the underlying
      API key has been deactivated or expired mid-stream.
    """
    sym_u = symbol.upper()
    factory = get_session_factory()

    api_key_value = websocket.headers.get("x-api-key") or key
    async with factory() as session:
        api_key_row = await _authenticate_streaming_key(
            api_key_value, sym_u, session
        )

    if api_key_row is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    api_key_id = str(api_key_row.id)
    registered = await _ws_try_register(api_key_id)
    if not registered:
        # Accept-then-close so the client can read the policy-violation code
        # rather than seeing a generic handshake failure.
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Subscribe BEFORE accepting so a notifier failure cleanly releases the
    # slot without leaving an accepted-but-unpumped socket. We wrap the
    # subscribe + accept in try/except so any exception releases the slot
    # before propagating.
    notifier = get_stream_notifier()
    try:
        queue = notifier.subscribe(sym_u)
    except Exception:  # noqa: BLE001
        await _ws_release(api_key_id)
        logger.exception("stream_ws_subscribe_failed", symbol=sym_u)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        await websocket.accept()
    except Exception:  # noqa: BLE001
        notifier.unsubscribe(sym_u, queue)
        await _ws_release(api_key_id)
        raise

    # Rev 10 SRE-6: track the live socket so lifespan teardown can
    # broadcast a 1012 close before engine disposal.
    await _register_live_websocket(websocket)

    async def _send_json(payload: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(payload, default=str))

    # Rev 13 FE-1: when the client supplied ``?since_seq=`` we try to
    # replay buffered frames since that point BEFORE the cache prime.
    # This means a reconnecting client gets the missed frames in
    # publish-order followed by live updates — no duplicate prime, no
    # gap. If the buffer can fully cover the gap (``since_seq >=
    # latest_seq - len(buffer)``) we skip the cache prime entirely;
    # otherwise we fall through to the normal prime which the client
    # treats as "best we can do, assume cold start".
    skip_cache_prime = False
    if since_seq is not None:
        try:
            replay_frames, latest_seq = await notifier.replay_since(
                sym_u, since_seq
            )
        except Exception:  # noqa: BLE001 - never fail the connect on replay
            logger.exception("stream_ws_replay_failed", symbol=sym_u)
            replay_frames, latest_seq = ([], notifier.latest_seq(sym_u))
        # Cover the gap fully when ``since_seq`` is in-buffer (the
        # caller's pointer is at or past the oldest buffered seq). The
        # check ``replay_frames`` covers the empty-but-up-to-date case
        # — if since_seq == latest_seq the buffer returns no frames
        # and we still want to skip the redundant prime.
        if since_seq >= max(0, latest_seq - notifier.frame_buffer_maxlen):
            skip_cache_prime = True
        for queued in replay_frames:
            try:
                await websocket.send_text(_coerce_wire_frame(sym_u, queued))
            except (RuntimeError, ConnectionError, WebSocketDisconnect):
                # Peer went away mid-replay; bail out cleanly. The
                # finally-block below will release the slot + queue.
                break

    # Prime the connection with the latest snapshot so subscribers don't have
    # to wait a full pipeline cycle to see data. Reuse the in-process cache
    # populated by the pipeline on every successful tick — a reconnect
    # storm would otherwise hammer the DB with one set of ~26 metric_type
    # queries per connecting client. Cold-cache priming is now single-
    # flighted (Rev 8 OPS-4) so N reconnecting clients trigger ONE batch
    # read instead of N.
    if not skip_cache_prime:
        cached = get_cached_snapshot(sym_u)
        try:
            if cached is not None:
                initial_payload, computed_at = cached
            else:
                async with factory() as session:
                    initial_payload, computed_at = (
                        await build_snapshot_payload_single_flight(session, sym_u)
                    )
            # Rev 13 FE-1: stamp the prime frame with the latest known
            # seq so the client has a starting point for ``since_seq``
            # if the connection drops before the next live publish.
            prime_seq = notifier.latest_seq(sym_u)
            await _send_json(
                _frame(
                    sym_u,
                    computed_at,
                    initial_payload,
                    seq=prime_seq if prime_seq > 0 else None,
                )
            )
        except Exception:  # noqa: BLE001 - best-effort prime
            logger.exception("stream_ws_initial_snapshot_failed", symbol=sym_u)
            # Rev 12 BC-10: surface a structured error frame so consumers
            # waiting on ``type === "error"`` see the prime failure rather
            # than an opaque silent-no-data window.
            await _emit_error_frame(
                websocket,
                code=503,
                message="initial snapshot prime failed",
            )

    async def _heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                await _send_json(
                    {"type": "heartbeat", "ts": datetime.now(UTC).isoformat()}
                )
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return

    async def _pump() -> None:
        # Rev 9 PF-4: the publisher pre-serialises the frame once and hands
        # the same string reference to every subscriber queue. Send it as
        # text without re-decoding/re-encoding. ``_coerce_wire_frame``
        # tolerates dict payloads pushed by tests.
        try:
            while True:
                queued = await queue.get()
                await websocket.send_text(_coerce_wire_frame(sym_u, queued))
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return

    # Rev 8 ARCH-6: register on the process-global revocation watcher so
    # K connections × N keys = K queries / interval, not K·N.
    revocation_event = await _register_revocation_subscriber(api_key_row.id)

    async def _revocation_listener() -> None:
        """Wait for the global watcher to flag this key, then close."""
        try:
            await revocation_event.wait()
        except asyncio.CancelledError:
            raise
        try:
            await websocket.close(code=WS_REVOKED_CODE)
        except (RuntimeError, ConnectionError):
            pass

    pump_task = asyncio.create_task(_pump(), name=f"ws_pump:{sym_u}")
    heartbeat_task = asyncio.create_task(_heartbeat(), name=f"ws_hb:{sym_u}")
    revoke_task = asyncio.create_task(
        _revocation_listener(), name=f"ws_revoke:{sym_u}"
    )

    try:
        while True:
            # Block until the client disconnects; ``receive_text`` raises
            # ``WebSocketDisconnect`` when the peer closes.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("stream_ws_error", symbol=sym_u)
        # Rev 12 BC-10: surface the unexpected fatal as a structured
        # error frame before the finally-block tears the socket down.
        # Best-effort — the close path that follows handles cleanup.
        await _emit_error_frame(
            websocket,
            code=500,
            message="stream handler error",
        )
    finally:
        for t in (pump_task, heartbeat_task, revoke_task):
            t.cancel()
        for t in (pump_task, heartbeat_task, revoke_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await _deregister_revocation_subscriber(
            api_key_row.id, revocation_event
        )
        notifier.unsubscribe(sym_u, queue)
        await _ws_release(api_key_id)
        await _deregister_live_websocket(websocket)


# ── Server-Sent Events fallback ─────────────────────────────────────────────


@router.websocket("/v1/{symbol}/stream/ticks")
async def stream_ticks_ws(
    websocket: WebSocket,
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    key: str | None = Query(default=None),
) -> None:
    """Push raw spot/futures ticks for ``symbol`` as the GLBX feed prints.

    Channel is high-frequency (each ES/NQ trade fans out a frame) — the
    underlying :class:`TickNotifier` is sized for hundreds of ticks/sec and
    drops oldest-on-overflow. Clients must consume promptly; slow consumers
    will lose ticks rather than block the publisher.

    Auth + per-key cap mirror the snapshot stream.
    """
    sym_u = symbol.upper()
    factory = get_session_factory()

    api_key_value = websocket.headers.get("x-api-key") or key
    async with factory() as session:
        api_key_row = await _authenticate_streaming_key(
            api_key_value, sym_u, session
        )
    if api_key_row is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    api_key_id = str(api_key_row.id)
    registered = await _ws_try_register(api_key_id)
    if not registered:
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    notifier = get_tick_notifier()
    try:
        queue = notifier.subscribe(sym_u)
    except Exception:  # noqa: BLE001
        await _ws_release(api_key_id)
        logger.exception("stream_ticks_subscribe_failed", symbol=sym_u)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        await websocket.accept()
    except Exception:  # noqa: BLE001
        notifier.unsubscribe(sym_u, queue)
        await _ws_release(api_key_id)
        raise

    # Rev 10 SRE-6: track the live socket so lifespan teardown can
    # broadcast a 1012 close before engine disposal.
    await _register_live_websocket(websocket)

    async def _send_json(payload: dict[str, Any]) -> None:
        await websocket.send_text(json.dumps(payload, default=str))

    async def _heartbeat() -> None:
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                await _send_json(
                    {"type": "heartbeat", "ts": datetime.now(UTC).isoformat()}
                )
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return

    async def _is_revoked() -> bool:
        try:
            async with factory() as session:
                row = await session.get(ApiKey, api_key_row.id)
        except Exception:  # noqa: BLE001
            logger.exception("stream_ticks_revocation_check_failed", symbol=sym_u)
            return False
        if row is None or not row.is_active:
            return True
        if row.expires_at is not None:
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < datetime.now(UTC):
                return True
        return False

    async def _pump() -> None:
        try:
            while True:
                payload = await queue.get()
                await _send_json({"type": "tick", "symbol": sym_u, "data": payload})
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            return

    # Rev 8 ARCH-6: share the global revocation watcher with the snapshot
    # stream so K connections × N keys = K queries / interval.
    revocation_event = await _register_revocation_subscriber(api_key_row.id)

    async def _revocation_listener() -> None:
        try:
            await revocation_event.wait()
        except asyncio.CancelledError:
            raise
        try:
            await websocket.close(code=WS_REVOKED_CODE)
        except (RuntimeError, ConnectionError):
            pass

    pump_task = asyncio.create_task(_pump(), name=f"ws_ticks_pump:{sym_u}")
    heartbeat_task = asyncio.create_task(_heartbeat(), name=f"ws_ticks_hb:{sym_u}")
    revoke_task = asyncio.create_task(
        _revocation_listener(), name=f"ws_ticks_revoke:{sym_u}"
    )

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        logger.exception("stream_ticks_ws_error", symbol=sym_u)
        # Rev 12 BC-10: surface the unexpected fatal as a structured
        # error frame so consumers can render a "stream broke" UI
        # instead of guessing from a bare close.
        await _emit_error_frame(
            websocket,
            code=500,
            message="tick stream handler error",
        )
    finally:
        for t in (pump_task, heartbeat_task, revoke_task):
            t.cancel()
        for t in (pump_task, heartbeat_task, revoke_task):
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await _deregister_revocation_subscriber(
            api_key_row.id, revocation_event
        )
        notifier.unsubscribe(sym_u, queue)
        await _ws_release(api_key_id)
        await _deregister_live_websocket(websocket)


# ── Server-Sent Events fallback (snapshot stream) ───────────────────────────


@router.get("/v1/{symbol}/stream/sse")
async def stream_sse(
    request: Request,
    symbol: str = Path(..., min_length=1, max_length=20, pattern=_SYMBOL_PATTERN),
    key: str | None = Query(default=None),
    since_seq: int | None = Query(
        default=None,
        ge=0,
        description=(
            "Optional resume token (Rev 13 FE-1). Mirrors the WS endpoint: "
            "frames buffered with seq > since_seq are replayed before the "
            "live event stream starts. Omit on first connect."
        ),
    ),
) -> StreamingResponse:
    """Server-Sent Events fallback for clients that cannot use WebSockets.

    Auth (in priority order):
      * ``X-API-Key`` header — works for non-browser clients.
      * ``?key=...`` query param — fallback for browser ``EventSource``
        clients that cannot set custom headers (mirrors the WS endpoint).
    """
    sym_u = symbol.upper()

    api_key_value = request.headers.get("x-api-key") or key
    factory = get_session_factory()
    async with factory() as session:
        api_key_row = await _authenticate_streaming_key(
            api_key_value, sym_u, session
        )
    if api_key_row is None:
        raise HTTPException(status_code=401, detail="invalid_api_key")

    notifier = get_stream_notifier()
    queue = notifier.subscribe(sym_u)

    async def _is_revoked(api_key_id: Any) -> bool:
        """Return True if the API key has been deactivated/expired."""
        try:
            async with factory() as session:
                row = await session.get(ApiKey, api_key_id)
        except Exception:  # noqa: BLE001 - DB blip should not kick the client
            logger.exception("stream_sse_revocation_check_failed", symbol=sym_u)
            return False
        if row is None or not row.is_active:
            return True
        if row.expires_at is not None:
            expires_at = row.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < datetime.now(UTC):
                return True
        return False

    async def _stream() -> Any:
        try:
            # Rev 13 FE-1: replay buffered frames since ``since_seq``
            # before priming with the cached snapshot. When the buffer
            # fully covers the gap we skip the cache prime entirely so
            # the client doesn't see a redundant snapshot in front of
            # the missed-frame replay.
            skip_cache_prime = False
            if since_seq is not None:
                try:
                    replay_frames, latest_seq = await notifier.replay_since(
                        sym_u, since_seq
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "stream_sse_replay_failed", symbol=sym_u
                    )
                    replay_frames, latest_seq = (
                        [],
                        notifier.latest_seq(sym_u),
                    )
                if since_seq >= max(0, latest_seq - notifier.frame_buffer_maxlen):
                    skip_cache_prime = True
                for queued in replay_frames:
                    yield _sse_event_raw(_coerce_wire_frame(sym_u, queued))

            # Prime with the latest snapshot — reuse the cache populated
            # by the pipeline tick to absorb reconnect storms. Cold-cache
            # priming is single-flighted (Rev 8 OPS-4) so N concurrent
            # SSE connects on a deploy = ONE batch read.
            if not skip_cache_prime:
                try:
                    cached = get_cached_snapshot(sym_u)
                    if cached is not None:
                        payload, computed_at = cached
                    else:
                        async with factory() as session:
                            payload, computed_at = (
                                await build_snapshot_payload_single_flight(
                                    session, sym_u
                                )
                            )
                    prime_seq = notifier.latest_seq(sym_u)
                    yield _sse_event(
                        _frame(
                            sym_u,
                            computed_at,
                            payload,
                            seq=prime_seq if prime_seq > 0 else None,
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "stream_sse_initial_snapshot_failed", symbol=sym_u
                    )

            # Wallclock-driven revocation check: do not rely on the
            # heartbeat-timeout branch — a pipeline that publishes faster
            # than ``HEARTBEAT_INTERVAL_SECONDS`` would otherwise stream
            # forever to a revoked key.
            last_revocation_check = datetime.now(UTC)
            while True:
                if (
                    datetime.now(UTC) - last_revocation_check
                ).total_seconds() >= _revocation_check_interval_seconds():
                    last_revocation_check = datetime.now(UTC)
                    if await _is_revoked(api_key_row.id):
                        break
                try:
                    queued = await asyncio.wait_for(
                        queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS
                    )
                except TimeoutError:
                    yield _sse_event(
                        {"type": "heartbeat", "ts": datetime.now(UTC).isoformat()},
                        event="heartbeat",
                    )
                    continue
                # Rev 9 PF-4: ``queued`` is normally a pre-serialised JSON
                # string. Coerce defensively so tests pushing dicts still
                # land on the wire.
                yield _sse_event_raw(_coerce_wire_frame(sym_u, queued))
        finally:
            notifier.unsubscribe(sym_u, queue)

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
