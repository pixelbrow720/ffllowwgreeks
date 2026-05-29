# Incident — archive backpressure / drop counters rising

**Symptom matchers:** `ArchiveTicksDropped` or `StateRowsDropped` alerts
firing; `flowgreeks_archive_ticks_dropped_total` or
`flowgreeks_state_rows_dropped_total` increasing > 0;
`StateFlushErrors` may also fire if Postgres is the proximate cause.

Hot path drops by design when downstream is slow — losing one tick is
preferable to wedging the publisher. The incident is about *why*
downstream is slow and how to clear it without losing more data.

## Triage in 60 seconds

```sh
# 1. Drop counters and write counters
curl -s http://localhost:8080/metrics | grep -E 'archive_(ticks|state)_(written|dropped)'

# 2. Postgres write throughput right now
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT
     pg_stat_get_xact_tuples_inserted(d.oid) AS inserts,
     pg_stat_get_xact_tuples_updated(d.oid) AS updates
   FROM pg_database d WHERE datname='flowgreeks';"

# 3. Flush latency histogram
curl -s http://localhost:8080/metrics | grep flowgreeks_state_flush_duration_seconds_

# 4. WS publish vs drop ratio (separate symptom but often correlated
#    if the api binary is the bottleneck rather than postgres)
curl -s http://localhost:8080/metrics | grep -E 'flowgreeks_ws_(publishes|drops)_total'
```

## Decision tree

### Postgres slow on COPY FROM
Most common. `dealer_state_1s` is a hypertable; COPY FROM batches at
~1Hz. If Postgres is heavily loaded (compaction, long select on the
same table, autovacuum), COPY blocks and the in-memory channel fills.

Check active Postgres workload:

```sh
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT pid, age(now(), query_start) AS qage, state, query
     FROM pg_stat_activity
    WHERE datname='flowgreeks' AND state <> 'idle'
    ORDER BY qage DESC NULLS LAST
    LIMIT 10;"
```

If a long-running query is identifiable, decide whether to terminate
(see [postgres-pool-storm.md](postgres-pool-storm.md)).

### Disk IOPS saturated
```sh
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT * FROM pg_stat_io WHERE backend_type='client backend';"

# Host-side, if access:
iostat -x 1 5 | grep -E '/dev/'
```

If the postgres data device is at 100% util, you've outgrown the box.
Short-term: lower the archive batch size or temporarily raise the
in-channel cap (`internal/store/archive.go` `inChanCap`). Long-term:
faster disk or split storage by hypertable.

### Compaction blocking writes
TimescaleDB compression runs in background, but on a small box it
contends with the COPY path:

```sh
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT * FROM timescaledb_information.jobs WHERE job_id IN
   (SELECT job_id FROM timescaledb_information.job_stats
     WHERE last_run_status='Success' AND last_finish > now() - interval '10 min');"
```

If compression is running mid-burst, defer it to off-hours via the job
schedule.

### Compute publishing faster than capacity
If trade rate spiked (event session, vol crush), the channel can fill
even with healthy Postgres. The drop counter incrementing for 30-60s
during a market event is expected. Persistent drop > 60s is the
incident.

## Recovery verification

```sh
# Drop counter rate over the next 60s should be 0
curl -s http://localhost:8080/metrics | grep archive_ticks_dropped_total

# Flush latency p99 back below 1s
curl -s http://localhost:8080/metrics | grep state_flush_duration

# ArchiveTicksDropped alert clears
```

## Postmortem checklist

- [ ] Was the bottleneck Postgres, disk, or upstream tick rate?
- [ ] Should batch size / channel cap change?
- [ ] Was the alert threshold appropriate (didn't fire too late, didn't
      fire on healthy bursts)?
- [ ] Update `docs/STACK.md` capacity numbers if this shifted them.
