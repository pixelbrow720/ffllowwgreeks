"""Probe Databento DBN files for one trading day.

Counts records per file and dumps a few samples so we can confirm
schemas decode cleanly before running parity tests.

Usage:
    python probe_data.py 2026-02-02
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import databento as db


REPO_ROOT = Path(__file__).resolve().parents[3]  # backend/scripts/validation/.. -> repo root
DATA_ROOT = REPO_ROOT / "backend" / "data" / "databento"

# (subdir, filename, friendly_label)
EXPECTED_FILES = [
    ("OPRA_PILLAR", "definition__SPX-OPT_SPXW-OPT.dbn.zst",  "OPRA SPX  definition"),
    ("OPRA_PILLAR", "definition__NDX-OPT_NDXP-OPT.dbn.zst",  "OPRA NDX  definition"),
    ("OPRA_PILLAR", "statistics__SPX-OPT_SPXW-OPT.dbn.zst",  "OPRA SPX  statistics"),
    ("OPRA_PILLAR", "statistics__NDX-OPT_NDXP-OPT.dbn.zst",  "OPRA NDX  statistics"),
    ("OPRA_PILLAR", "tcbbo__SPX-OPT_SPXW-OPT.dbn.zst",       "OPRA SPX  tcbbo"),
    ("OPRA_PILLAR", "tcbbo__NDX-OPT_NDXP-OPT.dbn.zst",       "OPRA NDX  tcbbo"),
    ("GLBX_MDP3",   "mbp-1__ES-FUT.dbn.zst",                 "GLBX ES   mbp-1"),
    ("GLBX_MDP3",   "mbp-1__NQ-FUT.dbn.zst",                 "GLBX NQ   mbp-1"),
    ("GLBX_MDP3",   "trades__ES-FUT.dbn.zst",                "GLBX ES   trades"),
    ("GLBX_MDP3",   "trades__NQ-FUT.dbn.zst",                "GLBX NQ   trades"),
]


def probe_one(path: Path, label: str) -> None:
    if not path.exists():
        print(f"  [MISS] {label:<22}  {path.name}")
        return
    size_mb = path.stat().st_size / 1_048_576
    try:
        store = db.DBNStore.from_file(path)
        df = store.to_df()
    except Exception as exc:  # noqa: BLE001
        print(f"  [ERR ] {label:<22}  {path.name}  ({size_mb:6.1f} MB)  decode failed: {exc}")
        return

    n = len(df)
    cols = list(df.columns)
    print(f"  [ OK ] {label:<22}  {size_mb:6.1f} MB  {n:>10,} rows  cols={len(cols)}")
    if n == 0:
        return
    # Print the first row as a one-liner of the most interesting fields if present.
    sample_fields = [c for c in ("ts_event", "instrument_id", "raw_symbol", "expiration",
                                  "strike_price", "instrument_class", "bid_px_00", "ask_px_00",
                                  "price", "size", "open_interest_qty")
                     if c in cols]
    if sample_fields:
        first = df.iloc[0]
        bits = ", ".join(f"{f}={first[f]!s:.40}" for f in sample_fields)
        print(f"           sample[0]: {bits}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date", help="trading day in YYYY-MM-DD")
    args = ap.parse_args()

    day_dir = DATA_ROOT / args.date
    if not day_dir.exists():
        print(f"FATAL: {day_dir} does not exist", file=sys.stderr)
        return 2

    print(f"Probing {day_dir}")
    print()
    for subdir, fname, label in EXPECTED_FILES:
        probe_one(day_dir / subdir / fname, label)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
