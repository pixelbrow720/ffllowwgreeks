"""Join scipy reference IV+Greeks + FlowGreeks IV+Greeks, compute parity diff.

Reads two CSVs produced earlier in the pipeline:
  - iv_ref_<ROOT>_<HHMMSSZ>.csv  (scipy reference, from iv_parity.py — IV + Δ Γ Θ Vega Charm)
  - iv_fg_<ROOT>_<HHMMSSZ>.csv   (FlowGreeks IV+Greeks, from cmd/dump_fg_greeks)

Joins on instrument_id, computes:
  - iv_diff_abs   = |iv_fg - iv_ref|
  - iv_diff_rel   = iv_diff_abs / iv_ref
  - delta_diff_abs / gamma_diff_abs / theta_diff_abs / vega_diff_abs / charm_diff_abs
  - same with _rel suffix (relative to ref magnitude)
  - converged_match (FG converged AND ref valid)

Writes:
  - iv_diff_<ROOT>_<HHMMSSZ>.csv  full joined table
  - iv_diff_<ROOT>_<HHMMSSZ>.png  histogram + scatter plots

Usage:
    python iv_diff.py 2026-02-02 --root SPX --snapshot 16:00:00Z

Decision rule for "math is correct":
    For each Greek (and IV): abs p99 < 1e-4 OR rel p99 < 1e-4 ⇒ PASS
    All Greeks PASS                                          ⇒ overall PASS
    Any Greek fails both abs and rel bars                     ⇒ INVESTIGATE
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd


HERE = Path(__file__).resolve().parent
OUT_ROOT = HERE / "outputs"

# Greeks tracked for parity (Vanna intentionally excluded — see docs).
GREEKS = ["delta", "gamma", "theta", "vega", "charm"]


def load_pair(date: str, root: str, snapshot: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    snap_tag = snapshot.replace(":", "")
    out_dir = OUT_ROOT / date
    ref_path = out_dir / f"iv_ref_{root.upper()}_{snap_tag}.csv"
    fg_path = out_dir / f"iv_fg_{root.upper()}_{snap_tag}.csv"
    for p in (ref_path, fg_path):
        if not p.exists():
            print(f"FATAL: missing {p}", file=sys.stderr)
            sys.exit(2)
    ref = pd.read_csv(ref_path)
    fg = pd.read_csv(fg_path)
    return ref, fg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date")
    ap.add_argument("--root", default="SPX", choices=["SPX", "NDX"])
    ap.add_argument("--snapshot", default="16:00:00Z")
    args = ap.parse_args()

    ref, fg = load_pair(args.date, args.root, args.snapshot)

    # Normalise key types (CSV roundtrip can give str ids)
    ref["instrument_id"] = ref["instrument_id"].astype(str)
    fg["instrument_id"] = fg["instrument_id"].astype(str)

    keep_ref = ["instrument_id", "strike_price", "instrument_class",
                "iv_ref", "mid", "t_years",
                "delta_ref", "gamma_ref", "theta_ref", "vega_ref", "charm_ref"]
    keep_ref = [c for c in keep_ref if c in ref.columns]
    keep_fg = ["instrument_id", "fg_iv", "fg_converged", "fg_iters", "fg_reason",
               "fg_delta", "fg_gamma", "fg_theta", "fg_vega", "fg_charm", "fg_vanna"]
    keep_fg = [c for c in keep_fg if c in fg.columns]

    df = ref[keep_ref].merge(fg[keep_fg], on="instrument_id", how="inner")
    print(f"joined rows: {len(df):,}")

    # Quality buckets
    ref_valid = df["iv_ref"].notna() & (df["iv_ref"] > 0) & (df["iv_ref"] < 9.99)
    fg_valid  = df["fg_iv"].notna() & (df["fg_iv"] > 0) & df["fg_converged"].fillna(False).astype(bool)
    both_valid = ref_valid & fg_valid

    print(f"  ref valid:   {ref_valid.sum():,}")
    print(f"  fg valid:    {fg_valid.sum():,}")
    print(f"  both valid:  {both_valid.sum():,}")
    print(f"  ref-only:    {(ref_valid & ~fg_valid).sum():,}")
    print(f"  fg-only:     {(~ref_valid & fg_valid).sum():,}")
    print(f"  neither:     {(~ref_valid & ~fg_valid).sum():,}")

    if both_valid.sum() == 0:
        print("FATAL: no rows where both solvers converged", file=sys.stderr)
        return 2

    diff = df.loc[both_valid].copy()
    diff["iv_diff_abs"] = (diff["fg_iv"] - diff["iv_ref"]).abs()
    diff["iv_diff_rel"] = diff["iv_diff_abs"] / diff["iv_ref"].abs()

    # Stats
    abs_p = diff["iv_diff_abs"].quantile([0.50, 0.90, 0.95, 0.99, 1.0]).to_dict()
    rel_p = diff["iv_diff_rel"].quantile([0.50, 0.90, 0.95, 0.99, 1.0]).to_dict()

    print()
    print(f"IV abs diff (vol points):")
    for q, v in abs_p.items():
        print(f"  p{int(q*100):>3}  {v:.6e}")
    print(f"IV rel diff (fraction of ref):")
    for q, v in rel_p.items():
        print(f"  p{int(q*100):>3}  {v:.6e}")

    # Per-Greek diff stats
    greek_summary: dict[str, dict[str, float]] = {}
    for g in GREEKS:
        ref_col = f"{g}_ref"
        fg_col = f"fg_{g}"
        if ref_col not in diff.columns or fg_col not in diff.columns:
            print(f"\n{g.upper()}: skipped (missing column)")
            continue
        # Mask: both finite and ref nonzero (rel diff would blow up at zero)
        ref_v = diff[ref_col]
        fg_v = diff[fg_col]
        mask = ref_v.notna() & fg_v.notna() & np.isfinite(ref_v) & np.isfinite(fg_v)
        sub = diff.loc[mask].copy()
        if sub.empty:
            print(f"\n{g.upper()}: no comparable rows")
            continue
        abs_d = (sub[fg_col] - sub[ref_col]).abs()
        # Avoid div-by-zero for rel; use |ref| with small floor
        denom = sub[ref_col].abs().clip(lower=1e-12)
        rel_d = abs_d / denom
        diff.loc[mask, f"{g}_diff_abs"] = abs_d.values
        diff.loc[mask, f"{g}_diff_rel"] = rel_d.values
        ap50 = float(abs_d.quantile(0.50))
        ap99 = float(abs_d.quantile(0.99))
        amax = float(abs_d.max())
        rp50 = float(rel_d.quantile(0.50))
        rp99 = float(rel_d.quantile(0.99))
        rmax = float(rel_d.max())
        greek_summary[g] = {
            "n": int(mask.sum()),
            "abs_p50": ap50, "abs_p99": ap99, "abs_max": amax,
            "rel_p50": rp50, "rel_p99": rp99, "rel_max": rmax,
        }
        print()
        print(f"{g.upper()} diff (n={int(mask.sum()):,}):")
        print(f"  abs   p50={ap50:.3e}  p99={ap99:.3e}  max={amax:.3e}")
        print(f"  rel   p50={rp50:.3e}  p99={rp99:.3e}  max={rmax:.3e}")

    # Overall verdict: PASS iff IV + every tracked Greek pass (abs p99 < 1e-4 OR rel p99 < 1e-4)
    failures: list[str] = []
    iv_p99 = abs_p[0.99]
    iv_rel_p99 = rel_p[0.99]
    if not (iv_p99 < 1e-4 or iv_rel_p99 < 1e-4):
        failures.append(f"IV (abs={iv_p99:.2e}, rel={iv_rel_p99:.2e})")
    for g, s in greek_summary.items():
        if not (s["abs_p99"] < 1e-4 or s["rel_p99"] < 1e-4):
            failures.append(f"{g} (abs={s['abs_p99']:.2e}, rel={s['rel_p99']:.2e})")

    if not failures:
        verdict = "PASS - IV + Greeks all match scipy at p99 < 1e-4 (abs or rel)"
    elif iv_p99 < 1e-3:
        verdict = f"INVESTIGATE - failed: {', '.join(failures)}"
    else:
        verdict = f"INVESTIGATE - failed: {', '.join(failures)}"
    print()
    print(f"VERDICT: {verdict}")

    # Save merged with all diff columns; recompute on full df (unfiltered) so
    # consumers see NaN for invalid rows rather than rows being silently dropped.
    out_dir = OUT_ROOT / args.date
    out_csv = out_dir / f"iv_diff_{args.root.upper()}_{args.snapshot.replace(':', '')}.csv"
    df_full = df.copy()
    df_full["iv_diff_abs"] = (df_full["fg_iv"] - df_full["iv_ref"]).abs()
    df_full["iv_diff_rel"] = df_full["iv_diff_abs"] / df_full["iv_ref"].abs()
    for g in GREEKS:
        ref_col = f"{g}_ref"
        fg_col = f"fg_{g}"
        if ref_col in df_full.columns and fg_col in df_full.columns:
            ad = (df_full[fg_col] - df_full[ref_col]).abs()
            denom = df_full[ref_col].abs().clip(lower=1e-12)
            df_full[f"{g}_diff_abs"] = ad
            df_full[f"{g}_diff_rel"] = ad / denom
    df_full.to_csv(out_csv, index=False)
    print(f"  -> {out_csv.relative_to(HERE.parent.parent.parent)}")

    # Plots
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))
        # Panel A: histogram of abs diff
        ax = axes[0]
        ax.hist(np.log10(diff["iv_diff_abs"].clip(lower=1e-12)), bins=80, color="#1c1c20")
        ax.set_xlabel("log10(|fg_iv - iv_ref|)")
        ax.set_ylabel("count")
        ax.set_title(f"IV diff distribution  |  {args.root} {args.date} {args.snapshot}\n"
                      f"n={len(diff):,}  p99={abs_p[0.99]:.2e}")
        ax.axvline(np.log10(1e-4), color="#10b981", linestyle="--", linewidth=1, label="1bp (PASS bar)")
        ax.axvline(np.log10(1e-3), color="#f59e0b", linestyle="--", linewidth=1, label="10bp (ACCEPTABLE bar)")
        ax.legend()
        ax.grid(True, alpha=0.3)

        # Panel B: scatter ref vs fg, with y=x line
        ax = axes[1]
        for cls, marker, color in [("C", "^", "#10b981"), ("P", "v", "#ef4444")]:
            sub = diff[diff["instrument_class"].str.upper().str.startswith(cls)]
            if sub.empty:
                continue
            ax.scatter(sub["iv_ref"], sub["fg_iv"], s=8, marker=marker,
                       color=color, alpha=0.5, label=cls)
        lo = max(1e-3, diff[["iv_ref", "fg_iv"]].min().min())
        hi = min(10.0, diff[["iv_ref", "fg_iv"]].max().max())
        ax.plot([lo, hi], [lo, hi], color="#71717a", linestyle="--", linewidth=1, label="y=x")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("scipy iv_ref")
        ax.set_ylabel("FlowGreeks fg_iv")
        ax.set_title("Solver agreement (log-log)")
        ax.legend()
        ax.grid(True, alpha=0.3, which="both")

        out_png = out_csv.with_suffix(".png")
        fig.tight_layout()
        fig.savefig(out_png, dpi=120)
        print(f"  -> {out_png.relative_to(HERE.parent.parent.parent)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN: plot failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
