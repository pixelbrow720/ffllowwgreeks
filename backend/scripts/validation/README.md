# FlowGreeks math validation harness

Offline cross-check of FlowGreeks's Go math against reference implementations and real OPRA data.

**This is not part of the production binary.** Output goes to `outputs/` and `docs/methodology/`.

## What's here

| File | What it does |
|---|---|
| `requirements.txt` | Python deps (pinned) |
| `setup.ps1` | One-shot venv create + install (Windows) |
| `probe_data.py` | Sanity check: load one day's DBN files, print record counts and a few samples |
| `iv_parity.py` | Load OPRA tcbbo + definition for an RTH snapshot, solve IV per strike via `py_vollib_vectorized`, save CSV + scatter plot |

## Setup

```powershell
cd backend\scripts\validation
.\setup.ps1
```

This creates `.venv\` (gitignored), installs deps from `requirements.txt`, and prints the activation command.

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python probe_data.py 2026-02-02
python iv_parity.py 2026-02-02 --snapshot 16:00:00Z
```

Outputs land in `outputs/<date>/`.

## Reference implementations used

- `py_vollib_vectorized` — NumPy-vectorized Black-Scholes + IV solver. Pure Python, no C extensions, works on Python 3.14.
- `databento` — official SDK to read `.dbn.zst` files.

## What this validates (in order, by future test scripts)

1. **Greeks parity** — FlowGreeks's [`internal/greeks/greeks.go`](../../internal/greeks/) Δ, Γ, Θ, ν, charm vs `py_vollib_vectorized` analytic Greeks. Target tolerance: 1e-6 absolute on Greeks, 1e-4 relative on price.
2. **IV solver** — bracket auto-widen behavior on deep-OTM 0DTE strikes that previously returned `"no bracket"`. Verify against `py_vollib_vectorized.implied_volatility` ground truth.
3. **Lee-Ready classifier** — aggressor side from FlowGreeks vs trade-vs-tcbbo NBBO ground truth (since tcbbo gives NBBO at trade time, we have ground truth labels).
4. **GEX aggregator** — recompute Net GEX, walls, regime from raw quotes; cross-check against SpotGamma published readings on selected days.

## Day 1 trial: 2026-02-02

This was the first day's data pulled (Plan C). 10 files in `backend/data/databento/2026-02-02/` totaling ~340 MB compressed:

- OPRA: definition + statistics + tcbbo for SPX+SPXW and NDX+NDXP
- GLBX: mbp-1 + trades for ES.FUT and NQ.FUT
