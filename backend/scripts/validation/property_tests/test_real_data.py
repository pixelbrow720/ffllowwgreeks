"""Property 10 — Smile shape on real OPRA data.

For each iv_ref_*.csv snapshot from 2026-02-02 we verify the universal
"smile" property of equity-index options:

    median IV (deep OTM puts) > median IV (ATM)  AND
    median IV (deep OTM calls) > median IV (ATM)

Both wings carry higher IV than ATM. The skew direction (put-wing vs
call-wing dominance) is recorded but NOT asserted — equity-index 0DTE
flips between put-skew and call-skew based on flow regime, so a strict
"OTM put > ATM > OTM call" ordering is empirical, not universal.

Soft empirical property: require global pass rate ≥ 80%. Pointwise
ordering is checked on bucket medians, not raw quotes.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

VALIDATION_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = VALIDATION_ROOT / "outputs" / "2026-02-02"

# Moneyness buckets (K/S):
DEEP_OTM_PUT = (0.85, 0.95)
ATM = (0.98, 1.02)
DEEP_OTM_CALL = (1.05, 1.15)
MIN_BUCKET_SIZE = 5  # need enough strikes per bucket for a stable median


def _snapshots() -> list[tuple[Path, Path]]:
    if not DATA_DIR.exists():
        return []
    pairs = []
    for csv in sorted(DATA_DIR.glob("iv_ref_*.csv")):
        meta = csv.with_suffix(".json")
        if meta.exists():
            pairs.append((csv, meta))
    return pairs


def _classify(df: pd.DataFrame, spot: float) -> dict[str, np.ndarray]:
    df = df.dropna(subset=["iv_ref", "strike_price", "instrument_class"]).copy()
    df = df[df["iv_ref"] > 0.0]
    # Take the dominant expiration per snapshot — mixing tenors muddies
    # the smile (different vol-of-vol). Use mode.
    if "t_years" in df.columns and len(df):
        dominant_t = df["t_years"].round(6).mode()
        if len(dominant_t):
            df = df[np.isclose(df["t_years"], dominant_t.iloc[0], rtol=1e-3)]
    df["m"] = df["strike_price"] / spot
    puts = df[df["instrument_class"].str.upper() == "P"]
    calls = df[df["instrument_class"].str.upper() == "C"]
    return {
        "deep_otm_put": puts.loc[(puts["m"] >= DEEP_OTM_PUT[0]) &
                                  (puts["m"] <= DEEP_OTM_PUT[1]),
                                  "iv_ref"].to_numpy(),
        "atm": pd.concat([
            calls.loc[(calls["m"] >= ATM[0]) & (calls["m"] <= ATM[1]),
                      "iv_ref"],
            puts.loc[(puts["m"] >= ATM[0]) & (puts["m"] <= ATM[1]),
                     "iv_ref"],
        ]).to_numpy(),
        "deep_otm_call": calls.loc[(calls["m"] >= DEEP_OTM_CALL[0]) &
                                    (calls["m"] <= DEEP_OTM_CALL[1]),
                                    "iv_ref"].to_numpy(),
    }


SNAPSHOTS = _snapshots()


@pytest.mark.skipif(not SNAPSHOTS, reason="no iv_ref_*.csv outputs found")
def test_smile_shape_global_pass_rate():
    """Aggregate property: ≥80% of snapshots show a smile (both wings > ATM).

    Per-snapshot result, including skew direction, is persisted for the
    methodology doc.
    """
    results: list[dict] = []
    for csv, meta in SNAPSHOTS:
        spot = float(json.loads(meta.read_text())["spot"])
        buckets = _classify(pd.read_csv(csv), spot)
        sizes = {k: len(v) for k, v in buckets.items()}
        if any(sizes[k] < MIN_BUCKET_SIZE for k in sizes):
            results.append({"snapshot": csv.stem, "status": "skipped",
                            "reason": f"thin buckets {sizes}"})
            continue
        med = {k: float(np.median(v)) for k, v in buckets.items()}
        smile = (med["deep_otm_put"] > med["atm"]
                 and med["deep_otm_call"] > med["atm"])
        skew = ("put" if med["deep_otm_put"] > med["deep_otm_call"]
                else "call")
        results.append({
            "snapshot": csv.stem,
            "status": "pass" if smile else "fail",
            "skew": skew,
            "iv_otm_put": med["deep_otm_put"],
            "iv_atm": med["atm"],
            "iv_otm_call": med["deep_otm_call"],
            "sizes": sizes,
        })

    scored = [r for r in results if r["status"] in ("pass", "fail")]
    pass_rate = (sum(r["status"] == "pass" for r in scored) / len(scored)
                 if scored else 0.0)

    # Persist for the methodology doc.
    out = VALIDATION_ROOT / "property_tests" / "_smile_results.json"
    out.write_text(json.dumps({
        "n_snapshots": len(SNAPSHOTS),
        "n_scored": len(scored),
        "n_pass": sum(r["status"] == "pass" for r in scored),
        "n_fail": sum(r["status"] == "fail" for r in scored),
        "n_skipped": sum(r["status"] == "skipped" for r in results),
        "pass_rate": pass_rate,
        "n_put_skew": sum(r.get("skew") == "put" for r in scored),
        "n_call_skew": sum(r.get("skew") == "call" for r in scored),
        "per_snapshot": results,
    }, indent=2))

    assert scored, "no scoreable snapshots — bucket thresholds too tight?"
    assert pass_rate >= 0.80, (
        f"smile pass rate {pass_rate:.0%} < 80% across {len(scored)} "
        f"snapshots — see {out}"
    )
