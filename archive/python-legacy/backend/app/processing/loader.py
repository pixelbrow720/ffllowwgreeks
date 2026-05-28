"""Load the latest options-chain snapshot per (expiration, strike, option_type) for a symbol.

When live OI is missing/zero (common for OPRA Pillar definition-only feeds),
we fall back to the most recently ingested end-of-day Open Interest snapshot
from ``eod_open_interest`` so downstream metrics (GEX-by-OI, walls-by-OI) still
have meaningful weights to use.

Underlying spot synthesis lives in :mod:`app.processing.spot`. Rev 4 wires
:func:`app.processing.spot.resolve_spot` directly from
:mod:`app.processing.pipeline`, which then overwrites ``underlying_price``
on every chain row before metrics run — so the loader does not attempt to
populate spot here.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.logging import get_logger
from app.processing.session import _now_eastern

logger = get_logger(__name__)

# Window is parameterised at call time (settings are loaded once per process
# but the pipeline can run with overridden settings during tests). The query
# itself is cached as a single ``text()`` so SQLAlchemy compiles it once.
SNAPSHOT_QUERY = text(
    """
    SELECT DISTINCT ON (expiration, strike, option_type)
        ts, symbol, expiration, strike, option_type,
        oi, volume, iv, delta, gamma, last_price, bid, ask, underlying_price
    FROM options_chain
    WHERE symbol = :symbol
      AND ts > NOW() - make_interval(hours => :window_hours)
    ORDER BY expiration, strike, option_type, ts DESC
    """
)


# DR-13/14: include ``oi_date`` in the projection so the loader can apply a
# freshness gate. Pre-Rev-11 the column was discarded; multi-day-stale OI
# was treated as authoritative current OI and silently weighted GEX-by-OI.
# REV11-LANE-M-followup: widen eod_open_interest PK to include oi_date so a
# back-dated historical row cannot lock out a fresher entry. Outside the
# scope of this lane (Lane M owns migrations).
EOD_OI_QUERY = text(
    """
    SELECT expiration, strike, option_type, open_interest, oi_date
    FROM eod_open_interest
    WHERE symbol = :symbol
    """
)


async def load_latest_snapshot(
    session: AsyncSession,
    symbol: str,
    *,
    as_of_date: date | None = None,
) -> pd.DataFrame:
    """Load the latest chain snapshot per contract for ``symbol``.

    ``as_of_date`` controls the EOD-OI freshness gate (DR-13/14). When
    omitted, the eastern-time today is used; tests can pin a deterministic
    date by passing it explicitly. EOD OI rows older than
    ``as_of_date - EOD_OI_MAX_AGE_DAYS`` are not used as the fallback —
    ``oi`` propagates as NaN so GEX-by-OI's existing weight-source chain
    (volume → premium → uniform) takes over instead.
    """
    settings = get_settings()
    result = await session.execute(
        SNAPSHOT_QUERY,
        {
            "symbol": symbol,
            "window_hours": int(settings.loader_snapshot_window_hours),
        },
    )
    rows = result.mappings().all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame.from_records([dict(r) for r in rows])
    # Coerce numeric columns to floats for downstream math.
    for col in ("strike", "iv", "delta", "gamma", "last_price", "bid", "ask", "underlying_price"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("oi", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if as_of_date is None:
        as_of_date = _now_eastern().date()

    # Fold in EOD OI fallback for any rows where live OI is null/zero.
    df = await _apply_eod_oi_fallback(session, symbol, df, as_of_date=as_of_date)
    return df


async def _apply_eod_oi_fallback(
    session: AsyncSession,
    symbol: str,
    df: pd.DataFrame,
    *,
    as_of_date: date,
) -> pd.DataFrame:
    """Fill rows where ``oi`` is null or zero from ``eod_open_interest``.

    DR-13/14: rows are filtered to those whose ``oi_date`` is at most
    ``EOD_OI_MAX_AGE_DAYS`` older than ``as_of_date`` before the merge.
    Older rows are dropped so ``oi`` stays NaN — GEX-by-OI then falls
    through its existing weight-source chain. The merged ``oi_date`` is
    surfaced as ``eod_oi_age_days`` in the output frame so downstream
    consumers can persist the provenance via ``extra_json``.
    """
    if df.empty or "oi" not in df.columns:
        return df

    needs_fill = df["oi"].isna() | (df["oi"].fillna(0) == 0)
    if not needs_fill.any():
        return df

    result = await session.execute(EOD_OI_QUERY, {"symbol": symbol})
    rows = result.mappings().all()
    if not rows:
        return df

    eod = pd.DataFrame.from_records([dict(r) for r in rows])
    if eod.empty:
        return df

    settings = get_settings()
    max_age_days = int(getattr(settings, "eod_oi_max_age_days", 3))
    cutoff_date = as_of_date - timedelta(days=max_age_days)

    eod["strike"] = pd.to_numeric(eod["strike"], errors="coerce")
    eod["open_interest"] = pd.to_numeric(eod["open_interest"], errors="coerce").fillna(0)
    eod["option_type"] = eod["option_type"].astype(str).str.upper()
    eod["oi_date"] = pd.to_datetime(eod["oi_date"], errors="coerce").dt.date
    df["option_type"] = df["option_type"].astype(str).str.upper()

    fresh_mask = eod["oi_date"].apply(
        lambda d: d is not None and not pd.isna(d) and d >= cutoff_date
    )
    stale_count = int((~fresh_mask).sum())
    fresh_eod = eod[fresh_mask]
    if stale_count > 0:
        logger.warning(
            "loader.eod_oi_stale_skipped",
            symbol=symbol,
            stale_rows=stale_count,
            cutoff_date=str(cutoff_date),
            max_age_days=max_age_days,
        )
    if fresh_eod.empty:
        # No usable EOD OI within the freshness window — leave ``oi`` NaN
        # so GEX-by-OI falls back through its weight-source chain.
        df["eod_oi_age_days"] = pd.NA
        return df

    merged = df.merge(
        fresh_eod[["expiration", "strike", "option_type", "open_interest", "oi_date"]],
        on=["expiration", "strike", "option_type"],
        how="left",
    )
    fallback = merged["open_interest"].fillna(0)
    df.loc[needs_fill, "oi"] = fallback[needs_fill].values
    # Provenance: ``eod_oi_age_days`` exposes how stale the merged OI is
    # so consumers can persist into ``extra_json``. NaN where no merge
    # happened or the live ``oi`` was already populated.
    age_days = merged["oi_date"].apply(
        lambda d: (as_of_date - d).days
        if d is not None and not pd.isna(d)
        else None
    )
    df["eod_oi_age_days"] = age_days.where(needs_fill, pd.NA).values
    return df
