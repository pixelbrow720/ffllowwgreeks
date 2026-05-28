"""Batch IV parity validation: run the full pipeline across many days × snapshots.

For each (date, root, snapshot):
  1. iv_parity.py    -> scipy reference IV CSV + sidecar JSON (spot/r/q)
  2. dump_fg_greeks  -> FlowGreeks IV+Greeks CSV
  3. iv_diff.py      -> joined diff stats

Aggregates all per-snapshot stats into a single summary CSV + Markdown report.

Usage:
    python batch_validate.py \
        --dates 2026-02-02 2026-02-03 2026-02-04 \
        --roots SPX NDX \
        --snapshots 14:30:00Z 16:00:00Z 18:00:00Z 19:30:00Z 20:00:00Z

The Go dumper binary must already be built:
    go build -o bin/dump_fg_greeks.exe ./cmd/dump_fg_greeks
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd


HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent
REPO = BACKEND.parent
OUT_ROOT = HERE / "outputs"
DUMPER = BACKEND / "bin" / "dump_fg_greeks.exe"
PYTHON = HERE / ".venv" / "Scripts" / "python.exe"


@dataclass
class Result:
    date: str
    root: str
    snapshot: str
    spot: float
    n_strikes: int
    n_both_valid: int
    n_neither: int
    iv_diff_p50: float
    iv_diff_p99: float
    iv_diff_max: float
    rel_diff_p99: float
    verdict: str
    elapsed_s: float
    error: str = ""


def run_step(label: str, cmd: list[str | Path], cwd: Path, env_extra: dict | None = None) -> str:
    """Run a subprocess; return stdout (and stderr concatenated). Raise on nonzero."""
    import os
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"{label} exit={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc.stdout + proc.stderr


def parse_diff_output(text: str) -> tuple[dict, str]:
    """Pull p50 / p99 / max from iv_diff.py stdout.

    Format from iv_diff.py:
        p 50  1.445510e-08
        p 90  1.853824e-07
        ...

    Note: 'p' and the percentile are space-separated, hence parts[1] for
    the percentile and parts[2] for the value.
    """
    stats: dict[str, float] = {}
    verdict = ""
    in_abs = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("IV abs diff"):
            in_abs = True
            continue
        if s.startswith("IV rel diff") or s.startswith("VERDICT"):
            in_abs = False
        if in_abs and s.startswith("p"):
            parts = s.split()
            # parts: ["p", "50", "1.445e-08"] or ["p100", "5.486e-06"]
            if len(parts) == 3 and parts[0] == "p":
                try:
                    stats[parts[1]] = float(parts[2])
                except ValueError:
                    pass
            elif len(parts) == 2 and parts[0].startswith("p"):
                try:
                    stats[parts[0][1:]] = float(parts[1])
                except ValueError:
                    pass
        if s.startswith("VERDICT:"):
            verdict = s.split(":", 1)[1].strip()
    return stats, verdict


def parse_rel_p99(text: str) -> float:
    """Pull rel p99 from iv_diff.py stdout."""
    in_rel = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("IV rel diff"):
            in_rel = True
            continue
        if s.startswith("VERDICT"):
            in_rel = False
        if in_rel and s.startswith("p"):
            parts = s.split()
            if len(parts) == 3 and parts[0] == "p" and parts[1] == "99":
                try:
                    return float(parts[2])
                except ValueError:
                    pass
    return float("nan")


def parse_count(text: str, label: str) -> int:
    """Pull a count line like 'both valid:  5,449' from iv_diff.py stdout."""
    for line in text.splitlines():
        s = line.strip().lower()
        if s.startswith(label.lower()):
            tail = s.split(":", 1)[-1].strip().replace(",", "")
            try:
                return int(tail.split()[0])
            except (ValueError, IndexError):
                pass
    return -1


def has_data(date: str, root: str) -> bool:
    """Return True iff the day has the OPRA bundle for this root on disk."""
    day_dir = BACKEND / "data" / "databento" / date / "OPRA_PILLAR"
    if root.upper() == "SPX":
        files = ["definition__SPX-OPT_SPXW-OPT.dbn.zst", "tcbbo__SPX-OPT_SPXW-OPT.dbn.zst"]
    else:
        files = ["definition__NDX-OPT_NDXP-OPT.dbn.zst", "tcbbo__NDX-OPT_NDXP-OPT.dbn.zst"]
    return all((day_dir / f).exists() for f in files)


def run_one(date: str, root: str, snapshot: str) -> Result:
    snap_tag = snapshot.replace(":", "")
    out_dir = OUT_ROOT / date
    ref_csv = out_dir / f"iv_ref_{root}_{snap_tag}.csv"
    fg_csv = out_dir / f"iv_fg_{root}_{snap_tag}.csv"
    ref_meta = ref_csv.with_suffix(".json")

    print(f"[{date} {root} {snapshot}] running...", flush=True)
    t0 = time.time()
    try:
        # 1) scipy reference
        run_step(
            "iv_parity",
            [PYTHON, "iv_parity.py", date, "--root", root, "--snapshot", snapshot],
            cwd=HERE,
        )
        with open(ref_meta, encoding="utf-8") as f:
            meta = json.load(f)

        # 2) Go dumper
        run_step(
            "dump_fg_greeks",
            [DUMPER, "-in", ref_csv, "-out", fg_csv,
             "-spot", f"{meta['spot']:.6f}",
             "-r", f"{meta['rfr']:.6f}",
             "-q", f"{meta['div']:.6f}"],
            cwd=BACKEND,
        )

        # 3) diff
        diff_out = run_step(
            "iv_diff",
            [PYTHON, "iv_diff.py", date, "--root", root, "--snapshot", snapshot],
            cwd=HERE,
        )

        stats, verdict = parse_diff_output(diff_out)
        rel_p99 = parse_rel_p99(diff_out)
        n_both = parse_count(diff_out, "both valid")
        n_neither = parse_count(diff_out, "neither")
        joined = parse_count(diff_out, "joined rows")

        return Result(
            date=date, root=root, snapshot=snapshot,
            spot=meta["spot"],
            n_strikes=joined,
            n_both_valid=n_both,
            n_neither=n_neither,
            iv_diff_p50=stats.get("50", float("nan")),
            iv_diff_p99=stats.get("99", float("nan")),
            iv_diff_max=stats.get("100", float("nan")),
            rel_diff_p99=rel_p99,
            verdict=verdict,
            elapsed_s=time.time() - t0,
        )
    except RuntimeError as exc:
        return Result(
            date=date, root=root, snapshot=snapshot, spot=0.0,
            n_strikes=0, n_both_valid=0, n_neither=0,
            iv_diff_p50=float("nan"), iv_diff_p99=float("nan"),
            iv_diff_max=float("nan"), rel_diff_p99=float("nan"),
            verdict="ERROR", elapsed_s=time.time() - t0,
            error=str(exc).splitlines()[0][:200],
        )


def write_summary(results: list[Result], path: Path) -> None:
    df = pd.DataFrame([asdict(r) for r in results])
    df.to_csv(path, index=False)


def write_markdown(results: list[Result], path: Path) -> None:
    df = pd.DataFrame([asdict(r) for r in results])
    df_ok = df[df["verdict"].str.startswith("PASS")]
    lines = []
    lines.append("# IV parity validation — batch summary")
    lines.append("")
    lines.append(f"- runs: **{len(df)}**")
    lines.append(f"- PASS: **{(df['verdict'].str.startswith('PASS')).sum()}**")
    lines.append(f"- ACCEPTABLE: **{(df['verdict'].str.startswith('ACCEPTABLE')).sum()}**")
    lines.append(f"- INVESTIGATE: **{(df['verdict'].str.startswith('INVESTIGATE')).sum()}**")
    lines.append(f"- ERROR: **{(df['verdict'] == 'ERROR').sum()}**")
    lines.append("")
    if not df_ok.empty:
        lines.append("## Aggregate stats (PASS rows only)")
        lines.append("")
        agg = df_ok[["iv_diff_p50", "iv_diff_p99", "iv_diff_max", "rel_diff_p99"]].agg(
            ["min", "median", "max"]
        )
        lines.append("| | iv_diff_p50 | iv_diff_p99 | iv_diff_max | rel_diff_p99 |")
        lines.append("|---|---|---|---|---|")
        for stat in ["min", "median", "max"]:
            row = agg.loc[stat]
            lines.append(
                f"| {stat} | {row['iv_diff_p50']:.3e} | {row['iv_diff_p99']:.3e} "
                f"| {row['iv_diff_max']:.3e} | {row['rel_diff_p99']:.3e} |"
            )
        lines.append("")
        total_strikes = int(df_ok["n_both_valid"].sum())
        lines.append(f"- total strikes verified across all snapshots: **{total_strikes:,}**")
        lines.append(f"- worst single-run p99: **{df_ok['iv_diff_p99'].max():.3e}** vol points")
        lines.append("")

    lines.append("## Per-run detail")
    lines.append("")
    lines.append("| date | root | snapshot | spot | strikes | both ok | p50 | p99 | max | verdict |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        lines.append(
            f"| {r.date} | {r.root} | {r.snapshot} | "
            f"{r.spot:.2f} | {r.n_strikes} | {r.n_both_valid} | "
            f"{r.iv_diff_p50:.2e} | {r.iv_diff_p99:.2e} | {r.iv_diff_max:.2e} | "
            f"{r.verdict[:40]} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dates", nargs="+", required=True, help="trading days YYYY-MM-DD ...")
    ap.add_argument("--roots", nargs="+", default=["SPX", "NDX"], choices=["SPX", "NDX"])
    ap.add_argument("--snapshots", nargs="+",
                    default=["14:30:00Z", "16:00:00Z", "18:00:00Z", "19:30:00Z", "20:00:00Z"])
    args = ap.parse_args()

    if not DUMPER.exists():
        print(f"FATAL: Go dumper not built. Run:", file=sys.stderr)
        print(f"  cd {BACKEND} && go build -o bin/dump_fg_greeks.exe ./cmd/dump_fg_greeks",
              file=sys.stderr)
        return 2

    plan = []
    for date in args.dates:
        for root in args.roots:
            if not has_data(date, root):
                print(f"  skip {date} {root}: data not on disk yet")
                continue
            for snap in args.snapshots:
                plan.append((date, root, snap))

    print(f"plan: {len(plan)} runs across {len(args.dates)} days × {len(args.roots)} roots × {len(args.snapshots)} snapshots")
    print()

    results: list[Result] = []
    for i, (date, root, snap) in enumerate(plan, 1):
        print(f"--- [{i}/{len(plan)}] ---")
        r = run_one(date, root, snap)
        if r.verdict == "ERROR":
            print(f"  ERROR ({r.elapsed_s:.1f}s): {r.error}")
        else:
            print(f"  {r.verdict[:60]}  p99={r.iv_diff_p99:.2e}  ({r.elapsed_s:.1f}s)")
        results.append(r)

    summary_csv = OUT_ROOT / "_batch_summary.csv"
    summary_md = OUT_ROOT / "_batch_summary.md"
    write_summary(results, summary_csv)
    write_markdown(results, summary_md)
    print()
    print(f"summary -> {summary_csv.relative_to(REPO)}")
    print(f"summary -> {summary_md.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
