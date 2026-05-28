"""Spot resolver — futures first, parity second, stale cache last.

Rev 4 — Agent 2.

Why ``ES − basis`` (and not put-call parity) is the primary path
================================================================

OPRA Pillar **does not publish the cash SPX or NDX index price** — those
are computed indices, not exchange-traded contracts. The old (Rev 3)
approach was to recover spot via put-call parity from the option chain:

    C − P = S − K · e^{−rT}   ⇒   S = K · e^{−rT} + (C − P)

That works, but it has two problems for a day-trader use case:

1.  **Latency** — parity reads the most-recent option mids, which can
    lag the underlying by hundreds of milliseconds during fast prints.
2.  **Quote noise** — an ATM call/put pair with a 5-tick wide spread can
    drift the parity-implied spot by half a point in an instant.

The CME front-month future (ES for SPX, NQ for NDX) trades 1.5 million
contracts a day and effectively *leads* the cash index intraday. If we
know the cash-minus-futures basis, we can recover cash from the futures
price in near real time with far less noise than parity:

    cash ≈ futures + basis        (basis ≈ −carry − dividends; usually negative)

The basis itself is slow-moving (it's a structural relationship driven
by the cost of carry, dividend yield, and term to expiry), so we
EMA-smooth it across pipeline ticks. The result is a spot estimate that
follows ES tick-by-tick but stays anchored to the parity reality.

Resolution priority
-------------------

* **Priority 1 (futures_basis)** — front-month ES/NQ last price + cached
  EMA basis. Used whenever a fresh-enough futures tick exists.
* **Priority 2 (parity)** — put-call parity from the freshest near-the-
  money pair. Used when no futures price is available.
* **Priority 3 (stale_cache)** — the previous tick's spot, capped at
  ``SPOT_STALE_CACHE_MAX_AGE_SECONDS``.

On every tick where **both** legs are available we update the EMA basis
(α = ``SPOT_BASIS_EMA_ALPHA``). If the two estimates diverge by more
than ``SPOT_PARITY_DEVIATION_WARN_PCT`` we log a WARNING so feed health
issues surface fast.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.processing.session import _now_eastern

logger = get_logger(__name__)


# ──────────────────────────────────────────────────────────────────────────
# Public types
# ──────────────────────────────────────────────────────────────────────────


SpotSource = Literal["futures_basis", "parity", "stale_cache"]


@dataclass
class SpotResult:
    """Authoritative spot indication used by every Greek computation."""

    price: float
    source: SpotSource

    futures_price: float | None = None
    """Raw front-month future last price, if available."""

    basis: float | None = None
    """Cash − futures EMA used to translate the futures price into cash space.

    A *negative* basis is normal for index equities — the future trades over
    the cash index by the cost of carry net of dividends.
    """

    basis_age_seconds: float | None = None
    """Seconds since the EMA basis was last updated. None if no update has
    happened yet this session."""

    parity_price: float | None = None
    """Spot from put-call parity, kept as a cross-check even when the
    primary source is futures_basis."""

    parity_deviation_pct: float | None = None
    """``|primary − parity| / parity × 100``. None if either price missing."""

    cached_at: datetime | None = None
    """When the cached value was first observed. Only set for source='stale_cache'."""

    weight_source: str | None = None
    """Provenance annotation for the basis EMA. ``"basis_post_roll"`` for
    the first N ticks after a CME quarterly roll (DR-23) so consumers know
    the EMA is still warming up; ``None`` once the EMA has converged."""


# ──────────────────────────────────────────────────────────────────────────
# Module-level basis cache (one entry per cash symbol)
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class _BasisEntry:
    value: float
    updated_at: datetime


_basis_cache: dict[str, _BasisEntry] = {}
_last_spot_cache: dict[str, tuple[float, datetime]] = {}
# DR-23: track the front-month futures contract per cash symbol so we can
# detect a CME quarterly roll (e.g. ESH → ESM) and reset the EMA before
# the prior contract's basis contaminates the new contract's tape. The
# inter-contract calendar spread on ES is typically 5–10 SPX points; a
# stale basis would otherwise carry that bias for ~30 ticks under α=0.1.
_basis_contract: dict[str, str] = {}
# DR-23: post-roll annotation budget — for the next ``_BASIS_POST_ROLL_TICKS``
# resolves on a symbol, ``SpotResult`` tags ``weight_source="basis_post_roll"``
# so consumers know the EMA is still warming up after a contract change.
_basis_post_roll_remaining: dict[str, int] = {}
_BASIS_POST_ROLL_TICKS: int = 10


def reset_basis_cache(symbol: str | None = None) -> None:
    """Clear the EMA basis (test helper / session-open reset)."""
    if symbol is None:
        _basis_cache.clear()
        _last_spot_cache.clear()
        _basis_contract.clear()
        _basis_post_roll_remaining.clear()
    else:
        sym_u = symbol.upper()
        _basis_cache.pop(sym_u, None)
        _last_spot_cache.pop(sym_u, None)
        _basis_contract.pop(sym_u, None)
        _basis_post_roll_remaining.pop(sym_u, None)


def get_basis(symbol: str) -> float | None:
    """Public accessor for the cached EMA basis for ``symbol``.

    Returns the smoothed cash-minus-futures basis (or ``None`` if no entry
    has been recorded yet). Cross-process consumers — most notably
    :mod:`app.ingestion.databento_globex` — should prefer this over
    reaching into ``_basis_cache`` directly.
    """
    entry = _basis_cache.get(symbol.upper())
    return entry.value if entry is not None else None


def _update_basis_ema(
    symbol: str,
    new_basis: float,
    *,
    reference: float | None = None,
) -> _BasisEntry | None:
    """Update the per-symbol basis EMA and return the new entry.

    DR-2: rejects observations that deviate from the prior EMA by more
    than ``BASIS_EMA_DEVIATION_THRESHOLD`` (relative). A single crossed
    or wildly-stale ATM pair can drive a noisy ``instantaneous_basis``
    that would contaminate the smoother for ~30 ticks under α=0.1; the
    gate logs a warning and skips the update without polluting the EMA.
    Returns ``None`` when the observation was rejected (caller should
    keep using the prior cached entry).

    The deviation is measured as ``|new_basis - prev_ema| / |reference|``
    where ``reference`` is the spot price the basis was derived against
    (parity or futures price). Normalising by spot rather than by the
    basis itself is correct: the basis is structurally small (a few SPX
    points at SPX ≈ 5000), so a fractional change in the basis is
    naturally large but the absolute move is tiny relative to spot. A
    crossed-pair excursion drives parity by hundreds of SPX points,
    which is the regime the gate is meant to catch.
    """
    settings = get_settings()
    alpha = float(getattr(settings, "spot_basis_ema_alpha", 0.1))
    deviation_threshold = float(
        getattr(settings, "basis_ema_deviation_threshold", 0.005)
    )
    now = datetime.now(UTC)
    sym_u = symbol.upper()
    prev = _basis_cache.get(sym_u)
    if prev is not None:
        # Reference for normalisation — prefer the supplied spot, fall
        # back to a fixed scale so a missing reference can't disable the
        # gate by inflating the denominator.
        ref = (
            abs(float(reference))
            if reference is not None and math.isfinite(float(reference)) and float(reference) > 0
            else 1.0
        )
        deviation = abs(float(new_basis) - prev.value) / ref
        if deviation > deviation_threshold:
            logger.warning(
                "spot.basis_ema_outlier_skipped",
                symbol=sym_u,
                prev_basis=round(prev.value, 4),
                new_basis=round(float(new_basis), 4),
                deviation=round(deviation, 6),
                threshold=deviation_threshold,
            )
            return None
        ema = (1.0 - alpha) * prev.value + alpha * float(new_basis)
    else:
        ema = float(new_basis)
    entry = _BasisEntry(value=ema, updated_at=now)
    _basis_cache[sym_u] = entry
    return entry


# ──────────────────────────────────────────────────────────────────────────
# Put-call parity (Rev 3 logic, preserved as the fallback path)
# ──────────────────────────────────────────────────────────────────────────


def _years_to_expiry(today: pd.Timestamp, expiry) -> float:
    today_d = today.date() if hasattr(today, "date") else today
    expiry_d = expiry.date() if hasattr(expiry, "date") else pd.Timestamp(expiry).date()
    days = max(1, (expiry_d - today_d).days)
    return days / 365.0


def synthesize_underlying_price(
    df: pd.DataFrame,
    *,
    risk_free_rate: float = 0.05,
    dividend_yield: float = 0.015,
    today: pd.Timestamp | None = None,
    max_expiries: int = 3,
) -> float | None:
    """Recover spot via put-call parity from the freshest near-the-money pair.

    Kept as the secondary path. Returns ``None`` when no usable call/put
    pair exists. Values outside ``[1, 1e6]`` are filtered as artefacts.
    """
    if df.empty:
        return None
    needed = {"strike", "expiration", "option_type"}
    if not needed.issubset(df.columns):
        return None
    if not (({"bid", "ask"}.issubset(df.columns)) or ("last_price" in df.columns)):
        return None

    if today is None:
        today = pd.Timestamp(_now_eastern().date())

    work = df.copy()
    bid = (
        pd.to_numeric(work["bid"], errors="coerce")
        if "bid" in work.columns
        else pd.Series(np.nan, index=work.index, dtype=float)
    )
    ask = (
        pd.to_numeric(work["ask"], errors="coerce")
        if "ask" in work.columns
        else pd.Series(np.nan, index=work.index, dtype=float)
    )
    last = (
        pd.to_numeric(work["last_price"], errors="coerce")
        if "last_price" in work.columns
        else pd.Series(np.nan, index=work.index, dtype=float)
    )
    good_quote = (bid > 0) & (ask > 0) & (ask > bid)
    work["mid"] = ((bid + ask) / 2.0).where(good_quote, last.where(last > 0))
    work = work.dropna(subset=["mid"])
    if work.empty:
        return None
    work["option_type_u"] = work["option_type"].astype(str).str.upper()

    expiries = sorted(pd.to_datetime(work["expiration"].unique()))[:max_expiries]
    candidates: list[float] = []

    for expiry in expiries:
        T = _years_to_expiry(today, expiry)
        sub = work[pd.to_datetime(work["expiration"]) == expiry]
        calls = sub[sub["option_type_u"] == "C"][["strike", "mid"]]
        puts = sub[sub["option_type_u"] == "P"][["strike", "mid"]]
        if calls.empty or puts.empty:
            continue
        merged = calls.merge(puts, on="strike", suffixes=("_c", "_p"))
        if merged.empty:
            continue
        merged = merged.assign(diff=lambda d: (d["mid_c"] - d["mid_p"]).abs())
        atm = merged.sort_values("diff").iloc[0]
        K = float(atm["strike"])
        spot = math.exp(dividend_yield * T) * (K * math.exp(-risk_free_rate * T) + float(atm["mid_c"]) - float(atm["mid_p"]))
        if 1.0 < spot < 1e6 and math.isfinite(spot):
            candidates.append(spot)

    if not candidates:
        return None
    return float(np.median(candidates))


# ──────────────────────────────────────────────────────────────────────────
# Front-month futures contract selection
# ──────────────────────────────────────────────────────────────────────────


_FUTURES_ROOT_FOR_SYMBOL: dict[str, str] = {
    "SPXW": "ES",
    "SPX": "ES",
    "NDXP": "NQ",
    "NDX": "NQ",
}


# CME quarterly month codes for ES / NQ. ``H`` = March, ``M`` = June,
# ``U`` = September, ``Z`` = December.
_QUARTERLY_CODES: dict[str, int] = {"H": 3, "M": 6, "U": 9, "Z": 12}


def _quarterly_expiry(code: str, year_two_digit: int) -> pd.Timestamp | None:
    """Approximate the 3rd-Friday quarterly expiry for an ES/NQ contract.

    Good enough for "is this contract still alive today" filtering — we
    only need a rough date, not the exact CME settlement convention.
    """
    month = _QUARTERLY_CODES.get(code)
    if month is None:
        return None
    # CME convention: futures listed as YY where YY is the trailing two
    # digits of the calendar year. For two-digit codes below 70 we treat
    # them as 2000-era contracts so e.g. ``25`` → 2025.
    year = 2000 + year_two_digit if year_two_digit < 70 else 1900 + year_two_digit
    try:
        first = pd.Timestamp(year=year, month=month, day=1)
    except ValueError:
        return None
    # 3rd Friday of the month: 0 = Monday … 4 = Friday
    days_to_friday = (4 - first.weekday()) % 7
    third_friday = first + pd.Timedelta(days=days_to_friday + 14)
    return third_friday


def _parse_contract_symbol(contract: str) -> tuple[str, str, int] | None:
    """Parse ``ESM5`` / ``NQH26`` etc. into ``(root, month_code, year)``.

    Returns ``None`` for spreads (``ESH7-ESM7``) or unparseable strings.
    """
    if "-" in contract or not contract:
        return None
    root: str | None = None
    for r in ("ES", "NQ"):
        if contract.startswith(r):
            root = r
            break
    if root is None:
        return None
    suffix = contract[len(root):]
    if not suffix:
        return None
    code = suffix[0]
    if code not in _QUARTERLY_CODES:
        return None
    year_part = suffix[1:]
    if not year_part.isdigit():
        return None
    year = int(year_part)
    if year < 10:
        # 1-digit year code → assume current decade
        year += (_now_eastern().year // 10) * 10 % 100
    return (root, code, year)


def get_front_month_contract(
    symbol: str,
    futures_df: pd.DataFrame,
    *,
    today: pd.Timestamp | None = None,
) -> str | None:
    """Return the contract symbol of the front-month future for ``symbol``.

    Filters to outright quarterly contracts (H/M/U/Z), drops expired ones,
    and ties on highest recent volume (most active = front month).
    Expects ``futures_df`` to have at least ``contract_symbol``; uses
    ``volume`` and/or ``ts`` columns when present for tie-breaking.
    """
    root = _FUTURES_ROOT_FOR_SYMBOL.get(symbol.upper())
    if root is None or futures_df.empty or "contract_symbol" not in futures_df.columns:
        return None

    today_d = (today or pd.Timestamp(_now_eastern().date())).date()

    contracts = futures_df["contract_symbol"].dropna().astype(str).unique().tolist()
    candidates: list[tuple[str, pd.Timestamp]] = []
    for c in contracts:
        parsed = _parse_contract_symbol(c)
        if parsed is None:
            continue
        c_root, code, year = parsed
        if c_root != root:
            continue
        exp = _quarterly_expiry(code, year)
        if exp is None:
            continue
        if exp.date() < today_d:
            continue  # expired
        candidates.append((c, exp))

    if not candidates:
        return None

    # Implement volume-based contract selection to support roll-overs automatically
    if "volume" in futures_df.columns:
        candidate_names = [c for c, e in candidates]
        vols = (
            futures_df[futures_df["contract_symbol"].isin(candidate_names)]
            .groupby("contract_symbol")["volume"]
            .sum()
            .sort_values(ascending=False)
        )
        if not vols.empty:
            return str(vols.index[0])

    # Fallback to nearest expiry
    candidates.sort(key=lambda x: x[1])
    nearest_expiry = candidates[0][1]
    same_month = [c for c, e in candidates if e == nearest_expiry]
    return sorted(same_month)[0]


# ──────────────────────────────────────────────────────────────────────────
# Main resolver
# ──────────────────────────────────────────────────────────────────────────


_FUTURES_LAST_QUERY = text(
    """
    SELECT symbol AS contract_symbol, price, ts, size AS volume
    FROM futures_ticks
    WHERE symbol LIKE :prefix
      AND ts > NOW() - INTERVAL '15 minutes'
    ORDER BY ts DESC
    LIMIT 200
    """
)


async def _latest_futures_frame(session: AsyncSession, symbol: str) -> pd.DataFrame:
    """Return the most-recent ~15 min of front-month futures ticks for ``symbol``.

    Used both for selecting the front month and for sourcing the price.
    """
    root = _FUTURES_ROOT_FOR_SYMBOL.get(symbol.upper())
    if root is None:
        return pd.DataFrame()
    result = await session.execute(_FUTURES_LAST_QUERY, {"prefix": f"{root}%"})
    rows = result.mappings().all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame.from_records([dict(r) for r in rows])
    if "price" in df.columns:
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    return df


async def resolve_spot(
    symbol: str,
    chain_df: pd.DataFrame,
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> SpotResult | None:
    """Resolve the authoritative spot price for ``symbol``.

    See module docstring for the resolution priority. Returns ``None``
    only when **no** path yields a usable value (no futures + no parity +
    no fresh cache) — callers that need a value can treat ``None`` as an
    instruction to skip the tick.
    """
    settings = get_settings()
    now = now or datetime.now(UTC)

    # 1. Parity (always compute when we can — it both anchors the EMA basis
    #    and serves as the secondary path).
    parity_spot = synthesize_underlying_price(
        chain_df,
        risk_free_rate=settings.risk_free_rate,
        dividend_yield=getattr(settings, "dividend_yield", 0.015),
    )

    # 2. Front-month futures last price.
    fut_df = await _latest_futures_frame(session, symbol)
    front_contract: str | None = None
    fut_price: float | None = None
    if not fut_df.empty:
        front_contract = get_front_month_contract(symbol, fut_df)
        if front_contract:
            sub = fut_df[fut_df["contract_symbol"] == front_contract]
            sub = sub.dropna(subset=["price"])
            if not sub.empty:
                fut_price = float(sub.iloc[0]["price"])

    # DR-23: detect a CME quarterly roll. When the active contract changes
    # (e.g. ESH → ESM) the prior EMA carries the old contract's basis and
    # mis-translates the new contract's price by the inter-contract
    # calendar spread until the EMA reconverges. Reset the cache and arm
    # the post-roll annotation budget so the next ``_BASIS_POST_ROLL_TICKS``
    # ticks emit ``weight_source="basis_post_roll"``.
    sym_u = symbol.upper()
    if front_contract is not None:
        prior_contract = _basis_contract.get(sym_u)
        if prior_contract is not None and prior_contract != front_contract:
            logger.info(
                "spot.basis_post_roll_reset",
                symbol=sym_u,
                prior_contract=prior_contract,
                new_contract=front_contract,
            )
            _basis_cache.pop(sym_u, None)
            _basis_post_roll_remaining[sym_u] = _BASIS_POST_ROLL_TICKS
        _basis_contract[sym_u] = front_contract

    # 3. Refresh the EMA basis when both legs are available this tick.
    basis_entry: _BasisEntry | None = _basis_cache.get(sym_u)
    if parity_spot is not None and fut_price is not None:
        instantaneous_basis = float(parity_spot) - float(fut_price)
        updated = _update_basis_ema(
            symbol, instantaneous_basis, reference=float(parity_spot)
        )
        if updated is not None:
            basis_entry = updated

    # 4. Pick the primary path.
    futures_basis_spot: float | None = None
    if fut_price is not None and basis_entry is not None:
        futures_basis_spot = float(fut_price) + basis_entry.value

    primary_price: float | None = None
    source: SpotSource | None = None
    if futures_basis_spot is not None and math.isfinite(futures_basis_spot) and futures_basis_spot > 0:
        primary_price = futures_basis_spot
        source = "futures_basis"
    elif parity_spot is not None:
        primary_price = float(parity_spot)
        source = "parity"
    else:
        cached = _last_spot_cache.get(sym_u)
        if cached is not None:
            cached_price, cached_at = cached
            age = (now - cached_at).total_seconds()
            if age <= settings.spot_stale_cache_max_age_seconds:
                primary_price = float(cached_price)
                source = "stale_cache"

    if primary_price is None or source is None:
        return None

    # 5. Cross-check parity deviation, log on excess.
    parity_deviation_pct: float | None = None
    if parity_spot is not None and primary_price > 0:
        parity_deviation_pct = (
            abs(primary_price - float(parity_spot)) / float(parity_spot)
        ) * 100.0
        if parity_deviation_pct > settings.spot_parity_deviation_warn_pct:
            logger.warning(
                "spot.parity_divergence",
                symbol=symbol,
                primary_price=round(primary_price, 4),
                parity_price=round(float(parity_spot), 4),
                deviation_pct=round(parity_deviation_pct, 4),
                source=source,
            )

    # 6. Cache for the next tick (used only when both fresh paths fail).
    if source != "stale_cache":
        _last_spot_cache[sym_u] = (primary_price, now)

    basis_age = (
        (now - basis_entry.updated_at).total_seconds()
        if basis_entry is not None
        else None
    )

    # DR-23: tag the basis EMA's provenance for the post-roll warm-up
    # window. ``weight_source`` annotation persists for the next
    # ``_BASIS_POST_ROLL_TICKS`` resolves on this symbol so consumers can
    # surface "EMA still warming up after roll" without re-reading the
    # cache themselves.
    weight_source: str | None = None
    if source == "futures_basis":
        remaining = _basis_post_roll_remaining.get(sym_u, 0)
        if remaining > 0:
            weight_source = "basis_post_roll"
            new_remaining = remaining - 1
            if new_remaining <= 0:
                _basis_post_roll_remaining.pop(sym_u, None)
            else:
                _basis_post_roll_remaining[sym_u] = new_remaining

    return SpotResult(
        price=primary_price,
        source=source,
        futures_price=fut_price,
        basis=basis_entry.value if basis_entry is not None else None,
        basis_age_seconds=basis_age,
        parity_price=parity_spot,
        parity_deviation_pct=parity_deviation_pct,
        cached_at=now if source == "stale_cache" else None,
        weight_source=weight_source,
    )


def spot_result_to_payload(result: SpotResult | None) -> dict[str, object | None]:
    """Serialize a SpotResult into the WebSocket / REST snapshot shape."""
    if result is None:
        return {
            "price": None,
            "source": None,
            "futures_price": None,
            "basis": None,
            "basis_age_seconds": None,
            "parity_price": None,
            "parity_deviation_pct": None,
            "weight_source": None,
        }
    return {
        "price": round(result.price, 6),
        "source": result.source,
        "futures_price": result.futures_price,
        "basis": result.basis,
        "basis_age_seconds": (
            round(result.basis_age_seconds, 3) if result.basis_age_seconds is not None else None
        ),
        "parity_price": result.parity_price,
        "parity_deviation_pct": (
            round(result.parity_deviation_pct, 4)
            if result.parity_deviation_pct is not None
            else None
        ),
        "weight_source": result.weight_source,
    }
