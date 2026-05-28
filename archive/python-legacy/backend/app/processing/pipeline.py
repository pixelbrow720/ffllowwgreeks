"""Compute pipeline: load the latest snapshot, run all metrics, persist results.

Rev 3 hardening (Agent 7):

* Every ``_persist_metrics`` call is wrapped in a single DB transaction so a
  partial failure rolls back cleanly — there is no half-written metric set.
* The loader's snapshot is sanity-checked for minimum coverage (bid+ask **or**
  IV present on ≥30% of rows). If neither holds, the tick is recorded as
  ``partial`` in ``pipeline_runs`` and metric computation is skipped.
* Every scheduler tick per symbol now persists a row to ``pipeline_runs``
  with ``started_at`` / ``finished_at`` / ``duration_ms`` / ``status`` /
  ``rows_read`` / ``metric_rows_written`` / ``missing_metric_types`` /
  ``error``.
* After ``_persist_metrics``, the latest ``metric_type`` set for the run's
  ``(symbol, ts)`` is diffed against :data:`EXPECTED_METRIC_TYPES`. Any
  shortfall is surfaced via ``missing_metric_types`` and downgrades the run
  status from ``ok`` to ``partial``.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from time import perf_counter

import orjson
import pandas as pd
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.db.models import ComputedMetric, PipelineRun, SessionEvent
from app.db.session import get_session_factory
from app.processing import move_tracker as move_tracker_mod
from app.processing import pipeline_runtime_flags
from app.processing.gex import GexSummary, compute_gex
from app.processing.iv import IVSummary, compute_iv_summary, fill_missing_iv_async
from app.processing.loader import load_latest_snapshot
from app.processing.max_pain import MaxPainSummary, compute_max_pain
from app.processing.move_tracker import MoveSnapshot, compute_move_tracker
from app.processing.pin_probability import compute_pin_probability
from app.processing.regime import RegimeSummary, compute_regime
from app.processing.session import (
    is_expiration_day,
    is_rth_now,
    session_snapshot,
    set_available_expirations,
    time_to_expiry_0dte_years,
)
from app.processing.spot import (
    SpotResult,
    reset_basis_cache,
    resolve_spot,
    spot_result_to_payload,
)
from app.processing.term_structure import compute_term_structure
from app.processing.vanna_charm import GreekSummary, compute_charm, compute_vanna
from app.processing.walls import WallsSummary, compute_walls
from app.processing.zero_dte import (
    BackMonthSummary,
    ZeroDteSummary,
    compute_back_month_summary,
    compute_zero_dte_summary,
)

logger = get_logger(__name__)


# ── Completeness contract ────────────────────────────────────────────────────
#
# The canonical list of ``metric_type`` discriminators that a single chain-
# pipeline tick is expected to produce for a "healthy" symbol. The list is
# derived from the ``metric_type`` literals written by :func:`_persist_metrics`
# below plus the Rev 3 additions (vanna/charm/term-structure/move-tracker/
# pin-probability). After every tick we diff the latest persisted set against
# this contract and surface the shortfall as ``pipeline_runs.missing_metric_types``.
#
# Metric types produced by *other* pipelines (``HIRO``, ``BASIS_SPX_ES``,
# ``VOLUME_PROFILE_ES`` from the flow pipeline) are intentionally **not**
# part of this contract — they run on their own cadence and we don't want
# a slow flow pipeline to mark the chain pipeline as partial.
EXPECTED_METRIC_TYPES: frozenset[str] = frozenset(
    {
        "GEX_NET_TOTAL",
        "GEX_LEVEL",
        "GEX_NET_TOTAL_VOL",
        "GEX_LEVEL_VOL",
        "MAX_PAIN",
        "MAX_PAIN_AGG",
        "CALL_WALL_OI",
        "PUT_WALL_OI",
        "CALL_WALL_VOL",
        "PUT_WALL_VOL",
        "ATM_IV",
        "IV_SKEW",
        "IV_SURFACE",
        "REGIME_OI",
        "REGIME_VOL",
        "VANNA_NET_TOTAL",
        "VANNA_LEVEL",
        "CHARM_NET_TOTAL",
        "CHARM_LEVEL",
        "IV_TERM_STRUCTURE",
        "RISK_REVERSAL_25D",
        "MOVE_TRACKER",
        "PIN_PROBABILITY",
        # Rev 4 — 0DTE + back-month split. These rows are always written;
        # on non-0DTE days every 0DTE row has value=0 and an explanatory
        # ``extra_json.reason`` so subscribers don't see gaps.
        "GEX_0DTE_NET_TOTAL",
        "GEX_0DTE_LEVEL",
        "GEX_0DTE_NET_TOTAL_VOL",
        "GEX_0DTE_LEVEL_VOL",
        "GEX_BACK_NET_TOTAL",
        "GEX_BACK_LEVEL",
        "GEX_BACK_NET_TOTAL_VOL",
        "GEX_BACK_LEVEL_VOL",
        "CHARM_0DTE_NET_TOTAL",
        "CHARM_0DTE_LEVEL",
        "CHARM_0DTE_DECAY_RATE",
        "GEX_0DTE_FLIP_SPEED",
        # Spot resolution snapshot — value=price, extra_json carries
        # source/futures/basis diagnostics.
        "SPOT",
    }
)

# Minimum fraction of rows that must carry usable bid+ask **or** IV for a
# snapshot to be considered worth computing on. Set deliberately low — we
# want to compute when the feed is at least partially healthy, but flag
# obviously-broken snapshots before they emit a fleet of zero metrics.
MIN_COVERAGE_FRACTION: float = 0.30


# ── Rev 4: flip-speed cache (symbol → (prev_net_gex_0dte, prev_ts_seconds)) ─
# Module-level so the next tick can compute Δ/Δt. Reset in
# :func:`reset_session_state` so flip-speed doesn't carry overnight noise.
_flip_speed_cache: dict[str, tuple[float, float]] = {}


# ── Rev 8 — observability counters ──────────────────────────────────────────
# Module-level monotonically-increasing counters consumed by the
# ``/admin/metrics`` Prometheus exposition. Kept dependency-free (no
# ``prometheus_client``) to match the existing text-format scrape surface
# in ``app.api.endpoints.admin.admin_metrics``.
_pipeline_counters: dict[str, float] = {
    "flowgreeks_pipeline_partial_total": 0.0,
    "pipeline_run_finalize_errors_total": 0.0,
    "streaming_publish_errors_total": 0.0,
}


def get_pipeline_counters() -> dict[str, float]:
    """Return a defensive copy of the pipeline observability counters."""
    return dict(_pipeline_counters)


def reset_pipeline_counters_for_tests() -> None:
    """Test helper: zero every counter."""
    for key in _pipeline_counters:
        _pipeline_counters[key] = 0.0


# ── Rev 8 ARCH-3: streaming publish failure tracking ────────────────────────
# Per-symbol consecutive failure counter. After
# ``STREAMING_PUBLISH_FAILURE_THRESHOLD`` consecutive failures the next
# pipeline run is downgraded to ``partial`` and audited via
# ``extra_json.streaming_publish_failed=True`` so dashboards can flag the
# symbol without re-querying the WS subsystem.
_streaming_publish_failures: dict[str, int] = {}
STREAMING_PUBLISH_FAILURE_THRESHOLD: int = 3


def reset_streaming_publish_failures_for_tests(symbol: str | None = None) -> None:
    """Test helper: clear consecutive-failure state for ``symbol`` (or all)."""
    if symbol is None:
        _streaming_publish_failures.clear()
    else:
        _streaming_publish_failures.pop(symbol.upper(), None)


def get_streaming_publish_failures(symbol: str) -> int:
    """Return the current consecutive-failure count for ``symbol``."""
    return _streaming_publish_failures.get(symbol.upper(), 0)


# ── Rev 8 ARCH-2: orphan sweep cadence ──────────────────────────────────────
# A ``running`` row stuck for >15 min is treated as a crashed worker. The
# startup sweep in ``app.main.lifespan`` runs once; this in-process loop
# runs every 5 minutes alongside the scheduler so a finalize that silently
# fails (DB blip swallowed inside ``_finalize_pipeline_run``) is reaped
# without operator intervention.
ORPHAN_SWEEP_INTERVAL_SECONDS: float = 300.0
ORPHAN_SWEEP_THRESHOLD_MINUTES: int = 15


def reset_flip_speed_cache(symbol: str | None = None) -> None:
    """Drop cached previous-tick GEX (used at session open + in tests)."""
    if symbol is None:
        _flip_speed_cache.clear()
    else:
        _flip_speed_cache.pop(symbol.upper(), None)


def _extract_available_expirations(df: pd.DataFrame) -> frozenset[date]:
    """Return the distinct ``date`` set listed in the chain's ``expiration`` column."""
    if df is None or df.empty or "expiration" not in df.columns:
        return frozenset()
    exp_set: set[date] = set()
    for raw in df["expiration"].dropna().unique():
        try:
            exp_set.add(pd.Timestamp(raw).date())
        except (TypeError, ValueError):
            continue
    return frozenset(exp_set)


@dataclass
class PipelineResult:
    symbol: str
    ts: datetime
    duration_ms: float
    rows: int
    gex: GexSummary
    gex_volume: GexSummary
    max_pain: MaxPainSummary
    walls: WallsSummary
    iv: IVSummary
    regime: RegimeSummary
    vanna: GreekSummary
    charm: GreekSummary
    term_structure: list[dict]
    move_tracker: MoveSnapshot
    pin_probability: list[dict]
    spot: SpotResult | None = None
    session_state: dict[str, object] | None = None
    zero_dte: ZeroDteSummary | None = None
    back_month: BackMonthSummary | None = None
    available_expirations: frozenset[date] = frozenset()


def _coverage_ok(df: pd.DataFrame) -> tuple[bool, dict[str, float]]:
    """Return (acceptable, diagnostics) for the loader's chain snapshot.

    A snapshot is acceptable when **either**:

    * ``bid`` *and* ``ask`` are present on at least
      :data:`MIN_COVERAGE_FRACTION` of rows, **or**
    * ``iv`` is present on at least :data:`MIN_COVERAGE_FRACTION` of rows.

    Diagnostics are returned alongside so callers can log them as
    structured context on the partial-run warning.
    """
    total = int(len(df))
    if total == 0:
        return False, {"rows_total": 0.0}

    have_bid = float(df["bid"].notna().sum()) if "bid" in df.columns else 0.0
    have_ask = float(df["ask"].notna().sum()) if "ask" in df.columns else 0.0
    have_iv = float(df["iv"].notna().sum()) if "iv" in df.columns else 0.0
    bid_ask_present = (
        float(((df["bid"].notna()) & (df["ask"].notna())).sum())
        if {"bid", "ask"}.issubset(df.columns)
        else 0.0
    )

    quote_frac = bid_ask_present / total
    iv_frac = have_iv / total
    diagnostics = {
        "rows_total": float(total),
        "rows_with_bid": have_bid,
        "rows_with_ask": have_ask,
        "rows_with_bid_and_ask": bid_ask_present,
        "rows_with_iv": have_iv,
        "quote_fraction": round(quote_frac, 4),
        "iv_fraction": round(iv_frac, 4),
        "min_required_fraction": MIN_COVERAGE_FRACTION,
    }
    acceptable = (
        quote_frac >= MIN_COVERAGE_FRACTION or iv_frac >= MIN_COVERAGE_FRACTION
    )
    return acceptable, diagnostics


async def _persist_metrics(
    session: AsyncSession, *, symbol: str, ts: datetime, result: PipelineResult
) -> tuple[int, set[str]]:
    """Upsert all metrics into ``computed_metrics`` inside a single transaction.

    If any statement in the transaction fails the entire upsert is rolled
    back so the next tick observes the prior state, not a half-written one.
    Returns ``(row_count, persisted_metric_type_set)`` so the caller can
    derive ``missing_metric_types`` from the in-memory set instead of
    re-querying the DB on a fresh session (saves one round-trip per tick).
    """
    rows: list[dict] = []
    sentinel_expiry = pd.Timestamp("1970-01-01").date()

    # GEX rows — both OI-weighted and Volume-weighted variants are persisted
    # under distinct metric_type discriminators so the API can expose both
    # in /v1/{symbol}/snapshot.
    for gex_summary, total_type, level_type in (
        (result.gex,        "GEX_NET_TOTAL",     "GEX_LEVEL"),
        (result.gex_volume, "GEX_NET_TOTAL_VOL", "GEX_LEVEL_VOL"),
    ):
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": total_type,
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": gex_summary.net_total,
                "extra_json": {
                    "underlying_price": gex_summary.underlying_price,
                    "curve": gex_summary.curve,
                    "top_positive": gex_summary.top_positive,
                    "top_negative": gex_summary.top_negative,
                    "zero_gamma": gex_summary.zero_gamma,
                    "weight_col": gex_summary.weight_col,
                    "weight_source": gex_summary.weight_source,
                },
            }
        )
        for level in gex_summary.curve:
            rows.append(
                {
                    "ts": ts,
                    "symbol": symbol,
                    "metric_type": level_type,
                    "strike": level["strike"],
                    "expiration": sentinel_expiry,
                    "computed_at": ts,
                    "value": level.get("net_gex", 0.0),
                    "extra_json": level,
                }
            )

    # Max pain
    for entry in result.max_pain.per_expiry:
        if entry["strike"] is None:
            continue
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "MAX_PAIN",
                "strike": entry["strike"],
                "expiration": pd.Timestamp(entry["expiration"]).date(),
                "computed_at": ts,
                "value": entry.get("pain"),
                "extra_json": {"curve": entry.get("curve", [])},
            }
        )
    if result.max_pain.aggregate_strike is not None:
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "MAX_PAIN_AGG",
                "strike": result.max_pain.aggregate_strike,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": result.max_pain.aggregate_value,
                "extra_json": {"window_expiries": 5},
            }
        )

    # Walls — mirror the GEX provenance pattern: persist ``weight_source``
    # alongside the rank so consumers can tell whether the wall ranking was
    # driven by real OI vs the uniform-weight fallback.
    for kind, payload in (("OI", result.walls.by_oi), ("VOL", result.walls.by_volume)):
        weight_source = payload.get("weight_source")
        for side, arr in (("CALL_WALL", payload.get("call_wall", [])),
                          ("PUT_WALL", payload.get("put_wall", []))):
            metric_type = f"{side}_{kind}"
            for rank, entry in enumerate(arr, start=1):
                rows.append(
                    {
                        "ts": ts,
                        "symbol": symbol,
                        "metric_type": metric_type,
                        "strike": entry["strike"],
                        "expiration": sentinel_expiry,
                        "computed_at": ts,
                        "value": entry["value"],
                        "extra_json": {
                            "rank": rank,
                            "weight_source": weight_source,
                        },
                    }
                )

    # IV
    if result.iv.atm_iv is not None:
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "ATM_IV",
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": result.iv.atm_iv,
                "extra_json": None,
            }
        )
    for expiry, skew_value in result.iv.skew_per_expiry.items():
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "IV_SKEW",
                "strike": 0,
                "expiration": pd.Timestamp(expiry).date(),
                "computed_at": ts,
                "value": skew_value,
                "extra_json": None,
            }
        )
    if result.iv.surface:
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "IV_SURFACE",
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": None,
                "extra_json": {"surface": result.iv.surface},
            }
        )

    # Regime (one row per mode, score in [-1, +1] in `value`).
    for mode_name, mode_payload in (("REGIME_OI", result.regime.oi),
                                    ("REGIME_VOL", result.regime.vol)):
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": mode_name,
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": mode_payload.score,
                "extra_json": {
                    "label": mode_payload.label,
                    "call_wall_total": mode_payload.call_wall_total,
                    "put_wall_total": mode_payload.put_wall_total,
                    "net_gex": mode_payload.net_gex,
                },
            }
        )

    # ── Vanna & Charm (mirror of GEX persistence layout) ─────────────────
    for greek_summary, total_type, level_type in (
        (result.vanna, "VANNA_NET_TOTAL", "VANNA_LEVEL"),
        (result.charm, "CHARM_NET_TOTAL", "CHARM_LEVEL"),
    ):
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": total_type,
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": greek_summary.net_total,
                "extra_json": {
                    "underlying_price": greek_summary.underlying_price,
                    "curve": greek_summary.curve,
                    "top_positive": greek_summary.top_positive,
                    "top_negative": greek_summary.top_negative,
                    "weight_col": greek_summary.weight_col,
                },
            }
        )
        for level in greek_summary.curve:
            value = level.get("vanna_exposure",
                              level.get("charm_exposure", 0.0))
            rows.append(
                {
                    "ts": ts,
                    "symbol": symbol,
                    "metric_type": level_type,
                    "strike": level["strike"],
                    "expiration": sentinel_expiry,
                    "computed_at": ts,
                    "value": value,
                    "extra_json": level,
                }
            )

    # ── Term-structure (one row per expiration) ──────────────────────────
    for entry in result.term_structure:
        try:
            exp_date = pd.Timestamp(entry["expiration"]).date()
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "IV_TERM_STRUCTURE",
                "strike": 0,
                "expiration": exp_date,
                "computed_at": ts,
                "value": entry.get("atm_iv"),
                "extra_json": entry,
            }
        )
        if entry.get("risk_reversal_25d") is not None:
            rows.append(
                {
                    "ts": ts,
                    "symbol": symbol,
                    "metric_type": "RISK_REVERSAL_25D",
                    "strike": 0,
                    "expiration": exp_date,
                    "computed_at": ts,
                    "value": entry["risk_reversal_25d"],
                    "extra_json": {
                        "call_25d_iv": entry.get("call_25d_iv"),
                        "put_25d_iv": entry.get("put_25d_iv"),
                    },
                }
            )

    # ── Realized vs Implied Move tracker (single row) ────────────────────
    if (
        result.move_tracker.realized_move is not None
        or result.move_tracker.implied_move is not None
    ):
        move_extra: dict[str, object] = {
            "underlying_price": result.move_tracker.underlying_price,
            "open_price": result.move_tracker.open_price,
            "realized_move": result.move_tracker.realized_move,
            "implied_move": result.move_tracker.implied_move,
            "implied_dte": result.move_tracker.implied_dte,
            "ratio": result.move_tracker.ratio,
        }
        if result.move_tracker.reason is not None:
            move_extra["reason"] = result.move_tracker.reason
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "MOVE_TRACKER",
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": result.move_tracker.ratio,
                "extra_json": move_extra,
            }
        )

    # ── Pin probability heatmap (one row per 0DTE strike) ────────────────
    if result.pin_probability:
        for entry in result.pin_probability:
            rows.append(
                {
                    "ts": ts,
                    "symbol": symbol,
                    "metric_type": "PIN_PROBABILITY",
                    "strike": entry["strike"],
                    "expiration": sentinel_expiry,
                    "computed_at": ts,
                    "value": entry["prob"],
                    "extra_json": entry,
                }
            )
    else:
        # Sentinel row so EXPECTED_METRIC_TYPES sees PIN_PROBABILITY even
        # when the chain produced no rows. Distinguish "no 0DTE today"
        # (non-expiration day) from "empty pin result" (expiration day
        # but compute_pin_probability returned []).
        reason = (
            "empty_pin_result"
            if is_expiration_day(symbol, available_expirations=result.available_expirations)
            else "no_0dte_today"
        )
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "PIN_PROBABILITY",
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": None,
                "extra_json": {"reason": reason},
            }
        )

    # ── Rev 4: 0DTE-specific + back-month split ──────────────────────────
    # Always written, even on non-0DTE days, with value=0 and an
    # explanatory ``extra_json.reason``. This keeps the completeness
    # check happy and lets the UI distinguish "no 0DTE today" from
    # "computation failed".
    if result.zero_dte is not None:
        zdte = result.zero_dte
        reason = None if zdte.has_0dte else "no_0dte_today"
        # Net totals (always one row, even when has_0dte=False).
        for summary, total_type, level_type in (
            (zdte.gex_oi, "GEX_0DTE_NET_TOTAL", "GEX_0DTE_LEVEL"),
            (zdte.gex_vol, "GEX_0DTE_NET_TOTAL_VOL", "GEX_0DTE_LEVEL_VOL"),
        ):
            rows.append(
                {
                    "ts": ts,
                    "symbol": symbol,
                    "metric_type": total_type,
                    "strike": 0,
                    "expiration": sentinel_expiry,
                    "computed_at": ts,
                    "value": summary.net_total,
                    "extra_json": {
                        "underlying_price": summary.underlying_price,
                        "curve": summary.curve,
                        "top_positive": summary.top_positive,
                        "top_negative": summary.top_negative,
                        "zero_gamma": summary.zero_gamma,
                        "tau_years": zdte.tau_years,
                        "reason": reason,
                    },
                }
            )
            for level in summary.curve:
                rows.append(
                    {
                        "ts": ts,
                        "symbol": symbol,
                        "metric_type": level_type,
                        "strike": level["strike"],
                        "expiration": sentinel_expiry,
                        "computed_at": ts,
                        "value": level.get("net_gex", 0.0),
                        "extra_json": level,
                    }
                )

        # Charm rows (0DTE cohort only).
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "CHARM_0DTE_NET_TOTAL",
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": zdte.charm.net_total,
                "extra_json": {
                    "underlying_price": zdte.charm.underlying_price,
                    "curve": zdte.charm.curve,
                    "tau_years": zdte.tau_years,
                    "reason": reason,
                },
            }
        )
        for level in zdte.charm.curve:
            rows.append(
                {
                    "ts": ts,
                    "symbol": symbol,
                    "metric_type": "CHARM_0DTE_LEVEL",
                    "strike": level["strike"],
                    "expiration": sentinel_expiry,
                    "computed_at": ts,
                    "value": level.get("charm_exposure", 0.0),
                    "extra_json": level,
                }
            )

        # Scalars: decay rate + flip speed.
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "CHARM_0DTE_DECAY_RATE",
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": zdte.charm_decay_rate,
                "extra_json": {"reason": reason, "tau_years": zdte.tau_years},
            }
        )
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "GEX_0DTE_FLIP_SPEED",
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": zdte.flip_speed,
                "extra_json": {"reason": reason},
            }
        )

    if result.back_month is not None:
        bm = result.back_month
        for summary, total_type, level_type in (
            (bm.gex_oi, "GEX_BACK_NET_TOTAL", "GEX_BACK_LEVEL"),
            (bm.gex_vol, "GEX_BACK_NET_TOTAL_VOL", "GEX_BACK_LEVEL_VOL"),
        ):
            rows.append(
                {
                    "ts": ts,
                    "symbol": symbol,
                    "metric_type": total_type,
                    "strike": 0,
                    "expiration": sentinel_expiry,
                    "computed_at": ts,
                    "value": summary.net_total,
                    "extra_json": {
                        "underlying_price": summary.underlying_price,
                        "curve": summary.curve,
                        "top_positive": summary.top_positive,
                        "top_negative": summary.top_negative,
                        "zero_gamma": summary.zero_gamma,
                    },
                }
            )
            for level in summary.curve:
                rows.append(
                    {
                        "ts": ts,
                        "symbol": symbol,
                        "metric_type": level_type,
                        "strike": level["strike"],
                        "expiration": sentinel_expiry,
                        "computed_at": ts,
                        "value": level.get("net_gex", 0.0),
                        "extra_json": level,
                    }
                )

    # ── Rev 4: persist spot resolution result so /v1/{symbol}/spot can
    # serve the most recent reading without re-running the resolver.
    if result.spot is not None:
        rows.append(
            {
                "ts": ts,
                "symbol": symbol,
                "metric_type": "SPOT",
                "strike": 0,
                "expiration": sentinel_expiry,
                "computed_at": ts,
                "value": float(result.spot.price),
                "extra_json": spot_result_to_payload(result.spot),
            }
        )

    if not rows:
        return 0, set()

    stmt = insert(ComputedMetric).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ts", "symbol", "metric_type", "strike", "expiration"],
        set_={
            "computed_at": stmt.excluded.computed_at,
            "value": stmt.excluded.value,
            "extra_json": stmt.excluded.extra_json,
        },
    )
    # Atomicity: a single execute is already a single statement, but we
    # bracket commit/rollback explicitly so any future multi-statement
    # additions inherit the same "all or nothing" semantics.
    try:
        await session.execute(stmt)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    persisted = {row["metric_type"] for row in rows}
    return len(rows), persisted


async def _latest_persisted_metric_types(
    session: AsyncSession, *, symbol: str, ts: datetime
) -> set[str]:
    """Return the distinct ``metric_type`` set persisted at (symbol, ts)."""
    stmt = (
        select(ComputedMetric.metric_type)
        .where(ComputedMetric.symbol == symbol)
        .where(ComputedMetric.ts == ts)
        .distinct()
    )
    res = await session.execute(stmt)
    return {row[0] for row in res.all()}


def _missing_metric_types(persisted: set[str]) -> list[str]:
    """Diff a persisted set against :data:`EXPECTED_METRIC_TYPES`."""
    return sorted(EXPECTED_METRIC_TYPES - persisted)


async def _insert_pipeline_run(
    *, run_id: uuid.UUID, symbol: str, started_at: datetime
) -> None:
    """Insert the initial ``pipeline_runs`` row with status='running'.

    Uses its own session/transaction so the audit trail survives any
    later rollback of the metrics transaction.
    """
    factory = get_session_factory()
    async with factory() as s:
        s.add(
            PipelineRun(
                id=run_id,
                symbol=symbol,
                started_at=started_at,
                status="running",
            )
        )
        await s.commit()


async def _finalize_pipeline_run(
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
    is_expiration_day: bool = False,
    spot_source: str | None = None,
    spot_price: float | None = None,
    tau_0dte_years: float | None = None,
) -> None:
    """Update the previously-inserted ``pipeline_runs`` row with the result."""
    factory = get_session_factory()
    async with factory() as s:
        try:
            await s.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .values(
                    started_at=started_at,
                    finished_at=finished_at,
                    duration_ms=duration_ms,
                    status=status,
                    rows_read=rows_read,
                    metric_rows_written=metric_rows_written,
                    missing_metric_types=missing_metric_types,
                    error=error,
                    is_expiration_day=is_expiration_day,
                    spot_source=spot_source,
                    spot_price=spot_price,
                    tau_0dte_years=tau_0dte_years,
                )
            )
            await s.commit()
        except Exception:
            await s.rollback()
            # Rev 8 ARCH-2: a finalize that silently fails leaves the audit
            # row stuck ``running``. Bump the counter so a Prometheus alert
            # can catch it; the in-process orphan sweep below reaps the row
            # within ``ORPHAN_SWEEP_INTERVAL_SECONDS``.
            _pipeline_counters["pipeline_run_finalize_errors_total"] += 1.0
            # Never let audit-log persistence kill the pipeline tick.
            logger.exception(
                "pipeline_run_persist_error", symbol=None, run_id=str(run_id)
            )
            return


async def run_pipeline_for_symbol(symbol: str) -> PipelineResult | None:
    """Run the chain pipeline for ``symbol`` once.

    Every invocation persists exactly one ``pipeline_runs`` row regardless
    of whether the tick produced metrics. The row's ``status`` follows:

    * ``ok``       — snapshot loaded, coverage OK, ``_persist_metrics`` returned
      a complete metric set.
    * ``partial``  — snapshot was empty / under-covered / metric set missed
      some of :data:`EXPECTED_METRIC_TYPES`.
    * ``failed``   — an exception escaped one of the steps.
    """
    settings = get_settings()
    factory = get_session_factory()
    started = perf_counter()
    started_at = datetime.now(UTC)
    ts = started_at.replace(microsecond=0)

    run_id = uuid.uuid4()
    await _insert_pipeline_run(run_id=run_id, symbol=symbol, started_at=started_at)

    status: str = "ok"
    error_msg: str | None = None
    rows_read: int = 0
    metric_rows_written: int = 0
    missing: list[str] = []
    result: PipelineResult | None = None
    spot: SpotResult | None = None
    # Session snapshot defaults — refreshed AFTER the chain loads so
    # ``is_expiration_day`` consults the live ``_AVAILABLE_EXPIRATIONS``
    # cache populated below (Rev 8 ARCH-1).
    sess_state: dict[str, object] = session_snapshot(symbol=symbol)
    is_exp_today = bool(sess_state.get("is_expiration_day", False))
    tau_years = float(sess_state.get("time_to_expiry_0dte_years", 0.0))

    try:
        async with factory() as session:
            df = await load_latest_snapshot(session, symbol)
            # ── Rev 4: resolve spot via futures-first chain BEFORE metrics.
            #     The result overrides ``underlying_price`` on every row so
            #     every Greek computation downstream sees the same S.
            spot = await resolve_spot(symbol, df, session)
        rows_read = int(len(df))

        # Rev 12 SRE-19: operator-driven pause flag. We still load the chain
        # and resolve spot above so the audit row stays informative; only the
        # IV-fill + calculator phase is skipped. Tick is recorded as
        # ``partial`` with ``error=pipeline_paused=True``.
        paused = pipeline_runtime_flags.is_paused()

        # ── Rev 8 OPS-7: late session-open price capture ──────────────
        # ``reset_session_state`` couldn't capture the open print (GLBX
        # cold-start at 09:29 ET); retry on the next ``SESSION_OPEN_RETRY_
        # ATTEMPTS`` ticks until a fresh non-stale spot lands. A successful
        # capture writes a ``session_open_price_late`` audit row.
        sym_u = symbol.upper()
        if (
            spot is not None
            and spot.source != "stale_cache"
            and _pending_session_open_capture.get(sym_u, 0) > 0
        ):
            late_price = float(spot.price)
            move_tracker_mod.set_session_open_price(symbol, late_price)
            _pending_session_open_capture.pop(sym_u, None)
            await _record_session_event(
                event_type="session_open_price_late",
                symbol=symbol,
                extra={
                    "session_open_price": late_price,
                    "capture_source": spot.source,
                    "session_open_price_set": True,
                },
            )
        elif (
            spot is None or spot.source == "stale_cache"
        ) and _pending_session_open_capture.get(sym_u, 0) > 0:
            remaining = _pending_session_open_capture[sym_u] - 1
            if remaining <= 0:
                _pending_session_open_capture.pop(sym_u, None)
                logger.warning(
                    "session_open_price_capture_abandoned",
                    symbol=symbol,
                    hint=(
                        "Could not resolve a fresh spot in "
                        f"{SESSION_OPEN_RETRY_ATTEMPTS} ticks; realized_move "
                        "will fall back to chain.underlying_price."
                    ),
                )
            else:
                _pending_session_open_capture[sym_u] = remaining

        # ── Rev 8 ARCH-1: register the chain's actual expirations BEFORE
        # any session_snapshot / is_expiration_day call downstream so
        # Tue/Thu SPXW expiries are recognised by audit + envelope. The
        # set is also threaded through the PipelineResult for the
        # PIN_PROBABILITY sentinel branch in ``_persist_metrics``.
        chain_expirations = _extract_available_expirations(df)
        if chain_expirations:
            set_available_expirations(symbol, chain_expirations)
        # Re-snapshot with the cache now warm so the audit row + envelope
        # both reflect the chain-driven expiration day.
        sess_state = session_snapshot(symbol=symbol)
        is_exp_today = bool(sess_state.get("is_expiration_day", False))
        tau_years = float(sess_state.get("time_to_expiry_0dte_years", 0.0))

        if spot is not None and not df.empty:
            # ``df`` is a fresh DataFrame from ``load_latest_snapshot`` —
            # no shared ownership, so an in-place column write is safe and
            # avoids a ~10–20 MB allocation on every tick for SPX.
            df["underlying_price"] = float(spot.price)

        if df.empty:
            logger.info("pipeline_no_data", symbol=symbol)
            status = "partial"
            missing = sorted(EXPECTED_METRIC_TYPES)
        else:
            # Run IV inversion before the coverage check so synthesized IV
            # also counts toward the threshold. CPU-bound — runs in a
            # worker thread so the event loop keeps serving WS/SSE.
            df = await fill_missing_iv_async(
                df, risk_free_rate=settings.risk_free_rate
            )
            cov_ok, cov_diag = _coverage_ok(df)
            if paused:
                logger.info("pipeline_paused", symbol=symbol)
                status = "partial"
                error_msg = "pipeline_paused=True"
                missing = sorted(EXPECTED_METRIC_TYPES)
            elif not cov_ok:
                logger.warning(
                    "pipeline_low_coverage",
                    symbol=symbol,
                    **cov_diag,
                    hint=(
                        "Skipping metric computation: neither bid+ask nor IV "
                        f"meets the {MIN_COVERAGE_FRACTION:.0%} coverage "
                        "threshold. Check feed health (cmbp-1 NBBO present?)."
                    ),
                )
                status = "partial"
                missing = sorted(EXPECTED_METRIC_TYPES)
            else:
                result = _compute_metrics(df=df, symbol=symbol, ts=ts, settings=settings)
                result.spot = spot
                result.session_state = sess_state

                async with factory() as session:
                    # CORR-3 (Rev 12): use full-precision ``started_at`` for
                    # the metrics PK to avoid collapsing subsecond ticks.
                    # The pipeline_runs audit row keeps ``ts`` (microsecond=0)
                    # for human-readable inspection — only computed_metrics
                    # needs the precision because its PK is
                    # ``(ts, symbol, metric_type, strike, expiration)``.
                    metric_rows_written, persisted = await _persist_metrics(
                        session, symbol=symbol, ts=started_at, result=result
                    )

                # Derive the missing-metrics diff from the in-memory
                # persisted set rather than re-querying — saves one DB
                # round-trip per pipeline tick. ``_latest_persisted_metric_types``
                # remains exported for ad-hoc admin / inspector use.
                missing = _missing_metric_types(persisted)
                status = "ok" if not missing else "partial"

    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error_msg = f"{type(exc).__name__}: {exc}"
        # Drop the partially-computed result: callers must not consume
        # metrics that were never persisted.
        result = None
        logger.exception("pipeline_error", symbol=symbol)

    finished_at = datetime.now(UTC)
    duration_ms = (perf_counter() - started) * 1000

    # ── Rev 8 ARCH-3: streaming publish — execute BEFORE finalize so a
    # consecutive-failure breach can downgrade the audit row to ``partial``
    # in the same write. Failures here must never poison the pipeline tick.
    streaming_publish_failed_now = False
    if status in ("ok", "partial") and result is not None:
        try:
            await _publish_streaming_snapshot(symbol, result=result)
            _streaming_publish_failures.pop(symbol.upper(), None)
        except Exception:  # noqa: BLE001
            _pipeline_counters["streaming_publish_errors_total"] += 1.0
            sym_u = symbol.upper()
            consec = _streaming_publish_failures.get(sym_u, 0) + 1
            _streaming_publish_failures[sym_u] = consec
            streaming_publish_failed_now = (
                consec >= STREAMING_PUBLISH_FAILURE_THRESHOLD
            )
            logger.exception(
                "streaming_publish_failed",
                symbol=symbol,
                consecutive_failures=consec,
            )

    if streaming_publish_failed_now and status == "ok":
        # Surface the breach: status ``partial`` plus a marker baked into
        # ``error`` since ``pipeline_runs`` has no JSONB column. Frontend
        # can split on ``streaming_publish_failed=`` to surface the flag.
        status = "partial"
        marker = "streaming_publish_failed=True"
        error_msg = marker if not error_msg else f"{error_msg}; {marker}"

    await _finalize_pipeline_run(
        run_id=run_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        rows_read=rows_read,
        metric_rows_written=metric_rows_written,
        missing_metric_types=missing,
        error=error_msg,
        is_expiration_day=is_exp_today,
        spot_source=spot.source if spot is not None else None,
        spot_price=float(spot.price) if spot is not None else None,
        tau_0dte_years=tau_years,
    )

    # Rev 8 OPS-12: bump the cumulative ``partial`` counter once finalize
    # has either persisted or failed. Located in the caller (not inside
    # ``_finalize_pipeline_run``) so tests that monkeypatch the finalize
    # helper still observe the counter movement.
    if status == "partial":
        _pipeline_counters["flowgreeks_pipeline_partial_total"] += 1.0

    if result is not None:
        result.duration_ms = duration_ms
        logger.info(
            "pipeline_complete",
            symbol=symbol,
            status=status,
            duration_ms=duration_ms,
            snapshot_rows=rows_read,
            metric_rows=metric_rows_written,
            missing=missing,
        )

    return result


async def _publish_streaming_snapshot(
    symbol: str, *, result: PipelineResult | None = None
) -> None:
    """Build the comprehensive snapshot payload and broadcast to subscribers.

    Imported lazily so the processing module stays decoupled from the API
    surface at import time. The notifier itself drops the oldest queued
    frame for slow subscribers, so this never blocks the pipeline.

    Rev 8 ARCH-7: when ``result`` is supplied (the just-finished tick's
    in-memory payload) we synthesize the WS frame from it directly via
    :func:`app.api.endpoints.snapshot.payload_from_pipeline_result` and
    skip the redundant DB read of metrics this same tick just persisted.
    Cold-cache callers (admin tools, ad-hoc) still get the DB-read path.

    Rev 9 PF-4: the final wire frame is serialised **once** here via
    ``orjson`` and the string handed to the notifier as-is. The notifier
    fans the same string out to every subscriber queue, so 100 subscribers
    do not mean 100 redundant ``json.dumps`` invocations per tick.
    """
    from app.api.endpoints.snapshot import (
        build_snapshot_payload,
        payload_from_pipeline_result,
        set_cached_snapshot,
    )
    from app.api.stream_notifier import get_stream_notifier

    notifier = get_stream_notifier()
    if notifier.subscriber_count(symbol) == 0 and result is None:
        return

    if result is not None:
        payload, computed_at = payload_from_pipeline_result(result)
    else:
        factory = get_session_factory()
        async with factory() as session:
            payload, computed_at = await build_snapshot_payload(session, symbol)
    # Write through so a reconnecting client primes from the in-memory
    # cache rather than re-running the 26-metric batch query.
    set_cached_snapshot(symbol, payload, computed_at)
    if notifier.subscriber_count(symbol) > 0:
        # Single serialisation per tick: the notifier hands the same string
        # reference to every subscriber queue, eliminating 100×150KB of
        # duplicate string allocation on a busy SPX symbol.
        # Rev 12 BC-9: explicit ``type: "snapshot"`` discriminator so
        # consumers can branch uniformly across snapshot / tick /
        # heartbeat / error frames. Additive — pre-Rev-12 clients that
        # checked for ``data`` continue to work.
        # Rev 13 FE-1: reserve a per-symbol monotonic ``seq`` before
        # serialising and pass the same value to ``publish(seq=...)``
        # so the frame embedded in the replay ring buffer carries the
        # same seq the consumer sees on the wire.
        seq = await notifier.next_seq(symbol)
        wire_frame = {
            "type": "snapshot",
            "seq": seq,
            "symbol": symbol.upper(),
            "computed_at": (
                computed_at.isoformat() if computed_at is not None else None
            ),
            "data": payload,
        }
        frame_bytes = orjson.dumps(wire_frame, default=str)
        await notifier.publish(symbol, frame_bytes.decode("utf-8"), seq=seq)


def _compute_metrics(
    *,
    df: pd.DataFrame,
    symbol: str,
    ts: datetime,
    settings,
) -> PipelineResult:
    """Pure-CPU portion of the tick: compute every metric from the snapshot."""
    rows_total = int(len(df))
    have_underlying = (
        int(df["underlying_price"].notna().sum()) if "underlying_price" in df.columns else 0
    )
    have_iv = int(df["iv"].notna().sum()) if "iv" in df.columns else 0
    have_gamma = int(df["gamma"].notna().sum()) if "gamma" in df.columns else 0

    if have_underlying == 0:
        logger.warning(
            "pipeline_no_underlying",
            symbol=symbol,
            rows=rows_total,
            hint=(
                "Spot synthesis failed — chain has no usable bid/ask or last_price. "
                "Check ingester diagnostics in /admin/inspector for dropped schemas "
                "(cmbp-1 not available?) or live record_counts."
            ),
        )
    elif have_iv == 0 or have_gamma == 0:
        logger.warning(
            "pipeline_low_greek_coverage",
            symbol=symbol,
            rows=rows_total,
            have_iv=have_iv,
            have_gamma=have_gamma,
            have_underlying=have_underlying,
        )

    gex = compute_gex(
        df,
        weight_col="oi",
        risk_free_rate=settings.risk_free_rate,
        enable_fallback=True,
        compute_zero_gamma=False,
    )
    gex_vol = compute_gex(
        df,
        weight_col="volume",
        risk_free_rate=settings.risk_free_rate,
        enable_fallback=True,
    )
    mp = compute_max_pain(df)
    walls = compute_walls(df, enable_fallback=True)
    iv = compute_iv_summary(df)
    regime = compute_regime(walls, gex, gex_vol)
    vanna = compute_vanna(df, weight_col="oi", risk_free_rate=settings.risk_free_rate)
    charm = compute_charm(df, weight_col="oi", risk_free_rate=settings.risk_free_rate)
    term_structure = compute_term_structure(df)
    pin_probability = compute_pin_probability(
        df, risk_free_rate=settings.risk_free_rate
    )
    move_tracker = compute_move_tracker(df, open_price=None, symbol=symbol)

    # Rev 4 — 0DTE / back-month split. Pull the prior tick's 0DTE net GEX
    # from the symbol-local cache so we can derive flip speed Δ/Δt.
    prev = _flip_speed_cache.get(symbol)
    now_ts_seconds = ts.timestamp()
    prev_net_gex = prev[0] if prev is not None else None
    prev_ts_seconds = prev[1] if prev is not None else None

    zero_dte = compute_zero_dte_summary(
        df,
        risk_free_rate=settings.risk_free_rate,
        atm_band_pct=getattr(settings, "atm_band_pct_0dte", 0.005),
        prev_net_gex=prev_net_gex,
        prev_ts_seconds=prev_ts_seconds,
        now_ts_seconds=now_ts_seconds,
    )
    back_month = compute_back_month_summary(
        df, risk_free_rate=settings.risk_free_rate
    )
    # Update flip-speed cache with this tick's OI-weighted 0DTE net GEX.
    _flip_speed_cache[symbol] = (zero_dte.gex_oi.net_total, now_ts_seconds)

    # Distinct expiration set drives ``is_expiration_day``'s chain-aware
    # detection so Tue/Thu SPXW expiries are recognised. The same set is
    # also written to the per-symbol cache by ``run_pipeline_for_symbol``
    # before this function is called (Rev 8 ARCH-1) — we still attach it
    # to the result so the PIN_PROBABILITY sentinel branch can use the
    # in-memory copy without re-reading the cache.
    available_expirations: frozenset[date] = _extract_available_expirations(df)

    return PipelineResult(
        symbol=symbol,
        ts=ts,
        duration_ms=0.0,
        rows=rows_total,
        gex=gex,
        gex_volume=gex_vol,
        max_pain=mp,
        walls=walls,
        iv=iv,
        regime=regime,
        vanna=vanna,
        charm=charm,
        term_structure=term_structure,
        move_tracker=move_tracker,
        pin_probability=pin_probability,
        zero_dte=zero_dte,
        back_month=back_month,
        available_expirations=available_expirations,
    )


# ────────────────────────────────────────────────────────────────────────────
# Rev 4 — session lifecycle hooks
# ────────────────────────────────────────────────────────────────────────────


# ── Rev 8 OPS-7: late session-open price capture ────────────────────────────
# When ``reset_session_state`` runs at 09:29 ET but ``resolve_spot`` returns
# None or stale_cache (GLBX feed offline / not yet warm), we retry on the
# first N ticks of the session until a fresh non-stale spot lands. State is
# ``{symbol: remaining_retry_attempts}``; each successful capture removes
# the symbol from the dict and writes a ``session_open_price_late`` audit
# row so the timeline shows when the real open print was resolved.
_pending_session_open_capture: dict[str, int] = {}
SESSION_OPEN_RETRY_ATTEMPTS: int = 5


def reset_session_open_capture_for_tests(symbol: str | None = None) -> None:
    """Test helper: clear the OPS-7 retry-state map."""
    if symbol is None:
        _pending_session_open_capture.clear()
    else:
        _pending_session_open_capture.pop(symbol.upper(), None)


def has_pending_session_open_capture(symbol: str) -> bool:
    """Return True when the OPS-7 retry loop still owes ``symbol`` a print."""
    return _pending_session_open_capture.get(symbol.upper(), 0) > 0


async def _record_session_event(
    *,
    event_type: str,
    symbol: str | None,
    extra: dict[str, object] | None = None,
) -> None:
    """Insert a row into ``session_events`` so the admin/inspector knows
    when the scheduler last opened / closed / reset state."""
    factory = get_session_factory()
    async with factory() as s:
        try:
            s.add(
                SessionEvent(
                    event_type=event_type,
                    symbol=symbol,
                    extra_json=extra or {},
                )
            )
            await s.commit()
        except Exception:
            await s.rollback()
            logger.exception(
                "session_event_persist_error",
                event_type=event_type,
                symbol=symbol,
            )


async def reset_session_state(symbols: list[str]) -> None:
    """Wipe per-session caches at 09:29 ET.

    * Clears the futures-basis EMA cache (each new session needs to
      re-establish basis as the carry / dividend assumption may have
      changed overnight).
    * Captures spot-at-open into the move_tracker registry so realized
      move anchors against the 09:30 print, not an overnight tick.
    * Inserts a ``session_open`` audit row per symbol so the timeline
      view in /admin/inspector lines up cleanly.

    HIRO accumulators live in :mod:`app.processing.hiro` and reset
    automatically on the first call of a new session because that
    module keys its bucket cumulative by trade-date.
    """
    logger.info("session.reset", symbols=symbols)
    factory = get_session_factory()
    for symbol in symbols:
        reset_basis_cache(symbol)
        reset_flip_speed_cache(symbol)

        captured_open: float | None = None
        capture_source: str | None = None
        try:
            async with factory() as session:
                df = await load_latest_snapshot(session, symbol)
                spot_at_open = await resolve_spot(symbol, df, session)
            # Rev 8 OPS-7: only treat a fresh (non-stale) spot as the
            # session-open print. ``stale_cache`` here is the prior
            # afternoon's trailing tick — anchoring realized_move to it
            # would silently mis-state the move all session.
            if spot_at_open is not None and spot_at_open.source != "stale_cache":
                captured_open = float(spot_at_open.price)
                capture_source = spot_at_open.source
                move_tracker_mod.set_session_open_price(symbol, captured_open)
            elif spot_at_open is not None:
                capture_source = spot_at_open.source
        except Exception:  # noqa: BLE001
            logger.exception("session_open_capture_failed", symbol=symbol)

        if captured_open is None:
            # Schedule retry for the next N ticks until a fresh print lands.
            _pending_session_open_capture[symbol.upper()] = (
                SESSION_OPEN_RETRY_ATTEMPTS
            )
            logger.info(
                "session_open_price_unset",
                symbol=symbol,
                attempts=SESSION_OPEN_RETRY_ATTEMPTS,
                capture_source=capture_source,
                hint="will capture on next pipeline tick once spot resolves",
            )

        await _record_session_event(
            event_type="session_open",
            symbol=symbol,
            extra={
                "reset_basis_cache": True,
                "reset_flip_speed_cache": True,
                "session_open_price": captured_open,
                "session_open_price_set": captured_open is not None,
                "capture_source": capture_source,
            },
        )

    # Sentinel pipeline_runs row so /admin/system/status can show
    # "last session opened at HH:MM" without joining session_events.
    now = datetime.now(UTC)
    async with factory() as s:
        try:
            for symbol in symbols:
                s.add(
                    PipelineRun(
                        id=uuid.uuid4(),
                        symbol=symbol,
                        started_at=now,
                        finished_at=now,
                        duration_ms=0,
                        status="session_open",
                        # Rev 8 ARCH-1: ``is_expiration_day`` consults the
                        # ``_AVAILABLE_EXPIRATIONS`` cache populated by every
                        # tick of ``run_pipeline_for_symbol`` so Tue/Thu
                        # SPXW expiries are recognised here.
                        is_expiration_day=is_expiration_day(symbol),
                        tau_0dte_years=time_to_expiry_0dte_years(),
                    )
                )
            await s.commit()
        except Exception:
            await s.rollback()
            logger.exception("session_open_sentinel_persist_error")


async def finalize_session(symbols: list[str]) -> None:
    """End-of-session hook called at 16:16 ET.

    Today this only records the close in ``session_events`` and writes a
    sentinel ``pipeline_runs`` row. The richer end-of-day HIRO summary
    is computed by the flow pipeline; this hook is the synchronization
    point that tells everyone "no more frames after this".
    """
    logger.info("session.finalize", symbols=symbols)
    for symbol in symbols:
        await _record_session_event(
            event_type="session_close",
            symbol=symbol,
            extra=None,
        )

    factory = get_session_factory()
    now = datetime.now(UTC)
    async with factory() as s:
        try:
            for symbol in symbols:
                s.add(
                    PipelineRun(
                        id=uuid.uuid4(),
                        symbol=symbol,
                        started_at=now,
                        finished_at=now,
                        duration_ms=0,
                        status="session_close",
                        # Rev 8 ARCH-1: chain-driven expiration day via the
                        # cache populated by every pipeline tick.
                        is_expiration_day=is_expiration_day(symbol),
                        tau_0dte_years=0.0,
                    )
                )
            await s.commit()
        except Exception:
            await s.rollback()
            logger.exception("session_close_sentinel_persist_error")


# ── Rev 8 ARCH-2: orphan-sweep helpers ─────────────────────────────────────


async def sweep_orphan_pipeline_runs() -> int:
    """Mark stale ``running`` pipeline_runs rows as ``aborted``.

    A hard crash or a finalize that silently raises (the in-process
    ``_finalize_pipeline_run`` swallows DB errors so the pipeline tick
    survives a transient blip) leaves audit rows wedged in ``running``.
    Without a periodic sweep, the completeness checker treats them as
    in-flight and ``/admin/system/status`` shows phantom open runs.

    Returns the number of rows updated; ``-1`` if the sweep failed.
    """
    factory = get_session_factory()
    try:
        async with factory() as s:
            res = await s.execute(
                text(
                    "UPDATE pipeline_runs SET status='aborted', "
                    "error='process restarted while running' "
                    "WHERE status='running' "
                    f"AND started_at < NOW() - INTERVAL '{ORPHAN_SWEEP_THRESHOLD_MINUTES} minutes'"
                )
            )
            await s.commit()
            count = int(getattr(res, "rowcount", 0) or 0)
            if count:
                logger.info(
                    "pipeline_runs_orphan_sweep_periodic",
                    rows=count,
                )
            return count
    except Exception:  # noqa: BLE001
        logger.exception("pipeline_runs_orphan_sweep_periodic_failed")
        return -1


async def orphan_sweep_loop(
    *, interval_seconds: float = ORPHAN_SWEEP_INTERVAL_SECONDS
) -> None:
    """Run :func:`sweep_orphan_pipeline_runs` forever on a fixed cadence.

    Intended to be wired from ``app.main.lifespan`` as
    ``asyncio.create_task(orphan_sweep_loop())`` so a finalize that
    silently fails or a crashed worker is reaped within
    ``ORPHAN_SWEEP_INTERVAL_SECONDS`` instead of waiting for the next
    deploy. Cancellation propagates as in any background task.
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        try:
            await sweep_orphan_pipeline_runs()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - never let the loop die
            logger.exception("orphan_sweep_loop_iteration_failed")


__all__ = [
    "EXPECTED_METRIC_TYPES",
    "MIN_COVERAGE_FRACTION",
    "ORPHAN_SWEEP_INTERVAL_SECONDS",
    "ORPHAN_SWEEP_THRESHOLD_MINUTES",
    "PipelineResult",
    "SESSION_OPEN_RETRY_ATTEMPTS",
    "STREAMING_PUBLISH_FAILURE_THRESHOLD",
    "finalize_session",
    "get_pipeline_counters",
    "get_streaming_publish_failures",
    "has_pending_session_open_capture",
    "is_rth_now",
    "orphan_sweep_loop",
    "reset_pipeline_counters_for_tests",
    "reset_session_open_capture_for_tests",
    "reset_session_state",
    "reset_streaming_publish_failures_for_tests",
    "run_pipeline_for_symbol",
    "spot_result_to_payload",
    "sweep_orphan_pipeline_runs",
]
