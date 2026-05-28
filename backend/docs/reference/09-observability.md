# 09 — Observability

> Validated against commit `3e5b0ec`.
> Source:
> - [`internal/trace/`](../../internal/trace/) — request-scoped trace ids
> - `internal/{api,auth,alerts,replay,store}/metrics.go` + `cmd/{api,ingest,compute}/metrics.go` — Prometheus counters/gauges/histograms
> - `internal/auth/audit.go` — structured audit events
> - [`deploy/prometheus/flowgreeks.rules.yml`](../../deploy/prometheus/flowgreeks.rules.yml) — alert rules
> - [`deploy/grafana/flowgreeks-pipeline.json`](../../deploy/grafana/flowgreeks-pipeline.json) — starter dashboard

## Three signal types

| Signal | What | Cardinality |
|---|---|---|
| **Traces** | Request-scoped id propagated across binaries | unlimited (1 per request, lifetime = request) |
| **Audit log** | Security-relevant events as structured slog records | unlimited (1 per event) |
| **Metrics** | Aggregated counters / gauges / histograms | bounded (label sets fixed) |

Each signal is independent; a security investigation typically pulls all three (metric tells you "burst happened"; alert page brings you in; audit log tells you "who tried what"; trace id lets you stitch one user's failed flow across binaries).

## Distributed traces

```
Layer 1: HTTP ingress
   chi RequestID middleware     → req_id in context
   traceMiddleware              → reads X-Trace-ID header,
                                   falls back to req_id,
                                   else generates 8-byte hex
                                  → trace_id in context
   requestLogger                → emits both in slog as req_id + trace_id

Layer 2: NATS publish (when api → another binary)
   pub.PublishWithContext       → copies trace_id from ctx into nats.Header
                                  X-Trace-ID

Layer 3: NATS subscribe (consumer binary)
   sub.Subscribe                → reads X-Trace-ID,
                                   attaches to context,
                                   downstream slog inherits
```

`internal/trace/trace.go` exports `HeaderName = "X-Trace-ID"`. The id is **8 bytes hex** (16 chars) — short enough for log readability, large enough that collisions over a session are negligible.

Scope is request-level only ([`trace.go:7-22`](../../internal/trace/trace.go#L7)) — we explicitly do **not** tag every tick or per-second state publish, since the volume would dominate log size for no debugging value.

## Metric catalog

Every metric the binaries emit, grouped by surface. All are exposed at `GET /metrics` on the api binary; ingest and compute publish on the same Prometheus convention.

### HTTP surface

```
flowgreeks_http_requests_total{method,route,status_class}            counter
flowgreeks_http_request_duration_seconds{method,route}               histogram (1ms..16s)
flowgreeks_http_response_bytes{method,route}                         histogram
```

`route` uses the chi route pattern (e.g. `/api/snapshot/{symbol}`) instead of the raw path — keeps cardinality bounded as the strike axis would otherwise explode.

### WebSocket fanout

```
flowgreeks_ws_subscribers                                            gauge
flowgreeks_ws_published_total{symbol,kind}                           counter
flowgreeks_ws_drops_total{symbol,kind}                               counter
```

`symbol ∈ {spx, ndx, unknown}`, `kind ∈ {gex, alert, ...}`. The drops counter is the slow-client signal.

### Auth (added in `8d36519`)

```
flowgreeks_auth_login_attempts_total{result=ok|fail|locked}          counter
flowgreeks_auth_signup_attempts_total{result=ok|fail}                counter
flowgreeks_auth_refresh_attempts_total{result=ok|fail|reuse_detected} counter
flowgreeks_auth_logouts_total                                        counter
flowgreeks_auth_account_lockouts_total                               counter
```

Bounded cardinality: `result` is a small enum, no per-user labels. Pairs with the audit log — you alert on counters, you investigate via slog records.

### Alerts engine

```
flowgreeks_alerts_rules                                              gauge
flowgreeks_alerts_evaluations_total                                  counter
flowgreeks_alerts_fires_total{kind}                                  counter
flowgreeks_alerts_cooldown_suppressed_total{kind}                    counter
flowgreeks_alerts_deliveries_total{sink}                             counter
flowgreeks_alerts_delivery_errors_total{sink}                        counter
flowgreeks_alerts_webhook_async_errors_total                         counter
```

### Replay manager + sessions

```
flowgreeks_replay_sessions_active                                    gauge
flowgreeks_replay_sessions_created_total                             counter
flowgreeks_replay_sessions_rejected_total{reason}                    counter
flowgreeks_replay_sessions_finished_total{outcome}                   counter
flowgreeks_replay_ticks_published_total{symbol}                      counter
flowgreeks_replay_publish_errors_total                               counter
```

### Cold-path archive (`internal/store`)

```
flowgreeks_archive_ticks_written_total                               counter
flowgreeks_archive_ticks_dropped_total                               counter
flowgreeks_archive_flush_duration_seconds                            histogram
flowgreeks_archive_flush_errors_total                                counter

flowgreeks_state_rows_written_total                                  counter
flowgreeks_state_rows_dropped_total                                  counter
flowgreeks_state_flush_duration_seconds                              histogram
flowgreeks_state_flush_errors_total                                  counter
```

### Compute pipeline

```
flowgreeks_compute_ticks_processed_total{symbol,tick_type}           counter
flowgreeks_compute_iv_solver_attempts_total                          counter
flowgreeks_compute_iv_solver_failures_total                          counter
flowgreeks_compute_aggregator_iterations_total                       counter
flowgreeks_compute_aggregator_duration_seconds                       histogram
flowgreeks_compute_active_strikes{symbol}                            gauge
```

### Ingest dispatch

```
flowgreeks_ingest_published_total{tick_type}                         counter
flowgreeks_ingest_publish_errors_total                               counter
flowgreeks_ingest_feed_errors_total                                  counter
```

## Audit log

[`internal/auth/audit.go`](../../internal/auth/audit.go) — `SlogAuditSink` writes to the same slog logger as every other line, but with a fixed structured shape:

```json
{
  "level": "INFO",
  "msg": "audit",
  "kind": "auth.login.ok",
  "user_id": 42,
  "email": "alice@example.com",
  "ip": "203.0.113.4",
  "user_agent": "Mozilla/5.0...",
  "detail": "",
  "occurred_at": "2026-05-27T14:30:21.123Z",
  "req_id": "...",
  "trace_id": "..."
}
```

Field rules:
- `email` lowercased + trimmed
- `user_agent` truncated to 256 chars by the sink
- `detail` is free-form human-readable; **never includes secrets / tokens**
- INFO level for all kinds **except** `refresh.reuse_detected`, `login.locked_trip`, `login.locked_out` — those are WARN

Audit kinds (full list — [`audit.go:34-44`](../../internal/auth/audit.go#L34)):

```
auth.login.ok                      INFO
auth.login.fail                    INFO
auth.login.locked_trip             WARN  (account just got locked)
auth.login.locked_out              WARN  (already-locked attempt)
auth.signup.ok                     INFO
auth.signup.fail                   INFO
auth.refresh.ok                    INFO
auth.refresh.fail                  INFO
auth.refresh.reuse_detected        WARN  (token leak)
auth.logout                        INFO
alert.rule.upsert                  INFO  (emitted from api.AlertHandlers)
alert.rule.delete                  INFO
```

Same `AuditSink` is wired into `api.AlertHandlers` so rule mutation events live on the same log stream as auth events ([`internal/api/alerts.go`](../../internal/api/alerts.go) — see `audit()` helper).

## Prometheus alert rules

[`deploy/prometheus/flowgreeks.rules.yml`](../../deploy/prometheus/flowgreeks.rules.yml) — five rule groups:

### `flowgreeks-pipeline-liveness`

| Alert | Trigger | Severity |
|---|---|---|
| `ComputeTicksStalled` | `rate(compute_ticks_processed) == 0 for 3m` | page |
| `AggregatorStuck` | `rate(aggregator_iterations) < 0.5/s for 2m` (target ≥ 1/s) | page |
| `IngestNoArchiveWrites` | `rate(archive_ticks_written) == 0 for 5m` | warn |

### `flowgreeks-backpressure`

| Alert | Trigger | Severity |
|---|---|---|
| `ArchiveTicksDropped` | `rate(archive_ticks_dropped) > 0 for 2m` | warn |
| `StateRowsDropped` | `rate(state_rows_dropped) > 0 for 2m` | warn |
| `StateFlushErrors` | `rate(state_flush_errors[5m]) > 0 for 1m` | page |
| `WSDropsHigh` | `sum(rate(ws_drops)) > 5 for 2m` | warn |

### `flowgreeks-http`

| Alert | Trigger | Severity |
|---|---|---|
| `HTTPHighErrorRate` | 5xx fraction > 5% for 5m | page |
| `HTTPLatencyP99High` | p99 > 1s on any route for 10m | warn |

### `flowgreeks-quote-quality`

| Alert | Trigger | Severity |
|---|---|---|
| `IVSolverFailureRateHigh` | failure / attempts > 20% for 5m | warn |

### `flowgreeks-auth` (added in `b8fff04`)

| Alert | Trigger | Severity |
|---|---|---|
| `AuthLoginFailureBurst` | `rate(login.fail) > 5/s for 2m` | warn — distributed brute force |
| `AuthLockoutTripBurst` | `rate(account_lockouts[5m]) > 0.5 for 5m` | page — credential-stuffing run |
| `AuthRefreshReuseDetected` | `increase(reuse_detected[5m]) > 0` immediate | page — token leak |

## Grafana dashboard

[`deploy/grafana/flowgreeks-pipeline.json`](../../deploy/grafana/flowgreeks-pipeline.json) — 10 panels:

```
┌────────────────────┬────────────────────┬────────────────────┐
│ Tick rate by       │ IV solver attempts │ Aggregator         │
│ symbol/type        │ + failures         │ duration p50/p99   │
├────────────────────┼────────────────────┼────────────────────┤
│ Active strikes     │ HTTP RPS by route  │ HTTP p50/p95/p99   │
│ gauge per symbol   │                    │                    │
├────────────────────┼────────────────────┼────────────────────┤
│ WS subscribers     │ WS drops by        │ Archive write rate │
│ + publish rate     │ symbol/kind        │ + drop rate        │
├────────────────────┴────────────────────┴────────────────────┤
│             State writer flush duration histogram             │
└──────────────────────────────────────────────────────────────┘
```

Auto-provisions when the `obs` profile (or `app` / `demo`) is up. Bound on host port 3001.

## When something goes wrong — investigation playbook

```
1. PagerDuty alert lands
       │
       ▼
2. Check Grafana dashboard at host:3001
       Dashboard tells you WHICH metric flipped + WHEN.
       │
       ▼
3. Find the trace_id from the alert + slog
       grep slog for the time window:
         level=WARN OR level=ERROR
       Pick out trace_id values associated with the affected
       user_id / IP / route.
       │
       ▼
4. Replay the trace_id across binaries
       grep all binaries' slog for trace_id=<value>
       Now you have the full request path: HTTP → NATS → NATS sub →
       Postgres write — every step on one filter.
       │
       ▼
5. If it's a security event, also pull the audit log
       Filter: kind=auth.* OR kind=alert.rule.*
       Maps the timeline of who did what, when, from where.
       │
       ▼
6. If it's a backend health event, check /metrics directly
       curl http://api:8080/metrics | grep flowgreeks_
       Sanity-check counters/gauges for live values.
```

## Recommended k8s scrape config

```yaml
- job_name: flowgreeks-api
  static_configs:
    - targets: ['api:8080']
  metrics_path: /metrics

- job_name: flowgreeks-ingest
  static_configs:
    - targets: ['ingest:9100']

- job_name: flowgreeks-compute
  static_configs:
    - targets: ['compute:9101']
```

The compose `prometheus.yml` covers the demo profile — for k8s, replicate the scrape config in your Prometheus operator's CRD.

## What this section does **not** cover

- Per-handler validation logic — read the code (`io.LimitReader`, decoder errors).
- Cost dashboards (cloud-side billing alerts) — out of binary scope.
- Synthetic monitoring (uptime probes from outside the cluster) — out of binary scope; recommend Pingdom / UptimeRobot / etc. against `/health/live`.
