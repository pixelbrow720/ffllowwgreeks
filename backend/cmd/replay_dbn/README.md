# cmd/replay_dbn

Replays a day of pre-pulled Databento DBN files into the FlowGreeks
pipeline. Sibling to `cmd/ingest`: same NATS subjects, same converters,
no Databento Live connection.

## What it does

1. Walks `data/databento/<day>/{OPRA_PILLAR,GLBX_MDP3}/` for `*.dbn.zst`
   files.
2. Drains every `definition__*.dbn.zst` first to populate the shared
   instrument registry (no NATS publishes).
3. Opens the streaming files (`tcbbo`, `mbp-1`, `trades`, `statistics`)
   simultaneously and merges them by `ts_event` using a min-heap.
4. Each popped record is converted via the same helpers used by
   `cmd/ingest` and published onto the canonical `ticks.*` subjects.
   `cmd/compute` consumes them and writes `dealer_state_1s` rows.

## Prerequisites

- Docker stack up: `make up` (NATS + Postgres).
- Schema migrations applied (the docker stack's first-run init handles
  this via `deploy/postgres/init/`).
- `cmd/compute` running in another terminal so the produced ticks get
  consumed and `dealer_state_1s` rows are populated:

  ```bash
  go run ./cmd/compute
  ```

## One-day replay

```bash
make replay-dbn DIR=data/databento/2026-02-02
```

Equivalent direct invocation:

```bash
go run ./cmd/replay_dbn -dir data/databento/2026-02-02
```

Useful flags:

| Flag | Default | Notes |
|---|---|---|
| `-dir` | (required) | Path to a day directory under `data/databento/` |
| `-nats-url` | `nats://localhost:4222` | Override for non-default NATS deployments |
| `-speed` | `0` | `0` = unpaced (fastest); `1` = realtime; `10` = 10x |
| `-symbol-filter` | `both` | `spx`, `ndx`, or `both` |

Progress lines arrive every 100k records:

```
progress elapsed=12.341s records=400000 published=399812 publish_errors=0 rate_per_sec=32413 open_sources=6
```

## Multi-day driver

Replay every directory present under `data/databento/`:

```bash
for day in data/databento/*/; do
  echo "=== ${day} ==="
  make replay-dbn DIR="${day%/}"
done
```

For a specific date range, list directories explicitly:

```bash
for day in 2026-02-02 2026-02-03 2026-02-04; do
  make replay-dbn DIR="data/databento/${day}"
done
```

## Expected outcome

After a successful run:

- `cmd/compute` logs show normalize + greeks + dealer-state writes for
  the replayed window.
- `dealer_state_1s` rows land for every second the replay covered. Spot
  check:

  ```sql
  SELECT date_trunc('day', ts) AS day, count(*)
    FROM dealer_state_1s
   WHERE ts >= '2026-02-02' AND ts < '2026-02-03'
   GROUP BY 1;
  ```

- The backtest API can then be pointed at the replayed day.

## Notes

- Definition files always drain first; without them OPRA records would
  drop because the instrument registry is empty.
- Missing schema files are skipped silently (partial days are fine).
- NATS publish failures are logged + counted; the replay does not retry.
- Do not point this binary at the live Databento account — it is
  file-only by design.
