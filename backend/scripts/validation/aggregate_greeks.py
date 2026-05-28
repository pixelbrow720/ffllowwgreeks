"""Aggregate per-snapshot Greek parity stats across the 6 runs."""
from __future__ import annotations
from pathlib import Path
import json
import pandas as pd
import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "outputs"
GREEKS = ["delta", "gamma", "theta", "vega", "charm"]

runs = [
    ("2026-02-02", "SPX"),
    ("2026-02-02", "NDX"),
    ("2026-02-03", "SPX"),
    ("2026-02-03", "NDX"),
    ("2026-02-04", "SPX"),
    ("2026-02-04", "NDX"),
]

rows = []
total_strikes = 0
agg = {g: {"abs": [], "rel": []} for g in ["iv"] + GREEKS}

for date, root in runs:
    csv = OUT / date / f"iv_diff_{root}_160000Z.csv"
    meta = OUT / date / f"iv_ref_{root}_160000Z.json"
    df = pd.read_csv(csv)
    m = json.loads(meta.read_text())
    valid = df["iv_ref"].notna() & (df["iv_ref"] > 0) & (df["iv_ref"] < 9.99) & \
            df["fg_iv"].notna() & (df["fg_iv"] > 0) & df["fg_converged"].fillna(False).astype(bool)
    sub = df.loc[valid].copy()
    n = len(sub)
    total_strikes += n
    row = {
        "date": date, "root": root, "spot": m["spot"], "n": n,
    }
    # IV
    iv_abs = (sub["fg_iv"] - sub["iv_ref"]).abs()
    iv_rel = iv_abs / sub["iv_ref"].abs().clip(lower=1e-12)
    row["iv_abs_p50"] = float(iv_abs.quantile(0.50))
    row["iv_abs_p99"] = float(iv_abs.quantile(0.99))
    row["iv_abs_max"] = float(iv_abs.max())
    row["iv_rel_p99"] = float(iv_rel.quantile(0.99))
    agg["iv"]["abs"].append(iv_abs.values)
    agg["iv"]["rel"].append(iv_rel.values)

    for g in GREEKS:
        ref = sub[f"{g}_ref"]
        fg = sub[f"fg_{g}"]
        mask = ref.notna() & fg.notna() & np.isfinite(ref) & np.isfinite(fg)
        ad = (fg[mask] - ref[mask]).abs()
        denom = ref[mask].abs().clip(lower=1e-12)
        rd = ad / denom
        row[f"{g}_abs_p50"] = float(ad.quantile(0.50))
        row[f"{g}_abs_p99"] = float(ad.quantile(0.99))
        row[f"{g}_abs_max"] = float(ad.max())
        row[f"{g}_rel_p99"] = float(rd.quantile(0.99))
        agg[g]["abs"].append(ad.values)
        agg[g]["rel"].append(rd.values)
    rows.append(row)

df = pd.DataFrame(rows)
print(f"total strikes: {total_strikes:,}")
print()
print("Per snapshot summary:")
print(df[["date", "root", "spot", "n", "iv_abs_p99", "delta_abs_p99", "gamma_abs_p99",
         "theta_abs_p99", "vega_abs_p99", "charm_abs_p99"]].to_string(index=False))
print()
print("Aggregate (pooled across all 6 runs):")
for k in ["iv"] + GREEKS:
    a = np.concatenate(agg[k]["abs"])
    r = np.concatenate(agg[k]["rel"])
    print(f"  {k:6s} abs p50={np.percentile(a, 50):.3e}  p99={np.percentile(a, 99):.3e}  max={np.max(a):.3e}    "
          f"rel p99={np.percentile(r, 99):.3e}  rel max={np.max(r):.3e}")

# Write per-run table to CSV for the doc
df.to_csv(OUT / "_greeks_parity_summary.csv", index=False)
print(f"\nsaved -> {OUT / '_greeks_parity_summary.csv'}")
