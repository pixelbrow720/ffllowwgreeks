# Data — 9 Trading Days Historical Archive

## What you have

**4.1 GB Databento DBN archive across 9 US trading days (Feb 2-12, 2026).**

This is your most valuable asset. It's the only thing letting you build / iterate without a live OPRA subscription (vendor account currently locked).

## Where it lives

```
C:\FLOWGREEKS\backend\data\databento\
```

**Gitignored** (4.1 GB doesn't belong in git). Lives on your local disk only. If you switch machines, you must re-download or copy this folder.

## Layout per day

```
backend/data/databento/<YYYY-MM-DD>/
  *.dbn.zst    # zstd-compressed Databento files (97 files total, ~4.1 GB)
  *.csv        # 1 csv file
  *.log        # 1 log file
  *.out        # 3 stdout captures
```

Plus a `_wide/` folder (empty, 0 MB).

## Per-day tick count (after Postgres ingest)

| Date | Ticks ingested | Archive size |
|---|---|---|
| 2026-02-02 | 18,067,617 | 340.8 MB |
| 2026-02-03 | 30,081,044 | 568.3 MB |
| 2026-02-04 | 32,409,647 | 623.3 MB |
| 2026-02-05 | 30,746,767 | 596.5 MB |
| 2026-02-06 | 20,439,490 | 398.2 MB |
| 2026-02-09 | 15,057,487 | 290.2 MB |
| 2026-02-10 | 11,776,137 | 226.3 MB |
| 2026-02-11 | 23,205,552 | 443.7 MB |
| 2026-02-12 | 29,612,113 | 561.2 MB |
| 2026-02-13 | (partial) | 64.9 MB |
| **Total** | **211,395,854** | **~4.1 GB** |

Note: 2026-02-07/08 missing (weekend). 2026-02-13 partial (vendor blocker).

## What's inside the DBN files

Each `.dbn.zst` carries one of these schemas from Databento:

- **`mbp-1`** — top-of-book quote updates (bid/ask). Highest volume.
- **`trades`** — executed trades with size + side.
- **`ohlcv-1s`** — 1-second OHLCV bars (rare, used as fallback).
- **`definition`** — instrument metadata + open interest snapshots. **Critical**: OI snapshots arrive once per session at ~11:30 UTC and are required to seed the position tracker before any quote/trade computation makes sense.

## Symbology

### OPRA Pillar (options)

```
SPX  260213C06900000   →  SPX, expiry 2026-02-13, Call, strike 6900.000
SPX  260213P06850000   →  SPX, expiry 2026-02-13, Put,  strike 6850.000
NDX  260213C20000000   →  NDX, expiry 2026-02-13, Call, strike 20000.000
```

Filter: 0DTE expiries only.

### CME GLBX MDP3 (futures)

```
ESH6   →  Mar 2026 ES quarterly (S&P 500 E-mini front-month)
NQH6   →  Mar 2026 NQ quarterly (Nasdaq 100 E-mini front-month)
```

Used for the **basis** that pulls SPX cash spot from ES front-mid (since cash SPX has no direct tick-level feed in the same way ES does).

Front-month rollover: third Friday of Mar/Jun/Sep/Dec at 09:30 ET.

## How to ingest into Postgres

The codebase provides `backend/scripts/dbn_to_postgres.py` (Python bridge — needed because `dbn-go` v0.9.1 cannot decode Databento's v1 InstrumentDef record).

```bash
# Has its own venv:
cd backend/scripts/validation
.\.venv\Scripts\Activate.ps1
python ../../dbn_to_postgres.py --date 2026-02-12 --dataset OPRA.PILLAR
```

After ingest you get:
- 211M rows in TimescaleDB `ticks` hypertable
- 27 chunks
- ~2.4 GB compressed (saved ~10x vs raw)

If building a NEW project from scratch, you have 2 options:

1. **Reuse this Python bridge** as-is. It works. Slow but reliable.
2. **Wait for `dbn-go` v1 InstrumentDef support** and write a Go ingest path. Faster, single-language stack.

## Postgres schema for ingested ticks

```sql
CREATE TABLE ticks (
    ts                TIMESTAMPTZ NOT NULL,
    ts_recv           TIMESTAMPTZ,
    symbol            SMALLINT NOT NULL,    -- 0=SPX, 1=NDX, ...
    asset_class       SMALLINT NOT NULL,    -- 0=Option, 1=Future
    side              SMALLINT,             -- 0=Call, 1=Put, NULL for futures
    strike            INTEGER,              -- 1e-3 USD per unit (6900.0 → 6_900_000)
    expiry            DATE,                 -- NULL for futures
    tick_type         SMALLINT NOT NULL,    -- 0=Quote, 1=Trade, 2=OI, ...
    bid               DOUBLE PRECISION,
    ask               DOUBLE PRECISION,
    last              DOUBLE PRECISION,
    size              BIGINT,
    open_interest     BIGINT,
    futures_contract  TEXT                  -- e.g. "ESH6"
);

SELECT create_hypertable('ticks', 'ts', chunk_time_interval => INTERVAL '1 day');
```

Plus indexes on `(symbol, ts)`, `(symbol, expiry, strike, ts)`.

## How to use this archive

### As a replay source for backtest / dev
The current `cmd/replay` binary reads from the Postgres `ticks` table and republishes to NATS as if it were live ingest. The compute pipeline downstream is unchanged. Pass `-Speed 1` for real-time, `-Speed 60` for 60×, `-Speed 0` for unpaced.

### As ground truth for calibration
`cmd/calibrate` walks `dealer_state_1s` (output of replay→compute) and emits R-7 percentile-based normalizers per symbol. This is how you fit DPI/Charm/Pin priors empirically against realized 0DTE flow.

### As input to a new pipeline (if you start fresh)
The DBN files are vendor-standard. Any ingest engine that reads Databento DBN format works:
- Python: `databento` SDK (https://databento.com/docs/python)
- Go: `dbn-go` (https://github.com/databento/dbn-go) — has the v1 InstrumentDef gap
- Rust: `databento-dbn` crate

Schema reference: https://databento.com/docs/schemas-and-data-formats

## Vendor reference

- **Databento** — https://databento.com (your account is currently locked; manual support recovery pending)
- **OPRA Pillar dataset** — https://databento.com/datasets/OPRA.PILLAR
- **CME GLBX MDP3 dataset** — https://databento.com/datasets/GLBX.MDP3
- **DBN format spec** — https://databento.com/docs/standards-and-conventions/databento-binary-encoding
- **Pricing** — OPRA full feed ~$2-3k/month live, archive ~$0.10/GB

## Risks

- **Account locked** — until Databento support unlocks, no new data, no live verification.
- **Schema drift** — Databento has versioned `InstrumentDef` before. Pin your decoder version.
- **OPRA cost** — full live feed is expensive. Cap subscription to SPX + NDX only.

## Action items if starting fresh

1. Don't move/delete `backend/data/databento/`. It is irreplaceable until OPRA unlocks.
2. Optional: copy the folder to a safe backup location (`D:\FLOWGREEKS-archive\`).
3. If new project needs the data, point it at the same path or symlink.
4. Email Databento support to unlock the OPRA account.
