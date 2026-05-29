# Operations runbooks

Every alert rule in `deploy/prometheus/flowgreeks.rules.yml` should
trace to a runbook here. If you write a new alert, write the runbook in
the same PR.

## Backup & disaster recovery

- [db-backup-restore.md](db-backup-restore.md) — backup mechanism,
  full-DB restore, single-table restore, quarterly drill procedure.

## Service objectives

- [sli-slo.md](sli-slo.md) — what we measure, what we promise, error
  budgets, burn-rate alerting (planned).

## Incident response

- [incidents/nats-down.md](incidents/nats-down.md) — NATS unreachable,
  JetStream stream rebuild, app reconnect issues.
- [incidents/postgres-pool-storm.md](incidents/postgres-pool-storm.md) —
  pool exhaustion, long-running query / lock cleanup, capacity tuning.
- [incidents/opra-stall.md](incidents/opra-stall.md) — Databento OPRA
  / GLBX disconnect, bootstrap reload, vendor escalation path.
- [incidents/iv-solver-collapse.md](incidents/iv-solver-collapse.md) —
  IV solver failure-rate spike, deep-OTM bracket exhaustion, upstream
  quote storm.
- [incidents/archive-backpressure.md](incidents/archive-backpressure.md)
  — drop counters rising on archive or state writers; identifying
  Postgres / disk / compute as the proximate cause.

## Drill log

- [_drill-log.md](_drill-log.md) — append-only record of quarterly
  restore drills. Empty until first drill executes.

## Runbook style guide

When you write a new runbook, follow the same shape as the existing ones:

1. **Symptom matchers** — alert names, log lines, metric expressions.
2. **Triage in 60 seconds** — copy-paste shell commands the operator can
   run blind. No prose between commands; the incident timer is running.
3. **Decision tree** — branches based on what triage reveals. Each
   branch ends with a concrete action.
4. **Recovery verification** — how to know it's actually fixed.
5. **Postmortem checklist** — what to capture for the post-incident
   write-up.

Keep the prose terse. Operators read these at 02:00 WIB; long
explanations lose. Link to deeper context in `docs/reference/`.
