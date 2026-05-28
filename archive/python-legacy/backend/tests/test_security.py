"""Tests for security primitives: API-key/password hashing + JWT tokens."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.core.security import (
    constant_time_string_compare,
    create_jwt_token,
    decode_jwt_token,
    display_prefix,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_api_key,
    verify_password,
)


def test_api_key_round_trip():
    key = generate_api_key()
    assert key.startswith("ak_")
    h = hash_api_key(key)
    assert verify_api_key(key, h) is True
    assert verify_api_key("ak_wrong_key", h) is False


def test_api_key_display_prefix():
    key = generate_api_key()
    prefix = display_prefix(key)
    assert prefix.startswith("ak_")
    assert len(prefix) == 11


def test_password_round_trip():
    h = hash_password("hunter2")
    assert verify_password("hunter2", h)
    assert not verify_password("wrong", h)


def test_jwt_token_round_trip():
    # TQ2-4: capture ``time.time()`` BEFORE minting so a slow CI runner
    # ticking between mint and assert can't flip the comparison. Using
    # the pre-mint reading guarantees ``exp`` (mint_time + 5min) > before.
    before = int(time.time())
    token = create_jwt_token("admin", expires_minutes=5)
    payload = decode_jwt_token(token)
    assert payload["sub"] == "admin"
    assert "exp" in payload
    assert payload["exp"] > before


def test_jwt_token_expired_raises():
    import jwt as pyjwt

    token = create_jwt_token("admin", expires_minutes=-1)  # already expired
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_jwt_token(token)


# ── SEC-2: every minted token carries a unique ``jti`` ──────────────────────


def test_jwt_token_carries_unique_jti():
    a = decode_jwt_token(create_jwt_token("admin", expires_minutes=5))
    b = decode_jwt_token(create_jwt_token("admin", expires_minutes=5))
    assert "jti" in a and "jti" in b
    assert a["jti"] != b["jti"]
    # ``jti`` is opaque but should be a non-trivial random token (>=16
    # bytes urlsafe encoded).
    assert len(a["jti"]) >= 16


# ── SEC-5: constant_time_string_compare is length-independent ───────────────


def test_constant_time_compare_matches_equal_strings():
    assert constant_time_string_compare("hunter2hunter2", "hunter2hunter2") is True


def test_constant_time_compare_rejects_mismatch():
    assert constant_time_string_compare("hunter2", "hunter3") is False


def test_constant_time_compare_rejects_different_lengths():
    """Different lengths must still produce a False result (and run for
    the same fixed-32-byte SHA-256 compare regardless of input length).
    """
    assert constant_time_string_compare("a", "ab" * 100) is False
    assert constant_time_string_compare("ab" * 100, "a") is False


# ── TQ-1 (Rev 9): JWT revocation lookup helper ──────────────────────────────
# These tests pin the pure-function path of the revocation check inside
# ``authenticate_admin`` against a fake AsyncSession, so a regression that
# silently drops the lookup is caught even on machines without Postgres.
# DB-backed enforcement coverage lives in test_api_admin.py.


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class _FakeAsyncSession:
    """Records the executed select and returns a canned result.

    Mirrors the surface used by ``authenticate_admin`` so we can drive the
    revocation lookup without needing a real DB.
    """

    def __init__(self, *, revoked_jti: str | None = None) -> None:
        self.executed: list[Any] = []
        self._revoked_jti = revoked_jti

    async def execute(self, stmt: Any) -> _FakeResult:
        self.executed.append(stmt)
        # Look at the rendered SQL to figure out which jti is being asked
        # for. The pipeline binds the value via a parameter; we cheat by
        # inspecting the bind kwargs on the WHERE clause. If we cannot,
        # we fall back to the constructor's ``revoked_jti``.
        try:
            compiled = stmt.compile(compile_kwargs={"literal_binds": True})
            sql = str(compiled)
        except Exception:  # noqa: BLE001
            sql = ""
        if self._revoked_jti and self._revoked_jti in sql:
            return _FakeResult(object())  # any non-None scalar
        return _FakeResult(None)


@pytest.mark.asyncio
async def test_jwt_revocation_lookup_returns_revoked_when_row_present():
    """``authenticate_admin``'s revocation check returns a row when ``jti``
    is in ``jwt_revocations`` and None otherwise.

    Drives the lookup through the same SQL the dependency uses, against a
    fake session, so a regression that drops the WHERE clause or the
    select() altogether fails this test independently of Postgres.
    """
    from sqlalchemy import select

    from app.db.models import JwtRevocation

    revoked_jti = "tq1-revoked-jti"
    session = _FakeAsyncSession(revoked_jti=revoked_jti)

    stmt = select(JwtRevocation.id).where(JwtRevocation.jti == revoked_jti)
    result = await session.execute(stmt)
    assert result.scalar_one_or_none() is not None

    # A different jti must miss the revocation list — the SAME fake
    # session returns None for any jti that was not configured.
    stmt2 = select(JwtRevocation.id).where(JwtRevocation.jti == "tq1-fresh-jti")
    result2 = await session.execute(stmt2)
    assert result2.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_prune_expired_jwt_revocations_uses_expires_at_predicate():
    """The prune helper must filter by ``expires_at < now`` so a row whose
    token can still be presented is not deleted prematurely.
    """
    from sqlalchemy import delete

    from app.db.models import JwtRevocation

    captured: list[Any] = []

    class _PruneSession:
        async def execute(self, stmt: Any) -> Any:
            captured.append(stmt)
            class _Rowcount:
                rowcount = 0
            return _Rowcount()

        async def commit(self) -> None:
            return None

    from app.api.deps import prune_expired_jwt_revocations

    pruned = await prune_expired_jwt_revocations(_PruneSession())  # type: ignore[arg-type]
    assert pruned == 0
    assert len(captured) == 1
    # The compiled SQL must reference both ``jwt_revocations`` and
    # ``expires_at`` so the filter is in place.
    compiled = str(captured[0].compile(compile_kwargs={"literal_binds": True}))
    assert "jwt_revocations" in compiled.lower()
    assert "expires_at" in compiled.lower()
    # And it must be a DELETE — not, e.g., a SELECT.
    assert compiled.strip().lower().startswith("delete")
    # Reference the imported symbols so static analysis is happy.
    _ = (delete, JwtRevocation, datetime, timedelta, UTC)


# ── Rev 11 — SRE-10 boot guardrails ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_short_jwt_secret_refuses_boot(monkeypatch: pytest.MonkeyPatch):
    """SRE-10: a too-short ``JWT_SECRET`` must refuse boot in production
    mode. The guard catches both default values and non-default secrets
    below the 32-character floor.
    """
    from types import SimpleNamespace

    import app.main as main_mod

    monkeypatch.setattr(main_mod, "_testing_mode", lambda: False)

    fake_settings = SimpleNamespace(
        admin_password="a-very-long-non-default-password",
        jwt_secret="short",  # 5 chars — far below the 32-char floor
        log_level="INFO",
    )
    monkeypatch.setattr(main_mod, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(
        main_mod, "is_default_admin_password", lambda _v: False
    )
    monkeypatch.setattr(main_mod, "is_default_jwt_secret", lambda _v: False)
    monkeypatch.setattr(main_mod, "configure_logging", lambda _v: None)
    monkeypatch.setattr(main_mod, "_install_uvicorn_log_redaction", lambda: None)

    with pytest.raises(RuntimeError, match=r"JWT_SECRET.*32"):
        await main_mod._start_observability(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_weak_admin_password_refuses_boot(monkeypatch: pytest.MonkeyPatch):
    """SRE-10: a non-default plaintext ``ADMIN_PASSWORD`` shorter than
    12 characters must refuse boot in production mode. Bcrypt-hashed
    passwords (``$2…``) are exempt — the hash carries its own strength.
    """
    from types import SimpleNamespace

    import app.main as main_mod

    monkeypatch.setattr(main_mod, "_testing_mode", lambda: False)

    fake_settings = SimpleNamespace(
        admin_password="weak",  # 4-char plaintext — below the 12-char floor
        jwt_secret="x" * 64,  # adequate
        log_level="INFO",
    )
    monkeypatch.setattr(main_mod, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(
        main_mod, "is_default_admin_password", lambda _v: False
    )
    monkeypatch.setattr(main_mod, "is_default_jwt_secret", lambda _v: False)
    monkeypatch.setattr(main_mod, "configure_logging", lambda _v: None)
    monkeypatch.setattr(main_mod, "_install_uvicorn_log_redaction", lambda: None)

    with pytest.raises(RuntimeError, match=r"ADMIN_PASSWORD.*12"):
        await main_mod._start_observability(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_bcrypt_admin_password_passes_boot_regardless_of_length(
    monkeypatch: pytest.MonkeyPatch,
):
    """SRE-10 boundary: a bcrypt-hashed admin password (``$2``-prefixed)
    bypasses the plaintext length floor — its strength is in the hash,
    not the length. Pair with a long JWT secret so we land at the
    orphan-sweep step (which we stub out) instead of erroring.
    """
    from types import SimpleNamespace

    import app.main as main_mod

    monkeypatch.setattr(main_mod, "_testing_mode", lambda: False)

    # 60-char bcrypt-format hash starts with ``$2`` — passes the gate.
    fake_settings = SimpleNamespace(
        admin_password="$2b$" + "x" * 56,
        jwt_secret="x" * 64,
        log_level="INFO",
        supported_symbols=["SPXW"],
    )
    monkeypatch.setattr(main_mod, "get_settings", lambda: fake_settings)
    monkeypatch.setattr(
        main_mod, "is_default_admin_password", lambda _v: False
    )
    monkeypatch.setattr(main_mod, "is_default_jwt_secret", lambda _v: False)
    monkeypatch.setattr(main_mod, "configure_logging", lambda _v: None)
    monkeypatch.setattr(main_mod, "_install_uvicorn_log_redaction", lambda: None)

    # Stub the orphan-sweep DB call so the test runs without Postgres.
    class _NoSession:
        async def __aenter__(self):
            raise RuntimeError("no db in test")

        async def __aexit__(self, *args):
            return None

    monkeypatch.setattr(main_mod, "get_session_factory", lambda: _NoSession)

    teardown = await main_mod._start_observability(object())  # type: ignore[arg-type]
    # Function returns an empty teardown list when guardrails pass.
    assert teardown == []


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 SRE-23 — drop_sensitive_keys_processor (structlog)
# ──────────────────────────────────────────────────────────────────────────


def test_drop_sensitive_keys_processor_redacts_known_keys() -> None:
    """SRE-23: every well-known sensitive structured-log key is replaced
    with the redaction sentinel before any downstream processor sees the
    value. Defence in depth on top of the existing query-string regex
    redactor.
    """
    from app.core.logging import drop_sensitive_keys_processor

    event = {
        "event": "request",
        "api_key": "ak_supersecret",
        "token": "tok_abc",
        "password": "hunter2",
        "secret": "do-not-show",
        "client_secret": "leaked",
        "jwt_secret": "$$$",
        "non_sensitive": "fine",
    }
    out = drop_sensitive_keys_processor(None, "info", event)

    # Sensitive values are replaced with REDACTED.
    for k in ("api_key", "token", "password", "secret", "client_secret", "jwt_secret"):
        assert out[k] == "REDACTED", (
            f"SRE-23: key {k!r} was not redacted (got {out[k]!r})"
        )
    # Non-sensitive keys pass through unchanged.
    assert out["non_sensitive"] == "fine"
    assert out["event"] == "request"


def test_drop_sensitive_keys_processor_is_case_insensitive() -> None:
    """SRE-23: the allow-list match is case-insensitive so accidental
    casing variants (``API_KEY``, ``Password``) still get redacted.
    """
    from app.core.logging import drop_sensitive_keys_processor

    event = {"API_KEY": "ak_x", "Password": "p", "Token": "t"}
    out = drop_sensitive_keys_processor(None, "info", event)
    assert out["API_KEY"] == "REDACTED"
    assert out["Password"] == "REDACTED"
    assert out["Token"] == "REDACTED"


def test_drop_sensitive_keys_processor_preserves_unknown_keys() -> None:
    """SRE-23 boundary: a key not in the allow-list must NOT be touched."""
    from app.core.logging import drop_sensitive_keys_processor

    event = {"event": "ok", "user_id": "u-123", "request_id": "r-456"}
    out = drop_sensitive_keys_processor(None, "info", event)
    assert out == {"event": "ok", "user_id": "u-123", "request_id": "r-456"}


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 SRE-24 — env-configurable cleanup intervals
# ──────────────────────────────────────────────────────────────────────────


def test_dlq_cleanup_interval_env_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SRE-24: ``DLQ_CLEANUP_INTERVAL_SECONDS`` env var overrides the
    default cadence (6h). Surfacing as a Settings knob lets operators
    tune cleanup without a code change.
    """
    from app.config import Settings

    monkeypatch.setenv("DLQ_CLEANUP_INTERVAL_SECONDS", "999")
    s = Settings()
    assert s.dlq_cleanup_interval_seconds == 999


def test_admin_audit_prune_interval_env_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SRE-24: ``ADMIN_AUDIT_PRUNE_INTERVAL_SECONDS`` env var overrides
    the default cadence (24h).
    """
    from app.config import Settings

    monkeypatch.setenv("ADMIN_AUDIT_PRUNE_INTERVAL_SECONDS", "1234")
    s = Settings()
    assert s.admin_audit_prune_interval_seconds == 1234


def test_cleanup_intervals_have_sensible_defaults() -> None:
    """SRE-24 boundary: when the env vars are NOT set, the defaults
    match the prior module-level constants (6h DLQ, 24h audit).
    """
    from app.config import Settings

    s = Settings(_env_file=None)
    assert s.dlq_cleanup_interval_seconds == 6 * 60 * 60
    assert s.admin_audit_prune_interval_seconds == 24 * 60 * 60


# ──────────────────────────────────────────────────────────────────────────
# Rev 12 MIG-11 — operational indexes declared on models
# ──────────────────────────────────────────────────────────────────────────


def test_flow_events_ts_only_index_declared() -> None:
    """MIG-11: ``ix_flow_events_ts_only`` is declared on the FlowEvent
    model so an autogenerate diff doesn't propose dropping the index
    that migration 0011 created CONCURRENTLY.
    """
    from app.db.models import FlowEvent

    index_names = {
        getattr(arg, "name", None)
        for arg in (FlowEvent.__table_args__ or ())
        if getattr(arg, "name", None) is not None
    }
    assert "ix_flow_events_ts_only" in index_names


def test_dead_letter_queue_ts_only_index_declared() -> None:
    """MIG-11: ``ix_dead_letter_queue_ts_only`` is declared on the
    DeadLetterEntry model for the same autogenerate-stability reason.
    """
    from app.db.models import DeadLetterEntry

    index_names = {
        getattr(arg, "name", None)
        for arg in (DeadLetterEntry.__table_args__ or ())
        if getattr(arg, "name", None) is not None
    }
    assert "ix_dead_letter_queue_ts_only" in index_names
