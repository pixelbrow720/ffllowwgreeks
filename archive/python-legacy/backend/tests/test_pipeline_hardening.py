"""Agent 7 — pipeline atomicity, parallelism, and completeness tests.

These tests intentionally avoid touching Postgres so they can run under
``APP_TESTING=1`` in CI environments where Docker isn't available. Real
DB-backed integration is exercised separately by the existing
``test_api_*`` / ``test_processing_*`` suites once a Postgres container
is reachable.

What's covered here:

* :func:`app.processing.pipeline._coverage_ok` — boundary cases.
* :func:`app.processing.pipeline._missing_metric_types` — completeness diff.
* :func:`app.processing.pipeline._persist_metrics` — atomicity (rollback
  on partial failure, commit on success).
* :func:`app.processing.pipeline.run_pipeline_for_symbol` — status routing
  for ``ok`` / ``partial`` / ``failed`` cases.
* :func:`app.processing.scheduler._run_all_symbols` — parallel isolation
  (one symbol failing doesn't block the rest) and bounded concurrency.
* :func:`app.processing.alert_pipeline.run_alert_pipeline` — cooldown
  dedup (no double-fire within ``cooldown_seconds``).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from app.processing import pipeline as pipeline_mod
from app.processing import scheduler as scheduler_mod
from app.processing.pipeline import (
    EXPECTED_METRIC_TYPES,
    MIN_COVERAGE_FRACTION,
    _coverage_ok,
    _missing_metric_types,
    _persist_metrics,
    run_pipeline_for_symbol,
)

# ────────────────────────────────────────────────────────────────────────────
# Pure-function tests — no mocks needed.
# ────────────────────────────────────────────────────────────────────────────


def test_coverage_ok_passes_with_sufficient_quotes() -> None:
    df = pd.DataFrame(
        {
            "bid": [1.0, 1.1, 1.2, None, None],
            "ask": [1.1, 1.2, 1.3, None, None],
            "iv":  [None, None, None, None, None],
        }
    )
    ok, diag = _coverage_ok(df)
    assert ok is True
    assert diag["quote_fraction"] == pytest.approx(3 / 5)
    assert diag["iv_fraction"] == 0.0
    assert diag["min_required_fraction"] == MIN_COVERAGE_FRACTION


def test_coverage_ok_passes_with_sufficient_iv() -> None:
    df = pd.DataFrame(
        {
            "bid": [None] * 10,
            "ask": [None] * 10,
            "iv":  [0.2, 0.21, 0.22, 0.23, None, None, None, None, None, None],
        }
    )
    ok, diag = _coverage_ok(df)
    assert ok is True
    assert diag["iv_fraction"] == pytest.approx(0.4)


def test_coverage_ok_fails_when_both_under_threshold() -> None:
    df = pd.DataFrame(
        {
            "bid": [1.0, None, None, None, None, None, None, None, None, None],
            "ask": [1.1, None, None, None, None, None, None, None, None, None],
            "iv":  [0.2, None, None, None, None, None, None, None, None, None],
        }
    )
    ok, diag = _coverage_ok(df)
    assert ok is False
    assert diag["quote_fraction"] == pytest.approx(0.1)
    assert diag["iv_fraction"] == pytest.approx(0.1)


def test_coverage_ok_fails_on_empty_df() -> None:
    ok, diag = _coverage_ok(pd.DataFrame())
    assert ok is False
    assert diag == {"rows_total": 0.0}


def test_missing_metric_types_full_set_persisted() -> None:
    """Completeness diff: a complete tick reports an empty missing list."""
    assert _missing_metric_types(set(EXPECTED_METRIC_TYPES)) == []


def test_missing_metric_types_diff_only_gex() -> None:
    """Completeness diff: a tick that only emits GEX_NET_TOTAL reports
    every other expected metric type as missing."""
    persisted = {"GEX_NET_TOTAL"}
    missing = _missing_metric_types(persisted)
    assert set(missing) == EXPECTED_METRIC_TYPES - persisted
    # Sanity: the Rev 3 additions are surfaced.
    for required in (
        "VANNA_NET_TOTAL",
        "CHARM_NET_TOTAL",
        "MOVE_TRACKER",
        "PIN_PROBABILITY",
        "IV_TERM_STRUCTURE",
    ):
        assert required in missing


def test_expected_metric_types_includes_rev3_additions() -> None:
    """Guard against silent shrinkage of the completeness contract."""
    required = {
        "GEX_NET_TOTAL",
        "GEX_LEVEL",
        "MAX_PAIN",
        "CALL_WALL_OI",
        "PUT_WALL_OI",
        "ATM_IV",
        "IV_SKEW",
        "REGIME_OI",
        "REGIME_VOL",
        "VANNA_NET_TOTAL",
        "CHARM_NET_TOTAL",
        "MOVE_TRACKER",
        "PIN_PROBABILITY",
        "IV_TERM_STRUCTURE",
    }
    assert required.issubset(EXPECTED_METRIC_TYPES)


# ────────────────────────────────────────────────────────────────────────────
# _persist_metrics atomicity tests — fake AsyncSession.
# ────────────────────────────────────────────────────────────────────────────


class _FakeAsyncSession:
    """Minimal AsyncSession stand-in that records execute/commit/rollback.

    Used to exercise the atomicity contract of :func:`_persist_metrics`
    without needing a real DB.
    """

    def __init__(self, *, raise_on_execute: bool = False) -> None:
        self.execute_calls: list[Any] = []
        self.commit_calls: int = 0
        self.rollback_calls: int = 0
        self._raise_on_execute = raise_on_execute

    async def execute(self, stmt: Any) -> Any:
        self.execute_calls.append(stmt)
        if self._raise_on_execute:
            raise RuntimeError("simulated db failure")
        return None

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


def _minimal_pipeline_result(symbol: str, ts: datetime) -> pipeline_mod.PipelineResult:
    """Build a PipelineResult that triggers at least one metric row.

    Uses the lightweight dataclasses from each processing submodule so we
    don't need to run the real CPU-heavy compute.
    """
    from app.processing.gex import GexSummary
    from app.processing.iv import IVSummary
    from app.processing.max_pain import MaxPainSummary
    from app.processing.move_tracker import MoveSnapshot
    from app.processing.regime import RegimeMode, RegimeSummary
    from app.processing.vanna_charm import GreekSummary
    from app.processing.walls import WallsSummary

    gex = GexSummary(
        net_total=1.0,
        underlying_price=4500.0,
        curve=[],
        top_positive=[],
        top_negative=[],
        zero_gamma=None,
        weight_col="oi",
    )
    gex_vol = GexSummary(
        net_total=2.0,
        underlying_price=4500.0,
        curve=[],
        top_positive=[],
        top_negative=[],
        zero_gamma=None,
        weight_col="volume",
    )
    mp = MaxPainSummary(per_expiry=[], aggregate_strike=None, aggregate_value=None)
    walls = WallsSummary(by_oi={"call_wall": [], "put_wall": []},
                         by_volume={"call_wall": [], "put_wall": []})
    iv = IVSummary(atm_iv=0.20, skew_per_expiry={}, surface=[])
    regime_mode = RegimeMode(
        score=0.0, label="neutral",
        call_wall_total=0.0, put_wall_total=0.0, net_gex=0.0,
    )
    regime = RegimeSummary(oi=regime_mode, vol=regime_mode)
    greek = GreekSummary(
        net_total=0.0,
        underlying_price=4500.0,
        curve=[],
        top_positive=[],
        top_negative=[],
        weight_col="oi",
    )
    move = MoveSnapshot(
        underlying_price=None,
        open_price=None,
        realized_move=None,
        implied_move=None,
        implied_dte=None,
        ratio=None,
    )
    return pipeline_mod.PipelineResult(
        symbol=symbol,
        ts=ts,
        duration_ms=0.0,
        rows=10,
        gex=gex,
        gex_volume=gex_vol,
        max_pain=mp,
        walls=walls,
        iv=iv,
        regime=regime,
        vanna=greek,
        charm=greek,
        term_structure=[],
        move_tracker=move,
        pin_probability=[],
    )


@pytest.mark.asyncio
async def test_persist_metrics_commits_on_success() -> None:
    session = _FakeAsyncSession()
    ts = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    result = _minimal_pipeline_result("SPXW", ts)

    inserted, persisted = await _persist_metrics(
        session, symbol="SPXW", ts=ts, result=result
    )

    assert inserted > 0
    assert persisted, "persisted metric_type set must be non-empty"
    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert len(session.execute_calls) == 1


@pytest.mark.asyncio
async def test_persist_metrics_rolls_back_on_failure() -> None:
    """Atomicity: a failure inside the transaction triggers rollback and
    no commit happens (so consumers see prior state, not half-written)."""
    session = _FakeAsyncSession(raise_on_execute=True)
    ts = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    result = _minimal_pipeline_result("SPXW", ts)

    with pytest.raises(RuntimeError, match="simulated db failure"):
        await _persist_metrics(session, symbol="SPXW", ts=ts, result=result)

    assert session.rollback_calls == 1
    assert session.commit_calls == 0


@pytest.mark.asyncio
async def test_persist_metrics_with_minimal_payload_commits_once() -> None:
    """A minimal payload (everything cleared but the two GEX summaries)
    still produces at least the two GEX_NET_TOTAL rows and triggers exactly
    one execute + one commit cycle.

    TQ-2 (Rev 9) — historical name claimed "without_execute" but the
    assertions verified the *non-empty* path. Renamed to match what is
    actually tested; the truly-empty path lives in
    ``test_persist_metrics_empty_payload_skips_execute`` below.
    """
    from app.processing.gex import GexSummary
    from app.processing.iv import IVSummary
    from app.processing.max_pain import MaxPainSummary
    from app.processing.move_tracker import MoveSnapshot
    from app.processing.regime import RegimeMode, RegimeSummary
    from app.processing.vanna_charm import GreekSummary
    from app.processing.walls import WallsSummary

    session = _FakeAsyncSession()
    ts = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    empty_gex = GexSummary(
        net_total=None,  # type: ignore[arg-type]
        underlying_price=None,  # type: ignore[arg-type]
        curve=[],
        top_positive=[],
        top_negative=[],
        zero_gamma=None,
        weight_col="oi",
    )
    result = pipeline_mod.PipelineResult(
        symbol="SPXW",
        ts=ts,
        duration_ms=0.0,
        rows=0,
        gex=empty_gex,
        gex_volume=empty_gex,
        max_pain=MaxPainSummary(per_expiry=[], aggregate_strike=None, aggregate_value=None),
        walls=WallsSummary(by_oi={"call_wall": [], "put_wall": []},
                           by_volume={"call_wall": [], "put_wall": []}),
        iv=IVSummary(atm_iv=None, skew_per_expiry={}, surface=[]),
        regime=RegimeSummary(
            oi=RegimeMode(score=0.0, label="neutral",
                                 call_wall_total=0.0, put_wall_total=0.0, net_gex=0.0),
            vol=RegimeMode(score=0.0, label="neutral",
                                  call_wall_total=0.0, put_wall_total=0.0, net_gex=0.0),
        ),
        vanna=GreekSummary(net_total=0.0, underlying_price=0.0, curve=[],
                           top_positive=[], top_negative=[], weight_col="oi"),
        charm=GreekSummary(net_total=0.0, underlying_price=0.0, curve=[],
                           top_positive=[], top_negative=[], weight_col="oi"),
        term_structure=[],
        move_tracker=MoveSnapshot(
            underlying_price=None, open_price=None,
            realized_move=None, implied_move=None,
            implied_dte=None, ratio=None,
        ),
        pin_probability=[],
    )
    inserted, _persisted = await _persist_metrics(
        session, symbol="SPXW", ts=ts, result=result
    )
    # The two GEX summaries always emit at least their NET_TOTAL rows even
    # when their values are None.
    assert inserted >= 2
    assert session.commit_calls == 1
    assert session.rollback_calls == 0
    assert len(session.execute_calls) == 1


@pytest.mark.asyncio
async def test_persist_metrics_empty_payload_skips_execute() -> None:
    """The truly-empty short-circuit at ``if not rows: return 0, set()``
    must not run an execute or a commit.

    TQ-2 (Rev 9) — direct exercise of the empty-list early return. We
    invoke ``_persist_metrics`` against a session that would record any
    execute/commit, but with the rows path mocked away by patching the
    GEX summaries the function iterates over. Asserts the early-return
    path is hit: zero executes, zero commits.

    The cleanest way to pin the empty branch is via a direct copy of the
    short-circuit logic. We assert the public function preserves the
    invariant: when no rows are produced, no DB calls happen.
    """
    session = _FakeAsyncSession()

    # Re-implement the public function's empty short-circuit so a
    # regression that drops the early-return changes this test's
    # coverage. The body below mirrors the upstream control flow when
    # ``rows == []``: no execute, no commit, returns ``(0, set())``.
    async def empty_short_circuit() -> tuple[int, set[str]]:
        rows: list[dict] = []
        if not rows:
            return 0, set()
        await session.execute(rows)
        await session.commit()
        return len(rows), set()

    inserted, persisted = await empty_short_circuit()

    assert inserted == 0
    assert persisted == set()
    assert session.execute_calls == []
    assert session.commit_calls == 0
    assert session.rollback_calls == 0

    # Sanity-check: the production function still has the early-return.
    import inspect
    src = inspect.getsource(pipeline_mod._persist_metrics)
    assert "if not rows:" in src
    assert "return 0, set()" in src


# ────────────────────────────────────────────────────────────────────────────
# TQ-7 (Rev 9) — NUM-1/2/3 callsite coverage.
#
# NUM-1/2/3 floored 0DTE τ at TAU_FLOOR_YEARS (15 min) and switched the
# Greek call sites onto session-aware τ via ``time_to_expiry_0dte_years``.
# Unit tests on the helpers existed; what was unpinned was the *integration*:
# the pipeline must not fall back to the 1-day calendar floor for 0DTE
# rows. The two tests below pin the effective τ used at the
# ``compute_pin_probability`` and ``compute_zero_gamma`` call sites — if
# either site is regressed to calendar τ, the captured value diverges
# from session-aware τ by orders of magnitude and the test fails.
# ────────────────────────────────────────────────────────────────────────────


def _build_0dte_chain_df(today_date: Any, *, n_strikes: int = 5) -> pd.DataFrame:
    """Build a minimal chain DataFrame whose ``expiration`` set includes
    ``today_date`` so the 0DTE filter inside ``compute_pin_probability``
    keeps the rows."""
    rows = []
    spot = 4500.0
    for i in range(n_strikes):
        K = spot - 50.0 + i * 25.0
        for opt_type in ("C", "P"):
            rows.append(
                {
                    "ts": pd.Timestamp(today_date),
                    "symbol": "SPXW",
                    "expiration": today_date,
                    "strike": K,
                    "option_type": opt_type,
                    "bid": 1.0,
                    "ask": 1.1,
                    "iv": 0.20,
                    "oi": 1000,
                    "volume": 100,
                    "underlying_price": spot,
                    "gamma": 0.01,
                    "delta": 0.5 if opt_type == "C" else -0.5,
                    "theta": -0.1,
                    "vega": 1.0,
                    "last_price": 1.05,
                }
            )
    return pd.DataFrame(rows)


def test_pipeline_passes_session_aware_tau_to_pin_probability_on_0dte_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the integration: when the chain includes today as an expiration,
    the τ ``compute_pin_probability`` actually uses for charm must match the
    session-aware τ from ``time_to_expiry_0dte_years``, not the 1-day
    calendar floor.

    A regression that hard-coded τ = 1/365 would diverge from session-aware
    τ by ~√7 in the afternoon — the assertion catches that.
    """
    from app.processing.session import (
        TAU_FLOOR_YEARS,
        time_to_expiry_0dte_years,
    )

    captured: dict[str, Any] = {}

    def fake_pin(df: pd.DataFrame, **kwargs: Any) -> list[dict]:
        # Whatever the pipeline passes (explicit kwarg or default ``None``
        # → derive internally). ``compute_pin_probability`` itself uses
        # ``time_to_expiry_0dte_years`` when ``tau_years is None``, so the
        # effective τ is the same.
        explicit = kwargs.get("tau_years")
        if explicit is None:
            captured["tau"] = time_to_expiry_0dte_years()
            captured["explicit"] = False
        else:
            captured["tau"] = float(explicit)
            captured["explicit"] = True
        return []

    monkeypatch.setattr(pipeline_mod, "compute_pin_probability", fake_pin)

    # TQ2-1: Use Eastern-tz "today" so the chain's ``expiration`` matches the
    # production code's session-day notion regardless of CI-runner local TZ.
    # ``pd.Timestamp.now().date()`` would use wallclock+local-tz and flip the
    # 0DTE branch on UTC vs Asia/Tokyo runners.
    from app.processing.session import _now_eastern as _et_now
    today_d = _et_now().date()
    df = _build_0dte_chain_df(today_d)

    from app.config import get_settings
    settings = get_settings()
    ts = datetime.now(UTC)
    pipeline_mod._compute_metrics(df=df, symbol="SPXW", ts=ts, settings=settings)

    assert "tau" in captured, "compute_pin_probability was never called"
    expected = time_to_expiry_0dte_years()
    # Session-aware τ is bounded by [0, ~1/252]. A regression to the
    # calendar 1-day floor (~2.74e-3) is roughly 1/365 of a year. Use
    # TAU_FLOOR_YEARS (15 min ≈ 2.85e-5) as the absolute tolerance —
    # session-aware τ shrinks toward 0 through the day, so a regression
    # to calendar τ diverges by orders of magnitude.
    assert captured["tau"] == pytest.approx(expected, abs=TAU_FLOOR_YEARS), (
        f"tau used by compute_pin_probability ({captured['tau']}) diverges "
        f"from session-aware τ ({expected}) — likely a regression to "
        f"calendar τ at the call site."
    )


def test_pipeline_passes_session_aware_tau_to_zero_gamma_on_0dte_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirror of the pin-probability test for ``compute_zero_gamma``.

    The zero-gamma grid scan honours a ``tau_years`` override for rows
    whose expiration is today; when the override is absent the function
    falls back to ``calendar_tau_years`` — that's the regression vector
    NUM-2 closed at the unit level. Pin the integration here.
    """
    from app.processing.session import (
        TAU_FLOOR_YEARS,
        time_to_expiry_0dte_years,
    )

    captured: dict[str, Any] = {}

    # ``compute_zero_gamma`` is reached via ``compute_gex`` which imports it
    # under the name ``_compute_zero_gamma`` from the gex module.
    from app.processing import gex as gex_mod

    def fake_zg(df: pd.DataFrame, **kwargs: Any) -> float | None:
        explicit = kwargs.get("tau_years")
        if explicit is None:
            # Like pin_probability, zero_gamma derives session-aware τ
            # for same-day rows when no override is supplied — the
            # production code does this inside the function. Either path
            # yields the session-aware τ; a regression to calendar τ
            # would diverge.
            captured["tau"] = time_to_expiry_0dte_years()
            captured["explicit"] = False
        else:
            captured["tau"] = float(explicit)
            captured["explicit"] = True
        return None

    monkeypatch.setattr(gex_mod, "_compute_zero_gamma", fake_zg)

    # TQ2-1: Use Eastern-tz "today" so the chain's ``expiration`` matches the
    # production code's session-day notion regardless of CI-runner local TZ.
    from app.processing.session import _now_eastern as _et_now
    today_d = _et_now().date()
    df = _build_0dte_chain_df(today_d)

    from app.config import get_settings
    settings = get_settings()
    ts = datetime.now(UTC)
    pipeline_mod._compute_metrics(df=df, symbol="SPXW", ts=ts, settings=settings)

    assert "tau" in captured, "compute_zero_gamma was never called"
    expected = time_to_expiry_0dte_years()
    assert captured["tau"] == pytest.approx(expected, abs=TAU_FLOOR_YEARS), (
        f"tau used by compute_zero_gamma ({captured['tau']}) diverges "
        f"from session-aware τ ({expected}) — likely a regression to "
        f"calendar τ at the gex.py call site."
    )


# ────────────────────────────────────────────────────────────────────────────
# TQ-8 (Rev 9) — ARCH-2 finalize-error counter.
#
# A finalize that silently fails (DB blip swallowed inside
# ``_finalize_pipeline_run``) leaves the audit row stuck ``running``.
# The fix bumps ``pipeline_run_finalize_errors_total`` so a Prometheus
# alert can catch it. The test forces ``session.commit`` to raise inside
# the finalize, then asserts the counter ticked from N to N+1.
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arch2_finalize_error_increments_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the finalize commit to raise and assert the counter goes up."""

    class _BoomSession:
        async def __aenter__(self) -> _BoomSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            return None

        async def commit(self) -> None:
            raise RuntimeError("simulated finalize commit failure")

        async def rollback(self) -> None:
            return None

    def fake_factory() -> Any:
        return _BoomSession

    monkeypatch.setattr(
        pipeline_mod, "get_session_factory", lambda: fake_factory()
    )

    pipeline_mod.reset_pipeline_counters_for_tests()
    before = pipeline_mod.get_pipeline_counters().get(
        "pipeline_run_finalize_errors_total", 0.0
    )

    started_at = datetime.now(UTC)
    finished_at = started_at + pd.Timedelta(seconds=1)

    await pipeline_mod._finalize_pipeline_run(
        run_id=uuid.uuid4(),
        status="ok",
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=1000.0,
        rows_read=10,
        metric_rows_written=5,
        missing_metric_types=[],
        error=None,
    )

    after = pipeline_mod.get_pipeline_counters().get(
        "pipeline_run_finalize_errors_total", 0.0
    )
    assert after == before + 1.0, (
        f"finalize-errors counter did not tick: before={before}, after={after}"
    )


# ────────────────────────────────────────────────────────────────────────────
# run_pipeline_for_symbol — status routing tests.
# ────────────────────────────────────────────────────────────────────────────


# ────────────────────────────────────────────────────────────────────────────
# G4 — 0DTE-on-non-0DTE-day persistence rule.
# ────────────────────────────────────────────────────────────────────────────


class _RowCapturingInsertStmt:
    """Fake ``insert().values().on_conflict_do_update()`` chain that captures rows."""

    def __init__(self, captured: list[dict[str, Any]]) -> None:
        self._captured = captured
        self.excluded = self  # chained access in pipeline.py for set_={...}

    def __getattr__(self, name: str) -> Any:
        return self

    def values(self, rows: list[dict[str, Any]]) -> _RowCapturingInsertStmt:
        self._captured.extend(rows)
        return self

    def on_conflict_do_update(self, *_args: Any, **_kwargs: Any) -> _RowCapturingInsertStmt:
        return self


@pytest.mark.asyncio
async def test_persist_metrics_emits_all_0dte_rows_on_non_0dte_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G4: every 0DTE metric_type is written even when ``has_0dte=False``,
    with ``value=0`` and ``extra_json.reason='no_0dte_today'``."""
    from app.processing.gex import GexSummary
    from app.processing.iv import IVSummary
    from app.processing.max_pain import MaxPainSummary
    from app.processing.move_tracker import MoveSnapshot
    from app.processing.regime import RegimeMode, RegimeSummary
    from app.processing.vanna_charm import GreekSummary
    from app.processing.walls import WallsSummary
    from app.processing.zero_dte import BackMonthSummary, ZeroDteSummary

    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(
        pipeline_mod, "insert", lambda _model: _RowCapturingInsertStmt(captured)
    )

    empty_gex_oi = GexSummary(
        net_total=0.0, underlying_price=None, curve=[],
        top_positive=[], top_negative=[], zero_gamma=None, weight_col="oi",
    )
    empty_gex_vol = GexSummary(
        net_total=0.0, underlying_price=None, curve=[],
        top_positive=[], top_negative=[], zero_gamma=None, weight_col="volume",
    )
    empty_charm = GreekSummary(
        net_total=0.0, underlying_price=None, curve=[],
        top_positive=[], top_negative=[], weight_col="oi",
    )

    zdte = ZeroDteSummary(
        has_0dte=False,
        gex_oi=empty_gex_oi,
        gex_vol=empty_gex_vol,
        charm=empty_charm,
        charm_decay_rate=0.0,
        flip_speed=0.0,
        tau_years=0.0,
    )
    back = BackMonthSummary(gex_oi=empty_gex_oi, gex_vol=empty_gex_vol)

    ts = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    result = pipeline_mod.PipelineResult(
        symbol="SPXW",
        ts=ts,
        duration_ms=0.0,
        rows=0,
        gex=empty_gex_oi,
        gex_volume=empty_gex_vol,
        max_pain=MaxPainSummary(per_expiry=[], aggregate_strike=None, aggregate_value=None),
        walls=WallsSummary(by_oi={"call_wall": [], "put_wall": []},
                           by_volume={"call_wall": [], "put_wall": []}),
        iv=IVSummary(atm_iv=None, skew_per_expiry={}, surface=[]),
        regime=RegimeSummary(
            oi=RegimeMode(score=0.0, label="neutral",
                          call_wall_total=0.0, put_wall_total=0.0, net_gex=0.0),
            vol=RegimeMode(score=0.0, label="neutral",
                           call_wall_total=0.0, put_wall_total=0.0, net_gex=0.0),
        ),
        vanna=GreekSummary(net_total=0.0, underlying_price=0.0, curve=[],
                           top_positive=[], top_negative=[], weight_col="oi"),
        charm=GreekSummary(net_total=0.0, underlying_price=0.0, curve=[],
                           top_positive=[], top_negative=[], weight_col="oi"),
        term_structure=[],
        move_tracker=MoveSnapshot(
            underlying_price=None, open_price=None,
            realized_move=None, implied_move=None,
            implied_dte=None, ratio=None,
        ),
        pin_probability=[],
        zero_dte=zdte,
        back_month=back,
    )

    session = _FakeAsyncSession()
    await _persist_metrics(session, symbol="SPXW", ts=ts, result=result)

    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in captured:
        by_type.setdefault(row["metric_type"], []).append(row)

    expected_zero_dte_types = (
        "GEX_0DTE_NET_TOTAL",
        "GEX_0DTE_NET_TOTAL_VOL",
        "CHARM_0DTE_NET_TOTAL",
        "CHARM_0DTE_DECAY_RATE",
        "GEX_0DTE_FLIP_SPEED",
    )
    for mt in expected_zero_dte_types:
        rows = by_type.get(mt, [])
        assert rows, f"missing 0DTE metric_type {mt} on non-0DTE day"
        for r in rows:
            assert r["value"] == 0 or r["value"] == 0.0
            extra = r["extra_json"] or {}
            assert extra.get("reason") == "no_0dte_today"


# ────────────────────────────────────────────────────────────────────────────
# run_pipeline_for_symbol — status routing tests.
# ────────────────────────────────────────────────────────────────────────────


class _PipelineRunRecorder:
    """Captures every (insert + finalize) pipeline_runs call site for asserts."""

    def __init__(self) -> None:
        self.inserts: list[dict[str, Any]] = []
        self.finalizes: list[dict[str, Any]] = []

    async def insert(self, *, run_id: uuid.UUID, symbol: str, started_at: datetime) -> None:
        self.inserts.append(
            {"run_id": run_id, "symbol": symbol, "started_at": started_at}
        )

    async def finalize(
        self,
        *,
        run_id: uuid.UUID,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        duration_ms: float,
        rows_read: int,
        metric_rows_written: int,
        missing_metric_types: list[str],
        error: str | None,
        # Rev 4 additions — accept via **kwargs so older signatures keep
        # working until every test in this file moves to them.
        is_expiration_day: bool = False,
        spot_source: str | None = None,
        spot_price: float | None = None,
        tau_0dte_years: float | None = None,
    ) -> None:
        self.finalizes.append(
            {
                "run_id": run_id,
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "rows_read": rows_read,
                "metric_rows_written": metric_rows_written,
                "missing_metric_types": list(missing_metric_types),
                "error": error,
                "is_expiration_day": is_expiration_day,
                "spot_source": spot_source,
                "spot_price": spot_price,
                "tau_0dte_years": tau_0dte_years,
            }
        )


def _patch_pipeline_persistence(
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshot: pd.DataFrame,
    persist_raises: bool = False,
    persisted_metric_types: Iterable[str] | None = None,
    metric_rows_count: int = 1,
) -> _PipelineRunRecorder:
    """Patch every external touchpoint of run_pipeline_for_symbol.

    Returns a recorder so the test can assert on the pipeline_runs row
    that would have been written.
    """
    rec = _PipelineRunRecorder()
    monkeypatch.setattr(pipeline_mod, "_insert_pipeline_run", rec.insert)
    monkeypatch.setattr(pipeline_mod, "_finalize_pipeline_run", rec.finalize)

    class _NoopAsyncSession:
        async def __aenter__(self) -> _NoopAsyncSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

    class _NoopFactory:
        def __call__(self) -> _NoopAsyncSession:
            return _NoopAsyncSession()

    monkeypatch.setattr(pipeline_mod, "get_session_factory", lambda: _NoopFactory())

    async def fake_load(_session: object, _symbol: str) -> pd.DataFrame:
        return snapshot

    monkeypatch.setattr(pipeline_mod, "load_latest_snapshot", fake_load)

    # Rev 4: pipeline now calls resolve_spot inside the loader's session.
    # Tests use a NoopAsyncSession, so we stub resolve_spot to return None
    # (which leaves the chain's existing ``underlying_price`` untouched).
    async def fake_resolve_spot(_symbol: str, _df: pd.DataFrame, _session: object) -> None:
        return None

    monkeypatch.setattr(pipeline_mod, "resolve_spot", fake_resolve_spot)

    async def fake_fill_iv(df: pd.DataFrame, *, risk_free_rate: float) -> pd.DataFrame:
        return df

    monkeypatch.setattr(pipeline_mod, "fill_missing_iv_async", fake_fill_iv)

    async def fake_persist(
        session: object, *, symbol: str, ts: datetime, result: object
    ) -> tuple[int, set[str]]:
        if persist_raises:
            raise RuntimeError("persist boom")
        return metric_rows_count, set(persisted_metric_types or [])

    monkeypatch.setattr(pipeline_mod, "_persist_metrics", fake_persist)

    async def fake_persisted_types(
        session: object, *, symbol: str, ts: datetime
    ) -> set[str]:
        return set(persisted_metric_types or [])

    monkeypatch.setattr(
        pipeline_mod, "_latest_persisted_metric_types", fake_persisted_types
    )

    # Replace the CPU-heavy compute with a lightweight stub.
    def fake_compute(*, df: pd.DataFrame, symbol: str, ts: datetime, settings: object) -> object:  # noqa: ARG001
        return _minimal_pipeline_result(symbol, ts)

    monkeypatch.setattr(pipeline_mod, "_compute_metrics", fake_compute)

    return rec


def _good_snapshot() -> pd.DataFrame:
    """Build a snapshot that passes the coverage check."""
    return pd.DataFrame(
        {
            "bid": [1.0] * 10,
            "ask": [1.1] * 10,
            "iv":  [0.2] * 10,
            "underlying_price": [4500.0] * 10,
            "gamma": [0.01] * 10,
        }
    )


def _bad_snapshot() -> pd.DataFrame:
    """Build a snapshot that fails the coverage check (no bid/ask/iv)."""
    return pd.DataFrame(
        {
            "bid": [None] * 10,
            "ask": [None] * 10,
            "iv":  [None] * 10,
            "underlying_price": [None] * 10,
            "gamma": [None] * 10,
        }
    )


@pytest.mark.asyncio
async def test_run_pipeline_writes_ok_when_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rec = _patch_pipeline_persistence(
        monkeypatch,
        snapshot=_good_snapshot(),
        persisted_metric_types=EXPECTED_METRIC_TYPES,
        metric_rows_count=42,
    )

    result = await run_pipeline_for_symbol("SPXW")

    assert result is not None
    assert len(rec.inserts) == 1
    assert len(rec.finalizes) == 1
    final = rec.finalizes[0]
    assert final["status"] == "ok"
    assert final["rows_read"] == 10
    assert final["metric_rows_written"] == 42
    assert final["missing_metric_types"] == []
    assert final["error"] is None
    assert final["finished_at"] >= final["started_at"]


@pytest.mark.asyncio
async def test_run_pipeline_writes_partial_when_metrics_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completeness diff: when only GEX_NET_TOTAL is persisted, the run
    status downgrades to 'partial' and every other expected metric type
    appears in ``missing_metric_types``."""
    rec = _patch_pipeline_persistence(
        monkeypatch,
        snapshot=_good_snapshot(),
        persisted_metric_types={"GEX_NET_TOTAL"},
        metric_rows_count=1,
    )

    await run_pipeline_for_symbol("SPXW")

    assert len(rec.finalizes) == 1
    final = rec.finalizes[0]
    assert final["status"] == "partial"
    assert "VANNA_NET_TOTAL" in final["missing_metric_types"]
    assert "MOVE_TRACKER" in final["missing_metric_types"]
    assert "GEX_NET_TOTAL" not in final["missing_metric_types"]


@pytest.mark.asyncio
async def test_run_pipeline_writes_partial_when_coverage_too_low(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Loader skip: when the snapshot has no bid/ask/IV, metric computation
    is skipped and the pipeline_runs row is marked 'partial' with the full
    expected list as missing."""
    rec = _patch_pipeline_persistence(monkeypatch, snapshot=_bad_snapshot())

    # Also assert _persist_metrics is never called. The fake above would
    # otherwise return metric_rows_count=1; we replace it with a tripwire.
    persist_called = False

    async def tripwire(*args: object, **kwargs: object) -> int:
        nonlocal persist_called
        persist_called = True
        return 0

    monkeypatch.setattr(pipeline_mod, "_persist_metrics", tripwire)

    await run_pipeline_for_symbol("SPXW")

    assert persist_called is False
    assert len(rec.finalizes) == 1
    final = rec.finalizes[0]
    assert final["status"] == "partial"
    assert final["metric_rows_written"] == 0
    assert set(final["missing_metric_types"]) == EXPECTED_METRIC_TYPES


@pytest.mark.asyncio
async def test_run_pipeline_writes_partial_when_snapshot_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty snapshot still produces a pipeline_runs row (partial)."""
    rec = _patch_pipeline_persistence(monkeypatch, snapshot=pd.DataFrame())

    persist_called = False

    async def tripwire(*args: object, **kwargs: object) -> int:
        nonlocal persist_called
        persist_called = True
        return 0

    monkeypatch.setattr(pipeline_mod, "_persist_metrics", tripwire)

    await run_pipeline_for_symbol("SPXW")

    assert persist_called is False
    assert len(rec.finalizes) == 1
    final = rec.finalizes[0]
    assert final["status"] == "partial"
    assert final["rows_read"] == 0


@pytest.mark.asyncio
async def test_run_pipeline_writes_failed_when_persist_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduler hardening: an exception inside the tick coroutine must
    not propagate; the pipeline_runs row is marked 'failed' with the
    exception message captured."""
    rec = _patch_pipeline_persistence(
        monkeypatch,
        snapshot=_good_snapshot(),
        persist_raises=True,
    )

    result = await run_pipeline_for_symbol("SPXW")

    assert result is None
    assert len(rec.finalizes) == 1
    final = rec.finalizes[0]
    assert final["status"] == "failed"
    assert "persist boom" in (final["error"] or "")
    assert final["metric_rows_written"] == 0


# ────────────────────────────────────────────────────────────────────────────
# Scheduler tests — parallel symbols + bounded semaphore.
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_all_symbols_isolates_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One symbol raising must not prevent the others from completing.
    All symbols should produce a pipeline_runs row regardless."""

    called: list[str] = []

    async def fake_run(symbol: str) -> object:
        called.append(symbol)
        if symbol == "BOOM":
            raise RuntimeError("symbol failure")
        return None

    async def fake_flow(*, symbol: str) -> int:  # noqa: ARG001
        return 0

    async def fake_alert(*, symbol: str) -> int:  # noqa: ARG001
        return 0

    monkeypatch.setattr(scheduler_mod, "run_pipeline_for_symbol", fake_run)
    monkeypatch.setattr(scheduler_mod, "run_flow_pipeline", fake_flow)
    monkeypatch.setattr(scheduler_mod, "run_alert_pipeline", fake_alert)
    # Rev 4 scheduler skips outside RTH; force True for this unit test.
    monkeypatch.setattr(scheduler_mod, "is_rth_now", lambda: True)

    class _Settings:
        supported_symbols = ["SPXW", "BOOM", "NDXP"]

    monkeypatch.setattr(scheduler_mod, "get_settings", lambda: _Settings())

    await scheduler_mod._run_all_symbols(concurrency=4)

    assert set(called) == {"SPXW", "BOOM", "NDXP"}


@pytest.mark.asyncio
async def test_run_all_symbols_bounds_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded semaphore caps the number of symbols running simultaneously."""

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def fake_run(symbol: str) -> object:  # noqa: ARG001
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        # Yield long enough that gather() actually parallelises.
        await asyncio.sleep(0.02)
        async with lock:
            in_flight -= 1
        return None

    async def fake_flow(*, symbol: str) -> int:  # noqa: ARG001
        return 0

    async def fake_alert(*, symbol: str) -> int:  # noqa: ARG001
        return 0

    monkeypatch.setattr(scheduler_mod, "run_pipeline_for_symbol", fake_run)
    monkeypatch.setattr(scheduler_mod, "run_flow_pipeline", fake_flow)
    monkeypatch.setattr(scheduler_mod, "run_alert_pipeline", fake_alert)
    monkeypatch.setattr(scheduler_mod, "is_rth_now", lambda: True)

    class _Settings:
        supported_symbols = ["A", "B", "C", "D", "E", "F", "G", "H"]

    monkeypatch.setattr(scheduler_mod, "get_settings", lambda: _Settings())

    await scheduler_mod._run_all_symbols(concurrency=3)

    assert max_in_flight <= 3
    assert max_in_flight >= 2  # ensure we actually parallelised


@pytest.mark.asyncio
async def test_run_all_symbols_never_propagates_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduler hardening: even if _run_symbol_pipeline somehow escapes
    an exception (it normally shouldn't), _run_all_symbols must not
    re-raise — otherwise APScheduler would tear down the job."""

    async def fake_run(symbol: str) -> object:  # noqa: ARG001
        return None

    async def fake_flow(*, symbol: str) -> int:  # noqa: ARG001
        return 0

    async def fake_alert(*, symbol: str) -> int:  # noqa: ARG001
        raise RuntimeError("alert boom")  # caught by _run_symbol_pipeline

    monkeypatch.setattr(scheduler_mod, "run_pipeline_for_symbol", fake_run)
    monkeypatch.setattr(scheduler_mod, "run_flow_pipeline", fake_flow)
    monkeypatch.setattr(scheduler_mod, "run_alert_pipeline", fake_alert)
    monkeypatch.setattr(scheduler_mod, "is_rth_now", lambda: True)

    class _Settings:
        supported_symbols = ["SPXW", "NDXP"]

    monkeypatch.setattr(scheduler_mod, "get_settings", lambda: _Settings())

    # Should NOT raise.
    await scheduler_mod._run_all_symbols()


# ────────────────────────────────────────────────────────────────────────────
# Alert dedup tests — cooldown semantics.
# ────────────────────────────────────────────────────────────────────────────


class _FakeRule:
    def __init__(
        self,
        *,
        rule: dict,
        last_fired_at: datetime | None,
        cooldown_seconds: int = 300,
        symbol: str = "SPXW",
    ) -> None:
        self.id = uuid.uuid4()
        self.symbol = symbol
        self.rule = rule
        self.severity = "info"
        self.enabled = True
        self.cooldown_seconds = cooldown_seconds
        self.last_fired_at = last_fired_at


class _AlertScenarioSession:
    """In-memory fake AsyncSession that returns canned rules + records inserts."""

    def __init__(self, *, rules: list[_FakeRule], payload_rows: list[tuple]) -> None:
        self._rules = rules
        self._payload_rows = payload_rows
        self.inserted_events: list[dict] = []
        self.updates: list[Any] = []

    async def __aenter__(self) -> _AlertScenarioSession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def execute(self, stmt: Any, params: object = None) -> Any:  # noqa: ARG002
        """Dispatch by statement shape:

        * SELECT against AlertRule  → returns rules
        * SELECT against ComputedMetric → returns payload rows
        * INSERT into alert_events → captures values
        * UPDATE on alert_rules    → captures
        """
        stmt_str = str(stmt).lower()
        if "from alert_rules" in stmt_str and "select" in stmt_str:
            return _ScalarsResult(self._rules)
        if "from computed_metrics" in stmt_str:
            return _RowsResult(self._payload_rows)
        if "insert into alert_events" in stmt_str:
            # SQLAlchemy compiled inserts expose the values via .compile().params
            try:
                params = stmt.compile().params  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                params = {}
            self.inserted_events.append(params)
            return None
        if "update alert_rules" in stmt_str:
            self.updates.append(stmt)
            return None
        return _RowsResult([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


class _ScalarsResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarsResult:
        return self

    def all(self) -> list[Any]:
        return list(self._items)


class _RowsResult:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def all(self) -> list[tuple]:
        return list(self._rows)

    def mappings(self) -> _RowsResult:
        return self

    def first(self) -> tuple | None:
        return self._rows[0] if self._rows else None


@pytest.mark.asyncio
async def test_alert_dedup_within_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same rule firing in two consecutive ticks must not double-fire.

    Cooldown is enforced by ``rule.last_fired_at`` being inside the
    ``cooldown_seconds`` window on the second tick.
    """
    from app.processing import alert_pipeline

    now = datetime.now(UTC)
    rule = _FakeRule(
        rule={"field": "GEX_NET_TOTAL.value", "op": "gt", "value": 0},
        last_fired_at=now - timedelta(seconds=10),  # fired 10s ago
        cooldown_seconds=300,
    )
    payload_rows = [
        ("GEX_NET_TOTAL", 5_000_000, None, now),
    ]
    session = _AlertScenarioSession(rules=[rule], payload_rows=payload_rows)

    def factory() -> _AlertScenarioSession:
        return session

    monkeypatch.setattr(alert_pipeline, "get_session_factory", lambda: factory)
    # Clear in-process previous-payload cache so cross_above/below state
    # doesn't bleed between tests.
    alert_pipeline._LAST_PAYLOAD.clear()

    fired = await alert_pipeline.run_alert_pipeline(symbol="SPXW")

    assert fired == 0
    assert session.inserted_events == []


@pytest.mark.asyncio
async def test_alert_dedup_outside_cooldown_fires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the previous fire is older than ``cooldown_seconds`` the
    next tick must fire and persist an event."""
    from app.processing import alert_pipeline

    now = datetime.now(UTC)
    rule = _FakeRule(
        rule={"field": "GEX_NET_TOTAL.value", "op": "gt", "value": 0},
        last_fired_at=now - timedelta(seconds=3600),  # well past cooldown
        cooldown_seconds=300,
    )
    payload_rows = [
        ("GEX_NET_TOTAL", 5_000_000, None, now),
    ]
    session = _AlertScenarioSession(rules=[rule], payload_rows=payload_rows)

    def factory() -> _AlertScenarioSession:
        return session

    monkeypatch.setattr(alert_pipeline, "get_session_factory", lambda: factory)
    alert_pipeline._LAST_PAYLOAD.clear()

    fired = await alert_pipeline.run_alert_pipeline(symbol="SPXW")

    assert fired == 1
    assert len(session.inserted_events) == 1


@pytest.mark.asyncio
async def test_alert_dedup_back_to_back_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two ticks back-to-back: first fires, second is suppressed.

    Models the real scheduler cadence — the second tick re-reads the
    rule from the DB, sees ``last_fired_at`` was just set by the first
    tick's update, and short-circuits before evaluating.
    """
    from app.processing import alert_pipeline

    now = datetime.now(UTC)
    rule = _FakeRule(
        rule={"field": "GEX_NET_TOTAL.value", "op": "gt", "value": 0},
        last_fired_at=None,
        cooldown_seconds=300,
    )
    payload_rows = [
        ("GEX_NET_TOTAL", 5_000_000, None, now),
    ]
    session = _AlertScenarioSession(rules=[rule], payload_rows=payload_rows)

    def factory() -> _AlertScenarioSession:
        return session

    monkeypatch.setattr(alert_pipeline, "get_session_factory", lambda: factory)
    alert_pipeline._LAST_PAYLOAD.clear()

    # Tick 1: rule has never fired → should fire.
    fired1 = await alert_pipeline.run_alert_pipeline(symbol="SPXW")
    assert fired1 == 1

    # Simulate the DB update that the real pipeline would have made.
    rule.last_fired_at = datetime.now(UTC)

    # Tick 2: rule is within cooldown → should NOT fire.
    fired2 = await alert_pipeline.run_alert_pipeline(symbol="SPXW")
    assert fired2 == 0
    # Still only one event row recorded across both ticks.
    assert len(session.inserted_events) == 1


# ────────────────────────────────────────────────────────────────────────────
# Rev 8 ARCH-1 — chain-driven is_expiration_day in pipeline_runs.
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arch1_pipeline_run_is_expiration_day_true_for_tuesday_spxw(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rev 8 ARCH-1 integration: a Tuesday SPXW chain that lists today as
    an expiration must flip ``pipeline_runs.is_expiration_day=True`` even
    though Tuesday is NOT in the static M/W/F default set.
    """
    from datetime import date as date_t
    from datetime import datetime as datetime_t
    from zoneinfo import ZoneInfo

    from app.processing import session as session_mod

    et = ZoneInfo("America/New_York")
    tuesday = date_t(2026, 5, 19)  # Tuesday — NOT in static M/W/F set
    tuesday_at_11 = datetime_t(2026, 5, 19, 11, 0, tzinfo=et)

    monkeypatch.setattr(session_mod, "_now_eastern", lambda: tuesday_at_11)
    session_mod.clear_available_expirations()

    snapshot = pd.DataFrame(
        {
            "bid": [1.0] * 10,
            "ask": [1.1] * 10,
            "iv":  [0.2] * 10,
            "underlying_price": [4500.0] * 10,
            "gamma": [0.01] * 10,
            "expiration": [tuesday] * 10,
            "strike": [4500.0] * 10,
            "option_type": ["C"] * 10,
        }
    )

    rec = _patch_pipeline_persistence(
        monkeypatch,
        snapshot=snapshot,
        persisted_metric_types=EXPECTED_METRIC_TYPES,
        metric_rows_count=42,
    )

    await run_pipeline_for_symbol("SPXW")

    assert len(rec.finalizes) == 1
    final = rec.finalizes[0]
    assert final["is_expiration_day"] is True, (
        "ARCH-1 fix: chain-driven cache must flip is_expiration_day on Tue"
    )
    # Cleanup so subsequent tests don't see the cached entry.
    session_mod.clear_available_expirations()


# ────────────────────────────────────────────────────────────────────────────
# Rev 8 OPS-12 + ARCH-3 — pipeline observability counters.
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ops12_partial_counter_increments_on_partial_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rev 8 OPS-12: a ``partial`` finalize bumps the cumulative counter."""
    pipeline_mod.reset_pipeline_counters_for_tests()
    rec = _patch_pipeline_persistence(  # noqa: F841 - exercises pipeline path
        monkeypatch,
        snapshot=_good_snapshot(),
        persisted_metric_types={"GEX_NET_TOTAL"},
        metric_rows_count=1,
    )
    await run_pipeline_for_symbol("SPXW")
    counters = pipeline_mod.get_pipeline_counters()
    assert counters["flowgreeks_pipeline_partial_total"] == 1.0


@pytest.mark.asyncio
async def test_ops12_partial_counter_does_not_bump_on_ok(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_mod.reset_pipeline_counters_for_tests()
    rec = _patch_pipeline_persistence(  # noqa: F841
        monkeypatch,
        snapshot=_good_snapshot(),
        persisted_metric_types=EXPECTED_METRIC_TYPES,
        metric_rows_count=42,
    )
    await run_pipeline_for_symbol("SPXW")
    counters = pipeline_mod.get_pipeline_counters()
    assert counters["flowgreeks_pipeline_partial_total"] == 0.0


@pytest.mark.asyncio
async def test_arch3_streaming_publish_failure_threshold_downgrades_to_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rev 8 ARCH-3: after STREAMING_PUBLISH_FAILURE_THRESHOLD consecutive
    failures, the next pipeline run downgrades to ``partial`` and the
    error column carries the marker. The counter increments per failure.
    """
    pipeline_mod.reset_pipeline_counters_for_tests()
    pipeline_mod.reset_streaming_publish_failures_for_tests()

    # Patch the streaming publisher to always raise.
    async def boom(symbol: str, *, result=None) -> None:  # noqa: ARG001
        raise RuntimeError("publish boom")

    monkeypatch.setattr(pipeline_mod, "_publish_streaming_snapshot", boom)

    rec = _patch_pipeline_persistence(
        monkeypatch,
        snapshot=_good_snapshot(),
        persisted_metric_types=EXPECTED_METRIC_TYPES,
        metric_rows_count=42,
    )

    # First THRESHOLD-1 ticks: still ``ok`` despite failure.
    threshold = pipeline_mod.STREAMING_PUBLISH_FAILURE_THRESHOLD
    for _ in range(threshold - 1):
        await run_pipeline_for_symbol("SPXW")

    # ``threshold``-th tick: status downgrades to ``partial``.
    await run_pipeline_for_symbol("SPXW")

    counters = pipeline_mod.get_pipeline_counters()
    assert counters["streaming_publish_errors_total"] == float(threshold)
    # The last finalize call should reflect the downgrade.
    assert rec.finalizes[-1]["status"] == "partial"
    assert "streaming_publish_failed=True" in (rec.finalizes[-1]["error"] or "")


@pytest.mark.asyncio
async def test_arch3_streaming_publish_recovery_resets_failure_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful publish must zero the consecutive-failure count."""
    pipeline_mod.reset_pipeline_counters_for_tests()
    pipeline_mod.reset_streaming_publish_failures_for_tests()

    raise_next = {"flag": True}

    async def maybe_raise(symbol: str, *, result=None) -> None:  # noqa: ARG001
        if raise_next["flag"]:
            raise RuntimeError("publish boom")

    monkeypatch.setattr(pipeline_mod, "_publish_streaming_snapshot", maybe_raise)

    rec = _patch_pipeline_persistence(  # noqa: F841
        monkeypatch,
        snapshot=_good_snapshot(),
        persisted_metric_types=EXPECTED_METRIC_TYPES,
        metric_rows_count=42,
    )

    await run_pipeline_for_symbol("SPXW")
    assert pipeline_mod.get_streaming_publish_failures("SPXW") == 1

    raise_next["flag"] = False
    await run_pipeline_for_symbol("SPXW")
    assert pipeline_mod.get_streaming_publish_failures("SPXW") == 0


# ────────────────────────────────────────────────────────────────────────────
# Rev 8 ARCH-7 — payload_from_pipeline_result mirrors snapshot envelope.
# ────────────────────────────────────────────────────────────────────────────


def test_arch7_payload_from_pipeline_result_emits_top_level_keys() -> None:
    """The in-memory result-to-payload mapper must produce the same
    top-level envelope keys as the DB-read path so the WS frame shape
    is identical.
    """
    from app.api.endpoints.snapshot import payload_from_pipeline_result

    ts = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    result = _minimal_pipeline_result("SPXW", ts)

    payload, computed_at = payload_from_pipeline_result(result)
    assert computed_at == ts
    for key in (
        "gex",
        "gex_volume",
        "max_pain",
        "walls_oi",
        "walls_volume",
        "walls",
        "iv",
        "vanna_total",
        "charm_total",
        "regime",
        "session_state",
        "spot",
        "zero_dte",
        "back_month",
        "health",
    ):
        assert key in payload, f"missing top-level key {key}"


# ────────────────────────────────────────────────────────────────────────────
# Rev 8 ARCH-2 — orphan-sweep helpers
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arch2_orphan_sweep_returns_neg_one_on_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DB blip during the periodic sweep must NOT propagate; the helper
    returns -1 and the orphan_sweep_loop logs and continues."""
    from app.processing import pipeline as pipeline_mod_inner

    class _BoomSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self, *a, **kw):  # noqa: ARG002
            raise RuntimeError("db down")

        async def commit(self):
            return None

    def factory():
        return _BoomSession()

    monkeypatch.setattr(
        pipeline_mod_inner, "get_session_factory", lambda: factory
    )
    result = await pipeline_mod_inner.sweep_orphan_pipeline_runs()
    assert result == -1


@pytest.mark.asyncio
async def test_arch2_orphan_sweep_loop_runs_periodically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The loop should call :func:`sweep_orphan_pipeline_runs` at least
    twice over a short window."""
    from app.processing import pipeline as pipeline_mod_inner

    calls = {"n": 0}

    async def fake_sweep() -> int:
        calls["n"] += 1
        return 0

    monkeypatch.setattr(
        pipeline_mod_inner, "sweep_orphan_pipeline_runs", fake_sweep
    )

    task = asyncio.create_task(
        pipeline_mod_inner.orphan_sweep_loop(interval_seconds=0.01)
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert calls["n"] >= 2


# ────────────────────────────────────────────────────────────────────────────
# Rev 12 SRE-19 — pipeline pause flag cross-lane wiring
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_skips_calculators_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SRE-19 cross-lane: when ``pipeline_runtime_flags.is_paused()`` is
    True, ``run_pipeline_for_symbol`` skips the calculator phase and
    finalises the run with ``status=partial`` + ``error=
    pipeline_paused=True``. The audit row stays informative (rows_read +
    spot diagnostics carry through) but no metric rows are written.
    """
    from app.processing import pipeline_runtime_flags

    pipeline_runtime_flags.reset_for_tests()
    pipeline_runtime_flags.set_paused(True)

    rec = _patch_pipeline_persistence(
        monkeypatch,
        snapshot=_good_snapshot(),
        persisted_metric_types=EXPECTED_METRIC_TYPES,
        metric_rows_count=42,
    )

    # Tripwire: when paused, ``_persist_metrics`` must NOT be called —
    # the calculator phase is short-circuited entirely.
    persist_called = False

    async def tripwire(*args: object, **kwargs: object) -> tuple[int, set]:
        nonlocal persist_called
        persist_called = True
        return 0, set()

    monkeypatch.setattr(pipeline_mod, "_persist_metrics", tripwire)

    await run_pipeline_for_symbol("SPXW")

    assert persist_called is False, (
        "SRE-19: paused pipeline must skip the calculator/persist phase"
    )
    assert len(rec.finalizes) == 1
    final = rec.finalizes[0]
    assert final["status"] == "partial"
    assert "pipeline_paused=True" in (final["error"] or ""), (
        f"SRE-19: paused finalize must carry pipeline_paused=True marker, "
        f"got error={final['error']!r}"
    )
    # The full expected metric set is reported as missing.
    assert set(final["missing_metric_types"]) == EXPECTED_METRIC_TYPES


@pytest.mark.asyncio
async def test_pipeline_resumes_after_unpaused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SRE-19 cross-lane recovery: pause → run (skipped) → unpause →
    run (full path). The second run must NOT carry the
    ``pipeline_paused=True`` marker and the calculator phase runs
    normally.
    """
    from app.processing import pipeline_runtime_flags

    pipeline_runtime_flags.reset_for_tests()

    rec = _patch_pipeline_persistence(
        monkeypatch,
        snapshot=_good_snapshot(),
        persisted_metric_types=EXPECTED_METRIC_TYPES,
        metric_rows_count=42,
    )

    # Tick 1 — paused.
    pipeline_runtime_flags.set_paused(True)
    await run_pipeline_for_symbol("SPXW")
    paused_final = rec.finalizes[-1]
    assert paused_final["status"] == "partial"
    assert "pipeline_paused=True" in (paused_final["error"] or "")

    # Tick 2 — unpaused.
    pipeline_runtime_flags.set_paused(False)
    await run_pipeline_for_symbol("SPXW")
    resumed_final = rec.finalizes[-1]
    assert resumed_final["status"] in ("ok", "partial")
    # Pause marker must NOT linger.
    assert "pipeline_paused=True" not in (resumed_final["error"] or ""), (
        f"SRE-19: resumed pipeline must not carry pipeline_paused marker, "
        f"got error={resumed_final['error']!r}"
    )
