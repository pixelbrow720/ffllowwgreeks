"""IV parity test: FlowGreeks IV vs scipy/numpy reference for one OPRA snapshot.

Workflow:
  1. Load definition file → strike + expiry per instrument_id
  2. Load tcbbo file → walk forward to a target snapshot timestamp,
     keep latest (bid, ask, ts) per instrument_id
  3. For each strike with both bid + ask, compute mid price
  4. Solve IV via bs_reference.implied_vol_vec (scipy brentq)
  5. Save CSV + scatter plot

Usage:
    python iv_parity.py 2026-02-02 --snapshot 16:00:00Z --root SPX
    python iv_parity.py 2026-02-02 --snapshot 19:30:00Z --root NDX

This script intentionally only produces the *reference* IV column. A
companion Go dumper (cmd/dump_fg_greeks, future) will emit FlowGreeks's
own IV per (instrument_id, ts) so the two columns can be joined and
diffed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import databento as db
import numpy as np
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "backend" / "data" / "databento"
OUT_ROOT = Path(__file__).resolve().parent / "outputs"

# Default risk-free + dividend yield. Replace with curve-aware values
# once we have a yield-curve loader. For 0DTE on a single day these
# constants are essentially noise.
RFR_DEFAULT = 0.045
DIV_DEFAULT = {"SPX": 0.013, "NDX": 0.008}


def load_definition(path: Path) -> pd.DataFrame:
    """Return DataFrame with one row per instrument_id, indexed by id.

    Columns: raw_symbol, strike_price (USD), expiration (datetime),
    instrument_class ('C' / 'P').
    """
    store = db.DBNStore.from_file(path)
    df = store.to_df()
    if df.empty:
        raise SystemExit(f"definition empty: {path}")
    # Take the first record per instrument_id (definitions usually appear
    # once at session start).
    df = df.reset_index()  # ts_recv becomes a column
    df = df.drop_duplicates(subset="instrument_id", keep="first")
    keep = ["instrument_id", "raw_symbol", "strike_price",
            "expiration", "instrument_class"]
    keep = [c for c in keep if c in df.columns]
    out = df[keep].set_index("instrument_id")
    # Strike comes in 1e-9 fixed-point in DBN; databento SDK already
    # converts it to float USD when to_df() is used. Sanity check:
    if "strike_price" in out.columns:
        med = out["strike_price"].median()
        if med > 1e6:
            out["strike_price"] = out["strike_price"] / 1e9
    return out


def snapshot_quotes(path: Path, snapshot_utc: datetime) -> pd.DataFrame:
    """Walk tcbbo until snapshot_utc and keep last (bid, ask) per id.

    tcbbo emits a record on every trade carrying the BBO at trade time.
    For a snapshot we want the most recent BBO for every active strike
    seen up to the cutoff.
    """
    store = db.DBNStore.from_file(path)
    df = store.to_df().reset_index()
    if df.empty:
        raise SystemExit(f"tcbbo empty: {path}")
    ts_col = "ts_event" if "ts_event" in df.columns else "ts_recv"
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df[df[ts_col] <= snapshot_utc]
    if df.empty:
        raise SystemExit(f"no tcbbo records before {snapshot_utc.isoformat()}")
    df = df.sort_values(ts_col).drop_duplicates("instrument_id", keep="last")
    cols = ["instrument_id", ts_col, "bid_px_00", "ask_px_00",
            "bid_sz_00", "ask_sz_00", "price", "size"]
    cols = [c for c in cols if c in df.columns]
    return df[cols].set_index("instrument_id")


def compute_iv_reference(joined: pd.DataFrame, spot: float, rfr: float,
                          div: float, snapshot_utc: datetime) -> pd.DataFrame:
    """Add iv_ref + analytical Greek_ref columns using bs_reference (scipy)."""
    from bs_reference import implied_vol_vec, compute_greeks_vec

    # Mid price; drop crossed / locked / one-sided quotes.
    valid = (joined["bid_px_00"] > 0) & (joined["ask_px_00"] > 0) & \
            (joined["ask_px_00"] >= joined["bid_px_00"])
    joined = joined[valid].copy()
    joined["mid"] = (joined["bid_px_00"] + joined["ask_px_00"]) / 2.0

    # Time to expiry in years, cut off at 16:00 ET on expiry date.
    expiry_close = pd.to_datetime(joined["expiration"], utc=True)
    snap = snapshot_utc.replace(tzinfo=timezone.utc)
    seconds = (expiry_close - snap).dt.total_seconds()
    joined["t_years"] = seconds / (365.25 * 86400.0)
    joined = joined[joined["t_years"] > 0]
    if joined.empty:
        return joined

    flag = joined["instrument_class"].map(
        lambda c: "c" if str(c).upper().startswith("C") else "p"
    )

    iv = implied_vol_vec(
        prices=joined["mid"].values,
        spot=spot,
        strikes=joined["strike_price"].values,
        ts=joined["t_years"].values,
        r=rfr,
        q=div,
        kinds=flag.values,
    )
    joined["iv_ref"] = iv

    # Reference Greeks via analytical formulas (only where iv_ref is valid).
    greeks = compute_greeks_vec(
        spot=spot,
        strikes=joined["strike_price"].values,
        ts=joined["t_years"].values,
        r=rfr,
        q=div,
        sigmas=iv,
        kinds=flag.values,
    )
    joined["delta_ref"] = greeks["delta"]
    joined["gamma_ref"] = greeks["gamma"]
    joined["theta_ref"] = greeks["theta"]
    joined["vega_ref"] = greeks["vega"]
    joined["charm_ref"] = greeks["charm"]
    return joined


def parse_snapshot(date_str: str, snapshot_str: str) -> datetime:
    """Combine YYYY-MM-DD with HH:MM:SSZ into a tz-aware UTC datetime."""
    snap = snapshot_str.rstrip("Z")
    dt = datetime.fromisoformat(f"{date_str}T{snap}+00:00")
    return dt


def root_to_files(root: str) -> tuple[str, str]:
    if root.upper() == "SPX":
        return ("definition__SPX-OPT_SPXW-OPT.dbn.zst",
                "tcbbo__SPX-OPT_SPXW-OPT.dbn.zst")
    if root.upper() == "NDX":
        return ("definition__NDX-OPT_NDXP-OPT.dbn.zst",
                "tcbbo__NDX-OPT_NDXP-OPT.dbn.zst")
    raise SystemExit(f"unknown root: {root}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date", help="trading day YYYY-MM-DD")
    ap.add_argument("--snapshot", default="16:00:00Z",
                    help="UTC time within the day, e.g. 16:00:00Z (default 16:00 = 12:00 ET)")
    ap.add_argument("--root", default="SPX", choices=["SPX", "NDX"])
    ap.add_argument("--spot", type=float, default=None,
                    help="Override spot. Otherwise uses ATM mid heuristic.")
    ap.add_argument("--rfr", type=float, default=RFR_DEFAULT)
    args = ap.parse_args()

    snapshot_utc = parse_snapshot(args.date, args.snapshot)
    def_file, tcbbo_file = root_to_files(args.root)
    day_dir = DATA_ROOT / args.date / "OPRA_PILLAR"
    def_path = day_dir / def_file
    tcbbo_path = day_dir / tcbbo_file
    for p in (def_path, tcbbo_path):
        if not p.exists():
            print(f"FATAL: missing {p}", file=sys.stderr)
            return 2

    print(f"Loading definition: {def_path.name}")
    defs = load_definition(def_path)
    print(f"  {len(defs):,} instruments")

    print(f"Loading tcbbo (snapshot <= {snapshot_utc.isoformat()}): {tcbbo_path.name}")
    quotes = snapshot_quotes(tcbbo_path, snapshot_utc)
    print(f"  {len(quotes):,} active strikes at snapshot")

    joined = quotes.join(defs, how="inner")
    if joined.empty:
        print("FATAL: no overlap between tcbbo and definition", file=sys.stderr)
        return 2

    # Spot heuristic: median strike of the 20 most-tightly-quoted contracts
    # is usually within ~1% of true ATM. Better than nothing without an
    # underlying spot tick.
    if args.spot is None:
        joined["spread"] = joined["ask_px_00"] - joined["bid_px_00"]
        tight = joined.nsmallest(20, "spread")
        spot_est = float(tight["strike_price"].median())
        print(f"  estimated spot (from tightest 20 spreads' median strike): {spot_est:.2f}")
    else:
        spot_est = args.spot
        print(f"  using provided spot: {spot_est:.2f}")

    div = DIV_DEFAULT.get(args.root.upper(), 0.0)

    print(f"Solving IV via scipy brentq (S={spot_est:.2f}, r={args.rfr}, q={div})")
    result = compute_iv_reference(joined, spot_est, args.rfr, div, snapshot_utc)
    if result.empty:
        print("WARN: no rows survived expiry/quote filters", file=sys.stderr)
        return 1
    print(f"  {len(result):,} IVs solved")

    # Quality stats
    valid = result["iv_ref"].notna() & (result["iv_ref"] > 0)
    print(f"  valid IV: {valid.sum():,} / {len(result):,}  "
          f"({100 * valid.sum() / len(result):.1f}%)")
    if valid.any():
        iv_ok = result.loc[valid, "iv_ref"]
        print(f"  IV range: {iv_ok.min():.3f} – {iv_ok.max():.3f}")
        print(f"  IV median: {iv_ok.median():.3f}")

    # Save outputs
    out_dir = OUT_ROOT / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"iv_ref_{args.root.upper()}_{args.snapshot.replace(':', '')}.csv"
    result.to_csv(csv_path)
    print(f"  → {csv_path.relative_to(REPO_ROOT)}")

    # Sidecar metadata so batch runners can read spot/r/q without reparsing.
    import json
    meta_path = csv_path.with_suffix(".json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump({
            "date": args.date,
            "root": args.root.upper(),
            "snapshot": args.snapshot,
            "spot": float(spot_est),
            "rfr": float(args.rfr),
            "div": float(div),
            "n_strikes": int(len(result)),
            "n_valid_iv_ref": int(valid.sum()),
        }, f, indent=2)

    # Plot
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 6))
        for cls, marker, color in [("C", "^", "#10b981"), ("P", "v", "#ef4444")]:
            sub = result[result["instrument_class"].str.upper().str.startswith(cls) & valid]
            if sub.empty:
                continue
            ax.scatter(sub["strike_price"], sub["iv_ref"], s=10,
                       marker=marker, color=color, alpha=0.6, label=f"{cls}")
        ax.axvline(spot_est, color="#71717a", linestyle="--", linewidth=1, label=f"spot~{spot_est:.0f}")
        ax.set_xlabel("Strike")
        ax.set_ylabel("Implied vol (annualized)")
        ax.set_title(f"{args.root} IV smile  |  {args.date} {args.snapshot}  |  scipy brentq reference")
        ax.legend()
        ax.grid(True, alpha=0.3)
        png_path = csv_path.with_suffix(".png")
        fig.tight_layout()
        fig.savefig(png_path, dpi=120)
        print(f"  → {png_path.relative_to(REPO_ROOT)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: plot failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
