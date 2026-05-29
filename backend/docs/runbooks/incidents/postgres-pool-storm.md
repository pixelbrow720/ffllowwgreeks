# Incident — Postgres connection storm / pool exhaustion

**Symptom matchers:** api requests time out at the pgxpool acquire step,
`/health/ready` flips to 503 with `postgres: unreachable`, alert rule
`HTTPHighErrorRate` fires while NATS / compute look healthy. Test with:

```sh
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL;"
```

If that count is at or near `max_connections` (default 100), this is
your incident.

## Triage in 60 seconds

```sh
# 1. How many connections, by source?
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT application_name, state, count(*)
     FROM pg_stat_activity
    WHERE datname='flowgreeks'
    GROUP BY 1,2
    ORDER BY 3 DESC;"

# 2. Long-running queries holding connections?
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT pid, age(now(), xact_start) AS xact_age, state, query
     FROM pg_stat_activity
    WHERE datname='flowgreeks' AND state <> 'idle'
    ORDER BY xact_age DESC
    LIMIT 20;"

# 3. Locks?
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT pid, mode, locktype, relation::regclass, granted
     FROM pg_locks
    WHERE NOT granted;"
```

## Decision tree

### Long transaction holding row locks
Common cause: a long-running `POST /api/backtest/run` or replay session
holding open a transaction. Cancel it surgically:

```sh
# Replace PID with the offender from the query above.
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT pg_terminate_backend(PID);"
```

Then look at the binary that owned the connection. If it's `api`, the
backtest 30s deadline should already cap this — file a bug.

### Pool exhausted by `replay` or `backtest`
`cmd/api/main.go` opens a single shared pgxpool. Default `MaxConns =
4×CPU`. If multiple long-running backtest queries pile up, the pool
saturates and every other request waits.

Knob: bump `POSTGRES_MAX_CONNS` in the api env. Don't blow past
`max_connections` on the server (default 100 — leave headroom for
psql, migrations, monitoring):

```sh
# In .env
POSTGRES_MAX_CONNS=24
POSTGRES_MIN_CONNS=4
POSTGRES_MAX_CONN_LIFETIME=1h
POSTGRES_MAX_CONN_IDLE_TIME=15m
```

Rolling restart api after change.

### Postgres itself unhealthy
- Disk full: `df -h` on host. TimescaleDB without compression eats
  ~1 GB/day for SPX+NDX 0DTE. Compression policy lives in migration
  `000004`; verify it's running:
  ```sh
  docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
    "SELECT * FROM timescaledb_information.compression_settings;"
  ```
- Out of memory: `docker stats fg-postgres`. If at the cgroup limit,
  bump compose resource limits.

### Connection storm from outside
Unlikely (api binary owns the pool), but if you see external IPs in
`pg_stat_activity`, something's exposed Postgres on the public NIC.
That's the incident — close it at firewall and rotate password.

## Recovery verification

```sh
# Pool-acquire latency back below 5ms
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT count(*) FROM pg_stat_activity WHERE state IS NOT NULL;"

# /health/ready clears
curl -s http://localhost:8080/health/ready | jq .deps.postgres

# Backtest endpoint still works
curl -sf -X POST http://localhost:8080/api/backtest/run \
  -H 'Authorization: Bearer DEMO_KEY' -d '{"symbol":"SPX","since":"...","until":"..."}'
```

## Postmortem checklist

- [ ] Did `POSTGRES_MAX_CONNS` need raising or was a single bad query the cause?
- [ ] Was there a runaway client (replay session forgotten by frontend)?
- [ ] Should `/api/backtest/run` 30s deadline tighten?
- [ ] Are slow queries logged? (`log_min_duration_statement = 5s`)
