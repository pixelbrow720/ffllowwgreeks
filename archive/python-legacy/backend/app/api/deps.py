"""FastAPI dependencies: API-key auth, JWT admin auth, and rate limiting."""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from hashlib import blake2s
from time import monotonic
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.core.security import (
    api_key_lookup_digest,
    decode_jwt_token,
    verify_api_key,
)
from app.db.models import ApiKey, JwtRevocation
from app.db.session import get_db, get_session_factory

logger = get_logger(__name__)

# ── Rate limiter ─────────────────────────────────────────────────────────────


def _real_client_ip(request: Request) -> str:
    """Resolve the real client IP, optionally trusting reverse-proxy headers.

    When ``settings.trust_proxy_headers`` is False (default), this falls
    back to slowapi's ``get_remote_address`` which inspects only the
    socket peer. When True, the first comma-separated entry in
    ``X-Forwarded-For`` is preferred — that's the originating client
    when a trusted edge (Cloudflare / ingress) prepends to the header.
    Only enable when the front proxy strips client-supplied values,
    otherwise a remote client can spoof their IP.
    """
    settings = get_settings()
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",", 1)[0].strip()
            if first:
                return first
    return get_remote_address(request)


def _api_key_or_ip(request: Request) -> str:
    api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if api_key:
        # Hash the key so it never lands in slowapi state, log lines, or
        # tracebacks in plaintext form. blake2s with an 8-byte digest gives
        # 2^64 buckets — collision probability across realistic key counts
        # is negligible — and is fast enough to run on every request.
        digest = blake2s(api_key.encode("utf-8"), digest_size=8).hexdigest()
        return f"key:{digest}"
    return f"ip:{_real_client_ip(request)}"


def _limiter_enabled() -> bool:
    """Disable rate limiting under pytest so tightly-budgeted decorators
    (e.g. ``5/minute`` on ``/admin/login``) don't fail the test suite — the
    ``app_client`` fixture is session-scoped, so all login calls across all
    tests share the same in-memory limiter window. Production behaviour is
    unchanged."""
    if os.getenv("PYTEST_CURRENT_TEST") is not None:
        return False
    if os.getenv("APP_TESTING") == "1":
        return False
    return True


limiter = Limiter(key_func=_api_key_or_ip, enabled=_limiter_enabled())


# ── Lightweight in-process rate limiter for body-Pydantic routes ────────────
#
# slowapi's ``@limiter.limit(...)`` decorator wraps the route in a function
# whose ``__globals__`` belong to the slowapi module. Combined with
# ``from __future__ import annotations``, FastAPI's forward-ref resolver
# cannot find Pydantic body classes (e.g. ``AdminLoginRequest``) declared
# in the route's own module, so the app fails to start. The existing
# ``@limiter.limit`` decorators on ``/v1/*`` GET routes work because they
# only use primitive (``str``) body params with no forward refs.
#
# For new tight-budget endpoints (admin login, OAuth start, etc.) we use a
# tiny sliding-window limiter exposed as a FastAPI ``Depends`` factory.
# This sits in front of the route, so FastAPI's signature inspection only
# ever sees the route's own globals.


class _SlidingWindowLimiter:
    """O(N) per-key sliding window. N = limit (small). Thread-safe."""

    def __init__(self) -> None:
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, period_seconds: float) -> bool:
        now = monotonic()
        cutoff = now - period_seconds
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True


_simple_limiter = _SlidingWindowLimiter()


def rate_limit(
    limit: int, period_seconds: int = 60, *, key: str = ""
) -> Callable[..., Awaitable[None]]:
    """Build a FastAPI dependency that enforces a per-IP rate limit.

    Usage::

        @router.post("/login", dependencies=[Depends(rate_limit(5, 60, key="login"))])

    Disabled automatically under pytest (see :func:`_limiter_enabled`)
    so test suites that exhaust budgets don't break.
    """

    async def _dep(request: Request) -> None:
        if not _limiter_enabled():
            return
        ip = _real_client_ip(request)
        bucket_key = f"{key}:{ip}"
        if not _simple_limiter.allow(bucket_key, limit, period_seconds):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded: {limit} per {period_seconds}s",
            )

    return _dep


def get_limiter() -> Limiter:
    return limiter


# ── API key auth ─────────────────────────────────────────────────────────────


# Deferred-write buffer for ``api_keys.usage_count`` / ``last_used_at``. The
# REST and WS auth paths add to this in-memory dict instead of issuing one
# UPDATE per request — at 120/min/key on the hottest table that write-amp is
# real. ``_usage_flush_loop`` (started from ``main.lifespan``) drains the
# buffer every ``_USAGE_FLUSH_INTERVAL_S`` seconds with a single
# UPDATE-per-affected-key. State is process-local by design; nothing else
# in the app reads usage_count outside admin telemetry, which tolerates
# the flush-interval staleness.
_USAGE_DELTA: dict[uuid.UUID, int] = defaultdict(int)
_USAGE_LAST_SEEN: dict[uuid.UUID, datetime] = {}
_USAGE_LOCK = asyncio.Lock()
_USAGE_FLUSH_INTERVAL_S: float = 60.0


def _testing_mode() -> bool:
    return (
        os.getenv("PYTEST_CURRENT_TEST") is not None
        or os.getenv("APP_TESTING") == "1"
    )


async def record_api_key_usage(api_key_id: uuid.UUID) -> None:
    """Record one auth event against ``api_key_id`` in the deferred buffer."""
    now = datetime.now(UTC)
    async with _USAGE_LOCK:
        _USAGE_DELTA[api_key_id] += 1
        _USAGE_LAST_SEEN[api_key_id] = now


async def flush_usage_deltas(session: AsyncSession) -> int:
    """Drain the deferred-usage buffer to the DB.

    Returns the number of api_key rows updated. Safe to call from any async
    context; under the lock the dicts are swapped out so concurrent
    increments during the UPDATE land in the next flush window.
    """
    async with _USAGE_LOCK:
        if not _USAGE_DELTA:
            return 0
        deltas = dict(_USAGE_DELTA)
        last_seen = dict(_USAGE_LAST_SEEN)
        _USAGE_DELTA.clear()
        _USAGE_LAST_SEEN.clear()

    stmt = text(
        "UPDATE api_keys "
        "SET usage_count = COALESCE(usage_count, 0) + :delta, "
        "    last_used_at = :seen "
        "WHERE id = :id"
    )
    rows = 0
    for key_id, delta in deltas.items():
        seen = last_seen.get(key_id, datetime.now(UTC))
        try:
            await session.execute(
                stmt, {"delta": delta, "seen": seen, "id": key_id}
            )
            rows += 1
        except Exception:  # noqa: BLE001 - one bad row should not poison the batch
            logger.exception("api_key_usage_flush_row_failed", api_key_id=str(key_id))
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("api_key_usage_flush_commit_failed")
        await session.rollback()
        return 0
    return rows


async def _usage_flush_loop() -> None:
    """Periodic background drain of the deferred usage buffer.

    Started from ``app.main.lifespan`` outside ``APP_TESTING=1`` mode and
    cancelled on shutdown. A single failure must not kill the loop — log
    and keep ticking.
    """
    factory = get_session_factory()
    while True:
        try:
            await asyncio.sleep(_USAGE_FLUSH_INTERVAL_S)
            async with factory() as session:
                await flush_usage_deltas(session)
        except asyncio.CancelledError:
            # Final drain on shutdown so we don't lose the last window.
            try:
                async with factory() as session:
                    await flush_usage_deltas(session)
            except Exception:  # noqa: BLE001
                logger.exception("api_key_usage_final_flush_failed")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("api_key_usage_flush_loop_error")


async def authenticate_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    session: AsyncSession = Depends(get_db),
) -> ApiKey:
    """Validate the X-API-Key header and return the matching ApiKey row.

    Increments ``usage_count`` and updates ``last_used_at`` on success.

    Auth-failure responses are deliberately collapsed (Rev 8 SEC-3): an
    unknown key, a deactivated key, an expired key, and a key that
    lacks ACL access for the requested symbol all produce the same
    ``401 Invalid or unauthorized API key``. The detailed reason is
    only available in the structured log so an operator can debug a
    legitimate user issue without leaking enumerable state to attackers.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unauthorized API key",
        )

    # O(1) lookup via the keyed BLAKE2b digest column. Migration 0012
    # backfilled (or deactivated) every legacy NULL-key_lookup row, so
    # the prefix-scan fallback that used to live here is no longer
    # needed and has been removed (Rev 8 SEC-1). Auth is now exactly
    # one bcrypt verify per request — no amplification surface.
    matched: ApiKey | None = None
    lookup_digest = api_key_lookup_digest(x_api_key)
    fast = await session.execute(
        select(ApiKey).where(ApiKey.key_lookup == lookup_digest)
    )
    candidate = fast.scalar_one_or_none()
    if candidate is not None and verify_api_key(x_api_key, candidate.key_hash):
        matched = candidate

    if matched is None:
        logger.info(
            "api_key_auth_failed",
            reason="unknown_key",
            key_prefix=x_api_key[:11],
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unauthorized API key",
        )

    # Soft-deleted rows (SEC-10) cannot authenticate even when
    # ``is_active`` was left True before the delete. Treat them as
    # revoked.
    if getattr(matched, "deleted_at", None) is not None:
        logger.info(
            "api_key_auth_failed",
            reason="deleted",
            api_key_id=str(matched.id),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unauthorized API key",
        )

    if not matched.is_active:
        logger.info(
            "api_key_auth_failed",
            reason="inactive",
            api_key_id=str(matched.id),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unauthorized API key",
        )

    now = datetime.now(UTC)
    if matched.expires_at is not None:
        # Compare in UTC; row is stored as timezone-aware in PG.
        expires_at = matched.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < now:
            logger.info(
                "api_key_auth_failed",
                reason="expired",
                api_key_id=str(matched.id),
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or unauthorized API key",
            )

    # Update usage stats. In production we go through the deferred buffer
    # (``_USAGE_DELTA``) — at 120/min/key the per-request UPDATE+COMMIT on
    # ``api_keys`` is meaningful write-amp on the hottest table. Under
    # ``APP_TESTING=1`` the flush loop is not running, so we fall back to
    # the synchronous in-session update to preserve existing test contracts
    # that read ``usage_count`` / ``last_used_at`` after a single request.
    if _testing_mode():
        matched.usage_count = (matched.usage_count or 0) + 1
        matched.last_used_at = now
        await session.commit()
        await session.refresh(matched)
    else:
        await record_api_key_usage(matched.id)

    request.state.api_key = matched
    return matched


def require_symbol_access(symbol_param: str = "symbol"):
    """Factory: dependency that ensures the API key is allowed to access the path symbol."""

    async def _dep(
        request: Request,
        api_key: Annotated[ApiKey, Depends(authenticate_api_key)],
    ) -> ApiKey:
        symbol = request.path_params.get(symbol_param)
        if symbol is None:
            return api_key
        symbol_u = symbol.upper()
        if symbol_u not in [s.upper() for s in (api_key.allowed_symbols or [])]:
            # Collapse to the same 401 the rest of the auth path uses
            # (SEC-3) so an attacker probing symbol ACLs can't
            # distinguish "key valid but ACL miss" from "key invalid".
            logger.info(
                "api_key_auth_failed",
                reason="symbol_acl_miss",
                api_key_id=str(api_key.id),
                requested_symbol=symbol_u,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or unauthorized API key",
            )
        return api_key

    return _dep


# ── Admin (JWT) auth ─────────────────────────────────────────────────────────

bearer_scheme = HTTPBearer(auto_error=False)


async def authenticate_admin(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: AsyncSession = Depends(get_db),
) -> str:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    try:
        payload = decode_jwt_token(creds.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc

    sub = payload.get("sub")
    settings = get_settings()
    if sub != settings.admin_username:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin user"
        )

    # Server-side revocation check (Rev 8 SEC-2). A token whose ``jti``
    # appears in ``jwt_revocations`` was explicitly logged out and must
    # be rejected even though its signature still verifies. Tokens
    # minted before this change have no ``jti`` claim — those continue
    # to work until they expire (60 min default) so existing sessions
    # don't break across the upgrade.
    jti = payload.get("jti")
    if jti is not None:
        revoked = await session.execute(
            select(JwtRevocation.id).where(JwtRevocation.jti == jti)
        )
        if revoked.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token revoked",
            )
    return sub


async def admin_token_payload(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> dict:
    """Return the decoded admin JWT payload (used by ``/admin/logout``).

    Distinct from :func:`authenticate_admin` because logout needs the
    ``jti`` and ``exp`` claims, not just the subject. Performs the same
    signature + ``typ`` validation but skips the revocation lookup —
    revoking a token a second time is a no-op (UPSERT semantics in the
    handler).
    """
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token"
        )
    try:
        payload = decode_jwt_token(creds.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc
    return payload


async def prune_expired_jwt_revocations(session: AsyncSession) -> int:
    """Drop ``jwt_revocations`` rows whose ``expires_at`` is in the past.

    Once a token's ``exp`` claim is past, the JWT decoder rejects it
    independently of the revocation table — keeping the row around is
    pure storage waste. Run from a periodic background task; safe to
    call from any async context. Returns the number of rows dropped.
    """
    result = await session.execute(
        delete(JwtRevocation).where(
            JwtRevocation.expires_at < datetime.now(UTC)
        )
    )
    await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


_JWT_REVOCATION_PRUNE_INTERVAL_S: float = 15 * 60.0


async def _jwt_revocation_prune_loop() -> None:
    """Periodically drop expired ``jwt_revocations`` rows.

    Started from ``app.main.lifespan`` outside ``APP_TESTING=1`` mode.
    A single failure must not kill the loop — log and keep ticking.
    """
    factory = get_session_factory()
    while True:
        try:
            await asyncio.sleep(_JWT_REVOCATION_PRUNE_INTERVAL_S)
            async with factory() as session:
                pruned = await prune_expired_jwt_revocations(session)
            if pruned:
                logger.info("jwt_revocations_pruned", rows=pruned)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("jwt_revocations_prune_loop_error")
