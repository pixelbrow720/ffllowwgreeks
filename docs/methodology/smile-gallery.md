# Smile + Signed-Gamma Visual Gallery

Qualitative QA across the 9-day OPRA archive on disk
(2026-02-02 through 2026-02-12, RTH only). Two roots, six intraday
snapshots overlaid per day. Eyeballing this gallery is the cheapest
way to catch a broken IV solver, a bad mid filter, or a spot-estimate
regression before deeper statistical work.

This is **not** a parity proof. It is a smell test.

![smile + signed-gamma gallery index](../../backend/scripts/validation/outputs/_smile_gallery/_index.png)

## What each panel shows

Each day produces two PNGs: `<date>_SPX.png` and `<date>_NDX.png`,
each 1600x600 at 110 dpi.

- **Left panel — IV smile.** Strike on x-axis, scipy-brentq IV on
  y-axis. Up to six snapshots (14:45, 16:00, 17:30, 18:30, 19:30,
  20:00 UTC) are overlaid with a viridis color gradient (early =
  purple, late = yellow). Spot is the estimated ATM at the first
  snapshot, drawn as a dashed grey line. Capped at 200 strikes
  closest to ATM so deep-OTM noise does not dominate the plot.
- **Right panel — signed-gamma intensity profile.** Strike on
  x-axis, *cumulative* signed gamma across all snapshots on y-axis.
  Green bars are positive (calls below ATM, puts above ATM), red
  bars are negative (calls above ATM, puts below ATM). This is a
  visual proxy for where dealer hedging concentrates. **It is not
  GEX.** No OI weighting, no trade-imbalance signing, no actual
  positioning data. Pure heuristic from analytical Black-Scholes
  gamma.

## What to look for (good)

- **U-shape or smirk** on the left panel. SPX/NDX 0DTE smiles
  typically show a strong put-side skew that flattens through the
  day. Both directions visible = healthy.
- **Term-structure flattening** as snapshots progress: yellow
  (late) curves should sit lower / flatter than purple (early)
  curves. That is theta + realized-vol crush.
- **Concentrated bars near round strikes** on the right panel.
  Dealer "walls" cluster on increments of 50 (SPX) or 100 (NDX).
- **Sign continuity around ATM**: green/red transition occurs at
  spot. Discontinuities far from spot suggest a stale spot estimate.

## What to look for (bad)

- **Random scatter** with no smile shape on the left panel. Likely
  a solver bug or corrupt mid filter.
- **IV cluster at a single value** (e.g. 0.05 or 5.0) — solver hit
  the bracket walls. Currently filtered to (0, 5.0) before plotting.
- **Empty signed-gamma profile** — gamma went to zero everywhere.
  Spot estimate is probably wrong (ATM is outside the strike range
  we plotted).
- **Inverted skew** that flips snapshot-over-snapshot — likely a
  spot regression between snapshots.

## Output paths

```
backend/scripts/validation/outputs/_smile_gallery/
├── _index.png                 # 9x2 thumbnail grid
├── 2026-02-02_SPX.png
├── 2026-02-02_NDX.png
├── 2026-02-03_SPX.png
├── 2026-02-03_NDX.png
├── 2026-02-04_SPX.png
├── 2026-02-04_NDX.png
├── 2026-02-05_SPX.png
├── 2026-02-05_NDX.png
├── 2026-02-06_SPX.png
├── 2026-02-06_NDX.png
├── 2026-02-09_SPX.png
├── 2026-02-09_NDX.png
├── 2026-02-10_SPX.png
├── 2026-02-10_NDX.png
├── 2026-02-11_SPX.png
├── 2026-02-11_NDX.png
├── 2026-02-12_SPX.png
└── 2026-02-12_NDX.png
```

Regenerate everything:

```bash
cd backend/scripts/validation
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe smile_gallery.py
```

The script reuses any pre-existing `iv_ref_<ROOT>_<SNAP>.csv` /
`.json` sidecars under `outputs/<date>/` and only invokes
`iv_parity.py` for missing combinations.

## Honest caveat

This gallery answers one narrow question: **does the IV solver +
gamma compute produce sensibly-shaped curves day after day?** It
does **not** validate:

- Greeks against `py_vollib` (planned, separate effort).
- Dealer positioning against ground-truth OI / settlement data
  (blocked on Databento OPRA unlock).
- DPI / Charm / Pin signal calibration against realized 0DTE flow.

If a panel passes the eyeball test, it means we have not regressed
on math basics. Nothing more.
