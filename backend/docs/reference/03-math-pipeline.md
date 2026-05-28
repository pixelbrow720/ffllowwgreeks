# 03 — Math pipeline

> Validated against commit `3e5b0ec`.
> Source: [`internal/greeks/`](../../internal/greeks/) — pricing.go, solver.go, normal.go, types.go, greeks.go.

## Scope

The greeks package is the math kernel: Black-Scholes pricing, implied vol solving, analytical Greeks. Nothing here knows about dealers — that's `internal/dealer/`. Nothing here allocates — every function is goroutine-safe and zero-alloc on the steady-state path.

## Conventions

| Symbol | Meaning |
|---|---|
| `spot` | underlying price (USD) |
| `strike` | option strike (USD) |
| `t` | years to expiry; use `TimeToExpiryYears(tsEvent, expiryYYYYMMDD)` to derive |
| `r` | continuously-compounded risk-free rate (e.g. 0.045 = 4.5%) |
| `q` | continuous dividend yield (e.g. 0.013 for SPX) |
| `sigma` | annualized vol (e.g. 0.18) |
| `side` | `feed.SideCall` or `feed.SidePut` |

`SecondsPerYear = 365.25 * 86400.0` (calendar year). `TimeToExpiryYears` cuts off at 16:00 ET (PM-settled SPX). The America/New_York Location is cached at package init ([`types.go:87`](../../internal/greeks/types.go#L87)) so per-tick `time.LoadLocation` doesn't dominate the hot path.

## BS pricing

[`pricing.go:12`](../../internal/greeks/pricing.go#L12):

```
        ln(S/K) + (r - q + ½σ²)·T
d1 = ───────────────────────────────
              σ·√T

d2 = d1 - σ·√T

dfQ = exp(-q·T)        dfR = exp(-r·T)

call =  S·dfQ·Φ(d1) - K·dfR·Φ(d2)
put  =  K·dfR·Φ(-d2) - S·dfQ·Φ(-d1)
```

Where `Φ` is the standard normal CDF. Returns 0 on invalid inputs (`T ≤ 0`, `σ ≤ 0`, `spot ≤ 0`, `strike ≤ 0`). Cost: ~105 ns on amd64.

`Φ(x)` uses the `math.Erf` identity ([`normal.go:29`](../../internal/greeks/normal.go#L29)):

```
Φ(x) = ½(1 + erf(x/√2))
```

with input clamped to `[-8, 8]` to dodge IEEE-754 subnormal anomalies — `Φ(8) ≈ 1 - 6e-16`, well below the precision of any downstream calculation.

## IV solver — Brent + warm start

[`solver.go:15`](../../internal/greeks/solver.go#L15) implements Brent's method on the bracket `[VolMin, VolMax]` (default `[0.001, 5.0]`).

Default config:

```go
DefaultSolverConfig = SolverConfig{
    Tolerance: 1e-5,
    MaxIter:   50,
    VolMin:    0.001,
    VolMax:    5.0,
    InitGuess: 0.20,
}
```

```
ImpliedVol(mid, spot, strike, t, r, q, side, cfg)
       │
       ▼
  guard inputs (return Reason if invalid)
       │
       ▼
  f(σ) = BS(...) - mid
  fa = f(VolMin), fb = f(VolMax)
       │
       │ if fa*fb > 0 (same sign at both ends):
       │   widen ONCE:
       │     fa>0, fb>0 → true σ < VolMin → set a = VolMin/10 (floor 1e-6)
       │     fa<0, fb<0 → true σ > VolMax → set b = VolMax*2  (cap 10)
       │   recompute fa or fb
       │   if STILL same sign → return "no bracket"
       │
       ▼
  warm-start tightening:
     if cfg.InitGuess in (a, b):
        fg = f(InitGuess)
        if fg == 0 → return Converged at InitGuess
        if fg same-sign as fa → tighten a = InitGuess
        if fg same-sign as fb → tighten b = InitGuess
       │
       ▼
  Brent loop (Numerical Recipes 3e §9.3):
     prefer inverse-quadratic interpolation
     fall back to secant when degenerate
     fall back to bisection when interpolation steps out of bounds
     accept when |fb| < tol
       │
       ▼
  IVResult{ IV, Iterations, Converged, Reason }
```

The widening branch is the deep-OTM 0DTE rescue ([`solver.go:38-58`](../../internal/greeks/solver.go#L38)) — without it, far OTM strikes with σ > 5.0 silently dropped out of the snapshot. `TestImpliedVol_HighVolAutoWiden` proves the widened bracket converges.

Bench (per call): ~1.03 µs cold, faster with warm start. `BenchmarkImpliedVol_WarmStart` covers it.

### Warm-start cache (in compute, not greeks)

`cmd/compute/main.go` keeps a `map[ivKey]float64` per pipeline. On each quote:

1. Lookup last solved IV for `(expiry, strike, side)` under `RLock`
2. If present and > 0, set `cfg.InitGuess = last`
3. Solve
4. On success, write back under `Lock`

This is what brings the per-tick solver cost from ~1µs cold to ~few-hundred-ns amortised. Citation: [`cmd/compute/main.go:277-289`](../../cmd/compute/main.go#L277).

## Greeks bundle

[`greeks.go`](../../internal/greeks/greeks.go) `All(spot, strike, t, r, q, sigma, side) Greeks` shares `d1`, `d2`, `Φ(d1)`, `φ(d1)` across formulas in one analytical pass:

```
                  d1 = (ln(S/K) + (r - q + ½σ²)·T) / (σ·√T)
                  d2 = d1 - σ·√T
                  φ  = standard-normal pdf
                  Φ  = standard-normal cdf

  Δ_call =  e^(-qT) · Φ(d1)
  Δ_put  = -e^(-qT) · Φ(-d1)

  Γ      =  e^(-qT) · φ(d1) / (S·σ·√T)             [side-independent]

  Vega   =  S · e^(-qT) · φ(d1) · √T               [per 1 vol pt; /100 already applied]

  Θ_call = -S·e^(-qT)·φ(d1)·σ/(2√T)
           - r·K·e^(-rT)·Φ(d2) + q·S·e^(-qT)·Φ(d1)
  Θ_put  = -S·e^(-qT)·φ(d1)·σ/(2√T)
           + r·K·e^(-rT)·Φ(-d2) - q·S·e^(-qT)·Φ(-d1)

  Charm  = ∂Δ/∂t  (closed form — see greeks.go)
  Vanna  = ∂Δ/∂σ  (closed form — see greeks.go)
```

All Greeks are **per-contract** (multiplier applied later in `internal/dealer/`).

Bench: full bundle in 259 ns. `BenchmarkAll`.

## Latency anatomy of a quote tick

```
Tick arrives at compute.handleTick
     │
     ▼
   t.IsOption() ? Yes
   t.TickType == TickTypeQuote ? Yes
     │
     ▼
   Pipeline.quotes.Update(t)              ← ~tens of ns; cmps a fixed-size cache
     │
     ▼
   mid = (Bid+Ask)/2  (skip if ≤ 0)
     │
     ▼
   years = TimeToExpiryYears(...)         ← ns; nyLoc is cached
     │
     ▼
   spot = pipelineSpot(p)                 ← reads atomic; ns
     │
     ▼
   ivCache lookup (RLock)                 ← ns
     │
     ▼
   ImpliedVol(...)                        ← ~hundreds of ns warm, ~1µs cold
     │
     ▼
   ivCache write back (Lock)              ← ns
```

Total per-quote cost: ~500 ns – 1.5 µs depending on warm-start hit rate. Stays well within the 30ms compute budget.

## Edge-case behaviour catalog

| Input | Behaviour |
|---|---|
| `T ≤ 0` (past expiry) | `BS` returns 0, `ImpliedVol` returns `Reason: "invalid inputs"` |
| `σ ≤ 0` | `BS` returns 0 |
| `spot` or `strike` ≤ 0 | `BS` returns 0 |
| Unknown side | `BS` returns 0; `ImpliedVol` returns `Reason: "unknown side"` |
| `mid` higher than σ=10 BS | `Reason: "no bracket"` after widen |
| `mid` lower than σ=1e-6 BS | same — but virtually impossible for liquid quotes |
| Bracket inverted (`VolMin >= VolMax`) | `Reason: "invalid bracket"` |
| Iteration cap hit before tol | `Reason: "max iter"` |
| `InitGuess` exactly bracket boundary | falls through to plain Brent (no warm-start narrowing) |
| `InitGuess == 0` (no warm start) | falls through to plain Brent at `cfg.InitGuess = 0.20` default |

`IVResult.Reason` is the diagnostic string — preserve it in logs when convergence fails so the operator can spot whether the issue is data quality (no bracket) vs solver tuning (max iter).

## Test coverage map

| Test | Covers |
|---|---|
| `TestBS_Identity` | put-call parity at multiple `(S, K, σ, T)` points |
| `TestImpliedVol_RoundTrip` | price-then-solve recovers σ to 1e-4 across 8 scenarios × 5 vols |
| `TestImpliedVol_WarmStart` | warm config converges in fewer iterations than cold |
| `TestImpliedVol_NoBracket` | mid above maximum producible price |
| `TestImpliedVol_HighVolAutoWiden` | σ=6 (above default VolMax=5) converges via auto-widen |
| `TestImpliedVol_InvalidInputs` | each guard clause |
| `TestAll_GreeksMatchAnalytical` | bundle vs hand-derived reference |
| `BenchmarkBS`, `BenchmarkImpliedVol`, `BenchmarkImpliedVol_WarmStart`, `BenchmarkAll` | latency targets |

All in `internal/greeks/*_test.go`.

## What this section does **not** cover

- Aggregation across the chain (NetGEX, walls, Charm Clock, Pin) — see [`04-dealer-model.md`](04-dealer-model.md).
- The compute binary's per-tick orchestration (Pipeline, IV cache, aggregator) — see [`01-data-pipeline.md`](01-data-pipeline.md) §5–6.
