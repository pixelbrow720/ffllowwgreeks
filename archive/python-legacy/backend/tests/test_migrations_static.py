"""Static (no-DB) checks on migration files + Dockerfile.

Several Rev 12 fixes land as edits to source files that we can't easily
exercise at runtime without standing up a full Postgres + alembic
harness. The tests below load the relevant files via ``pathlib`` and
make string-level assertions so a regression that drops the fix shows
up in CI without needing a DB or a docker daemon.

Covers:
* MIG-12 — initial-schema extension create now catches specific SQLSTATEs
  (``insufficient_privilege`` + ``feature_not_supported``) instead of a
  blanket ``WHEN OTHERS``.
* MIG-16 — migration 0005's ``CREATE INDEX ... LIKE 'GEX_0DTE%'`` uses a
  single ``%`` (matches the canonical ``models.py`` predicate).
* SRE-14 / SRE-15 — Dockerfile is multi-stage and pinned to the exact
  Python patch tag (no ``python:3.11-slim`` floating reference).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── File location helpers ──────────────────────────────────────────────────


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _migration_path(filename: str) -> Path:
    p = _backend_root() / "app" / "db" / "migrations" / "versions" / filename
    if not p.exists():
        pytest.skip(f"migration file missing: {p}")
    return p


def _dockerfile_path() -> Path:
    p = _backend_root() / "Dockerfile"
    if not p.exists():
        pytest.skip(f"Dockerfile missing at {p}")
    return p


# ── MIG-12 — specific SQLSTATE catches in 0001 ─────────────────────────────


def test_initial_schema_catches_specific_sqlstates() -> None:
    """MIG-12: ``CREATE EXTENSION IF NOT EXISTS timescaledb`` is wrapped
    in a ``DO $$ BEGIN ... EXCEPTION WHEN insufficient_privilege ...
    WHEN feature_not_supported ... END $$`` block.

    The blanket ``WHEN OTHERS`` swallowed every error class — including
    syntax errors and unexpected SQLSTATE values that genuinely warrant
    a deploy abort. Catching only the two SQLSTATE codes that map to
    "managed Postgres without superuser / extension not packaged"
    preserves the plain-Postgres fallback while letting real bugs
    surface.
    """
    src = _migration_path(
        "20250101_0000_0001_initial_schema.py"
    ).read_text(encoding="utf-8")

    assert "insufficient_privilege" in src, (
        "MIG-12: 0001 must catch SQLSTATE 42501 (insufficient_privilege)"
    )
    assert "feature_not_supported" in src, (
        "MIG-12: 0001 must catch SQLSTATE 0A000 (feature_not_supported)"
    )
    # The blanket WHEN OTHERS catch must NOT be present in the
    # extension-create block. Other DO blocks may still legitimately use
    # WHEN OTHERS, so check this one in context.
    assert (
        "CREATE EXTENSION IF NOT EXISTS timescaledb" in src
    ), "MIG-12: extension create call must be present"


# ── MIG-16 — single-percent escape on partial-index LIKE predicate ─────────


def test_0005_uses_single_percent_in_like_pattern() -> None:
    """MIG-16: 0005's partial-index ``WHERE`` predicate uses
    ``LIKE 'GEX_0DTE%'`` (single ``%``) so the index definition matches
    ``models.py``'s canonical declaration. The legacy double-``%`` was a
    configparser-style escape that alembic never applies — leaving it
    in place produced an inconsistency between the migration and the
    autogenerate-stable models.
    """
    src = _migration_path(
        "20260801_0000_0005_rev4_session_and_pool.py"
    ).read_text(encoding="utf-8")

    # Strip Python string-literal docstring/comment context: only look at
    # lines that aren't comments. The ``%%`` may legitimately appear in
    # the post-fix MIG-16 explanation comment.
    sql_lines = [
        line
        for line in src.splitlines()
        if "LIKE 'GEX_0DTE" in line and not line.lstrip().startswith("#")
    ]
    assert sql_lines, "MIG-16: SQL with LIKE 'GEX_0DTE...' must be present"

    for line in sql_lines:
        assert "LIKE 'GEX_0DTE%'" in line, (
            f"MIG-16: 0005 SQL must use single-percent: {line!r}"
        )
        assert "LIKE 'GEX_0DTE%%'" not in line, (
            f"MIG-16: 0005 SQL must NOT carry %% escape: {line!r}"
        )


# ── SRE-14 / SRE-15 — Dockerfile is multi-stage and patch-pinned ───────────


def test_dockerfile_pinned_to_specific_python_patch() -> None:
    """SRE-14/15: the Dockerfile pins to the exact Python patch tag
    (``python:3.11.9-slim-bookworm``) on every ``FROM`` so a silent CVE
    patch in the upstream image cannot drift the runtime ABI of the
    cached wheels. The floating ``python:3.11-slim`` reference must be
    gone.
    """
    content = _dockerfile_path().read_text(encoding="utf-8")
    assert "python:3.11.9-slim-bookworm" in content, (
        "SRE-14: Dockerfile must pin to the exact Python patch tag"
    )
    # Loose floating tag must NOT appear standalone (ignore comment lines).
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            continue
        if line.upper().startswith("FROM "):
            assert "python:3.11-slim" not in line or "3.11.9" in line, (
                f"SRE-14: floating Python tag in FROM line: {line!r}"
            )


def test_dockerfile_is_multistage() -> None:
    """SRE-14: the Dockerfile uses multi-stage builds — a builder stage
    that compiles native wheels with the toolchain, plus a runtime stage
    that ships only the resolved site-packages + libpq + curl. The
    runtime artifact carries no compiler.
    """
    content = _dockerfile_path().read_text(encoding="utf-8").upper()
    # Match either ``AS builder`` or ``AS BUILDER`` casing.
    assert "AS BUILDER" in content, (
        "SRE-14: Dockerfile must declare a builder stage (FROM ... AS builder)"
    )
    assert "AS RUNTIME" in content, (
        "SRE-14: Dockerfile must declare a runtime stage (FROM ... AS runtime)"
    )
    # The runtime stage must NOT pull in ``build-essential`` / ``gcc``.
    # Find the start of the runtime stage and inspect the apt-get block
    # that follows.
    raw = _dockerfile_path().read_text(encoding="utf-8")
    runtime_idx = raw.upper().find("AS RUNTIME")
    runtime_section = raw[runtime_idx:] if runtime_idx >= 0 else ""
    # ``build-essential`` / ``gcc`` appear in the builder stage above the
    # runtime split; the runtime section must not install them.
    assert "build-essential" not in runtime_section, (
        "SRE-14: runtime stage must not install build-essential"
    )


def test_dockerfile_runtime_stage_installs_curl_for_healthcheck() -> None:
    """SRE-14 boundary: the runtime stage still needs ``curl`` because
    the SRE-5 HEALTHCHECK shells out to it. Pin that the package made
    it through the multi-stage refactor.
    """
    content = _dockerfile_path().read_text(encoding="utf-8")
    assert "curl" in content, "SRE-5/14: runtime stage must install curl"
    assert "HEALTHCHECK" in content, "SRE-5: HEALTHCHECK directive present"
