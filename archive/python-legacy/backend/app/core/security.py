"""Security primitives: API key generation/hashing and JWT helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import get_settings

API_KEY_PREFIX = "ak_"
API_KEY_RANDOM_BYTES = 24  # 32-char base64-urlsafe-no-pad
API_KEY_DISPLAY_PREFIX_LEN = 11  # "ak_" + 8 chars

# Bcrypt cost factor used for newly issued API keys + admin password hashes.
# 12 rounds ≈ 250ms per hash on a modern x86 server — cheap enough for the
# occasional admin login / API-key rotation, expensive enough that a stolen
# hash dump is impractical to brute-force. bcrypt's library default has been
# 12 since 2017; we pin it explicitly so the cost is auditable here. Existing
# hashes keep their stored cost — bcrypt encodes the cost in the digest, so
# raising this only affects hashes generated *after* the change.
BCRYPT_ROUNDS = 12

# Domain-separated keyed BLAKE2b for the ``api_keys.key_lookup`` column.
# Purpose: an O(1) equality probe that lets ``authenticate_api_key`` skip
# the bcrypt-everyone-with-this-prefix loop. Bcrypt remains the
# authoritative verifier — this column is an *index*, not a credential.
# The fixed key just domain-separates this digest from any other place
# the same plaintext might be hashed (no rotation concerns; if we ever
# need to rotate, generate a new column and migrate lazily on verify).
_API_KEY_LOOKUP_KEY: bytes = b"pantek-waang.api-key-lookup.v1"

# Defaults shipped in the source tree / used by the test suite. These are
# safe for local dev but must NEVER be left in place in production: see
# ``is_default_admin_password`` / ``is_default_jwt_secret``.
DEFAULT_ADMIN_PASSWORD_VALUES = frozenset({"", "changeme"})
DEFAULT_JWT_SECRET_VALUES = frozenset(
    {
        "",
        "dev-only-change-me",
        "test_secret_for_local_dev_only_at_least_32_chars_long",
        "test-secret",
    }
)


def generate_api_key() -> str:
    """Return a fresh plaintext API key. Display prefix = first 11 chars."""
    token = secrets.token_urlsafe(API_KEY_RANDOM_BYTES)
    return f"{API_KEY_PREFIX}{token}"


def display_prefix(api_key: str) -> str:
    return api_key[:API_KEY_DISPLAY_PREFIX_LEN]


def hash_api_key(api_key: str) -> str:
    """Hash an API key with bcrypt at the project-pinned cost factor."""
    return bcrypt.hashpw(
        api_key.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


def api_key_lookup_digest(api_key: str) -> str:
    """Return the BLAKE2b lookup digest for ``api_key`` (hex string).

    Used for O(1) candidate lookup in ``api_keys.key_lookup``. Prefer
    this over the legacy ``key_prefix`` scan whenever possible — prefix
    collisions made every authenticated request pay multiple bcrypt
    verifications when the prefix universe is densely populated.
    """
    return hashlib.blake2b(
        api_key.encode("utf-8"), key=_API_KEY_LOOKUP_KEY, digest_size=32
    ).hexdigest()


def verify_api_key(api_key: str, key_hash: str) -> bool:
    try:
        return bcrypt.checkpw(api_key.encode("utf-8"), key_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    ).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def is_default_admin_password(password: str | None) -> bool:
    """Return True when ``password`` matches a known dev/test default."""
    if password is None:
        return True
    return password in DEFAULT_ADMIN_PASSWORD_VALUES


def is_default_jwt_secret(secret: str | None) -> bool:
    """Return True when ``secret`` is a known dev/test default.

    Used by the startup banner to log a loud WARNING when the operator
    has not rotated the bundled secrets before exposing the server.
    """
    if secret is None:
        return True
    if secret in DEFAULT_JWT_SECRET_VALUES:
        return True
    if len(secret) < 32:
        return True
    return False


ADMIN_TOKEN_TYPE = "admin"


def create_jwt_token(subject: str, *, expires_minutes: int | None = None) -> str:
    """Mint a fresh admin JWT.

    Each token carries a fresh random ``jti`` so it can be individually
    revoked via the ``jwt_revocations`` table (Rev 8 SEC-2). Without
    ``jti``, server-side revocation would have to invalidate every
    token signed by the current key — too coarse for a logout flow.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=expires_minutes or settings.jwt_expire_minutes)
    payload = {
        "sub": subject,
        "typ": ADMIN_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_jwt_token(token: str) -> dict:
    settings = get_settings()
    payload = jwt.decode(
        token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
    )
    # Hard requirement: ``typ`` must equal ``admin``. The legacy carve-out
    # for tokens issued before this guard is gone now that the public
    # session flow has been removed — any cached non-admin token will
    # fail and require re-login. Acceptable.
    if payload.get("typ") != ADMIN_TOKEN_TYPE:
        raise jwt.InvalidTokenError("Wrong token type")
    return payload


def decode_jwt_token_allow_grace(token: str, grace_seconds: int) -> dict:
    """Decode an admin JWT, accepting expiry within the last ``grace_seconds``.

    Used by ``POST /admin/refresh-token`` (Rev 13 FE-3). The standard
    :func:`decode_jwt_token` rejects expired tokens via PyJWT's ``exp``
    enforcement; refresh wants to accept a recently-expired token so a
    consumer that was idle across the expiry boundary (or has minor
    clock drift) can still rotate the credential.

    Behaviour:
    * Within the original ``exp`` window — succeeds (same as the standard
      decoder).
    * Past ``exp`` but ``now - exp <= grace_seconds`` — succeeds, no
      different from the in-window case from the caller's perspective.
    * Past the grace window — raises :class:`jwt.ExpiredSignatureError`
      so the route handler returns 401 like any other expired-token path.
    * ``grace_seconds <= 0`` — falls through to :func:`decode_jwt_token`
      so an operator can disable the grace path without code changes.

    The signature, ``typ`` claim, and algorithm checks remain identical
    to :func:`decode_jwt_token` — grace ONLY softens the ``exp`` check.
    """
    if grace_seconds <= 0:
        return decode_jwt_token(token)
    settings = get_settings()
    # Run the strict decode first so we cover the in-window case without
    # a second round-trip.
    try:
        return decode_jwt_token(token)
    except jwt.ExpiredSignatureError:
        # Re-decode with ``verify_exp=False`` so we can read the claim,
        # then enforce the grace window manually. ``leeway`` would do
        # the same thing in one call but doesn't tell us which branch
        # we hit, which we want for log clarity.
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_exp": False},
        )
        if payload.get("typ") != ADMIN_TOKEN_TYPE:
            raise jwt.InvalidTokenError("Wrong token type") from None
        exp_claim = payload.get("exp")
        if exp_claim is None:
            # No exp = no way to check the grace window. Reject so a
            # malformed token can't slip through.
            raise jwt.InvalidTokenError("Token missing exp claim") from None
        try:
            exp_dt = datetime.fromtimestamp(int(exp_claim), tz=UTC)
        except (TypeError, ValueError) as exc:
            raise jwt.InvalidTokenError("Token exp claim malformed") from exc
        delta = (datetime.now(UTC) - exp_dt).total_seconds()
        if delta > grace_seconds:
            # Expired beyond the grace window — let the original
            # ExpiredSignatureError propagate so the caller's 401
            # branch fires.
            raise
        return payload


# ── Fixed-length constant-time compare (Rev 8 SEC-5) ─────────────────────────


def _sha256(value: str) -> bytes:
    """Return the SHA-256 digest of ``value`` as raw bytes (32-byte buffer)."""
    return hashlib.sha256(value.encode("utf-8")).digest()


def constant_time_string_compare(candidate: str, expected: str) -> bool:
    """Length-independent constant-time string compare.

    ``hmac.compare_digest`` runs in constant time *for inputs of the
    same length* but reveals length via early exit when they differ.
    For the admin-login plaintext path that means an attacker can
    enumerate the configured password's byte length one probe at a
    time. Hashing both sides to a fixed-width SHA-256 buffer first
    closes that side channel — every comparison is exactly 32 bytes
    regardless of caller-supplied input.

    Not a replacement for bcrypt: collisions on SHA-256 are
    cryptographically infeasible but the digest itself is fast, so do
    NOT use this against a stored hash.
    """
    return hmac.compare_digest(_sha256(candidate), _sha256(expected))
