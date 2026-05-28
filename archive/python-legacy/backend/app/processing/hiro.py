"""HIRO — Hedging Impact of Real-time Options.

Aligned with the SpotGamma definition:

* HIRO measures **dealer hedging pressure** from each option trade. It is
  *not* raw option volume, *not* premium paid, *not* open interest. The
  canonical unit is **delta notional** — how many shares of the underlying
  the dealer must buy or sell to keep their book delta-neutral after the
  trade::

      delta_notional = customer_side · size · delta · 100

  where ``delta`` is the option's signed delta (calls ∈ [0, 1], puts ∈
  [-1, 0]) and ``customer_side`` is +1 (customer buy) or -1 (customer
  sell). The sign of ``customer_side · delta`` already reflects the
  dealer's hedge: positive HIRO ⇒ dealer must BUY the underlying ⇒
  upward pressure.

* When delta is unavailable (no quote yet, IV not yet inverted) we fall
  back to a **signed-premium** approximation::

      signed_premium = customer_side · size · price · 100 · option_sign

  with ``option_sign = +1`` for calls and ``-1`` for puts. This recovers
  the directional sign convention but inflates magnitude vs. the
  delta-notional path. ``extra_json.weight_source`` records which path
  was used per bucket so consumers can disambiguate.

Outputs (per the SpotGamma chart):

* ``net`` (Total / Purple) — calls + puts combined
* ``call`` (Orange) — calls only
* ``put`` (Blue) — puts only
* ``next_expiry`` (Green) — only contracts whose expiration matches the
  earliest expiry in the window (~ "0DTE" on SPX/SPY/QQQ)

Bucketing
---------
Output is broken into time buckets at the resample frequency specified
by ``bucket`` (default ``1min``). The ``cumulative`` field on each
bucket row is the per-bucket sum (resets every bucket). Consumers that
want a session-wide running total can ``cumsum`` per-bucket
``net_delta_notional`` themselves.

``HiroSeries.cumulative`` reports the most-recent bucket's net delta
notional (or signed premium when delta was unavailable end-to-end).

Incremental aggregation
-----------------------
The legacy implementation re-aggregated the entire 60-minute window
from scratch on every pipeline tick. :func:`compute_hiro_incremental`
provides a stateful counterpart that only re-bucketises new trades and
expires buckets that have aged past the window. The flow pipeline uses
this path when a previous result is available — the first tick still
runs the full :func:`compute_hiro`.

Multi-leg / spread awareness
----------------------------
DR-11: OPRA tags multi-leg (combo) trades via sale-condition codes
that are not surfaced by the standard DBN public API. A spread print's
price is the leg-tagged net price, not a single-contract directional
flow — including it in the delta-notional sum overstates dealer hedge
pressure on the headline contract. Two paths exclude these prints:

1. **Ingest-side (active default).** When
   ``ENABLE_OPRA_MULTILEG_DETECTION`` is True the OPRA live ingester
   detects combo prints heuristically and persists them with
   ``side=None`` and ``signed_premium=None``. Rows with ``side=0`` /
   NaN are filtered out by ``_prepare_work`` regardless of the
   advisory column, so HIRO sees a delta-notional sum that does not
   include the combo's leg-tagged price.
2. **DataFrame column (advisory).** When an upstream caller
   materialises an ``is_multileg`` boolean column,
   ``_prepare_work`` excludes those rows up front. This is the path
   for tests / future ingest paths that surface the flag explicitly.

Persisting an ``is_multileg`` column on ``options_trades`` is deferred
pending a confirmed SDK-surfaced flag (would require a migration). The
limitation is documented in REVIEW_REV10 DR-11.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

CONTRACT_MULTIPLIER = 100


@dataclass
class HiroSeries:
    """Result of a HIRO computation over a window."""

    bucket_size: str
    """pandas frequency alias used for resampling (e.g. ``"1min"``)."""

    series: list[dict] = field(default_factory=list)
    """One entry per time bucket. Keys::

        ts                       ISO-8601 bucket start
        call_premium             legacy: signed premium of calls (USD)
        put_premium              legacy: signed premium of puts (USD)
        net_premium              legacy: call_premium + put_premium
        cumulative               per-bucket reset of net_premium
        call_delta_notional      shares-equivalent dealer-hedge for calls
        put_delta_notional       shares-equivalent dealer-hedge for puts
        net_delta_notional       call_delta_notional + put_delta_notional
        next_expiry_delta_notional  0DTE-only delta notional (or 0)
        weight_source            "delta_notional" | "signed_premium"
    """

    cumulative: float = 0.0
    """Net of the most-recent bucket. Delta notional when available,
    signed premium otherwise."""

    weight_source: str = "delta_notional"
    """Aggregate provenance — ``delta_notional`` if every bucket had
    delta data, ``signed_premium`` if every bucket fell back, ``mixed``
    when both paths were exercised in the window."""

    dropped_late_trades: int = 0
    """Count of trades dropped at the entry of
    :func:`compute_hiro_incremental` for landing strictly before the
    window cutoff (DR-9). Late prints would otherwise resurrect already-
    pruned buckets and skew ``cumulative``. Always 0 for the full
    :func:`compute_hiro` path which has no prior state to corrupt."""


# ── Pure-function aggregation ───────────────────────────────────────────────


def _expected_columns() -> set[str]:
    return {"ts", "side", "size", "price", "option_type"}


def _prepare_work(df: pd.DataFrame) -> pd.DataFrame | None:
    """Coerce the input DataFrame into the working shape used by both
    the full and incremental aggregators. Returns ``None`` if the input
    is unusable (empty, no classified trades, no parseable timestamps).

    DR-11: when an ``is_multileg`` column is present and truthy on a
    row, that row is excluded — multi-leg / spread prints carry leg-
    tagged net prices and would mis-attribute to the headline contract
    if summed into the delta-notional total. The column is *advisory*:
    today's persistence path doesn't write it (deferred pending a
    confirmed SDK-surfaced flag and a migration), so this branch only
    fires when an upstream caller materialises the column. The default
    end-to-end behaviour relies on the ingest-side suppression of
    ``side`` for detected combo prints (see databento_live._emit_trade)
    — those rows fail the ``side != 0`` filter below and get excluded
    naturally.
    """
    if df.empty:
        return None
    missing = _expected_columns().difference(df.columns)
    if missing:
        raise KeyError(
            f"HIRO requires {_expected_columns()}; missing {missing}"
        )

    work = df.copy()
    if "is_multileg" in work.columns:
        # Coerce to bool the safe way: NaN / None / 0 / ""  → False.
        ml_mask = pd.to_numeric(
            work["is_multileg"].astype("boolean").astype("Int8"),
            errors="coerce",
        ).fillna(0).astype(bool)
        work = work.loc[~ml_mask]
        if work.empty:
            return None
    work = work[pd.to_numeric(work["side"], errors="coerce").fillna(0) != 0]
    if work.empty:
        return None

    work["ts"] = pd.to_datetime(work["ts"], utc=True, errors="coerce")
    work = work.dropna(subset=["ts"])
    if work.empty:
        return None
    return work


def _annotate_metrics(work: pd.DataFrame) -> pd.DataFrame:
    """Fill in the per-trade ``_call_*`` / ``_put_*`` / ``_next_expiry_*``
    columns the resample step sums.

    ``delta`` is consumed when present and finite. Rows without a usable
    delta fall back to signed-premium magnitudes (per-row provenance is
    recorded in ``_weight_source_row``).
    """
    customer_side = (
        pd.to_numeric(work["side"], errors="coerce").fillna(0).astype(int)
    )
    size = pd.to_numeric(work["size"], errors="coerce").fillna(0.0)
    price = pd.to_numeric(work["price"], errors="coerce").fillna(0.0)
    is_call = work["option_type"].astype(str).str.upper() == "C"
    is_put = ~is_call

    # Delta-notional path (primary).
    if "delta" in work.columns:
        delta = pd.to_numeric(work["delta"], errors="coerce")
        delta_ok = delta.notna() & np.isfinite(delta)
        delta = delta.where(delta_ok, np.nan)
    else:
        delta = pd.Series(np.nan, index=work.index, dtype=float)
        delta_ok = pd.Series(False, index=work.index)

    delta_notional = customer_side * size * delta * CONTRACT_MULTIPLIER
    work["_call_delta_notional"] = np.where(
        is_call & delta_ok, delta_notional, 0.0
    )
    work["_put_delta_notional"] = np.where(
        is_put & delta_ok, delta_notional, 0.0
    )

    # Signed-premium fallback (legacy convention).
    base_premium = customer_side * size * price * CONTRACT_MULTIPLIER
    call_prem_legacy = np.where(is_call, base_premium, 0.0)
    # Put: customer buy (+side) on a put pushes the dealer to sell —
    # negative HIRO. The legacy convention encoded this as `-base_premium`
    # for puts.
    put_prem_legacy = np.where(is_put, -base_premium, 0.0)
    work["_call_premium"] = call_prem_legacy
    work["_put_premium"] = put_prem_legacy

    # Next-expiry isolation: keep delta-notional when available, otherwise
    # fall back to signed premium for the same row.
    if not work.empty:
        # ``expiration`` may not exist on legacy callers; guard it.
        if "expiration" in work.columns:
            min_expiry = pd.to_datetime(
                work["expiration"], errors="coerce"
            ).min()
            is_next_expiry = (
                pd.to_datetime(work["expiration"], errors="coerce")
                == min_expiry
            )
        else:
            is_next_expiry = pd.Series(False, index=work.index)
    else:
        is_next_expiry = pd.Series(False, index=work.index)

    next_dn = np.where(
        is_next_expiry & delta_ok, delta_notional, 0.0
    )
    next_prem = np.where(
        is_next_expiry & ~delta_ok,
        call_prem_legacy + put_prem_legacy,
        0.0,
    )
    work["_next_expiry_delta_notional"] = next_dn
    work["_next_expiry_premium"] = next_prem

    work["_delta_ok"] = delta_ok.astype(int)
    return work


def _emit_series(
    grouped: pd.DataFrame,
) -> tuple[list[dict], float, str]:
    """Convert resampled aggregates into the JSON-friendly series payload.

    Returns ``(series, latest_net, weight_source)``.
    """
    if grouped.empty:
        return [], 0.0, "delta_notional"

    grouped = grouped.copy()
    grouped["call_delta_notional"] = grouped[
        "_call_delta_notional"
    ].astype(float)
    grouped["put_delta_notional"] = grouped[
        "_put_delta_notional"
    ].astype(float)
    grouped["net_delta_notional"] = (
        grouped["call_delta_notional"] + grouped["put_delta_notional"]
    )
    grouped["call_premium"] = grouped["_call_premium"].astype(float)
    grouped["put_premium"] = grouped["_put_premium"].astype(float)
    grouped["net_premium"] = (
        grouped["call_premium"] + grouped["put_premium"]
    )
    grouped["next_expiry_delta_notional"] = grouped[
        "_next_expiry_delta_notional"
    ].astype(float)
    grouped["next_expiry_premium"] = grouped[
        "_next_expiry_premium"
    ].astype(float)
    has_delta = grouped["_delta_ok"].astype(int) > 0
    no_delta = ~has_delta

    # Per-bucket net falls back to signed premium when no row in the
    # bucket carried delta information.
    grouped["net"] = np.where(
        has_delta, grouped["net_delta_notional"], grouped["net_premium"]
    )
    grouped["weight_source_per_bucket"] = np.where(
        has_delta, "delta_notional", "signed_premium"
    )

    series_payload: list[dict] = []
    for ts, row in grouped.iterrows():
        if pd.isna(row["net"]):
            continue
        series_payload.append(
            {
                "ts": ts.isoformat(),
                "call_premium": float(row["call_premium"]),
                "put_premium": float(row["put_premium"]),
                "net_premium": float(row["net_premium"]),
                "cumulative": float(row["net"]),
                "call_delta_notional": float(row["call_delta_notional"]),
                "put_delta_notional": float(row["put_delta_notional"]),
                "net_delta_notional": float(row["net_delta_notional"]),
                "next_expiry_delta_notional": float(
                    row["next_expiry_delta_notional"]
                ),
                "next_expiry_premium": float(row["next_expiry_premium"]),
                "weight_source": str(row["weight_source_per_bucket"]),
            }
        )

    if not series_payload:
        return [], 0.0, "delta_notional"

    last_net = series_payload[-1]["cumulative"]

    if has_delta.all():
        weight_source = "delta_notional"
    elif no_delta.all():
        weight_source = "signed_premium"
    else:
        weight_source = "mixed"

    return series_payload, last_net, weight_source


def compute_hiro(
    df: pd.DataFrame,
    *,
    bucket: str = "1min",
) -> HiroSeries:
    """Aggregate classified option trades into a HIRO time series.

    Required input columns:

    * ``ts``          — trade timestamp (datetime-like).
    * ``side``        — +1 (customer buy) / -1 (customer sell), integer.
    * ``size``        — contracts traded.
    * ``price``       — trade price (per contract).
    * ``option_type`` — 'C' or 'P'.

    Optional columns (when present, drive the canonical delta-notional
    path):

    * ``delta``       — signed BSM delta of the contract at trade time.
    * ``expiration``  — expiration date (used to isolate the
      ``next_expiry`` bucket / 0DTE green line in the SpotGamma chart).

    See module docstring for the sign convention.
    """
    work = _prepare_work(df)
    if work is None:
        return HiroSeries(bucket_size=bucket)
    work = _annotate_metrics(work)

    work = work.set_index("ts")
    grouped = work.resample(bucket).agg(
        {
            "_call_delta_notional": "sum",
            "_put_delta_notional": "sum",
            "_call_premium": "sum",
            "_put_premium": "sum",
            "_next_expiry_delta_notional": "sum",
            "_next_expiry_premium": "sum",
            "_delta_ok": "sum",
        }
    )

    series_payload, last_net, weight_source = _emit_series(grouped)
    return HiroSeries(
        bucket_size=bucket,
        series=series_payload,
        cumulative=last_net,
        weight_source=weight_source,
    )


# ── Incremental aggregation ─────────────────────────────────────────────────


def compute_hiro_incremental(
    new_trades: pd.DataFrame,
    *,
    bucket: str = "1min",
    window_minutes: int,
    prev_series: list[dict] | None = None,
    now: datetime | None = None,
) -> HiroSeries:
    """Update an existing HIRO series with only the new trades.

    The legacy :func:`compute_hiro` re-aggregates the full window every
    tick. For a 60-minute window on SPX with 100k+ trades/min this is
    pure waste — every bucket older than the most recent one is
    immutable once the wallclock has rolled past it.

    Strategy:

    1. Bucketise ``new_trades`` exactly like :func:`compute_hiro`.
    2. Merge each new bucket into the in-memory ``prev_series`` (sum
       per-key).
    3. Evict buckets whose start ``ts`` is older than ``now -
       window_minutes``.

    Returns a fresh :class:`HiroSeries` reflecting the merged window.
    Callers that need a richer state representation (e.g. across
    process restarts) should persist ``HiroSeries.series`` and pass it
    back as ``prev_series`` next call.

    DR-9: late prints (``ts < cutoff``) are dropped at the entry of this
    function so they cannot resurrect already-pruned buckets and skew
    ``cumulative``. The drop count is exposed via ``HiroSeries.dropped_late_trades``
    so the caller can persist it in ``extra_json`` for provenance.
    """
    cutoff_dt = (now or datetime.now(UTC)) - timedelta(minutes=window_minutes)
    dropped_late = 0
    work = new_trades
    if work is not None and not work.empty and "ts" in work.columns:
        ts_col = pd.to_datetime(work["ts"], utc=True, errors="coerce")
        cutoff_aware = pd.Timestamp(cutoff_dt)
        if cutoff_aware.tzinfo is None:
            cutoff_aware = cutoff_aware.tz_localize("UTC")
        else:
            cutoff_aware = cutoff_aware.tz_convert("UTC")
        late_mask = ts_col < cutoff_aware
        dropped_late = int(late_mask.fillna(False).sum())
        if dropped_late > 0:
            work = work.loc[~late_mask.fillna(False)].copy()

    new_block = compute_hiro(work, bucket=bucket).series
    merged: dict[str, dict] = {}
    if prev_series:
        for entry in prev_series:
            merged[entry["ts"]] = dict(entry)
    for entry in new_block:
        ts_key = entry["ts"]
        existing = merged.get(ts_key)
        if existing is None:
            merged[ts_key] = dict(entry)
            continue
        # Bucket already in window — sum every numeric field. The
        # ``weight_source`` follows the most-recent (incoming) bucket.
        for k, v in entry.items():
            if k == "ts":
                continue
            if k == "weight_source":
                existing[k] = v
                continue
            try:
                existing[k] = float(existing.get(k, 0.0)) + float(v)
            except (TypeError, ValueError):
                existing[k] = v
        # ``cumulative`` is per-bucket "net" (delta_notional when available,
        # else signed_premium). Summing it alongside the constituent fields
        # double-counts on overlap. Recompute from the freshly-summed
        # fields so a partial-overlap call lands at the same value as a
        # single full-window aggregation.
        if existing.get("weight_source") == "delta_notional":
            existing["cumulative"] = float(existing.get("net_delta_notional", 0.0))
        else:
            existing["cumulative"] = float(existing.get("net_premium", 0.0))

    # Window expiry — drop everything older than the cutoff.
    cutoff_iso = cutoff_dt.isoformat()
    pruned: list[dict] = []
    for ts_iso, entry in sorted(merged.items()):
        # ``cutoff_iso`` may carry no tz suffix while ``ts_iso`` carries
        # ``+00:00``. Normalise both to a comparable representation.
        try:
            ts_dt = pd.Timestamp(ts_iso).to_pydatetime()
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=cutoff_dt.tzinfo)
            cutoff_aware_dt = cutoff_dt
            if cutoff_aware_dt.tzinfo is None and ts_dt.tzinfo is not None:
                cutoff_aware_dt = cutoff_aware_dt.replace(tzinfo=ts_dt.tzinfo)
            if ts_dt >= cutoff_aware_dt:
                pruned.append(entry)
        except (TypeError, ValueError):
            # Lexicographic fallback — ISO-8601 timestamps sort
            # correctly when tz suffixes match.
            if ts_iso >= cutoff_iso:
                pruned.append(entry)

    if not pruned:
        return HiroSeries(bucket_size=bucket, dropped_late_trades=dropped_late)

    last_net = float(pruned[-1].get("cumulative", 0.0))
    sources = {entry.get("weight_source", "delta_notional") for entry in pruned}
    if sources == {"delta_notional"}:
        weight_source = "delta_notional"
    elif sources == {"signed_premium"}:
        weight_source = "signed_premium"
    else:
        weight_source = "mixed"

    return HiroSeries(
        bucket_size=bucket,
        series=pruned,
        cumulative=last_net,
        weight_source=weight_source,
        dropped_late_trades=dropped_late,
    )
