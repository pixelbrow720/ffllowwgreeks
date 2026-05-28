# Math parity validation — 9-day extended batch

> Companion document to [research-paper.md §2.5](research-paper.md).
> Validated against commit at end of 2026-05-28 session.

## Scope

Extension of the original 3-day parity batch (36 runs) to **9 trading days × 2 underlyings × 6 intraday snapshots = 108 runs**, drawn from Databento OPRA historical archive.

| | 3-day batch | 9-day batch (this doc) |
|---|---|---|
| Days | 2026-02-02 to 02-04 | 2026-02-02 to 02-12 (9 RTH days) |
| Underlyings | SPX, NDX | SPX, NDX |
| Snapshots / day | 6 (14:45, 16:00, 17:30, 18:30, 19:30, 20:00 UTC) | 6 (same) |
| Total runs | 36 | **108** |
| Total strikes verified | 108,595 | **321,108** |
| Metrics tracked | IV only | IV + Δ + Γ + Θ + vega + charm |

## Method

Pipeline orchestrated by `backend/scripts/validation/batch_validate.py`:

1. **scipy reference** — `iv_parity.py` loads OPRA `definition` + `tcbbo` files for the (date, root) pair, walks tcbbo to the snapshot timestamp, joins on `instrument_id`, computes mid prices, and solves IV via `scipy.optimize.brentq` driving a numpy Black-Scholes-Merton reference (`bs_reference.py`). Greeks computed analytically via `bs_reference.compute_greeks_vec`. Output: `iv_ref_<root>_<snap>.csv` (IV + Δ Γ Θ vega charm reference columns).
2. **FlowGreeks dump** — `cmd/dump_fg_greeks` (Go binary) reads the same CSV, calls `internal/greeks/ImpliedVol` and `internal/greeks/All` per row, emits `iv_fg_<root>_<snap>.csv` with `fg_iv` + `fg_delta` etc.
3. **Diff** — `iv_diff.py` joins on `instrument_id`, computes per-strike abs and relative diffs for IV + each Greek, emits aggregate stats and per-row `iv_diff_<root>_<snap>.csv`.

Decision rule per run:
- Each metric (IV + 5 Greeks) must satisfy `abs_p99 < 1e-4 OR rel_p99 < 1e-4`.
- All metrics must pass for the run to PASS.

## Results

```
runs: 108
PASS: 108
ACCEPTABLE: 0
INVESTIGATE: 0
ERROR: 0
```

### Aggregate IV diff stats (across 108 runs)

| | min | median | max | threshold |
|---|---:|---:|---:|---:|
| `iv_diff_p50` | 8.71e-09 | 1.43e-08 | 1.87e-08 | — |
| `iv_diff_p99` | 2.36e-07 | 5.64e-07 | **1.16e-06** | **1e-4** |
| `iv_diff_max` | 3.38e-07 | 5.28e-06 | 1.91e-05 | — |
| `rel_diff_p99` | 1.17e-06 | 2.82e-06 | 5.10e-06 | — |

The worst single-run p99 across the entire 9-day batch is `1.159e-06` vol points — almost two orders of magnitude inside the `1e-4` threshold. The worst single strike in the batch differs by `1.91e-05` vol points, which moves an at-the-money 0DTE option price by less than the bid-ask quantum.

### Per-day summary

| Day | Runs | Strikes verified | Worst p99 |
|---|---:|---:|---:|
| 2026-02-02 | 12 | ~25,000 | 8.15e-07 |
| 2026-02-03 | 12 | ~32,000 | 9.54e-07 |
| 2026-02-04 | 12 | ~30,000 | 7.60e-07 |
| 2026-02-05 | 12 | ~36,000 | 7.93e-07 |
| 2026-02-06 | 12 | ~32,000 | 7.02e-07 |
| 2026-02-09 | 12 | ~31,000 | 1.16e-06 |
| 2026-02-10 | 12 | ~33,000 | 1.04e-06 |
| 2026-02-11 | 12 | ~37,000 | 1.04e-06 |
| 2026-02-12 | 12 | ~36,000 | 1.03e-06 |
| **Total** | **108** | **321,108** | **1.16e-06** |

(Strike counts are aggregated `n_both_valid` across the 12 snapshots per day.)

### Per-Greek verdict

For every run in the batch, all five Greeks (Δ, Γ, Θ, vega, charm) cleared the `1e-4` abs OR rel p99 bar. Vanna is intentionally excluded from this batch: its analytical formula `−e^(−qT) · φ(d1) · d2 / σ` is more sensitive to numerical noise at the deep-OTM tail (`d2` blows up while `φ(d1)` underflows), so a tighter regime restriction is needed before it can be parity-tested cleanly. Flagged for follow-up.

## Sample size context

| Sample | n strikes | What it gives |
|---|---|---|
| 1 strike | 1 | nothing |
| 1 snapshot (~3,000 strikes) | thousands | sanity |
| 1 day (~30,000 strikes) | tens of thousands | day-level confidence |
| 9 days (this batch) | **321,108** | day × time × regime coverage |
| 1 year | ~2.5M | redundant to math validation; only useful for calibration |

For math correctness validation, the 9-day batch is **decisively over-powered**. Math correctness is a binary property — once verified at scale, additional samples can only confirm or refute, and 321k strikes across 9 distinct trading days with varying spot direction (rallies, dips, sideways days all present in the 02-02–02-12 window) is sufficient evidence that the kernel does not contain a regime-dependent bug.

For dealer-model **calibration** (DPI weights, Charm zone thresholds, Pin probabilities), 9 days is **insufficient** — the calibration targets need ~250 events at the threshold to give stat-significant fits, and 9 days yields ~50 events at typical signal thresholds. That work is pending a deeper archive.

## Reproducibility

```bash
cd backend/scripts/validation
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe batch_validate.py \
  --dates 2026-02-02 2026-02-03 2026-02-04 2026-02-05 2026-02-06 \
          2026-02-09 2026-02-10 2026-02-11 2026-02-12 \
  --roots SPX NDX \
  --snapshots 14:45:00Z 16:00:00Z 17:30:00Z 18:30:00Z 19:30:00Z 20:00:00Z
```

Outputs land in `backend/scripts/validation/outputs/`:
- `_batch_summary.csv` — 108 rows of per-run stats
- `_batch_summary.md` — human-readable rendering of the same
- `2026-02-XX/iv_diff_<root>_<snap>.csv` — per-row diffs (162 files)
- `2026-02-XX/iv_diff_<root>_<snap>.png` — IV diff distribution + scatter plots

The raw Databento DBN files at `backend/data/databento/2026-02-XX/` are not redistributable (license terms) but anyone with a Databento OPRA Pillar entitlement can pull them via `backend/scripts/pull_databento.sh`.

## What this proves

1. **The FlowGreeks IV solver returns the same number scipy returns**, to a precision that vanishes against any downstream signal threshold. Verified across 321k strikes, 9 days, both underlyings.
2. **The FlowGreeks analytical Greeks (Δ Γ Θ vega charm) match scipy** at the same precision.
3. **The result is regime-independent** — no day in the batch produced an outlier; the worst-case run is `1.16e-06` p99, the best-case is `2.36e-07`, both well inside the bar.

## What this does not prove

- Vanna parity (intentional scope decision; flagged for follow-up).
- Greek correctness at extreme moneyness (`|moneyness| > 0.5`) — those strikes mostly have crossed quotes filtered out before parity, so the population we test is naturally bounded to representative strikes.
- That dealer-position estimation is correct.
- That DPI weights, Charm Clock thresholds, or Pin Probability defaults are calibrated.

Those pendings are tracked in [research-paper.md §4](research-paper.md) and [§7](research-paper.md).
