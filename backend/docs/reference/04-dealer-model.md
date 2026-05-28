# 04 — Dealer model

> Validated against commit `3e5b0ec`.
> Source: [`internal/dealer/`](../../internal/dealer/) — gex.go, dpi.go, charm_clock.go, flow_pulse.go, pin.go, simulator.go, position.go, classifier.go, basis.go.
>
> Math reference: [`docs/COMPUTE_MODEL.md`](../COMPUTE_MODEL.md) §4–§8.

## What lives here

Six derived measurements, all functions of dealer position × Greeks × order flow:

| Component | What it answers | Compute model § | Source |
|---|---|---|---|
| **GEX aggregator** | Where are the gamma walls? Are dealers long or short gamma? | §4 | [`gex.go`](../../internal/dealer/gex.go) |
| **DPI** | Single 0–100 score: how much hedging pressure is dealers under, right now? | §5 | [`dpi.go`](../../internal/dealer/dpi.go) |
| **Charm Clock** | Is the intraday charm-decay window WEAK / RISING / PEAK / FADING / PIN? | §6 | [`charm_clock.go`](../../internal/dealer/charm_clock.go) |
| **Flow Pulse** | 3-line oscillator decomposing pressure into gamma / charm / vanna lanes | §6 (extension) | [`flow_pulse.go`](../../internal/dealer/flow_pulse.go) |
| **Pin Probability** | At EOD, which strike does spot pin to? | §8 | [`pin.go`](../../internal/dealer/pin.go) |
| **What-If Simulator** | If spot moves X% / time advances Y min / vol shifts Z pts — how much does dealer have to hedge? | §7 | [`simulator.go`](../../internal/dealer/simulator.go) |

Plus three per-tick maintainers:

| Component | What it does |
|---|---|
| `LeeReadyClassifier` | Tags trades aggressor=BUY/SELL via tick test + cached NBBO |
| `Positions` | Maintains dealer net position per (strike, side) from OI seed + per-trade deltas |
| `BasisTracker` | Tracks ES/NQ basis vs SPX/NDX |

All consumers run inside `cmd/compute`'s 1Hz aggregator loop ([`cmd/compute/main.go:388`](../../cmd/compute/main.go#L388)) which fans out the result to NATS `state.<sym>.gex` + `narrative.<sym>` and to Postgres `dealer_state_1s`.

## GEX aggregator

```
StrikeRow (per (expiry, strike, side))
   │
   │ DealerPos (signed contracts)
   │ Gamma (per-contract, from greeks.All)
   │
   ▼
   GEXNotional[i] = DealerPos[i] · Gamma[i] · 100 · spot²·0.01
   │
   ▼
   NetGEX = Σ GEXNotional
   │
   ▼  walk strikes ascending
   │
   ▼  cumulative DealerPos·Gamma flips sign at ZeroGamma
   │
   ▼  walls = strikes with the largest |GEXNotional| above/below spot
   │      CallWall = max-positive-GEX strike > spot
   │      PutWall  = min-negative-GEX strike < spot
   │
   ▼  Regime = sign(NetGEX); LongGamma if NetGEX > +threshold, ShortGamma if < -threshold,
   │            else Neutral (threshold = $500M for SPX, [`gex.go:13`](../../internal/dealer/gex.go#L13))
   │
   ▼
AggregateView{ NetGEX, ZeroGamma, CallWall, PutWall, ExpectedMv, Regime }
```

`ExpectedMv` is the spot-move estimate that would flatten dealer gamma by the natural drift of the underlying — derived from `NetGEX / Σ|gamma|` per §4. Bench: `Aggregate` ~5.2µs at 200 strikes.

`contractMultiplier = 100.0` is the SPX/NDX option spec contract multiplier ([`gex.go:11`](../../internal/dealer/gex.go#L11)). The `0.01` factor on `spot²` makes GEX denominate in dollars-per-1%-spot-move.

## DPI — Dealer Pressure Index

A 0–100 EWMA composite of five components. From [`dpi.go`](../../internal/dealer/dpi.go):

```
Inputs every second
   │
   ▼
   ┌────────────────────────────────────────────────────────────────┐
   │  NetGEX_pressure   = clip01(|NetGEX| / GEXNorm) × 100           │
   │      default GEXNorm = 5e9 ($5B)                                │
   │                                                                 │
   │  Charm_velocity    = clip01(|charmVelocity| / CharmFlowRateNorm)│
   │      default CharmFlowRateNorm = 5e6 (delta/min × multiplier)   │
   │                                                                 │
   │  Vanna_sensitivity = clip01(|vannaPressure| / VannaPressureNorm)│
   │      default VannaPressureNorm = 1e6                            │
   │                                                                 │
   │  TTC               = ((sessionEnd-now) / sessionLen)^1.5 × 100  │
   │      ttcExponent = 1.5 — convex ramp toward EOD                 │
   │                                                                 │
   │  Flow_concentration= clip01(HHI × 5.0) × 100                    │
   │      HHI = Σ (flow_i / Σflow)²                                  │
   │      flowConcentrationScale = 5.0                               │
   └────────────────────────────────────────────────────────────────┘
   │
   ▼
   each component is EWMA-smoothed against the prior call:
       smoothed = α × raw + (1−α) × prior
       default EWMAAlpha = 0.3
   │
   ▼
   DPI_composite =
        0.30 × NetGEX_pressure
      + 0.25 × Charm_velocity
      + 0.20 × Vanna_sensitivity
      + 0.15 × TTC
      + 0.10 × Flow_concentration
   │
   ▼
   DPIBreakdown{ Composite, NetGEX, Charm, Vanna, TTC, Flow }
```

Five normalisation constants ([`dpi.go:31`](../../internal/dealer/dpi.go#L31)) are starting points for SPX 0DTE — production deployment should replace with rolling p90/p95 from the `dealer_state_1s` archive (this is the calibration step that's deferred until OPRA unlocks).

`Score()` is concurrency-safe per symbol ([`dpi.go:65`](../../internal/dealer/dpi.go#L65)) — `mu sync.Mutex` guards the per-symbol `dpiState` map.

## Charm Clock

Five intraday zones based on |charm velocity| evolution within the session:

```
                                          ▲
                                          │  RISING               PEAK
                          velocity        │     ┐               ┌─────┐
                          (delta/min)     │     │ ╱           ╲ │     │
                                          │     │╱             ╲│     │
                                          │     ┘                ┘     ╲
                                          │   WEAK                       FADING
                                          │ ┌──────┐                          ╲
                                          │ │      │                           ╲   PIN
                                          │ │      │                            ╲ ┌──┐
                                          └─┴──────┴───────────────────────────┴──┴──→
                                            09:30                              16:00 ET

  Zone        Triggered when                                    Constants
  ─────────── ─────────────────────────────────────────────────────────────
  WEAK        |vel| < 1e6 OR session age < 1h                   weakWindow=60min
  RISING      trend slope > +2% relative over 5-sample window   trendThreshold=0.02
  PEAK        |vel| ≥ 5e6 AND |vel| ≥ 0.75·sessionMax           peakBandFraction=0.75
  FADING      trend slope < -2% relative over 5-sample window
  PIN         time_to_close < 30 min                            charmPinWindow=30min
```

Implementation: [`charm_clock.go:18-29`](../../internal/dealer/charm_clock.go#L18). Per-symbol `charmSymbolState` keeps a 30-sample ring buffer ([`charm_clock.go:46`](../../internal/dealer/charm_clock.go#L46)) — the trend lookback is the last 5 samples.

`WindowSummary` returns:
- `CurrentZone` — the active zone label
- `SessionMaxAbsVel` — running max of |charmVelocity|
- `TimeInZone` — duration since current zone was entered
- `NextZoneETA` — slope-extrapolated estimate; zero when no usable trend

## Flow Pulse

Three-line oscillator that decomposes pressure into separate gamma / charm / vanna lanes ([`flow_pulse.go`](../../internal/dealer/flow_pulse.go)). Each lane is a normalised, EWMA-smoothed contribution; the chart shows the three lines plus a `Total` envelope so the operator can spot which Greek is driving pressure right now.

```
PulseBreakdown {
    Gamma   float32      ← clip01(|netDealerGamma·spot²·0.01| / norm) × 100
    Charm   float32      ← clip01(|netDealerCharm·spot| / norm) × 100
    Vanna   float32      ← clip01(|netDealerVanna·spot| / norm) × 100
    Total   float32      ← (Gamma + Charm + Vanna) / 3
}
```

EWMA smoothed identically to DPI (α default 0.3). Distinction from DPI: DPI is a **single number** for "how stressed is the dealer right now"; Flow Pulse is the **decomposition** so a UI can render three traces and ask "which Greek is the driver?".

## Pin Probability

Activates only in the last 90 minutes of session ([`pin.go:31`](../../internal/dealer/pin.go#L31)). For each candidate strike within ±20pt of spot, computes a `PinScore`:

```
PinScore[k] = 0.4 · gamma_strength[k]
            + 0.3 · distance_factor[k]
            + 0.2 · flow_persistence[k]
            + 0.1 · time_factor

  gamma_strength[k]   = |TotalGamma[k]| / max(|TotalGamma|)
  distance_factor[k]  = exp(-(S-K)² / (2σ²))         σ default = 8 spot pts
  flow_persistence[k] = recent_test_count[k] / Σ recent_test_count
  time_factor         = (close - now) / sessionLen   (closer to close → larger)
```

Then softmax across the candidate strike set:

```
PinProb[k] = exp(α · PinScore[k]) / Σ exp(α · PinScore[i])
             α default = 5
```

`PinResult` returns the candidate set as `[]PinCandidate{Strike, PinScore, PinProb}` plus the `TopStrike` + `TopProb` for fast UI consumption.

α = 5 controls how peaky the softmax is. Higher α = more concentrated probability mass on the top strike. Calibration against historical EOD outcomes is a deferred M9 task.

## What-If Dealer Simulator

Pure function over a `StrikeRow[]` snapshot + a hypothetical scenario:

```
ScenarioInput {                            ScenarioResult {
    SpotPctChange   float64                    NewSpot         float64
    DurationMinutes float64                    DurationYears   float64
    VolPtChange     float64                    ForcedDelta     float64
}                                              ForcedNotional  float64
                                               CharmAid        float64
                                               NetPressure     float64
                                               TopContributors []StrikeContribution
                                           }
```

```
For each StrikeRow:
   newGreeks = greeks.All(newSpot, strike, t-Δt, r, q, σ+ΔIV, side)
   ΔDelta_per_contract = newGreeks.Delta - row.Delta
   row_contribution    = DealerPos · ΔDelta_per_contract · contractMultiplier

ForcedDelta    = Σ row_contribution
ForcedNotional = -spot · ForcedDelta              ← sign flip per the convention below
CharmAid       = -spot · Σ (DealerPos · originalCharm · Δt_years · multiplier)

NetPressure    = ForcedNotional - CharmAid        ← deep-review fix; subtraction
```

**Sign convention** (the doc-comment at [`simulator.go:6-13`](../../internal/dealer/simulator.go#L6)): `ForcedNotional > 0` means dealer **buys** index (futures) to rehedge; `< 0` means **sells**. The flip on `-spot · ForcedDelta` derives from the short-gamma case: a spot rise gives the dealer more long-delta exposure, so they short futures equivalent — yielding a negative `ForcedNotional`.

The `NetPressure = ForcedNotional - CharmAid` correction (deep review fix `864f330`) is critical: both terms share the `-spot·Δ` sign convention, so addition would have **inflated** magnitude when charm and forced flow moved together. Subtraction matches "charm aid reduces magnitude of forced flow."

`TopContributors` sorts row contributions by |row_contribution| descending, returning the top N (default 5) for UI surfacing.

## Per-tick maintainers

### LeeReadyClassifier

Lee–Ready algorithm: a trade is BUY-aggressed if price > mid, SELL-aggressed if price < mid; on tie (price == mid), defer to tick test (price > last → BUY, < last → SELL).

[`classifier.go`](../../internal/dealer/classifier.go) keeps a per-strike last-price map. The deep review fixed two bugs:

1. **Cap-hit reset wiped all history** — replaced "wipe everything when map exceeds 10k entries" with a two-generation `curr/prev` map. After rotation, hot strikes are still recoverable from `prev`, so tick-test fallback doesn't regress to UNKNOWN for the entire chain ([`classifier.go:79-82`](../../internal/dealer/classifier.go#L79)).
2. The classifier needs a cached NBBO at trade time (trade ticks don't carry bid/ask) — that's what `Pipeline.quotes` (a `QuoteCache`) provides.

Bench: 71 ns per `Classify`.

### Positions

`Positions` is a per-(strike, side) map of net dealer contracts. Seeded from OI ticks via `SeedFromOI`; updated per trade by `Apply(tick, aggressor)`:

```
aggressor == BUY  → dealer is on the SELL side → -contracts
aggressor == SELL → dealer is on the BUY side  → +contracts
```

Dealer position is the **mirror** of customer aggressor flow — the assumption is that retail/institutional flow hits MM quotes, so a customer BUY = dealer SELL.

Bench: 49 ns per `Apply`.

### BasisTracker

Tracks ES/NQ futures vs SPX/NDX cash spot. `UpdateFuture(t)` updates the futures mid; `UpdateSpot(t)` updates the cash spot estimate. Returns `BasisSmoothed` (EWMA of basis residual) for use as the dealer-hedging proxy when SPX cash is unavailable.

Bench: 156 ns per `Update`.

## Aggregator orchestration (1Hz)

[`cmd/compute/main.go:388`](../../cmd/compute/main.go#L388) `runAggregator`:

```
every 1 second:
    snapshot positions, flow5min, pinFlow under RLock
    rows := buildStrikeRows(positions, ivCache, currentSpot)
    fillGreeks(p, rows)                          ← greeks.All per row
    aggView := dealer.Aggregate(rows, spot)      ← NetGEX, walls, regime, ExpectedMv
    charmVel := aggregateCharmVelocity(rows, spot)
    pulse := flowPulse.Update(rows, spot)
    zone := charmClock.Classify(symbol, charmVel, now)
    dpi := dpiScorer.Score(symbol, dpi inputs)
    pinResult := pinEngine.Compute(rows, spot, now)

    publishState(NATS, sym, gexSnapshot{ ... full state ... })
    publishNarrative(NATS, sym, narrative.Engine output)
    stateWriter.Write(StateRow{ ... })           ← non-blocking; drops on backpressure
```

Concurrency: only one aggregator goroutine per symbol. The `flow5min` and `pinFlow` maps are cloned (copy + RLock release) before passing to `Aggregate`/`Pin` so the hot tick path doesn't block on the aggregator. (Yes — the map clone per tick is one of the deferred items in `docs/REVIEW.md`. Profiler hasn't flagged it; left as-is.)

## State publication contract

`publishState` writes a JSON `gexSnapshot` to `state.<sym>.gex` (see [`cmd/compute/main.go:584`](../../cmd/compute/main.go#L584)). The shape contains:

```
{
  ts_ns:           int64
  spot:            float64
  basis_smooth:    float64
  net_gex:         float64
  zero_gamma:      float64
  call_wall:       float64
  put_wall:        float64
  expected_mv:     float64
  regime:          int        ← Long | Neutral | Short
  charm_zone:      int        ← Weak | Rising | Peak | Fading | Pin
  charm_velocity:  float64
  dpi:             { composite, netGex, charm, vanna, ttc, flow }
  pulse:           { gamma, charm, vanna, total }
  pin:             { active, top_strike, top_prob, candidates }
  strikes:         [...]
}
```

The mirror REST shape is documented in [`docs/openapi.yaml`](../openapi.yaml) `StateSnapshot`.

## Test coverage map

| Test | Covers |
|---|---|
| `TestAggregate_SignsAndWalls` | NetGEX, ZeroGamma, walls in known synthetic chain |
| `TestAggregate_RegimeFromNetGEX` | Long / Neutral / Short flips |
| `TestDPIScore_DeterministicDecomp` | each component output for known inputs |
| `TestDPIScore_EWMAResponds` | smoothing converges across calls |
| `TestCharmClock_ZoneTransitions` | WEAK→RISING→PEAK→FADING→PIN |
| `TestPinEngine_ActivatesInWindow` | activation gate (last 90 min only) |
| `TestPinEngine_ProbsSumToOne` | softmax normalisation |
| `TestSimulate_SignConvention` | `ForcedNotional > 0` ⇒ buy; `< 0` ⇒ sell |
| `TestSimulate_NetPressureSubtractsCharmAid` | regression for the deep-review sign fix |
| `TestClassifier_TickTestFallback` | mid-price tie → tick test resolves |
| `TestClassifier_TwoGenerationReset` | cap-hit doesn't wipe history |
| `TestPositions_ApplyMirrorsAggressor` | BUY → -contracts, SELL → +contracts |
| `TestBasisTracker_EWMASmoothing` | smoothing math |
| `internal/dealer/integration_test.go` | end-to-end synthetic chain → full M2+M3 outputs |

Plus benchmarks: `BenchmarkAggregate200`, `BenchmarkClassify`, `BenchmarkPositionApply`, `BenchmarkBasisUpdate`.

## What this section does **not** cover

- Compute binary lifecycle / per-symbol Pipeline orchestration → see [`01-data-pipeline.md`](01-data-pipeline.md).
- Black-Scholes / IV solver internals → see [`03-math-pipeline.md`](03-math-pipeline.md).
- Replay / backtest consumption of `dealer_state_1s` → see [`05-time-machine.md`](05-time-machine.md).
- Narrative engine that turns these numbers into prose → see [`06-alerts-engine.md`](06-alerts-engine.md).
