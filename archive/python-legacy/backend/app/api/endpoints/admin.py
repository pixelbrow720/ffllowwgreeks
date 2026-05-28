"""Admin endpoints (JWT-protected)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_token_payload, authenticate_admin, rate_limit
from app.api.schemas import (
    AdminLoginRequest,
    AdminLoginResponse,
    ApiKeyCreate,
    ApiKeyCreateResponse,
    ApiKeySummary,
    ApiKeyUpdate,
    DatabentoKeyCreate,
    DatabentoKeySummary,
    DatabentoKeyTestResult,
    DatabentoKeyUpdate,
    PipelineRunSummary,
    RefreshTokenResponse,
    SystemStatus,
)
from app.config import get_settings
from app.core.crypto import decrypt_secret, encrypt_secret, mask_prefix
from app.core.logging import get_logger
from app.core.security import (
    api_key_lookup_digest,
    constant_time_string_compare,
    create_jwt_token,
    decode_jwt_token_allow_grace,
    display_prefix,
    generate_api_key,
    hash_api_key,
    verify_password,
)
from app.db.models import (
    AdminAuditEvent,
    ApiKey,
    ComputedMetric,
    DatabentoApiKey,
    DeadLetterEntry,
    FlowEvent,
    FuturesTick,
    JwtRevocation,
    OptionsChain,
    PipelineRun,
)
from app.db.session import get_db
from app.ingestion.databento_live import get_live_ingester
from app.ingestion.dlq import get_dlq
from app.ingestion.writer import get_writer
from app.processing import pipeline_runtime_flags
from app.processing.pipeline import get_pipeline_counters
from app.processing.scheduler import get_pipeline_state

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Login ────────────────────────────────────────────────────────────────────

@router.post(
    "/login",
    response_model=AdminLoginResponse,
    dependencies=[Depends(rate_limit(5, 60, key="admin_login"))],
)
async def admin_login(payload: AdminLoginRequest) -> AdminLoginResponse:
    settings = get_settings()
    # The admin password is bootstrapped from env. We accept either:
    #   1) a plaintext value matching ADMIN_PASSWORD, or
    #   2) a bcrypt-hashed value matching the admin password (for prod).
    is_hash = settings.admin_password.startswith("$2")
    # Always run the password check BEFORE comparing usernames so a bad
    # username and a bad password take the same CPU time. Comparing the
    # username first short-circuits bcrypt and lets an attacker enumerate
    # valid usernames in <5 attempts via a timing oracle.
    if is_hash:
        password_ok = verify_password(payload.password, settings.admin_password)
    else:
        # Length-independent compare (Rev 8 SEC-5). ``hmac.compare_digest``
        # on the raw plaintext leaks the configured password's byte
        # length: if the candidate and configured strings differ in
        # length, the function exits early and the timing differential
        # is observable across enough samples. Hashing both sides to a
        # fixed-width SHA-256 buffer first makes every compare exactly
        # 32 bytes, regardless of caller-supplied input length.
        password_ok = constant_time_string_compare(
            payload.password, settings.admin_password
        )
    username_ok = constant_time_string_compare(
        payload.username, settings.admin_username
    )
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    token = create_jwt_token(settings.admin_username)
    return AdminLoginResponse(
        access_token=token, expires_in_seconds=settings.jwt_expire_minutes * 60
    )


@router.post("/logout", status_code=204, response_class=Response)
async def admin_logout(
    payload: Annotated[dict, Depends(admin_token_payload)],
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Server-side revocation of the presented admin JWT (Rev 8 SEC-2).

    Inserts the token's ``jti`` into ``jwt_revocations`` so subsequent
    requests carrying the same token are rejected by
    :func:`app.api.deps.authenticate_admin`. Idempotent — re-revoking
    is a no-op (unique-violation on ``jti`` is swallowed).
    """
    jti = payload.get("jti")
    if not jti:
        # Tokens minted before SEC-2 carry no ``jti``; we cannot
        # selectively revoke them. The 60-minute TTL bounds the leak
        # window. Return success-ish 204 so clients can pretend they
        # logged out without reasoning about the legacy state.
        return Response(status_code=204)

    exp_claim = payload.get("exp")
    expires_at = (
        datetime.fromtimestamp(int(exp_claim), tz=UTC)
        if exp_claim is not None
        else datetime.now(UTC) + timedelta(hours=24)
    )

    existing = await session.execute(
        select(JwtRevocation.id).where(JwtRevocation.jti == jti)
    )
    if existing.scalar_one_or_none() is not None:
        return Response(status_code=204)

    session.add(
        JwtRevocation(
            jti=jti,
            revoked_at=datetime.now(UTC),
            expires_at=expires_at,
        )
    )
    try:
        await session.commit()
    except Exception:  # noqa: BLE001 - racing logouts on the same token
        await session.rollback()
    return Response(status_code=204)


# ── Refresh (Rev 13 FE-3) ───────────────────────────────────────────────────


_refresh_bearer_scheme = HTTPBearer(auto_error=False)


@router.post(
    "/refresh-token",
    response_model=RefreshTokenResponse,
    dependencies=[Depends(rate_limit(30, 60, key="admin_refresh"))],
)
async def admin_refresh_token(
    creds: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(_refresh_bearer_scheme),
    ],
    session: AsyncSession = Depends(get_db),
) -> RefreshTokenResponse:
    """Mint a new admin JWT from a still-valid (or recently-expired) one.

    JWT lifetime was shortened to 60 min in Rev 8 SEC-2; without a
    refresh path the operator has to re-enter credentials every hour.
    This endpoint accepts the OLD bearer token, verifies signature,
    ``typ``, and revocation, then mints a fresh token with a new
    ``jti`` and ``exp = now + JWT_EXPIRE_MINUTES``. The old ``jti`` is
    inserted into ``jwt_revocations`` in the same transaction so the
    rotated credential cannot be reused (defence-in-depth: the new
    token still has its own short lifetime, but adding to the denylist
    closes the "two valid tokens at once" window).

    Acceptance window: the old token may be either still within its
    ``exp`` claim, OR expired within the last
    ``JWT_REFRESH_GRACE_SECONDS`` (default 300s = 5 min). The grace
    path absorbs clock drift and lets a consumer that was idle across
    the expiry boundary refresh without a re-login.

    Rate limit: 30/min/IP — generous enough that a consumer retrying
    on transient network failure never trips it, tight enough that a
    stolen token can't be used to mint a fleet of refreshed copies
    before the operator notices and revokes the originator.
    """
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    settings = get_settings()
    try:
        payload = decode_jwt_token_allow_grace(
            creds.credentials, settings.jwt_refresh_grace_seconds
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired beyond refresh grace window",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        ) from exc

    sub = payload.get("sub")
    if sub != settings.admin_username:
        # Same posture as ``authenticate_admin`` — non-admin sub never
        # gets to refresh.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not an admin user"
        )

    # Reject already-revoked tokens. Without this an attacker who
    # captured a logged-out token could refresh it back into a live
    # credential.
    old_jti = payload.get("jti")
    if old_jti is not None:
        revoked = await session.execute(
            select(JwtRevocation.id).where(JwtRevocation.jti == old_jti)
        )
        if revoked.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token revoked",
            )

    new_token = create_jwt_token(settings.admin_username)

    # Insert the OLD jti into the revocation table so the rotated
    # credential cannot be reused. Tokens minted before SEC-2 carry no
    # ``jti``; we skip the insert in that case (no row to file under).
    if old_jti is not None:
        exp_claim = payload.get("exp")
        old_expires_at = (
            datetime.fromtimestamp(int(exp_claim), tz=UTC)
            if exp_claim is not None
            else datetime.now(UTC) + timedelta(hours=24)
        )
        # Idempotent — uniqueness on jti means a parallel refresh
        # racing this one will hit unique-violation, which we swallow.
        existing = await session.execute(
            select(JwtRevocation.id).where(JwtRevocation.jti == old_jti)
        )
        if existing.scalar_one_or_none() is None:
            session.add(
                JwtRevocation(
                    jti=old_jti,
                    revoked_at=datetime.now(UTC),
                    expires_at=old_expires_at,
                )
            )
            try:
                await session.commit()
            except Exception:  # noqa: BLE001
                await session.rollback()

    return RefreshTokenResponse(
        access_token=new_token,
        expires_in_seconds=settings.jwt_expire_minutes * 60,
    )


# ── API key CRUD ────────────────────────────────────────────────────────────


def _to_summary(row: ApiKey) -> ApiKeySummary:
    return ApiKeySummary(
        id=row.id,
        key_prefix=row.key_prefix,
        label=row.label,
        allowed_symbols=list(row.allowed_symbols or []),
        created_at=row.created_at,
        expires_at=row.expires_at,
        is_active=row.is_active,
        last_used_at=row.last_used_at,
        usage_count=row.usage_count or 0,
    )


@router.get("/api-keys", response_model=list[ApiKeySummary])
async def list_api_keys(
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> list[ApiKeySummary]:
    rows = (
        await session.execute(
            select(ApiKey)
            .where(ApiKey.deleted_at.is_(None))
            .order_by(ApiKey.created_at.desc())
        )
    ).scalars().all()
    return [_to_summary(r) for r in rows]


@router.post("/api-keys", response_model=ApiKeyCreateResponse, status_code=201)
async def create_api_key(
    payload: ApiKeyCreate,
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> ApiKeyCreateResponse:
    plaintext = generate_api_key()
    record = ApiKey(
        key_hash=hash_api_key(plaintext),
        key_prefix=display_prefix(plaintext),
        key_lookup=api_key_lookup_digest(plaintext),
        label=payload.label,
        allowed_symbols=payload.allowed_symbols,
        expires_at=payload.expires_at,
        is_active=True,
        usage_count=0,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return ApiKeyCreateResponse(key=_to_summary(record), plaintext_key=plaintext)


@router.patch("/api-keys/{key_id}", response_model=ApiKeySummary)
async def update_api_key(
    key_id: UUID,
    payload: ApiKeyUpdate,
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> ApiKeySummary:
    row = await session.get(ApiKey, key_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="API key not found")
    if payload.label is not None:
        row.label = payload.label
    if payload.allowed_symbols is not None:
        row.allowed_symbols = payload.allowed_symbols
    if payload.expires_at is not None:
        row.expires_at = payload.expires_at
    if payload.is_active is not None:
        row.is_active = payload.is_active
    await session.commit()
    await session.refresh(row)
    return _to_summary(row)


@router.delete("/api-keys/{key_id}", status_code=204, response_class=Response)
async def delete_api_key(
    key_id: UUID,
    admin_username: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Soft-delete an API key (Rev 8 SEC-10).

    Sets ``is_active=False`` and stamps ``deleted_at`` instead of hard-
    deleting the row. The auth path treats either signal as
    "revoked", and the surviving row keeps the linkage that
    ``admin_audit_events`` references. An audit entry is appended in
    the same transaction so the actor + key prefix are preserved even
    if the API-key row is later GC'd.
    """
    row = await session.get(ApiKey, key_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="API key not found")
    row.is_active = False
    row.deleted_at = datetime.now(UTC)
    session.add(
        AdminAuditEvent(
            actor_username=admin_username,
            action="api_key_deleted",
            extra_json={
                "api_key_id": str(row.id),
                "key_prefix": row.key_prefix,
                "label": row.label,
            },
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/api-keys/{key_id}/usage")
async def api_key_usage(
    key_id: UUID,
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> dict:
    row = await session.get(ApiKey, key_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(status_code=404, detail="API key not found")
    return {
        "id": str(row.id),
        "label": row.label,
        "key_prefix": row.key_prefix,
        "usage_count": row.usage_count or 0,
        "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None,
        "is_active": row.is_active,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


# ── System status ────────────────────────────────────────────────────────────


def _lag_ms(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - ts).total_seconds() * 1000.0)


@router.get("/system/status", response_model=SystemStatus)
async def system_status(
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> SystemStatus:
    settings = get_settings()
    state = get_pipeline_state()
    writer = get_writer()

    rows_per_symbol: dict[str, int] = {}
    metric_rows_per_symbol: dict[str, int] = {}
    for symbol in settings.supported_symbols:
        chain_count = (
            await session.execute(
                select(func.count())
                .select_from(OptionsChain)
                .where(OptionsChain.symbol == symbol)
            )
        ).scalar_one()
        metric_count = (
            await session.execute(
                select(func.count())
                .select_from(ComputedMetric)
                .where(ComputedMetric.symbol == symbol)
            )
        ).scalar_one()
        rows_per_symbol[symbol] = int(chain_count or 0)
        metric_rows_per_symbol[symbol] = int(metric_count or 0)

    active_keys = (
        await session.execute(
            select(func.count())
            .select_from(ApiKey)
            .where(ApiKey.is_active.is_(True), ApiKey.deleted_at.is_(None))
        )
    ).scalar_one()

    # ── Rev 3 operational telemetry ─────────────────────────────────────────
    futures_latest = (
        await session.execute(select(func.max(FuturesTick.ts)))
    ).scalar_one_or_none()
    opra_latest = (
        await session.execute(select(func.max(OptionsChain.ts)))
    ).scalar_one_or_none()
    dlq_pending = int(
        (
            await session.execute(
                select(func.count()).select_from(DeadLetterEntry)
            )
        ).scalar_one()
        or 0
    )
    cutoff_1h = datetime.now(UTC) - timedelta(hours=1)
    flow_events_last_hour = int(
        (
            await session.execute(
                select(func.count())
                .select_from(FlowEvent)
                .where(FlowEvent.ts > cutoff_1h)
            )
        ).scalar_one()
        or 0
    )

    # Last pipeline run per symbol.
    last_runs: list[PipelineRunSummary] = []
    for sym in settings.supported_symbols:
        row = (
            await session.execute(
                select(PipelineRun)
                .where(PipelineRun.symbol == sym)
                .order_by(desc(PipelineRun.started_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            continue
        last_runs.append(
            PipelineRunSummary(
                symbol=row.symbol,
                started_at=row.started_at,
                finished_at=row.finished_at,
                duration_ms=float(row.duration_ms or 0.0),
                status=row.status or "unknown",
                rows_read=int(row.rows_read or 0),
                metric_rows_written=int(row.metric_rows_written or 0),
                missing_metric_types=list(row.missing_metric_types or []),
                error=row.error,
            )
        )

    try:
        live_diag: dict[str, Any] = get_live_ingester().diagnostics()
    except Exception as exc:  # noqa: BLE001
        live_diag = {"error": str(exc)}

    return SystemStatus(
        pipeline_running=bool(state.last_run),
        last_databento_event=writer.last_event_ts,
        last_compute_per_symbol={
            sym: state.last_run.get(sym) for sym in settings.supported_symbols
        },
        last_compute_duration_ms={
            sym: float(state.last_duration_ms.get(sym, 0.0))
            for sym in settings.supported_symbols
        },
        rows_per_symbol=rows_per_symbol,
        metric_rows_per_symbol=metric_rows_per_symbol,
        active_api_keys=int(active_keys or 0),
        futures_lag_ms=_lag_ms(futures_latest),
        opra_lag_ms=_lag_ms(opra_latest),
        dlq_pending=dlq_pending,
        flow_events_last_hour=flow_events_last_hour,
        last_pipeline_runs=last_runs,
        live_ingester=live_diag,
    )


# ── Prometheus exposition (Rev 6) ────────────────────────────────────────────


def _fmt_metric(name: str, value: float | None, labels: dict[str, str] | None = None) -> str:
    """Render one Prometheus sample line. ``None`` values are skipped."""
    if value is None:
        return ""
    if labels:
        label_str = ",".join(
            f'{k}="{v.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"'
            for k, v in sorted(labels.items())
        )
        return f"{name}{{{label_str}}} {value}\n"
    return f"{name} {value}\n"


@router.get("/metrics", response_class=Response)
async def admin_metrics(
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> Response:
    """Prometheus text-format exposition of the same gauges as ``/system/status``.

    Designed for an external Prometheus scraper. JWT-protected (no anonymous
    scrape) — set up your scraper with the admin token in
    ``authorization`` header. The contract is intentionally narrow:
    only stable gauges, no histograms (those would need an in-process
    metric registry which is out of scope).
    """
    settings = get_settings()
    state = get_pipeline_state()

    futures_latest = (
        await session.execute(select(func.max(FuturesTick.ts)))
    ).scalar_one_or_none()
    opra_latest = (
        await session.execute(select(func.max(OptionsChain.ts)))
    ).scalar_one_or_none()
    dlq_pending = int(
        (
            await session.execute(
                select(func.count()).select_from(DeadLetterEntry)
            )
        ).scalar_one()
        or 0
    )
    active_keys = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ApiKey)
                .where(
                    ApiKey.is_active.is_(True), ApiKey.deleted_at.is_(None)
                )
            )
        ).scalar_one()
        or 0
    )

    # Pool metrics — sync introspection on the underlying SQLAlchemy pool.
    pool = session.bind.sync_engine.pool  # type: ignore[union-attr]
    pool_size = getattr(pool, "size", lambda: 0)()
    pool_checked_out = getattr(pool, "checkedout", lambda: 0)()
    pool_overflow = getattr(pool, "overflow", lambda: 0)()

    lines: list[str] = []
    lines.append("# HELP ofa_pipeline_running 1 when the pipeline scheduler has run at least once\n")
    lines.append("# TYPE ofa_pipeline_running gauge\n")
    lines.append(_fmt_metric("ofa_pipeline_running", 1.0 if state.last_run else 0.0))

    lines.append("# HELP ofa_last_compute_age_seconds seconds since last successful pipeline tick\n")
    lines.append("# TYPE ofa_last_compute_age_seconds gauge\n")
    now = datetime.now(UTC)
    for sym in settings.supported_symbols:
        ts = state.last_run.get(sym)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age = (now - ts).total_seconds()
        lines.append(_fmt_metric("ofa_last_compute_age_seconds", age, {"symbol": sym}))

    lines.append("# HELP ofa_db_pool_size configured DB pool size\n")
    lines.append("# TYPE ofa_db_pool_size gauge\n")
    lines.append(_fmt_metric("ofa_db_pool_size", float(pool_size)))

    lines.append("# HELP ofa_db_pool_checked_out connections currently in use\n")
    lines.append("# TYPE ofa_db_pool_checked_out gauge\n")
    lines.append(_fmt_metric("ofa_db_pool_checked_out", float(pool_checked_out)))

    lines.append("# HELP ofa_db_pool_overflow connections beyond pool_size\n")
    lines.append("# TYPE ofa_db_pool_overflow gauge\n")
    lines.append(_fmt_metric("ofa_db_pool_overflow", float(pool_overflow)))

    lines.append("# HELP ofa_dlq_pending dead_letter_queue row count\n")
    lines.append("# TYPE ofa_dlq_pending gauge\n")
    lines.append(_fmt_metric("ofa_dlq_pending", float(dlq_pending)))

    opra_lag = _lag_ms(opra_latest)
    fut_lag = _lag_ms(futures_latest)
    lines.append("# HELP ofa_opra_lag_ms ms since last options_chain row\n")
    lines.append("# TYPE ofa_opra_lag_ms gauge\n")
    lines.append(_fmt_metric("ofa_opra_lag_ms", opra_lag))
    lines.append("# HELP ofa_futures_lag_ms ms since last futures_ticks row\n")
    lines.append("# TYPE ofa_futures_lag_ms gauge\n")
    lines.append(_fmt_metric("ofa_futures_lag_ms", fut_lag))

    lines.append("# HELP ofa_active_api_keys count of active API keys\n")
    lines.append("# TYPE ofa_active_api_keys gauge\n")
    lines.append(_fmt_metric("ofa_active_api_keys", float(active_keys)))

    # REV8 OPS-11 — DLQ ring eviction counter exposed for paging.
    # ``flowgreeks_dlq_evictions_total`` is a monotonic counter incremented
    # by the in-memory ring buffer whenever an append drops the oldest entry
    # because ``ingestion_dlq_max_size`` was reached. Persistent growth here
    # means the bounded ring is silently shedding payloads — that is the
    # signal an operator wants to alert on.
    try:
        dlq_evicted = float(get_dlq().evicted_count())
    except Exception:  # noqa: BLE001
        dlq_evicted = 0.0
    lines.append(
        "# HELP flowgreeks_dlq_evictions_total entries dropped because the "
        "in-memory DLQ ring buffer was full\n"
    )
    lines.append("# TYPE flowgreeks_dlq_evictions_total counter\n")
    lines.append(_fmt_metric("flowgreeks_dlq_evictions_total", dlq_evicted))

    # REV9 DT-12 — surface pipeline observability counters defined in
    # ``app.processing.pipeline._pipeline_counters``. These were emitted
    # in Rev 8 (Lane B) but never exposed via the Prometheus surface;
    # without them, partial-tick / finalize-error / streaming-publish-error
    # signals are invisible to the external scraper.
    try:
        pipeline_counters = get_pipeline_counters()
    except Exception:  # noqa: BLE001
        pipeline_counters = {}
    _COUNTER_HELP: dict[str, str] = {
        "flowgreeks_pipeline_partial_total": (
            "pipeline ticks that finished with status=partial"
        ),
        "pipeline_run_finalize_errors_total": (
            "pipeline_runs finalize attempts that raised before commit"
        ),
        "streaming_publish_errors_total": (
            "WS / SSE publish failures fanning out a pipeline tick"
        ),
    }
    for name, help_text in _COUNTER_HELP.items():
        value = float(pipeline_counters.get(name, 0.0))
        lines.append(f"# HELP {name} {help_text}\n")
        lines.append(f"# TYPE {name} counter\n")
        lines.append(_fmt_metric(name, value))

    body = "".join(lines)
    return Response(content=body, media_type="text/plain; version=0.0.4")


# ── Databento API key pool (Rev 4) ───────────────────────────────────────────


def _to_databento_summary(row: DatabentoApiKey) -> DatabentoKeySummary:
    return DatabentoKeySummary(
        id=row.id,
        label=row.label,
        dataset=row.dataset,
        api_key_prefix=row.api_key_prefix,
        priority=row.priority,
        is_active=row.is_active,
        last_used_at=row.last_used_at,
        last_error_at=row.last_error_at,
        last_error_msg=row.last_error_msg,
        error_count=row.error_count,
        created_at=row.created_at,
    )


@router.get("/databento-keys", response_model=list[DatabentoKeySummary])
async def list_databento_keys(
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> list[DatabentoKeySummary]:
    """Operator-visible Databento key pool, sorted by dataset + priority."""
    rows = (
        await session.execute(
            select(DatabentoApiKey).order_by(
                DatabentoApiKey.dataset, DatabentoApiKey.priority, DatabentoApiKey.id
            )
        )
    ).scalars().all()
    return [_to_databento_summary(r) for r in rows]


@router.post(
    "/databento-keys",
    response_model=DatabentoKeySummary,
    status_code=201,
)
async def create_databento_key(
    payload: DatabentoKeyCreate,
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> DatabentoKeySummary:
    record = DatabentoApiKey(
        label=payload.label.strip(),
        dataset=payload.dataset,
        api_key_encrypted=encrypt_secret(payload.api_key),
        api_key_prefix=mask_prefix(payload.api_key, chars=8),
        priority=payload.priority,
        is_active=payload.is_active,
        error_count=0,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return _to_databento_summary(record)


@router.patch("/databento-keys/{key_id}", response_model=DatabentoKeySummary)
async def update_databento_key(
    key_id: int,
    payload: DatabentoKeyUpdate,
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> DatabentoKeySummary:
    row = await session.get(DatabentoApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Databento key not found")
    if payload.label is not None:
        row.label = payload.label.strip()
    if payload.priority is not None:
        row.priority = payload.priority
    if payload.is_active is not None:
        row.is_active = payload.is_active
        # Disabling does NOT clear error_count — re-enabling means
        # operator manually believes the key is healthy again.
    await session.commit()
    await session.refresh(row)
    return _to_databento_summary(row)


@router.delete(
    "/databento-keys/{key_id}", status_code=204, response_class=Response
)
async def delete_databento_key(
    key_id: int,
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> Response:
    row = await session.get(DatabentoApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Databento key not found")
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


@router.post(
    "/databento-keys/{key_id}/test", response_model=DatabentoKeyTestResult
)
async def test_databento_key(
    key_id: int,
    _admin: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> DatabentoKeyTestResult:
    """Light-weight sanity check that the encrypted key can be decrypted.

    A *real* network probe against Databento would require their CDN
    to confirm the key, which we don't want to do from a synchronous
    HTTP endpoint. The ingester records auth/connect errors against
    ``error_count`` so the operator can see them on the listing.

    Stamps ``databento_api_keys.last_test_at`` regardless of outcome
    (Rev 9 DT-12) so the admin UI can show "last verified at" alongside
    the row's other diagnostics.
    """
    row = await session.get(DatabentoApiKey, key_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Databento key not found")
    row.last_test_at = datetime.now(UTC)
    try:
        decrypt_secret(row.api_key_encrypted)
    except Exception as exc:  # noqa: BLE001
        # SEC-7: don't echo the Fernet exception class / message — it
        # leaks library internals to the admin UI and any HTTP cache
        # in front of it. Log the structured exception side-side; the
        # API caller gets a stable generic message that points at the
        # right operator action (rotate ``DB_ENCRYPTION_KEY``, the
        # post-Rev-5 keying var — NOT ``JWT_SECRET``).
        logger.exception(
            "databento_key_decrypt_failed",
            databento_key_id=key_id,
            label=row.label,
            error_type=type(exc).__name__,
        )
        await session.commit()
        return DatabentoKeyTestResult(
            ok=False,
            message=(
                "Decryption failed. Verify DB_ENCRYPTION_KEY has not "
                "been rotated since this key was registered."
            ),
        )
    await session.commit()
    return DatabentoKeyTestResult(
        ok=True,
        message=(
            "Stored key decrypts cleanly. "
            "Live verification is performed by the ingester on the next connect."
        ),
    )


# ── Pipeline runtime control (Rev 12 SRE-19) ────────────────────────────────
#
# Small admin surface that flips runtime flags consulted by the pipeline.
# The pipeline-side check is wired by the lane that owns
# ``app/processing/pipeline.py``; this surface is responsible for the
# operator-visible side. Flags persist for the lifetime of the process —
# a restart resets them. Audited via ``admin_audit_events``.
#
# Rev 12 status: ``run_pipeline_for_symbol`` reads ``is_paused()`` at the
# top of each tick and downgrades to ``status=partial`` with
# ``error="pipeline_paused=True"`` when the flag is set. Per-calculator
# ``is_calculator_skipped(name)`` is exposed for future selective
# disabling but the pipeline does not yet branch on it (out of scope:
# would require per-calculator try/except in ``_compute_metrics``).


class _PipelineSkipRequest(BaseModel):
    """Body for /admin/pipeline/skip-calculator."""

    calculator: str = Field(min_length=1, max_length=64)


@router.post("/pipeline/pause", status_code=200)
async def pipeline_pause(
    admin_username: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Pause the processing pipeline (Rev 12 SRE-19).

    The pipeline tick sees ``pipeline_runtime_flags.is_paused() is True``
    and short-circuits the calculator phase. Audit + heartbeat
    persistence still happen so observability stays intact. Idempotent —
    re-pausing is a no-op.
    """
    pipeline_runtime_flags.set_paused(True)
    session.add(
        AdminAuditEvent(
            actor_username=admin_username,
            action="pipeline_paused",
            extra_json={},
        )
    )
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
    return {
        "paused": True,
        "skipped_calculators": pipeline_runtime_flags.snapshot_skipped(),
    }


@router.post("/pipeline/resume", status_code=200)
async def pipeline_resume(
    admin_username: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Resume a paused pipeline. Idempotent."""
    pipeline_runtime_flags.set_paused(False)
    session.add(
        AdminAuditEvent(
            actor_username=admin_username,
            action="pipeline_resumed",
            extra_json={},
        )
    )
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
    return {
        "paused": False,
        "skipped_calculators": pipeline_runtime_flags.snapshot_skipped(),
    }


@router.post("/pipeline/skip-calculator", status_code=200)
async def pipeline_skip_calculator(
    payload: _PipelineSkipRequest,
    admin_username: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Add a calculator name to the runtime skip set (Rev 12 SRE-19).

    Names are matched exactly (case-sensitive) against the calculator
    discriminator the pipeline knows. Common values: ``hiro``, ``gex``,
    ``vanna_charm``, ``zero_gamma``, ``flow_events``, ``walls``,
    ``max_pain``, ``regime``, ``term_structure``, ``move_tracker``,
    ``pin_probability``, ``zero_dte``, ``futures_levels``, ``basis``,
    ``volume_profile``, ``iv``. The skipped calculator's ``metric_type``
    rows will be missing from the next tick — this downgrades the run
    status to ``partial``, which is intentional so the operator sees the
    skip in ``/admin/system/status``.
    """
    name = payload.calculator.strip()
    if not name:
        raise HTTPException(status_code=400, detail="calculator must not be empty")
    pipeline_runtime_flags.add_skipped_calculator(name)
    session.add(
        AdminAuditEvent(
            actor_username=admin_username,
            action="pipeline_calculator_skipped",
            extra_json={"calculator": name},
        )
    )
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
    return {
        "paused": pipeline_runtime_flags.is_paused(),
        "skipped_calculators": pipeline_runtime_flags.snapshot_skipped(),
    }


@router.post("/pipeline/unskip-calculator", status_code=200)
async def pipeline_unskip_calculator(
    payload: _PipelineSkipRequest,
    admin_username: Annotated[str, Depends(authenticate_admin)],
    session: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Remove a calculator name from the runtime skip set."""
    name = payload.calculator.strip()
    pipeline_runtime_flags.remove_skipped_calculator(name)
    session.add(
        AdminAuditEvent(
            actor_username=admin_username,
            action="pipeline_calculator_unskipped",
            extra_json={"calculator": name},
        )
    )
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
    return {
        "paused": pipeline_runtime_flags.is_paused(),
        "skipped_calculators": pipeline_runtime_flags.snapshot_skipped(),
    }


@router.get("/pipeline/runtime-flags", status_code=200)
async def pipeline_runtime_flags_status(
    _admin: Annotated[str, Depends(authenticate_admin)],
) -> dict[str, Any]:
    """Read-only snapshot of the runtime flags."""
    return {
        "paused": pipeline_runtime_flags.is_paused(),
        "skipped_calculators": pipeline_runtime_flags.snapshot_skipped(),
    }
