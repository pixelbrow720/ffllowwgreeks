# dbn_to_postgres.py

Python bridge that loads Databento DBN historical files into the FlowGreeks
`ticks` hypertable so `cmd/replay` can pace history through the compute pipeline.

## Why this exists

`cmd/replay` was originally meant to consume DBN files directly via `dbn-go`.
That path is blocked because dbn-go cannot decode DBN v1 `definition` records
shipped in our 2026-02 archive. This script is a parallel Python loader: it
parses the DBN with the official `databento` SDK, normalizes records to the
canonical `feed.Tick` shape (mirroring `internal/feed/databento/convert.go`),
and `COPY`s them into Postgres. `cmd/replay` then reads from `ticks`, paces
events, and `cmd/compute` consumes them as if they were live.

## How to run

```bash
cd backend/scripts/validation && ./.venv/Scripts/pip install -r requirements.txt
cd backend
./scripts/validation/.venv/Scripts/python.exe scripts/dbn_to_postgres.py [flags]
```

Flags:
- `--days 2026-02-02 [2026-02-03 ...]` (default: every `2026-02-*` dir under `data/databento/`)
- `--reset` — TRUNCATE `ticks` before loading
- `--dry-run` — parse + count only, no DB writes
- `--data-dir PATH` — override `data/databento`

Postgres credentials are read from `POSTGRES_{HOST,PORT,USER,PASSWORD,DB}`
env vars. Defaults match `backend/.env`.

## What it produces

For each day under `data/databento/<day>/`:

| File pattern | Schema | Maps to |
|---|---|---|
| `OPRA_PILLAR/tcbbo__{SPX,NDX}-OPT_*.dbn.zst` | `tcbbo` (CMBP1) | `tick_type=QUOTE` |
| `OPRA_PILLAR/trades__*.dbn.zst` | `trades` | `tick_type=TRADE` |
| `OPRA_PILLAR/statistics__*.dbn.zst` | `statistics` (OI only) | `tick_type=OI` |
| `GLBX_MDP3/mbp-1__{ES,NQ}-FUT.dbn.zst` | `mbp-1` | `tick_type=QUOTE` |
| `GLBX_MDP3/trades__{ES,NQ}-FUT.dbn.zst` | `trades` | `tick_type=TRADE` |
| `OPRA_PILLAR/definition__*.dbn.zst` | `definition` | pre-loaded into in-memory `instrument_id → meta` map; not written to ticks |

OPRA option `raw_symbol` is parsed as 21-char OSI: 6-char root + YYMMDD + C/P + 8-digit strike (already strike_usd × 1000, matches `ticks.strike`). Futures map ES.* → SPX, NQ.* → NDX with NULL expiry/strike/side. Statistics records other than `OPEN_INTEREST` (settlement, VWAP, etc.) are skipped.

Throughput: ~50–150K rows/s via `COPY FROM STDIN`. One day (~18M rows) loads in ~7 min on a workstation.

## Limitations

- **No dedup.** Re-running without `--reset` creates duplicates. Either use `--reset` or load each day exactly once.
- **No upserts.** `ticks` has no PK; `INSERT` semantics only via `COPY`.
- **DBN v1 definition records only.** Newer DBN v2/v3 files would need version probing.
- **OPRA `trades` schema absent** in current dump — embedded prints in `tcbbo` are not extracted (matches the Go `convertCmbp1` semantics, which emits a single QUOTE per CMBP1 record).

## Cleanup

Truncate the ticks table:

```bash
docker compose -f deploy/docker-compose.yml exec -T postgres \
    psql -U flowgreeks -d flowgreeks -c "TRUNCATE ticks;"
```

Or pass `--reset` on next run.
