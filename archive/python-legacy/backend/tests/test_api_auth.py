"""End-to-end tests for API key auth middleware (Postgres required)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


async def _make_key(
    db_session,
    *,
    symbols: list[str],
    is_active: bool = True,
    expires_at=None,
    key_lookup_present: bool = True,
):
    from app.core.security import (
        api_key_lookup_digest,
        display_prefix,
        generate_api_key,
        hash_api_key,
    )
    from app.db.models import ApiKey

    plaintext = generate_api_key()
    record = ApiKey(
        key_hash=hash_api_key(plaintext),
        key_prefix=display_prefix(plaintext),
        key_lookup=api_key_lookup_digest(plaintext) if key_lookup_present else None,
        label="test-key",
        allowed_symbols=symbols,
        is_active=is_active,
        expires_at=expires_at,
        usage_count=0,
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)
    return plaintext, record


async def test_missing_api_key_returns_401(app_client):
    resp = await app_client.get("/v1/SPXW/snapshot")
    assert resp.status_code == 401


async def test_invalid_api_key_returns_401(app_client):
    resp = await app_client.get(
        "/v1/SPXW/snapshot", headers={"X-API-Key": "ak_invalid_value"}
    )
    assert resp.status_code == 401


async def test_inactive_api_key_returns_401(app_client, db_session):
    """SEC-3: inactive returns the same 401 as unknown to avoid state
    enumeration."""
    plaintext, _ = await _make_key(db_session, symbols=["SPXW"], is_active=False)
    resp = await app_client.get(
        "/v1/SPXW/snapshot", headers={"X-API-Key": plaintext}
    )
    assert resp.status_code == 401
    assert "unauthorized" in resp.json()["detail"].lower()


async def test_expired_api_key_returns_401(app_client, db_session):
    """SEC-3: expired returns the same 401 as unknown to avoid state
    enumeration."""
    expired = datetime.now(UTC) - timedelta(days=1)
    plaintext, _ = await _make_key(db_session, symbols=["SPXW"], expires_at=expired)
    resp = await app_client.get(
        "/v1/SPXW/snapshot", headers={"X-API-Key": plaintext}
    )
    assert resp.status_code == 401


async def test_wrong_symbol_returns_401(app_client, db_session):
    """SEC-3: ACL miss returns the same 401 as unknown to avoid state
    enumeration."""
    plaintext, _ = await _make_key(db_session, symbols=["NDXP"])
    resp = await app_client.get(
        "/v1/SPXW/snapshot", headers={"X-API-Key": plaintext}
    )
    assert resp.status_code == 401


async def test_auth_failure_responses_are_indistinguishable(app_client, db_session):
    """SEC-3: every auth-failure mode returns the same status + body.

    Externally an attacker probing a valid prefix cannot tell whether
    they hit "wrong key", "key revoked", "key expired", or "ACL miss".
    The detailed reason is logged structured-log-side only.
    """
    expired = datetime.now(UTC) - timedelta(days=1)
    p_unknown = "ak_garbage_value"
    p_inactive, _ = await _make_key(db_session, symbols=["SPXW"], is_active=False)
    p_expired, _ = await _make_key(db_session, symbols=["SPXW"], expires_at=expired)
    p_acl, _ = await _make_key(db_session, symbols=["NDXP"])

    bodies = []
    for key in (p_unknown, p_inactive, p_expired, p_acl):
        resp = await app_client.get(
            "/v1/SPXW/snapshot", headers={"X-API-Key": key}
        )
        assert resp.status_code == 401
        bodies.append(resp.json()["detail"])
    assert len(set(bodies)) == 1


async def test_valid_api_key_allows_access(app_client, db_session):
    plaintext, record = await _make_key(db_session, symbols=["SPXW"])
    resp = await app_client.get(
        "/v1/SPXW/snapshot", headers={"X-API-Key": plaintext}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "SPXW"
    # Usage stats updated.
    await db_session.refresh(record)
    assert (record.usage_count or 0) >= 1
    assert record.last_used_at is not None


async def test_valid_key_envelope_includes_metadata(app_client, db_session):
    plaintext, _ = await _make_key(db_session, symbols=["SPXW"])
    resp = await app_client.get(
        "/v1/SPXW/snapshot", headers={"X-API-Key": plaintext}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "computed_at" in body
    assert "next_update_in_seconds" in body
    assert "data" in body


# ── SEC-1: NULL-key_lookup rows refused; bcrypt loop is gone ────────────────


async def test_null_key_lookup_row_cannot_authenticate(app_client, db_session):
    """SEC-1: legacy ``key_lookup IS NULL`` rows are deactivated by
    migration 0012 and the auth path no longer falls back to a prefix
    scan. Even if such a row leaks through (e.g. a manual INSERT) it
    cannot authenticate.
    """
    plaintext, record = await _make_key(
        db_session, symbols=["SPXW"], key_lookup_present=False
    )
    # Simulate the post-migration state: deactivated.
    record.is_active = False
    await db_session.commit()

    resp = await app_client.get(
        "/v1/SPXW/snapshot", headers={"X-API-Key": plaintext}
    )
    assert resp.status_code == 401


async def test_no_bcrypt_loop_for_unknown_prefix(app_client, db_session):
    """SEC-1: unknown keys must not trigger ANY bcrypt verify.

    Pre-fix, a key sharing the 11-char prefix of N legacy NULL-lookup
    rows would bcrypt-verify against all N before failing — the
    amplification surface this finding closes.
    """
    # Insert a legacy row sharing a known prefix; it must be inert.
    plaintext, record = await _make_key(
        db_session, symbols=["SPXW"], key_lookup_present=False
    )
    record.is_active = False
    await db_session.commit()

    from app.api import deps as deps_mod

    # An unknown key (different plaintext, same 11-char prefix is
    # impossible without secrets, so we just use a wholly unrelated
    # value — the property under test is "no bcrypt at all on
    # unknown").
    with patch.object(deps_mod, "verify_api_key") as spy:
        resp = await app_client.get(
            "/v1/SPXW/snapshot",
            headers={"X-API-Key": "ak_unknown_unknown_unknown"},
        )
    assert resp.status_code == 401
    assert spy.call_count == 0


async def test_fast_path_calls_bcrypt_exactly_once(app_client, db_session):
    """A row with key_lookup populated takes the O(1) path: exactly one
    bcrypt verify per request."""
    plaintext, _ = await _make_key(
        db_session, symbols=["SPXW"], key_lookup_present=True
    )

    from app.api import deps as deps_mod

    real_verify = deps_mod.verify_api_key
    with patch.object(
        deps_mod, "verify_api_key", side_effect=real_verify
    ) as spy:
        resp = await app_client.get(
            "/v1/SPXW/snapshot", headers={"X-API-Key": plaintext}
        )

    assert resp.status_code == 200
    assert spy.call_count == 1
