# Service Level Indicators / Objectives

> Operational target floor for FlowGreeks. SLI = what we measure; SLO =
> the threshold we promise. Burn-rate alerts page when we'd exhaust the
> error budget faster than 30 days.

Targets are deliberately **soft for v1** since the audience is bootcamp
graduates from flowjob.id, not enterprise customers. Tighten on
post-launch operational data.

| SLI | Definition | SLO (target) | Error budget |
|---|---|---|---|
| **Live tick-to-WS latency** | p99 of `now() - tick.tsRecv` at WS publish | < 250ms (relaxed from 100ms target until OPRA verified) | 5 min/day above |
| **Snapshot REST availability** | Successful `/api/snapshot/{spx,ndx}` responses / total | 99.5% / 30d | 3.6h/30d outage |
| **Snapshot REST latency** | p99 over 5min window | < 150ms | 5 min/day above |
| **WS broker durability** | 1 - (drops / publishes) per symbol-kind | 99.9% / 30d | 0.1% drop budget |
| **Compute pipeline liveness** | `flowgreeks_compute_ticks_processed_total` increasing | No 60s gap during US RTH | 1 gap/quarter |
| **Auth correctness** | Successful auths / total auth attempts (excluding rate-limit) | 99.9% / 30d | Burst alert if dips |
| **Backup recency** | `now() - last_dump_mtime` | < 26h | Page if > 28h |
| **Quarterly restore drill** | Runbook executed against latest dump | 1/quarter | Block release if missed |

## Burn-rate alerting (planned, not yet implemented)

For each SLO with a 30d window:
- **Fast burn** (1h window, 14.4× rate): page — 2% of monthly budget consumed in 1h.
- **Slow burn** (6h window, 6× rate): page — would consume entire budget in 5 days.

Implementation note: requires `histogram_quantile()` recording rules in
Prometheus. Track in `deploy/prometheus/flowgreeks.rules.yml` once
post-OPRA traffic produces meaningful baselines.

## Out of scope for v1

- Geographic latency: single-region by design.
- Multi-tenant isolation: SPX/NDX are global, not per-tenant.
- Cold-start RTO: single binary; restart is sub-second.

## Updating the SLO

Bump the SLO threshold *down* (tighter) only after 90 days of operational
data confirms current numbers. Bump *up* (looser) when post-mortem
identifies a target as unrealistically tight (rare). Every change must
include a one-paragraph rationale in this file's git log.
