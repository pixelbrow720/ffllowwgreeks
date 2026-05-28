"""Snapshot prime cache TTL + write-through invariants.

Pins the contract for the in-memory cache populated by the pipeline tick
and consumed by the WS / SSE primer (Rev 6 #5):

* ``set_cached_snapshot`` then ``get_cached_snapshot`` returns the value
  while inside the TTL window.
* Once the monotonic clock advances past the TTL the cache returns None
  and evicts the entry.
* Different symbol keys are isolated.
* Write-through: the pipeline-side ``set_cached_snapshot`` produces a
  fresh entry that the streaming primer reads.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.api.endpoints import snapshot as snapshot_mod


@pytest.fixture(autouse=True)
def _reset_cache():
    snapshot_mod.reset_snapshot_cache_for_tests()
    yield
    snapshot_mod.reset_snapshot_cache_for_tests()


def _patch_monotonic(monkeypatch: pytest.MonkeyPatch, value: float) -> None:
    import time as time_mod

    monkeypatch.setattr(time_mod, "monotonic", lambda: value)


def test_set_then_get_within_ttl_returns_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_monotonic(monkeypatch, 1000.0)
    payload = {"gex": {"net_total": 1.0}}
    computed_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    snapshot_mod.set_cached_snapshot("SPXW", payload, computed_at)

    _patch_monotonic(monkeypatch, 1005.0)
    cached = snapshot_mod.get_cached_snapshot("SPXW")

    assert cached is not None
    got_payload, got_at = cached
    assert got_payload == payload
    assert got_at == computed_at


def test_get_after_ttl_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_monotonic(monkeypatch, 2000.0)
    snapshot_mod.set_cached_snapshot("SPXW", {"gex": {}}, None)

    _patch_monotonic(
        monkeypatch, 2000.0 + snapshot_mod._SNAPSHOT_CACHE_TTL_SECONDS + 0.01
    )
    assert snapshot_mod.get_cached_snapshot("SPXW") is None


def test_get_after_ttl_evicts_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_monotonic(monkeypatch, 3000.0)
    snapshot_mod.set_cached_snapshot("SPXW", {"gex": {}}, None)
    assert "SPXW" in snapshot_mod._snapshot_cache

    _patch_monotonic(
        monkeypatch, 3000.0 + snapshot_mod._SNAPSHOT_CACHE_TTL_SECONDS + 1.0
    )
    snapshot_mod.get_cached_snapshot("SPXW")
    assert "SPXW" not in snapshot_mod._snapshot_cache


def test_different_symbol_keys_are_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_monotonic(monkeypatch, 4000.0)
    snapshot_mod.set_cached_snapshot("SPXW", {"gex": {"net_total": 1.0}}, None)
    snapshot_mod.set_cached_snapshot("NDXP", {"gex": {"net_total": 2.0}}, None)

    spxw = snapshot_mod.get_cached_snapshot("SPXW")
    ndxp = snapshot_mod.get_cached_snapshot("NDXP")

    assert spxw is not None and spxw[0]["gex"]["net_total"] == 1.0
    assert ndxp is not None and ndxp[0]["gex"]["net_total"] == 2.0


def test_symbol_lookup_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_monotonic(monkeypatch, 5000.0)
    snapshot_mod.set_cached_snapshot("spxw", {"gex": {}}, None)
    assert snapshot_mod.get_cached_snapshot("SPXW") is not None
    assert snapshot_mod.get_cached_snapshot("SpXw") is not None


def test_write_through_overwrites_stale_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_monotonic(monkeypatch, 6000.0)
    first_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    snapshot_mod.set_cached_snapshot("SPXW", {"v": 1}, first_at)

    _patch_monotonic(monkeypatch, 6005.0)
    second_at = datetime(2026, 5, 1, 12, 1, tzinfo=UTC)
    snapshot_mod.set_cached_snapshot("SPXW", {"v": 2}, second_at)

    cached = snapshot_mod.get_cached_snapshot("SPXW")
    assert cached is not None
    payload, computed_at = cached
    assert payload == {"v": 2}
    assert computed_at == second_at


def test_get_on_unset_symbol_returns_none() -> None:
    assert snapshot_mod.get_cached_snapshot("SPXW") is None


def test_reset_clears_all_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_monotonic(monkeypatch, 7000.0)
    snapshot_mod.set_cached_snapshot("SPXW", {}, None)
    snapshot_mod.set_cached_snapshot("NDXP", {}, None)
    snapshot_mod.reset_snapshot_cache_for_tests()
    assert snapshot_mod.get_cached_snapshot("SPXW") is None
    assert snapshot_mod.get_cached_snapshot("NDXP") is None


# ──────────────────────────────────────────────────────────────────────────
# Rev 8 OPS-4 — single-flight cold-prime invariants.
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ops4_single_flight_collapses_concurrent_builds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N concurrent cold-prime callers must trigger ONE build, not N.

    Reconnect-storm regression: without single-flight, each WS / SSE
    primer fires its own ``build_snapshot_payload`` round-trip against
    ``computed_metrics``.
    """
    import asyncio

    call_count = {"n": 0}

    async def fake_build(session, symbol):  # noqa: ARG001
        call_count["n"] += 1
        # Yield once so concurrent callers stack on the future.
        await asyncio.sleep(0)
        return {"v": symbol}, None

    monkeypatch.setattr(snapshot_mod, "build_snapshot_payload", fake_build)

    async def caller():
        return await snapshot_mod.build_snapshot_payload_single_flight(
            None, "SPXW"
        )

    results = await asyncio.gather(*[caller() for _ in range(5)])

    assert call_count["n"] == 1, "expected single-flight to collapse to 1 build"
    for r in results:
        assert r == ({"v": "SPXW"}, None)


@pytest.mark.asyncio
async def test_ops4_single_flight_repopulates_cache_on_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful single-flight build must populate the prime cache."""
    import asyncio

    async def fake_build(session, symbol):  # noqa: ARG001
        await asyncio.sleep(0)
        return {"v": "filled"}, None

    monkeypatch.setattr(snapshot_mod, "build_snapshot_payload", fake_build)

    payload, _ = await snapshot_mod.build_snapshot_payload_single_flight(
        None, "SPXW"
    )
    assert payload == {"v": "filled"}

    cached = snapshot_mod.get_cached_snapshot("SPXW")
    assert cached is not None
    assert cached[0] == {"v": "filled"}


@pytest.mark.asyncio
async def test_ops4_single_flight_releases_future_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception in the build path must clear the in-flight slot so
    a subsequent caller can retry instead of awaiting a dead future."""

    raise_next = {"flag": True}

    async def fake_build(session, symbol):  # noqa: ARG001
        if raise_next["flag"]:
            raise RuntimeError("transient")
        return {"v": "later"}, None

    monkeypatch.setattr(snapshot_mod, "build_snapshot_payload", fake_build)

    with pytest.raises(RuntimeError, match="transient"):
        await snapshot_mod.build_snapshot_payload_single_flight(None, "SPXW")

    raise_next["flag"] = False
    payload, _ = await snapshot_mod.build_snapshot_payload_single_flight(
        None, "SPXW"
    )
    assert payload == {"v": "later"}


# ──────────────────────────────────────────────────────────────────────────
# Rev 8 OPS-13 — health flag derivation
# ──────────────────────────────────────────────────────────────────────────


def test_ops13_health_flag_healthy_for_fresh_spot() -> None:
    payload = {"price": 4500.0, "source": "futures_basis"}
    assert snapshot_mod._classify_health(payload) == "healthy"


def test_ops13_health_flag_stale_when_source_is_stale_cache() -> None:
    payload = {"price": 4500.0, "source": "stale_cache"}
    assert snapshot_mod._classify_health(payload) == "stale_spot"


def test_ops13_health_flag_healthy_when_spot_missing() -> None:
    """No spot block at all → still healthy (the chain pipeline ran but
    the resolver had no inputs); the consumer renders the rest of the
    envelope without the stale-spot banner."""
    assert snapshot_mod._classify_health(None) == "healthy"
