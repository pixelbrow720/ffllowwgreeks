#!/usr/bin/env python3
"""
DBN to Postgres bridge for FlowGreeks.

Loads Databento OPRA + GLBX historical DBN files into the ticks hypertable so
cmd/replay can pace history through the compute pipeline. Parallel path while
the dbn-go ingest cannot consume DBN v1 definition records.

Usage:
    python dbn_to_postgres.py [--days DAY1 DAY2 ...] [--reset] [--dry-run]

Default --days: every directory under data/databento/ matching 2026-02-*.

WARNING: no dedup is performed. Re-running without --reset creates duplicate
rows. Use --reset to TRUNCATE ticks before loading.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import databento as db
import databento_dbn as dbn
import psycopg


# ----- constants (must mirror internal/feed/types.go) -----------------------

UNDEF_PRICE = 9223372036854775807  # databento INT64_MAX sentinel
UNDEF_TS = 18446744073709551615    # databento UINT64_MAX sentinel

# Symbol enum
SYMBOL_SPX = 1
SYMBOL_NDX = 2

# Side enum (option side: Call/Put). NULL for futures.
SIDE_CALL = 1
SIDE_PUT = 2

# TickType enum
TICK_QUOTE = 1
TICK_TRADE = 2
TICK_OI = 3

# Aggressor enum (TradeMsg.side: 'A'=ask lifted=buy, 'B'=bid hit=sell)
AGG_UNKNOWN = 0
AGG_BUY = 1
AGG_SELL = 2

# AssetClass (internal sentinel; not persisted)
AC_OPTION = 1
AC_FUTURE = 2

# OPEN_INTEREST stat_type from databento_dbn.StatType (value=9)
STAT_OPEN_INTEREST = int(dbn.StatType.OPEN_INTEREST.value)


# ----- meta + symbol resolvers (replicate convert.go) -----------------------

class Meta:
    __slots__ = ("symbol", "asset_class", "expiry", "strike", "side")

    def __init__(self, symbol, asset_class, expiry=None, strike=None, side=None):
        self.symbol = symbol            # SYMBOL_SPX / SYMBOL_NDX
        self.asset_class = asset_class  # AC_OPTION / AC_FUTURE
        self.expiry = expiry            # date or None
        self.strike = strike            # int (OSI fixed = strike_usd * 1000) or None
        self.side = side                # SIDE_CALL / SIDE_PUT or None


def _root_to_symbol(root: str) -> Optional[int]:
    r = root.strip().upper()
    if r in ("SPX", "SPXW"):
        return SYMBOL_SPX
    if r in ("NDX", "NDXP"):
        return SYMBOL_NDX
    return None


def parse_opra_symbol(raw: str) -> Optional[Meta]:
    """Parse OSI 21-char option symbol e.g. 'SPXW  250620C05810000'."""
    if len(raw) != 21:
        return None
    sym = _root_to_symbol(raw[:6])
    if sym is None:
        return None
    try:
        yy = int(raw[6:8])
        mm = int(raw[8:10])
        dd = int(raw[10:12])
        side_ch = raw[12]
        if side_ch == "C":
            side = SIDE_CALL
        elif side_ch == "P":
            side = SIDE_PUT
        else:
            return None
        strike = int(raw[13:21])
        expiry = dt.date(2000 + yy, mm, dd)
    except (ValueError, IndexError):
        return None
    return Meta(sym, AC_OPTION, expiry=expiry, strike=strike, side=side)


def parse_future_symbol(raw: str) -> Optional[Meta]:
    """Map ES.* -> SPX, NQ.* -> NDX. Futures store NULL expiry/strike/side."""
    r = raw.strip().upper()
    if len(r) < 2:
        return None
    head = r[:2]
    if head == "ES":
        return Meta(SYMBOL_SPX, AC_FUTURE)
    if head == "NQ":
        return Meta(SYMBOL_NDX, AC_FUTURE)
    return None


def resolve_symbol(raw: str) -> Optional[Meta]:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    m = parse_opra_symbol(raw)
    if m:
        return m
    return parse_future_symbol(raw)


# ----- definition + symbology pre-loaders -----------------------------------

def build_instrument_map(definition_file: Path) -> Dict[int, Meta]:
    """Walk a definition DBN once, build {instrument_id: Meta} from raw_symbol."""
    out: Dict[int, Meta] = {}
    try:
        store = db.DBNStore.from_file(str(definition_file))
    except Exception as e:
        print(f"  [def] error opening {definition_file.name}: {e}", file=sys.stderr)
        return out
    n = 0
    for rec in store:
        if not isinstance(rec, (dbn.InstrumentDefMsg, dbn.InstrumentDefMsgV1, dbn.InstrumentDefMsgV2)):
            continue
        try:
            iid = int(rec.instrument_id)
            meta = resolve_symbol(rec.raw_symbol)
            if meta is None:
                continue
            out[iid] = meta
            n += 1
        except Exception:
            continue
    return out


def augment_from_symbology(store: "db.DBNStore", inst_map: Dict[int, Meta]) -> int:
    """Resolve instrument_id -> Meta from store.symbology metadata (GLBX path)."""
    added = 0
    try:
        sym = store.symbology
    except Exception:
        return 0
    if not isinstance(sym, dict):
        return 0
    mappings = sym.get("mappings") or sym.get("result")
    if mappings is None:
        return 0
    # mappings is dict { raw_symbol: [{start_date, end_date, symbol}, ...] } in newer versions
    if isinstance(mappings, dict):
        items = mappings.items()
    else:
        items = ((m.get("raw_symbol", ""), m.get("intervals", [])) for m in mappings)
    for raw, intervals in items:
        meta = resolve_symbol(raw)
        if meta is None:
            continue
        for interval in intervals:
            sval = interval.get("symbol") if isinstance(interval, dict) else None
            if not sval:
                continue
            try:
                iid = int(sval)
            except (TypeError, ValueError):
                continue
            if iid not in inst_map:
                inst_map[iid] = meta
                added += 1
    return added


# ----- per-record converters ------------------------------------------------

def ns_to_dt(ns) -> Optional[dt.datetime]:
    if ns is None:
        return None
    n = int(ns)
    if n == 0 or n == UNDEF_TS:
        return None
    sec, rem = divmod(n, 1_000_000_000)
    base = dt.datetime.fromtimestamp(sec, tz=dt.timezone.utc)
    return base.replace(microsecond=rem // 1000)


def _side_char(s) -> str:
    return s.value if hasattr(s, "value") else str(s)


def cmbp1_row(rec, meta: Meta):
    """tcbbo (OPRA CMBP1) -> single QUOTE row (matches convertCmbp1 in Go)."""
    if not rec.levels:
        return None
    lvl = rec.levels[0]
    bid = None if lvl.bid_px == UNDEF_PRICE else lvl.bid_px / 1e9
    ask = None if lvl.ask_px == UNDEF_PRICE else lvl.ask_px / 1e9
    ts = ns_to_dt(rec.ts_event)
    if ts is None:
        return None
    recv = ns_to_dt(rec.ts_recv) or ts
    return (
        ts, recv, meta.symbol, meta.expiry, meta.strike, meta.side,
        TICK_QUOTE, None, None, bid, ask,
        int(lvl.bid_sz) if lvl.bid_sz is not None else None,
        int(lvl.ask_sz) if lvl.ask_sz is not None else None,
        None, AGG_UNKNOWN, int(rec.publisher_id), int(rec.instrument_id),
    )


def trade_row(rec, meta: Meta):
    """trades (OPRA + GLBX TradeMsg) -> TRADE row."""
    sv = _side_char(rec.side)
    if sv == "A":
        agg = AGG_BUY
    elif sv == "B":
        agg = AGG_SELL
    else:
        agg = AGG_UNKNOWN
    price = None if rec.price == UNDEF_PRICE else rec.price / 1e9
    ts = ns_to_dt(rec.ts_event)
    if ts is None:
        return None
    recv = ns_to_dt(rec.ts_recv) or ts
    return (
        ts, recv, meta.symbol, meta.expiry, meta.strike, meta.side,
        TICK_TRADE, price, int(rec.size) if rec.size is not None else None,
        None, None, None, None, None, agg,
        int(rec.publisher_id), int(rec.instrument_id),
    )


def mbp1_row(rec, meta: Meta):
    """mbp-1 (GLBX MBP1) -> QUOTE row."""
    if not rec.levels:
        return None
    lvl = rec.levels[0]
    bid = None if lvl.bid_px == UNDEF_PRICE else lvl.bid_px / 1e9
    ask = None if lvl.ask_px == UNDEF_PRICE else lvl.ask_px / 1e9
    ts = ns_to_dt(rec.ts_event)
    if ts is None:
        return None
    recv = ns_to_dt(rec.ts_recv) or ts
    return (
        ts, recv, meta.symbol, meta.expiry, meta.strike, meta.side,
        TICK_QUOTE, None, None, bid, ask,
        int(lvl.bid_sz) if lvl.bid_sz is not None else None,
        int(lvl.ask_sz) if lvl.ask_sz is not None else None,
        None, AGG_UNKNOWN, int(rec.publisher_id), int(rec.instrument_id),
    )


def stat_row(rec, meta: Meta):
    """statistics (OPRA): only stat_type=OPEN_INTEREST -> OI_UPDATE row."""
    st = rec.stat_type
    sv = st.value if hasattr(st, "value") else st
    if int(sv) != STAT_OPEN_INTEREST:
        return None
    ts = ns_to_dt(rec.ts_event)
    if ts is None:
        return None
    recv = ns_to_dt(rec.ts_recv) or ts
    return (
        ts, recv, meta.symbol, meta.expiry, meta.strike, meta.side,
        TICK_OI, None, None, None, None, None, None,
        int(rec.quantity) if rec.quantity is not None else None,
        AGG_UNKNOWN, int(rec.publisher_id), int(rec.instrument_id),
    )


# ----- DB connection --------------------------------------------------------

def connect_pg() -> psycopg.Connection:
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        user=os.environ.get("POSTGRES_USER", "flowgreeks"),
        password=os.environ.get("POSTGRES_PASSWORD", "flowgreeks_dev_only"),
        dbname=os.environ.get("POSTGRES_DB", "flowgreeks"),
        autocommit=False,
    )


COPY_COLUMNS = (
    "ts, recv_ts, symbol, expiry, strike, side, tick_type, "
    "price, size, bid, ask, bid_size, ask_size, "
    "open_interest, aggressor, exchange, instrument_id"
)


# ----- file processing ------------------------------------------------------

def load_file(
    conn: Optional[psycopg.Connection],
    path: Path,
    inst_map: Dict[int, Meta],
    schema_kind: str,
    dry_run: bool,
) -> Tuple[int, int, int, float]:
    """Process one DBN file. Returns (records, inserted, skipped, elapsed_s)."""
    t0 = time.time()
    records = inserted = skipped = 0
    try:
        store = db.DBNStore.from_file(str(path))
    except Exception as e:
        print(f"  ERROR opening {path.name}: {e}", file=sys.stderr)
        return 0, 0, 0, 0.0

    # GLBX has no separate definition file; pull mapping from store.symbology
    if not inst_map:
        augment_from_symbology(store, inst_map)

    cur = None
    if not dry_run:
        cur = conn.cursor()
        cm_ctx = cur.copy(f"COPY ticks ({COPY_COLUMNS}) FROM STDIN")
        cm = cm_ctx.__enter__()
    else:
        cm_ctx = None
        cm = None

    try:
        for rec in store:
            records += 1
            iid_attr = getattr(rec, "instrument_id", None)
            if iid_attr is None:
                skipped += 1
                continue
            meta = inst_map.get(int(iid_attr))
            if meta is None:
                rs = getattr(rec, "raw_symbol", None)
                if rs:
                    meta = resolve_symbol(rs)
                    if meta is not None:
                        inst_map[int(iid_attr)] = meta
            if meta is None:
                skipped += 1
                continue

            row = None
            try:
                if schema_kind == "tcbbo":
                    if isinstance(rec, dbn.CMBP1Msg):
                        row = cmbp1_row(rec, meta)
                elif schema_kind == "trades":
                    if isinstance(rec, dbn.TradeMsg):
                        row = trade_row(rec, meta)
                elif schema_kind == "mbp-1":
                    if isinstance(rec, dbn.MBP1Msg):
                        row = mbp1_row(rec, meta)
                elif schema_kind == "statistics":
                    if isinstance(rec, (dbn.StatMsg, dbn.StatMsgV1)):
                        row = stat_row(rec, meta)
            except Exception as e:
                # log first few errors, then go quiet
                if skipped < 5:
                    print(f"    [warn] convert error: {e}", file=sys.stderr)
                row = None

            if row is None:
                skipped += 1
                continue

            if dry_run:
                inserted += 1
                continue

            try:
                cm.write_row(row)
                inserted += 1
            except Exception as e:
                if skipped < 5:
                    print(f"    [warn] copy error: {e}", file=sys.stderr)
                skipped += 1
    finally:
        if cm_ctx is not None:
            try:
                cm_ctx.__exit__(None, None, None)
            except Exception:
                pass
        if cur is not None:
            try:
                conn.commit()
            except Exception as e:
                print(f"    [error] commit failed: {e}", file=sys.stderr)
                conn.rollback()
            cur.close()

    return records, inserted, skipped, time.time() - t0


# ----- orchestration --------------------------------------------------------

SCHEMA_PATTERNS = [
    ("tcbbo__SPX-OPT_SPXW-OPT", "tcbbo", "OPRA_PILLAR"),
    ("tcbbo__NDX-OPT_NDXP-OPT", "tcbbo", "OPRA_PILLAR"),
    ("trades__SPX-OPT_SPXW-OPT", "trades", "OPRA_PILLAR"),
    ("trades__NDX-OPT_NDXP-OPT", "trades", "OPRA_PILLAR"),
    ("statistics__SPX-OPT_SPXW-OPT", "statistics", "OPRA_PILLAR"),
    ("statistics__NDX-OPT_NDXP-OPT", "statistics", "OPRA_PILLAR"),
    ("mbp-1__ES-FUT", "mbp-1", "GLBX_MDP3"),
    ("mbp-1__NQ-FUT", "mbp-1", "GLBX_MDP3"),
    ("trades__ES-FUT", "trades", "GLBX_MDP3"),
    ("trades__NQ-FUT", "trades", "GLBX_MDP3"),
]


def process_day(conn, day_dir: Path, dry_run: bool):
    print(f"=== {day_dir.name} ===", flush=True)

    spx_def = day_dir / "OPRA_PILLAR" / "definition__SPX-OPT_SPXW-OPT.dbn.zst"
    ndx_def = day_dir / "OPRA_PILLAR" / "definition__NDX-OPT_NDXP-OPT.dbn.zst"
    spx_map = build_instrument_map(spx_def) if spx_def.exists() else {}
    ndx_map = build_instrument_map(ndx_def) if ndx_def.exists() else {}
    print(f"  [def] SPX={len(spx_map)} NDX={len(ndx_map)}", flush=True)

    day_total_rec = day_total_in = day_total_skip = 0
    day_t0 = time.time()
    for prefix, kind, dataset in SCHEMA_PATTERNS:
        if dataset == "OPRA_PILLAR":
            path = day_dir / "OPRA_PILLAR" / f"{prefix}.dbn.zst"
            if "SPX-OPT" in prefix:
                inst_map = spx_map
            elif "NDX-OPT" in prefix:
                inst_map = ndx_map
            else:
                inst_map = {}
        else:
            path = day_dir / "GLBX_MDP3" / f"{prefix}.dbn.zst"
            inst_map = {}
        if not path.exists():
            print(f"  [skip] {path.name} (missing)", flush=True)
            continue
        rec_n, ins_n, skip_n, elapsed = load_file(conn, path, inst_map, kind, dry_run)
        rate = ins_n / elapsed if elapsed > 0 else 0
        print(
            f"  {path.name} records={rec_n} inserted={ins_n} skipped={skip_n} "
            f"elapsed={elapsed:.1f}s rate={rate:.0f}/s",
            flush=True,
        )
        day_total_rec += rec_n
        day_total_in += ins_n
        day_total_skip += skip_n

    day_elapsed = time.time() - day_t0
    print(
        f"  >> day {day_dir.name}: records={day_total_rec} inserted={day_total_in} "
        f"skipped={day_total_skip} elapsed={day_elapsed:.1f}s",
        flush=True,
    )
    return day_total_rec, day_total_in, day_total_skip


def main():
    parser = argparse.ArgumentParser(
        description="Load Databento DBN files into FlowGreeks ticks hypertable.",
        epilog=(
            "WARNING: no dedup is performed. Re-running without --reset creates "
            "duplicate rows. Use --reset to TRUNCATE ticks before loading."
        ),
    )
    parser.add_argument(
        "--days", nargs="+",
        help="Day folder names like 2026-02-02. Default: all 2026-02-* dirs.",
    )
    parser.add_argument("--reset", action="store_true",
                        help="TRUNCATE ticks table before loading.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse + count only; do not write to DB.")
    parser.add_argument("--data-dir", default="data/databento",
                        help="Root databento data dir (default: data/databento).")
    args = parser.parse_args()

    data_root = Path(args.data_dir)
    if not data_root.is_dir():
        print(f"ERROR: data dir not found: {data_root}", file=sys.stderr)
        sys.exit(2)

    if args.days:
        day_dirs = [data_root / d for d in args.days]
    else:
        day_dirs = sorted([
            p for p in data_root.iterdir()
            if p.is_dir() and p.name.startswith("2026-02-")
        ])

    conn = None
    if not args.dry_run:
        conn = connect_pg()
        if args.reset:
            print("TRUNCATE ticks ...", flush=True)
            with conn.cursor() as cur:
                cur.execute("TRUNCATE ticks;")
            conn.commit()

    grand_t0 = time.time()
    grand_rec = grand_in = grand_skip = 0
    try:
        for d in day_dirs:
            if not d.is_dir():
                print(f"WARN: missing day dir: {d}", file=sys.stderr)
                continue
            r, i, s = process_day(conn, d, args.dry_run)
            grand_rec += r
            grand_in += i
            grand_skip += s
    finally:
        if conn is not None:
            conn.close()

    print(
        f"=== DONE elapsed={time.time()-grand_t0:.1f}s "
        f"records={grand_rec} inserted={grand_in} skipped={grand_skip} "
        f"dry_run={args.dry_run} ===",
        flush=True,
    )


if __name__ == "__main__":
    main()
