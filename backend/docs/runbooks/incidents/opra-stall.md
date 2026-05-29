# Incident — OPRA / GLBX upstream stall

**Symptom matchers:** `IngestNoArchiveWrites` fires; api snapshot still
serves but `dealer_state_1s` rate flatlines; ingest container's logs
stop emitting `feed.databento.tick` events; subscribers connected but
publishes/sec drops to zero.

## Triage in 60 seconds

```sh
# Is the ingest container alive?
docker compose --profile app ps | grep fg-ingest
docker compose logs ingest --tail=200 | grep -E 'error|disconnect|reconnect'

# Last archive tick written?
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT to_timestamp(extract(epoch FROM now()) -
      (extract(epoch FROM now()) - max(ts_ns)/1e9)) AS last_tick_age,
          count(*) AS ticks_last_min
     FROM ticks
    WHERE ts_ns > extract(epoch from now() - interval '1 min') * 1e9;"

# Databento client error counter
curl -s http://localhost:8080/metrics | grep flowgreeks_ingest_

# Symbol disagreement?
docker compose logs ingest --tail=500 | grep -iE 'unknown symbol|drop'
```

## Decision tree

### Databento auth / quota
Most common. Check the `DATABENTO_API_KEY` value matches the active
plan and OPRA is unlocked. Vendor support: support@databento.com.

```sh
# Smoke test the client outside the binary
DATABENTO_API_KEY=$YOUR_KEY \
  go run ./scripts/pull_wide.sh   # adapt args to taste
```

If the smoke fails with `403` or `account locked`, raise a vendor
ticket. There is no fix on our side — see HANDOFF.md.

### Network-level disconnect
Vendor stream is alive but our reconnect loop isn't catching up:

```sh
# Restart ingest cleanly so the reconnect counter resets
docker compose --profile app restart ingest
docker compose logs -f ingest
```

If reconnects keep cycling, the upstream may have pinned a different
host pool and is rate-limiting our reconnects. Back off the reconnect
cadence (`internal/feed/databento/client.go`) — out of band fix, not
runbook.

### Bootstrap stale (mid-day reload)
OPRA bootstrap caches strike universe at session start. If we hot-restart
mid-day, the client must re-bootstrap. Symptoms: ingest connects, prints
`bootstrap complete`, then publishes fewer strikes than yesterday.

```sh
# Check bootstrap log
docker compose logs ingest | grep -E 'bootstrap|strike'
```

Force a clean bootstrap by restarting ingest at the next minute boundary
(reduces vendor "duplicate connection" rejections).

### Symbology drift
Databento occasionally renames symbols mid-day. Look for:

```sh
docker compose logs ingest | grep -E 'unknown root|cannot resolve'
```

If a SPX or NDX root rejection appears, check `internal/feed/symbol.go`
for the parser that maps vendor symbols to our internal enum. This is
a code fix, not an ops fix — escalate to dev.

## Recovery verification

```sh
# 1. ticks/sec metric back to baseline (~ few hundred during RTH for
#    SPX 0DTE active).
curl -s http://localhost:8080/metrics | grep ingest_ticks_processed_total

# 2. Archive table receiving rows
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT count(*) FROM ticks WHERE ts_ns > extract(epoch from now() - interval '5 min') * 1e9;"

# 3. dealer_state_1s also growing again (compute downstream)
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT max(ts_ns)/1e9 - extract(epoch from now()) AS lag_sec FROM dealer_state_1s;"
```

## Postmortem checklist

- [ ] Was the stall vendor-side (account / network) or our-side (parser, bootstrap)?
- [ ] Did the alert fire fast enough to catch a bad session?
- [ ] Document the root cause in this file under §"Failure modes seen before"
      so the next operator catches it faster.

## Failure modes seen before

| Symptom | Cause | Fix |
|---|---|---|
| ... | ... | ... |
