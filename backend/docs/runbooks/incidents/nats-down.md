# Incident — NATS down or unreachable

**Symptom matchers:** `ComputeTicksStalled`, `IngestNoArchiveWrites`,
api `/health/ready` flipping to 503 with `nats: unreachable`,
WebSocket clients suddenly stop receiving updates while REST
`/api/snapshot/{symbol}` continues to serve stale-cached responses.

## Triage in the first 60 seconds

```sh
# Is NATS responsive at the wire?
docker exec fg-nats nats-server --version
docker exec fg-nats wget -q -O - http://localhost:8222/healthz
docker exec fg-nats wget -q -O - http://localhost:8222/varz | head -50

# Is JetStream alive?
docker exec fg-nats wget -q -O - http://localhost:8222/jsz?streams=1 | head -100

# Are the 4 binaries still attached?
docker compose --profile app ps | grep -E 'fg-(api|ingest|compute|replay)'

# Recent NATS logs (compose default driver — short ring buffer).
docker compose logs nats --tail=200
```

## Decision tree

### Container exited
`docker compose --profile app start nats` and watch:

```sh
docker compose logs -f nats
```

If it crashes again on start, the JetStream `_data` directory is
likely corrupted (out-of-disk, sigkill mid-write). Move it aside and
let JetStream rebuild streams from the publishers:

```sh
docker compose --profile app stop nats
docker run --rm -v flowgreeks_nats-data:/data alpine mv /data /data.broken
docker compose --profile app up -d nats
docker exec fg-jetstream-setup go run /src/scripts/jetstream_setup
```

This loses any unack'd messages still in JetStream — the apps will
re-emit on reconnect.

### Container running, port unreachable
Bind mount issue or networking. Check Docker network:

```sh
docker network inspect flowgreeks_default | grep -A3 fg-nats
```

If the container reports a different IP from what api / compute /
ingest cached, restart the consumers — NATS client libraries do hold a
DNS resolution they expect to be stable.

### JetStream reports degraded streams
```sh
docker exec fg-nats wget -q -O - http://localhost:8222/jsz?streams=1
```

Look for `"state":{"messages":0,"first_seq":0,"last_seq":0}` on
TICKS / STATE / FLOW streams. Re-provision:

```sh
docker compose --profile app exec api /app -version  # confirm binary up
docker compose --profile app run --rm -T api /jetstream_setup
```

(Or run `scripts/jetstream_setup` from a host shell with `NATS_URL`
pointed at the running NATS.)

### NATS is healthy but compute publishers stalled
This is not a NATS problem — it's a downstream consumer problem.
See [opra-stall.md](opra-stall.md) for ingest stalls or
[archive-backpressure.md](archive-backpressure.md) for compute.

## Recovery verification

```sh
# 1. /health/ready reports nats: ok
curl -s http://localhost:8080/health/ready | jq .deps.nats

# 2. ComputeTicksStalled rule cleared in Prometheus
# 3. Test WS subscribe works:
go run ./scripts/smoke/ws -url ws://localhost:8080/ws/live -duration 10s

# 4. New NATS publishes are landing:
docker exec fg-nats nats sub -s nats://localhost:4222 'state.>' --count 3
```

## Postmortem checklist

- [ ] Was `restart: unless-stopped` insufficient? Why didn't auto-restart hold?
- [ ] Did the readiness probe flip in time for the LB to drain?
- [ ] Was the stream `_data` corruption preventable (disk full, signal handling)?
- [ ] Update this runbook if a step was missing or wrong.
