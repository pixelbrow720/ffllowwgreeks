"""End-to-end tests for admin endpoints (Postgres required)."""

from __future__ import annotations

import pytest


async def _login(app_client) -> str:
    resp = await app_client.post(
        "/admin/login", json={"username": "admin", "password": "test-password"}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


async def test_admin_login_success(app_client):
    token = await _login(app_client)
    assert token


async def test_admin_login_wrong_password(app_client):
    resp = await app_client.post(
        "/admin/login", json={"username": "admin", "password": "wrong"}
    )
    assert resp.status_code == 401


async def test_admin_endpoints_require_jwt(app_client):
    resp = await app_client.get("/admin/api-keys")
    assert resp.status_code in (401, 403)


async def test_create_list_update_delete_api_key(app_client):
    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}

    # CREATE
    resp = await app_client.post(
        "/admin/api-keys",
        json={"label": "alpha", "allowed_symbols": ["spxw"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    plaintext = body["plaintext_key"]
    key_id = body["key"]["id"]
    assert plaintext.startswith("ak_")
    assert body["key"]["allowed_symbols"] == ["SPXW"]
    assert body["key"]["is_active"] is True

    # LIST
    resp = await app_client.get("/admin/api-keys", headers=headers)
    assert resp.status_code == 200
    keys = resp.json()
    assert any(k["id"] == key_id for k in keys)

    # UPDATE: deactivate + relabel
    resp = await app_client.patch(
        f"/admin/api-keys/{key_id}",
        json={"label": "alpha-v2", "is_active": False},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["label"] == "alpha-v2"
    assert resp.json()["is_active"] is False

    # USAGE endpoint
    resp = await app_client.get(f"/admin/api-keys/{key_id}/usage", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["usage_count"] == 0

    # DELETE
    resp = await app_client.delete(f"/admin/api-keys/{key_id}", headers=headers)
    assert resp.status_code == 204

    resp = await app_client.get(f"/admin/api-keys/{key_id}/usage", headers=headers)
    assert resp.status_code == 404


async def test_admin_system_status(app_client):
    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.get("/admin/system/status", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert "rows_per_symbol" in body
    assert "active_api_keys" in body
    assert "last_compute_per_symbol" in body


# ── SEC-2: JWT server-side revocation via /admin/logout ─────────────────────


@pytest.mark.asyncio
async def test_logout_revokes_jwt(app_client):
    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}

    # Token works pre-logout.
    resp = await app_client.get("/admin/api-keys", headers=headers)
    assert resp.status_code == 200

    # Logout: 204.
    resp = await app_client.post("/admin/logout", headers=headers)
    assert resp.status_code == 204

    # Token rejected post-logout.
    resp = await app_client.get("/admin/api-keys", headers=headers)
    assert resp.status_code == 401
    assert "revoked" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_logout_idempotent(app_client):
    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}

    # First logout — succeeds.
    resp = await app_client.post("/admin/logout", headers=headers)
    assert resp.status_code == 204
    # Second logout with the same token — still 204 (idempotent), even
    # though authenticate_admin would reject the same token elsewhere.
    resp = await app_client.post("/admin/logout", headers=headers)
    assert resp.status_code == 204


# ── TQ-1 (Rev 9): JWT server-side revocation enforcement ────────────────────
# Pin the three admin-side guarantees from the SEC-2 fix:
#   1. A token whose ``jti`` is in ``jwt_revocations`` is rejected by
#      ``authenticate_admin`` (-> 401).
#   2. ``POST /admin/logout`` actually inserts a row keyed by the token's
#      ``jti`` (idempotency is already covered above; here we look at the DB).
#   3. ``prune_expired_jwt_revocations`` deletes rows whose ``expires_at``
#      is in the past while preserving rows with future expirations.


@pytest.mark.asyncio
async def test_revoked_jti_rejected_by_authenticate_admin(app_client, db_session):
    """Insert a revocation row directly + mint a token with that ``jti``;
    every admin endpoint must respond 401."""
    from datetime import UTC, datetime, timedelta

    from app.core.security import create_jwt_token, decode_jwt_token
    from app.db.models import JwtRevocation

    token = create_jwt_token("admin", expires_minutes=60)
    payload = decode_jwt_token(token)
    jti = payload["jti"]

    db_session.add(
        JwtRevocation(
            jti=jti,
            revoked_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(minutes=60),
        )
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.get("/admin/api-keys", headers=headers)
    assert resp.status_code == 401, resp.text
    assert "revoked" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_admin_logout_inserts_revocation_row(app_client, db_session):
    """``POST /admin/logout`` writes one row to ``jwt_revocations`` keyed
    on the token's ``jti``."""
    from sqlalchemy import select

    from app.core.security import decode_jwt_token
    from app.db.models import JwtRevocation

    token = await _login(app_client)
    payload = decode_jwt_token(token)
    jti = payload["jti"]

    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.post("/admin/logout", headers=headers)
    assert resp.status_code == 204

    rows = (
        await db_session.execute(
            select(JwtRevocation).where(JwtRevocation.jti == jti)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].jti == jti


@pytest.mark.asyncio
async def test_prune_expired_jwt_revocations_deletes_old_rows(db_session):
    """``prune_expired_jwt_revocations`` removes rows whose ``expires_at``
    is past, but leaves rows whose ``expires_at`` is in the future."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.api.deps import prune_expired_jwt_revocations
    from app.db.models import JwtRevocation

    now = datetime.now(UTC)
    expired = JwtRevocation(
        jti="tq1-prune-expired",
        revoked_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
    )
    fresh = JwtRevocation(
        jti="tq1-prune-fresh",
        revoked_at=now,
        expires_at=now + timedelta(hours=1),
    )
    db_session.add_all([expired, fresh])
    await db_session.commit()

    pruned = await prune_expired_jwt_revocations(db_session)
    assert pruned >= 1

    remaining = (
        await db_session.execute(
            select(JwtRevocation.jti).where(
                JwtRevocation.jti.in_(["tq1-prune-expired", "tq1-prune-fresh"])
            )
        )
    ).scalars().all()
    assert "tq1-prune-expired" not in remaining
    assert "tq1-prune-fresh" in remaining


# ── SEC-10: soft delete + audit ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_api_key_is_soft_delete_with_audit(app_client, db_session):
    from sqlalchemy import select

    from app.db.models import AdminAuditEvent, ApiKey

    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await app_client.post(
        "/admin/api-keys",
        json={"label": "soft-delete", "allowed_symbols": ["spxw"]},
        headers=headers,
    )
    assert resp.status_code == 201
    key_id = resp.json()["key"]["id"]

    resp = await app_client.delete(f"/admin/api-keys/{key_id}", headers=headers)
    assert resp.status_code == 204

    # Row still present, but soft-deleted.
    row = await db_session.get(ApiKey, key_id)
    if row is None:
        # Some test DBs may have refreshed: re-issue the query.
        result = await db_session.execute(
            select(ApiKey).where(ApiKey.id == key_id)
        )
        row = result.scalar_one_or_none()
    assert row is not None
    assert row.is_active is False
    assert row.deleted_at is not None

    # Audit row was written.
    audit = (
        await db_session.execute(
            select(AdminAuditEvent)
            .where(AdminAuditEvent.action == "api_key_deleted")
            .order_by(AdminAuditEvent.ts.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    assert audit is not None
    assert audit.actor_username == "admin"
    assert audit.extra_json["api_key_id"] == str(key_id)


# ── SEC-12: ApiKeyCreate validates symbols ──────────────────────────────────


@pytest.mark.asyncio
async def test_create_api_key_rejects_unsupported_symbol(app_client):
    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.post(
        "/admin/api-keys",
        json={"label": "rejected", "allowed_symbols": ["AAPL"]},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text
    assert "AAPL" in resp.text or "unsupported" in resp.text.lower()


# ── SEC-7: Databento test endpoint message ──────────────────────────────────


# ── Databento key pool (Rev 4) ──────────────────────────────────────────────


async def test_databento_key_pool_crud(app_client):
    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}

    # LIST starts empty.
    resp = await app_client.get("/admin/databento-keys", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    # CREATE — OPRA.PILLAR primary.
    resp = await app_client.post(
        "/admin/databento-keys",
        json={
            "label": "Primary OPRA",
            "dataset": "opra.pillar",  # lowercase ok — normalizes
            "api_key": "db-superSecret-12345",
            "priority": 1,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dataset"] == "OPRA.PILLAR"
    assert body["api_key_prefix"].startswith("db-")
    assert "superSecret" not in body["api_key_prefix"]
    key_id = body["id"]

    # CREATE — BOTH fallback.
    resp = await app_client.post(
        "/admin/databento-keys",
        json={
            "label": "Fallback Both",
            "dataset": "both",
            "api_key": "db-otherSecret-67890",
            "priority": 200,
        },
        headers=headers,
    )
    assert resp.status_code == 201

    # CREATE — rejected dataset
    resp = await app_client.post(
        "/admin/databento-keys",
        json={
            "label": "Bad",
            "dataset": "WHATEVER",
            "api_key": "db-x",
            "priority": 1,
        },
        headers=headers,
    )
    assert resp.status_code == 422

    # LIST returns both, ordered.
    resp = await app_client.get("/admin/databento-keys", headers=headers)
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2

    # PATCH priority
    resp = await app_client.patch(
        f"/admin/databento-keys/{key_id}",
        json={"priority": 999, "is_active": False},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["priority"] == 999
    assert resp.json()["is_active"] is False

    # TEST endpoint (decryption sanity check)
    resp = await app_client.post(
        f"/admin/databento-keys/{key_id}/test", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # DELETE
    resp = await app_client.delete(
        f"/admin/databento-keys/{key_id}", headers=headers
    )
    assert resp.status_code == 204

    resp = await app_client.post(
        f"/admin/databento-keys/{key_id}/test", headers=headers
    )
    assert resp.status_code == 404


async def test_databento_key_requires_jwt(app_client):
    resp = await app_client.get("/admin/databento-keys")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_databento_key_test_failure_message_does_not_leak_internals(
    app_client, db_session
):
    """SEC-7: a failed-decrypt response must not echo the Fernet exception
    nor reference ``JWT_SECRET`` (decoupled by Rev 5)."""
    from app.db.models import DatabentoApiKey

    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}

    # Insert a row with a bogus ciphertext so decrypt_secret raises.
    bogus = DatabentoApiKey(
        label="corrupt",
        dataset="OPRA.PILLAR",
        api_key_encrypted="not-a-fernet-token",
        api_key_prefix="bogus",
        priority=500,
        is_active=True,
        error_count=0,
    )
    db_session.add(bogus)
    await db_session.commit()
    await db_session.refresh(bogus)

    resp = await app_client.post(
        f"/admin/databento-keys/{bogus.id}/test", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    msg = body["message"]
    assert "JWT_SECRET" not in msg
    assert "DB_ENCRYPTION_KEY" in msg
    # Don't leak Fernet exception class names / cryptography details.
    for forbidden in ("Fernet", "InvalidToken", "Traceback", "cryptography."):
        assert forbidden not in msg


# ── SEC-4: body-size cap ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_oversized_request_body_returns_413(app_client):
    """A 200 KiB body on /admin/login must be rejected before bcrypt
    runs."""
    huge = "x" * (200 * 1024)
    resp = await app_client.post(
        "/admin/login", json={"username": "admin", "password": huge}
    )
    assert resp.status_code == 413, resp.text


# ── SEC-9: /health is anonymous-thin; /health/detail requires X-API-Key ─────


@pytest.mark.asyncio
async def test_health_anonymous_response_is_minimal(app_client):
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"status", "ts"}
    assert body["status"] in ("ok", "degraded")


@pytest.mark.asyncio
async def test_health_detail_requires_api_key(app_client):
    resp = await app_client.get("/health/detail")
    assert resp.status_code == 401


# ──────────────────────────────────────────────────────────────────────────
# Rev 11 — SRE-3 /ready endpoint regressions
# ──────────────────────────────────────────────────────────────────────────


async def _create_api_key(app_client) -> str:
    """Mint an API key with SPXW access. Returns the plaintext."""
    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.post(
        "/admin/api-keys",
        json={"label": "rev11-test", "allowed_symbols": ["SPXW"]},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["plaintext_key"]


@pytest.mark.asyncio
async def test_ready_returns_503_when_pipeline_stale(app_client, db_session):
    """SRE-3: when the most recent ``pipeline_runs`` row is older than
    2 × COMPUTE_INTERVAL_SECONDS, ``/ready`` returns 503 with
    ``ready: false``.
    """
    from datetime import UTC, datetime, timedelta

    from app.api.endpoints.health import reset_ready_cache_for_tests
    from app.db.models import PipelineRun

    reset_ready_cache_for_tests()

    # Insert a pipeline_runs row finished 600s ago — well past the
    # 2 × 60 = 120s readiness threshold.
    stale_finish = datetime.now(UTC) - timedelta(seconds=600)
    db_session.add(
        PipelineRun(
            symbol="SPXW",
            started_at=stale_finish - timedelta(seconds=10),
            finished_at=stale_finish,
            duration_ms=10.0,
            status="ok",
        )
    )
    db_session.add(
        PipelineRun(
            symbol="NDXP",
            started_at=stale_finish - timedelta(seconds=10),
            finished_at=stale_finish,
            duration_ms=10.0,
            status="ok",
        )
    )
    await db_session.commit()

    resp = await app_client.get("/ready")
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["ready"] is False


@pytest.mark.asyncio
async def test_ready_returns_200_when_pipeline_fresh(app_client, db_session):
    """SRE-3: a fresh run for every supported symbol → ``/ready`` 200."""
    from datetime import UTC, datetime

    from app.api.endpoints.health import reset_ready_cache_for_tests
    from app.db.models import PipelineRun

    reset_ready_cache_for_tests()

    fresh = datetime.now(UTC)
    for sym in ("SPXW", "NDXP"):
        db_session.add(
            PipelineRun(
                symbol=sym,
                started_at=fresh,
                finished_at=fresh,
                duration_ms=5.0,
                status="ok",
            )
        )
    await db_session.commit()

    resp = await app_client.get("/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ready"] is True
    assert body["last_tick_age_seconds"] is not None
    assert body["last_tick_age_seconds"] < 5.0


@pytest.mark.asyncio
async def test_ready_caches_result_for_5s(app_client, db_session, monkeypatch):
    """SRE-3: rapid back-to-back ``/ready`` requests must hit the cache —
    the underlying pipeline_runs query should fire ONCE for both calls.
    """
    from datetime import UTC, datetime

    from app.api.endpoints import health as health_mod
    from app.db.models import PipelineRun

    health_mod.reset_ready_cache_for_tests()

    fresh = datetime.now(UTC)
    for sym in ("SPXW", "NDXP"):
        db_session.add(
            PipelineRun(
                symbol=sym,
                started_at=fresh,
                finished_at=fresh,
                duration_ms=5.0,
                status="ok",
            )
        )
    await db_session.commit()

    call_count = {"n": 0}
    real_compute = health_mod._compute_ready_payload

    async def counting(session):
        call_count["n"] += 1
        return await real_compute(session)

    monkeypatch.setattr(health_mod, "_compute_ready_payload", counting)

    resp1 = await app_client.get("/ready")
    resp2 = await app_client.get("/ready")
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Cache TTL is 5s — two rapid calls must trigger only one compute.
    assert call_count["n"] == 1


# ──────────────────────────────────────────────────────────────────────────
# Rev 11 — BC-8 deprecation headers
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_iv_endpoint_carries_deprecation_headers_when_skew_present(
    app_client, db_session
):
    """BC-8: the ``/v1/{symbol}/iv`` endpoint emits ``Deprecation`` and
    ``Sunset`` response headers whenever the legacy ``skew`` field is
    populated. The headers signal codegen consumers to migrate to
    ``skew_per_expiry``.
    """
    from datetime import UTC, datetime

    from app.db.models import ComputedMetric

    plaintext = await _create_api_key(app_client)

    # Seed an IV_SKEW row so the legacy ``skew`` field gets populated.
    now = datetime.now(UTC)
    db_session.add(
        ComputedMetric(
            ts=now,
            symbol="SPXW",
            metric_type="IV_SKEW",
            expiration=None,
            value=0.05,
            extra_json={},
        )
    )
    await db_session.commit()

    resp = await app_client.get(
        "/v1/SPXW/iv", headers={"X-API-Key": plaintext}
    )
    assert resp.status_code == 200, resp.text
    # ``skew`` populated → deprecation headers must be present.
    assert "Deprecation" in resp.headers
    assert "Sunset" in resp.headers


# ──────────────────────────────────────────────────────────────────────────
# Rev 11 — BC-11 HIRO bucket vocabulary
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_hiro_endpoint_accepts_legacy_bucket_form(app_client):
    """BC-11: ``/v1/{symbol}/hiro?bucket=1m`` must be accepted (legacy
    vocabulary) AND the response field ``bucket`` must echo the
    canonical ``1min`` form so consumers see the unified value.
    """
    plaintext = await _create_api_key(app_client)

    resp = await app_client.get(
        "/v1/SPXW/hiro?bucket=1m", headers={"X-API-Key": plaintext}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Legacy bucket=1m → response.bucket == "1min" (canonical).
    assert body["bucket"] == "1min"


# ──────────────────────────────────────────────────────────────────────────
# Rev 11 — MIG-13 advisory-lock guard
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_migration_advisory_lock_held_during_run(db_session):
    """MIG-13: the alembic migrations advisory lock guards against two
    runners racing on the same database. While one session holds the
    lock, ``pg_try_advisory_lock`` from a separate session must return
    False.

    DB-skipped on TEST_DATABASE_URL absence (the conftest fixture
    auto-skips tests that require the engine_for_tests fixture).
    """
    import os

    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("MIG-13 advisory-lock test requires TEST_DATABASE_URL")

    from sqlalchemy import text

    from app.db.session import get_session_factory

    LOCK_KEY = 5_746_728_934_251

    factory = get_session_factory()
    # Open the lock in session A.
    async with factory() as session_a:
        a_lock = (
            await session_a.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}
            )
        ).scalar()
        assert a_lock is True

        # Try to acquire the same lock in a separate session B —
        # must contend.
        async with factory() as session_b:
            b_lock = (
                await session_b.execute(
                    text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}
                )
            ).scalar()
            assert b_lock is False

        # Release the lock from session A so the test doesn't leak it.
        await session_a.execute(
            text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY}
        )
        await session_a.commit()


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 BC-13 — Accept-Version header capture
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_accept_version_header_does_not_break_request(app_client) -> None:
    """BC-13: the ``Accept-Version`` header is reserved per API_POLICY.md
    § 5 for v1.2+ behavioural toggles. The middleware captures + logs the
    value; behaviourally the request must pass through untouched.
    """
    resp = await app_client.get(
        "/health", headers={"Accept-Version": "v1.2"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "status" in body


@pytest.mark.asyncio
async def test_accept_version_middleware_handles_missing_header(
    app_client,
) -> None:
    """BC-13 negative: no ``Accept-Version`` header → request still works.
    The middleware short-circuits without setting state when absent.
    """
    resp = await app_client.get("/health")
    assert resp.status_code == 200


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 BC-14 — CHANGELOG.json machine-readable
# ──────────────────────────────────────────────────────────────────────────


def test_changelog_json_is_valid_machine_readable() -> None:
    """BC-14: ``CHANGELOG.json`` (repo-root) must parse as valid JSON,
    declare a ``current_version``, and every version entry carries the
    documented keys (breaking / added / changed / date) so codegen
    consumers can read it without parsing markdown.
    """
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "CHANGELOG.json"
    assert path.exists(), f"CHANGELOG.json missing at {path}"

    raw = path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    assert "current_version" in payload
    assert isinstance(payload["current_version"], str) and payload["current_version"]
    assert "versions" in payload
    versions = payload["versions"]
    assert isinstance(versions, list) and len(versions) >= 1

    for entry in versions:
        for key in ("date", "breaking", "added", "changed"):
            assert key in entry, (
                f"BC-14: version {entry.get('version')!r} missing key {key!r}"
            )
        assert isinstance(entry["breaking"], list)
        assert isinstance(entry["added"], list)
        assert isinstance(entry["changed"], list)


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 BC-17 — anonymous /v1/symbols endpoint
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_symbols_endpoint_anonymous_returns_supported_list(
    app_client,
) -> None:
    """BC-17: ``GET /v1/symbols`` is anonymous and returns the
    supported-symbols list configured in ``Settings``. Restores a
    discovery channel for integrators bootstrapping a key request
    without re-exposing the operational telemetry that motivated SEC-9.
    """
    from app.config import get_settings

    resp = await app_client.get("/v1/symbols")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "symbols" in body
    assert isinstance(body["symbols"], list)
    assert set(body["symbols"]) == set(get_settings().supported_symbols)


@pytest.mark.asyncio
async def test_symbols_endpoint_does_not_require_auth(app_client) -> None:
    """BC-17: the endpoint is anonymous — no X-API-Key, no JWT, still 200."""
    resp = await app_client.get("/v1/symbols")
    assert resp.status_code == 200
    assert "symbols" in resp.json()


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 SRE-19 — admin pipeline pause / resume / skip endpoints
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_pause_pipeline_sets_paused_flag(app_client) -> None:
    """SRE-19: ``POST /admin/pipeline/pause`` flips
    ``pipeline_runtime_flags.is_paused()`` to True and returns the
    current state.
    """
    from app.processing import pipeline_runtime_flags

    pipeline_runtime_flags.reset_for_tests()
    assert pipeline_runtime_flags.is_paused() is False

    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.post("/admin/pipeline/pause", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["paused"] is True
    assert pipeline_runtime_flags.is_paused() is True


@pytest.mark.asyncio
async def test_admin_resume_pipeline_clears_paused_flag(app_client) -> None:
    """SRE-19: ``POST /admin/pipeline/resume`` clears the pause flag."""
    from app.processing import pipeline_runtime_flags

    pipeline_runtime_flags.reset_for_tests()
    pipeline_runtime_flags.set_paused(True)
    assert pipeline_runtime_flags.is_paused() is True

    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.post("/admin/pipeline/resume", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["paused"] is False
    assert pipeline_runtime_flags.is_paused() is False


@pytest.mark.asyncio
async def test_admin_skip_calculator_adds_to_skip_set(app_client) -> None:
    """SRE-19: ``POST /admin/pipeline/skip-calculator`` adds the named
    calculator to the runtime skip set; ``is_calculator_skipped`` flips
    True for that name only.
    """
    from app.processing import pipeline_runtime_flags

    pipeline_runtime_flags.reset_for_tests()

    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.post(
        "/admin/pipeline/skip-calculator",
        json={"calculator": "hiro"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "hiro" in body["skipped_calculators"]
    assert pipeline_runtime_flags.is_calculator_skipped("hiro") is True
    # Another calculator name is unaffected.
    assert pipeline_runtime_flags.is_calculator_skipped("gex") is False


@pytest.mark.asyncio
async def test_admin_runtime_flags_get_returns_current_state(
    app_client,
) -> None:
    """SRE-19: ``GET /admin/pipeline/runtime-flags`` reports the current
    paused flag + skipped calculator list.
    """
    from app.processing import pipeline_runtime_flags

    pipeline_runtime_flags.reset_for_tests()
    pipeline_runtime_flags.set_paused(True)
    pipeline_runtime_flags.add_skipped_calculator("vanna_charm")

    token = await _login(app_client)
    headers = {"Authorization": f"Bearer {token}"}
    resp = await app_client.get(
        "/admin/pipeline/runtime-flags", headers=headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["paused"] is True
    assert "skipped_calculators" in body
    assert isinstance(body["skipped_calculators"], list)
    assert "vanna_charm" in body["skipped_calculators"]


@pytest.mark.asyncio
async def test_admin_pipeline_endpoints_require_admin_jwt(app_client) -> None:
    """SRE-19: every pipeline runtime endpoint must be JWT-protected.
    Calling without a Bearer token must produce 401 / 403.
    """
    for method, path, body in [
        ("post", "/admin/pipeline/pause", None),
        ("post", "/admin/pipeline/resume", None),
        (
            "post",
            "/admin/pipeline/skip-calculator",
            {"calculator": "hiro"},
        ),
        ("get", "/admin/pipeline/runtime-flags", None),
    ]:
        if method == "post":
            resp = await app_client.post(path, json=body or {})
        else:
            resp = await app_client.get(path)
        assert resp.status_code in (401, 403), (
            f"SRE-19: {method.upper()} {path} must require admin JWT, "
            f"got {resp.status_code}"
        )

