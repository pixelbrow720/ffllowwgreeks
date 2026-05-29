# Incident — IV solver collapse / Greeks integrity

**Symptom matchers:** `IVSolverFailureRateHigh` (>20% over 5min); api
`/api/snapshot` response missing strikes that should be active; sudden
DPI rebase to 0 across-the-board; `flowgreeks_iv_solver_failures_total`
spike vs `_attempts_total`.

## Triage in 60 seconds

```sh
# Failure rate per side
curl -s http://localhost:8080/metrics | grep flowgreeks_iv_solver_

# Are quotes upstream sane?
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT side, count(*),
          avg(ask), avg(bid), avg(ask-bid) AS spread
     FROM ticks
    WHERE asset_class = 1 -- option
      AND ts_ns > extract(epoch from now() - interval '5 min') * 1e9
    GROUP BY 1;"

# Recent IV failure log lines (compute binary)
docker compose logs compute --tail=500 | grep -iE 'iv.*fail|widen|bracket'
```

## Decision tree

### Bracket failure on deep-OTM 0DTE
Known mode — the solver auto-widens once when residuals share sign
([fix afb7831](../../REVIEW.md)). If failure rate persists past auto-widen,
the issue is upstream:

- Spot price is wrong. Check the basis tracker and futures feed.
- Dividend yield (`q`) is set to 0 inconsistent with index fwd points.
- Quote skew is corrupted (one side at zero).

### Wide-spread / crossed quote storm
Vendor venue glitch. Check:

```sh
docker exec fg-postgres psql -U flowgreeks -d flowgreeks -c \
  "SELECT count(*) FILTER (WHERE bid > ask) AS crossed,
          count(*) FILTER (WHERE bid <= 0 OR ask <= 0) AS zero_side,
          count(*) AS total
     FROM ticks
    WHERE asset_class = 1
      AND ts_ns > extract(epoch from now() - interval '5 min') * 1e9;"
```

If `crossed` or `zero_side` is > 5% of total, this is upstream noise.
Classifier already drops crossed-quote ticks (see `classifier.go`); IV
solver fallthrough is harmless because Greeks for those strikes simply
won't appear. No action.

### Synthetic recenter drift
If the demo profile is running and `synth_state` chain has drifted off
ATM, the solver sees nonsensical price floors. Restart synth:

```sh
docker compose --profile demo restart synth-state
```

(Real ingest doesn't have this failure mode.)

### Spot price feed broken
If basis tracker last update is > 60s old, downstream IV trusts a stale
spot:

```sh
curl -s http://localhost:8080/metrics | grep basis_last_update_seconds
```

Check `feed/databento` GLBX / futures path. If basis is stuck, restart
ingest; if persistent, escalate to data-feed runbook
([opra-stall.md](opra-stall.md)).

## Recovery verification

```sh
# Failure rate back below 5%
curl -s http://localhost:8080/metrics | grep flowgreeks_iv_solver_

# Greeks present in snapshot for a known active strike
curl -s http://localhost:8080/api/snapshot/spx \
  | jq '.strikes[] | select(.iv > 0) | {strike, iv, delta}' | head -20

# DPI back to non-zero
curl -s http://localhost:8080/api/snapshot/spx | jq '.dpi'
```

## Postmortem checklist

- [ ] Was the bracket auto-widen triggered? If yes, did it actually
      converge or hit max-iter?
- [ ] Is there a class of input that needs a third widen? Update
      `internal/greeks/solver.go`.
- [ ] Should the alert threshold tighten?
- [ ] If a strike had a real solve failure, retain its bid/ask/price for
      regression test fixture.
