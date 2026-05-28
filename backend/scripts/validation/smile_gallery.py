"""Visual QC gallery: IV smile + signed-gamma profile per (day, root).

For each of 9 trading days x 2 roots (SPX, NDX) we render a 2-panel
figure that overlays up to 6 RTH snapshots:

    LEFT   smile shape per snapshot, color graded by time of day
    RIGHT  signed-gamma intensity vs strike (heuristic, NOT GEX)

Signed-gamma rule (heuristic; visualizes where dealer hedging
concentrates -- symmetric Black-Scholes gamma weighted by call/put
side relative to ATM):

    sign = +1 if (call & K<S) or (put & K>S) else -1
    signed_gamma = sign * gamma_bs

Outputs:
    outputs/_smile_gallery/<date>_<root>.png   (up to 18 panels, 1600x600 @ 110 dpi)
    outputs/_smile_gallery/_index.png          (9x2 thumbnail grid)

Missing iv_ref CSVs are generated on-demand by invoking iv_parity.py.

Usage:
    python smile_gallery.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import image as mpimg
import numpy as np
import pandas as pd
from scipy.stats import norm


HERE = Path(__file__).resolve().parent
OUT_ROOT = HERE / "outputs"
GALLERY_DIR = OUT_ROOT / "_smile_gallery"
PYTHON = HERE / ".venv" / "Scripts" / "python.exe"

DATES = [
    "2026-02-02", "2026-02-03", "2026-02-04",
    "2026-02-05", "2026-02-06", "2026-02-09",
    "2026-02-10", "2026-02-11", "2026-02-12",
]
ROOTS = ["SPX", "NDX"]
SNAPSHOTS = ["14:45:00Z", "16:00:00Z", "17:30:00Z",
             "18:30:00Z", "19:30:00Z", "20:00:00Z"]
MAX_STRIKES_PLOTTED = 200


def snap_tag(snap: str) -> str:
    return snap.replace(":", "")


def csv_path(date: str, root: str, snap: str) -> Path:
    return OUT_ROOT / date / f"iv_ref_{root}_{snap_tag(snap)}.csv"


def json_path(date: str, root: str, snap: str) -> Path:
    return csv_path(date, root, snap).with_suffix(".json")


def ensure_iv_ref(date: str, root: str, snap: str) -> bool:
    """Generate iv_ref CSV via iv_parity.py if missing. Returns True if usable."""
    if csv_path(date, root, snap).exists() and json_path(date, root, snap).exists():
        return True
    print(f"  [gen] {date} {root} {snap}", flush=True)
    t0 = time.time()
    cmd = [str(PYTHON), "iv_parity.py", date, "--root", root, "--snapshot", snap]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env)
    dt = time.time() - t0
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        msg = tail[-1] if tail else "unknown"
        print(f"    FAILED ({dt:.1f}s): {msg[:160]}", file=sys.stderr)
        return False
    print(f"    ok ({dt:.1f}s)", flush=True)
    return csv_path(date, root, snap).exists() and json_path(date, root, snap).exists()


def load_snapshot(date: str, root: str, snap: str):
    cp, jp = csv_path(date, root, snap), json_path(date, root, snap)
    if not (cp.exists() and jp.exists()):
        return None
    df = pd.read_csv(cp)
    valid = df["iv_ref"].notna() & (df["iv_ref"] > 0) & (df["iv_ref"] < 5.0)
    df = df.loc[valid].copy()
    if df.empty:
        return None
    with open(jp, "r", encoding="utf-8") as f:
        meta = json.load(f)
    spot = float(meta["spot"])
    df["strike_dist"] = (df["strike_price"].astype(float) - spot).abs()
    df = df.nsmallest(MAX_STRIKES_PLOTTED, "strike_dist")
    return df, meta


def bs_gamma(spot: float, k: np.ndarray, t: np.ndarray, sigma: np.ndarray,
             r: float, q: float) -> np.ndarray:
    """Analytical Black-Scholes-Merton gamma (call = put)."""
    sqrt_t = np.sqrt(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(spot / k) + (r - q + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
        g = np.exp(-q * t) * norm.pdf(d1) / (spot * sigma * sqrt_t)
    return np.where(np.isfinite(g), g, 0.0)


def signed_gamma(df: pd.DataFrame, spot: float, r: float, q: float) -> np.ndarray:
    k = df["strike_price"].values.astype(float)
    t = df["t_years"].values.astype(float)
    sigma = df["iv_ref"].values.astype(float)
    cls = df["instrument_class"].astype(str).str.upper().str[0].values
    g = bs_gamma(spot, k, t, sigma, r, q)
    is_call = (cls == "C")
    below = (k < spot)
    sign = np.where((is_call & below) | (~is_call & ~below), 1.0, -1.0)
    return sign * g


def plot_panel(date: str, root: str, snapshots: list, out_path: Path) -> dict:
    """Render one 2-panel figure. Returns small stats dict for anomaly reporting."""
    fig, (ax_l, ax_r) = plt.subplots(1, 2, figsize=(1600 / 110, 600 / 110), dpi=110)
    cmap = plt.get_cmap("viridis")
    n = len(snapshots)
    spot_first = float(snapshots[0][2]["spot"])
    accumulated: dict[float, float] = {}
    iv_min, iv_max = float("inf"), float("-inf")

    for i, (snap, df, meta) in enumerate(snapshots):
        color = cmap(0.05 + 0.85 * (i / max(n - 1, 1)))
        spot = float(meta["spot"])
        ax_l.scatter(df["strike_price"], df["iv_ref"], s=8, alpha=0.55,
                     color=color, label=f"{snap[:5]}")
        iv_min = min(iv_min, float(df["iv_ref"].min()))
        iv_max = max(iv_max, float(df["iv_ref"].max()))
        sg = signed_gamma(df, spot, float(meta["rfr"]), float(meta["div"]))
        for kv, gv in zip(df["strike_price"].values, sg):
            accumulated[float(kv)] = accumulated.get(float(kv), 0.0) + float(gv)

    ax_l.axvline(spot_first, color="#71717a", linestyle="--", linewidth=1, alpha=0.7)
    ax_l.set_xlabel("Strike")
    ax_l.set_ylabel("IV (annualized)")
    ax_l.set_title(f"{root} smile  |  {date}  |  {n} snapshots  |  spot~{spot_first:.0f}")
    ax_l.grid(True, alpha=0.25)
    ax_l.legend(fontsize=7, ncol=2, loc="upper right")

    if accumulated:
        strikes = np.array(sorted(accumulated.keys()))
        vals = np.array([accumulated[k] for k in strikes])
        if len(strikes) > 1:
            width = (strikes[-1] - strikes[0]) / len(strikes) * 0.85
        else:
            width = 1.0
        colors = ["#10b981" if v >= 0 else "#ef4444" for v in vals]
        ax_r.bar(strikes, vals, width=width, color=colors, alpha=0.8)
        ax_r.axvline(spot_first, color="#71717a", linestyle="--", linewidth=1, alpha=0.7)
        ax_r.axhline(0, color="#71717a", linewidth=0.6)
    ax_r.set_xlabel("Strike")
    ax_r.set_ylabel("Cumulative signed gamma  (heuristic)")
    ax_r.set_title(f"{root}  |  signed-gamma profile  |  {date}")
    ax_r.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return {"iv_min": iv_min, "iv_max": iv_max, "n_strikes_acc": len(accumulated)}


def build_index(panel_paths: list, out_path: Path) -> None:
    """9 rows x 2 columns thumbnail grid."""
    rows, cols = len(DATES), len(ROOTS)
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 5.0, rows * 1.9), dpi=100)
    by_key = {(d, r): p for d, r, p in panel_paths}
    for i, date in enumerate(DATES):
        for j, root in enumerate(ROOTS):
            ax = axes[i][j] if rows > 1 else axes[j]
            p = by_key.get((date, root))
            if p and p.exists():
                ax.imshow(mpimg.imread(p))
                ax.set_title(f"{date}  {root}", fontsize=8)
            else:
                ax.text(0.5, 0.5, "missing", ha="center", va="center", fontsize=10)
                ax.set_title(f"{date}  {root}  (missing)", fontsize=8)
            ax.set_xticks([])
            ax.set_yticks([])
    fig.suptitle("FlowGreeks smile + signed-gamma gallery (qualitative QA)",
                 fontsize=12, y=0.995)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(out_path, dpi=100, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    GALLERY_DIR.mkdir(parents=True, exist_ok=True)
    if not PYTHON.exists():
        print(f"FATAL: missing venv python at {PYTHON}", file=sys.stderr)
        return 2

    triples = [(d, r, s) for d in DATES for r in ROOTS for s in SNAPSHOTS]
    missing = [(d, r, s) for d, r, s in triples if not csv_path(d, r, s).exists()]
    print(f"iv_ref CSV coverage: have {len(triples) - len(missing)} / {len(triples)}, "
          f"missing {len(missing)}")
    generated = 0
    failed: list[tuple[str, str, str]] = []
    for d, r, s in missing:
        if ensure_iv_ref(d, r, s):
            generated += 1
        else:
            failed.append((d, r, s))

    panels: list[tuple[str, str, Path]] = []
    anomalies: list[str] = []
    for d in DATES:
        for r in ROOTS:
            loaded = []
            for s in SNAPSHOTS:
                got = load_snapshot(d, r, s)
                if got is not None:
                    loaded.append((s, got[0], got[1]))
            out = GALLERY_DIR / f"{d}_{r}.png"
            if not loaded:
                anomalies.append(f"  no usable snapshots: {d} {r}")
                continue
            try:
                stats = plot_panel(d, r, loaded, out)
                panels.append((d, r, out))
                spread = stats["iv_max"] - stats["iv_min"]
                if spread < 0.02:
                    anomalies.append(f"  flat smile suspect ({d} {r}): "
                                     f"IV spread = {spread:.4f}")
            except Exception as exc:  # noqa: BLE001
                anomalies.append(f"  panel failed {d} {r}: {exc}")

    idx_path = GALLERY_DIR / "_index.png"
    build_index(panels, idx_path)

    print()
    print(f"panels generated: {len(panels)} / {len(DATES) * len(ROOTS)}")
    print(f"iv_ref CSVs newly generated: {generated}")
    if failed:
        print(f"iv_parity failures: {len(failed)}")
        for d, r, s in failed[:10]:
            print(f"  {d} {r} {s}")
    print(f"index image: {idx_path}")
    if anomalies:
        print("anomalies:")
        for a in anomalies:
            print(a)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
