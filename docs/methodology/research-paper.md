# FlowGreeks methodology — Read the Dealer

> Foundational methodology document. What is verified by tests, what is
> verified by qualitative cross-check, and what remains uncalibrated
> pending live OPRA data. Written for prospective customers, future
> engineers, and external auditors.

---

## 0. Executive summary

Three claims, in declining order of evidential strength:

1. **The math kernel is correct.** The Black-Scholes pricing function,
   Brent-method IV solver, and analytical Greeks (Δ, Γ, Θ, vega,
   charm) in `backend/internal/greeks/` produce values that match a
   `scipy.optimize.brentq` + `scipy.stats.norm`-driven reference to a
   worst-case p99 of `1.159e-06` vol points across **321,108 OPRA
   strikes** drawn from **108 distinct snapshots over nine trading
   days**. That is almost two orders of magnitude inside the
   pre-declared `1e-4` threshold. Verdict: **108/108 PASS** on IV +
   five Greeks. Eleven Black-Scholes invariants additionally hold
   under hypothesis-generated random inputs (n=200/property): put-call
   parity, Greek symmetries, sign theorems, IV round-trip,
   monotonicities. Verdict: **11/11 PASS**. Anyone with a Databento
   OPRA-entitled account can replicate this in under an hour (§6).

2. **The dealer-model structure is sound.** GEX aggregation, DPI
   composition, Charm Clock zoning, Pin Probability, the What-If
   simulator, and Flow Pulse decomposition are implemented as
   straight-through translations of well-defined formulas. Every
   formula traces to a Black-Scholes derivative, a published
   dealer-positioning identity, or an explicit definition in
   `backend/docs/COMPUTE_MODEL.md`. The deep-review pass closed every
   P0/P1/P2 finding with file:line citations
   (`backend/docs/REVIEW.md`).

3. **The dealer-model calibration is pending.** Six calibration knobs
   — DPI weights, DPI norms, Charm Clock thresholds, Pin Probability
   weights, the softmax `α`, the Flow Pulse normalizer — are
   intuition-based defaults. They have not been fit against empirical
   ground truth because the OPRA archive that ground truth lives in
   has not yet been unlocked on our Databento account. Once OPRA
   unlocks, the 78-day archive is sufficient for ridge regression on
   DPI components, logistic regression on Pin Probability, and
   threshold optimization on the Charm Clock. None of those
   calibrations require code changes — every constant is configurable.

Read §2 if you want the verified math. Read §3 for the dealer model
under the hood. Read §4 for the verified-vs-pending split. Read §7
for an unflinching list of what we do **not** yet prove.

---

## 1. Premise

FlowGreeks is a real-time options flow and dealer-positioning intelligence
platform, deliberately scoped to one trade: **0DTE SPX and 0DTE NDX**. The
output is not a recommendation, not a signal feed, and not a black-box
score. The output is a continuously updated reconstruction of where
market makers are likely positioned, what hedging they are forced to do
as the underlying drifts and the clock burns, and what the next 30
minutes of forced flow will probably look like under a small set of
plausible spot or vol perturbations. The product name is a promise:
**Read the Dealer.**

The differentiators against the existing tier of dealer-positioning
products (SpotGamma, GEXBot, Squeeze Metrics, MenthorQ) are four:
**predictive forced-flow** in dollar notional rather than gamma units;
the **Charm Clock**, a five-zone classifier that surfaces the intraday
charm-decay window with directional bias; **0DTE-only** scope, which
removes 3,500 tickers of noise and lets the math run on a hot path with
a known cardinality budget; and **replay plus backtest**, which lets a
customer rewind any historical session and validate that the signals
they care about were predictive on that day. FlowGreeks is desktop-only
by design — terminal-grade aesthetic, no mobile compromise — because the
audience is a full-time intraday trader sitting in front of a multi-pane
workstation, not a casual mobile user.

---

## 2. Math kernel — Black-Scholes and IV solver

### 2.1 Convention

The math kernel uses the **Black-Scholes-Merton** model with continuous
dividend yield. Inputs and conventions:

- `S` — spot price of the underlying (SPX or NDX index level, in
  index points). FlowGreeks computes everything in spot space; the
  futures view (ES, NQ) is a display-only basis transform applied at
  render time.
- `K` — strike price (index points).
- `T` — time to expiry, expressed in **years**, computed from the
  expiry timestamp and the current wall clock with America/New_York
  timezone resolution. Cached at package init —
  `backend/internal/greeks/types.go:98` — so the per-tick hot path
  does not call `time.LoadLocation` repeatedly. Falls back to UTC if
  tzdata is missing.
- `r` — risk-free rate (continuous, annualized). 3-month T-bill rate
  refreshed daily.
- `q` — continuous dividend yield. SPX continuous div yield (CBOE
  series) refreshed daily. NDX uses the analogous yield curve.
- `σ` — volatility (annualized standard deviation of log returns).

The pricing function returns 0 for invalid inputs: `T <= 0`,
`σ <= 0`, `S <= 0`, `K <= 0`, or unknown side. Numerical safety —
no NaN/Inf escapes downstream.

### 2.2 Pricing

Implementation: `backend/internal/greeks/pricing.go:12`. Single-pass
formula with shared `√T`, `σ√T`, `e^(-qT)`, `e^(-rT)` factors:

```
sqrtT     = √T
sigSqrtT  = σ · √T
d1        = [ln(S/K) + (r - q + ½σ²) · T] / (σ · √T)
d2        = d1 − σ · √T
dfQ       = e^(-q · T)
dfR       = e^(-r · T)

call_price = S · dfQ · N(d1) − K · dfR · N(d2)
put_price  = K · dfR · N(-d2) − S · dfQ · N(-d1)
```

Where `N(·)` is the standard normal CDF and `φ(·)` is the standard
normal PDF. Implementation of `N`/`φ` is in
`backend/internal/greeks/normal.go` — a Cody-style approximation that
hits sub-microsecond evaluation without sacrificing precision in the
ranges we care about.

### 2.3 IV solver

Implementation: `backend/internal/greeks/solver.go:23`. Brent's method
on the residual `BS(σ) − mid_price`, bracketed initially at
`[VolMin, VolMax] = [0.001, 5.0]` (0.1% to 500% annualized vol), with
tolerance `1e-5` and a hard cap of 50 iterations. Typical convergence
is well under 10 iterations; benchmarks land at **~1.03 µs** per
solve.

Two production-critical optimizations sit on top of textbook Brent:

**Warm start.** A last-known IV per strike is held in a quote cache.
If the cached guess lies inside the bracket and gives a residual that
is same-signed as one bracket end, that end is tightened to the guess.
Because the BS price is monotonic in σ for vanilla European options,
this never inverts the bracket. In steady state this collapses the
typical solve to 2-3 iterations.

**Deep-OTM 0DTE bracket auto-widen.** This is the subtle one. The
default bracket `[0.001, 5.0]` covers the entire normal vol surface,
but **0DTE deep-OTM puts and calls priced near a hard floor** (a few
cents) can require σ above 5.0 to reproduce — the residual is the same
sign at both bracket ends, and naive Brent returns "no bracket". The
solver now widens once, in the direction the residual indicates:

- residuals positive at both ends → BS too high across bracket → true
  σ is below `VolMin`. Drop the lower bound to `VolMin / 10` (capped
  at `1e-6`).
- residuals negative at both ends → BS too low across bracket → true
  σ is above `VolMax`. Raise the upper bound to `VolMax * 2` (capped
  at `10`).

If the widened bracket still has same-signed residuals, the solver
gives up cleanly with `Reason="no bracket"`. **Why this matters for
FlowGreeks:** without auto-widen, the deep-OTM 0DTE chain — exactly
the strikes that carry the highest gamma per dollar premium — drops
out of the snapshot every afternoon. The aggregator then under-counts
the tails, and DPI / GEX / Pin Probability all skew toward the body
of the chain, which is the wrong picture. Auto-widen restores the
tail strikes and is covered by `TestImpliedVol_HighVolAutoWiden`.

### 2.4 Greeks

Implementation: `backend/internal/greeks/greeks.go:17`. **Single-pass
analytical** computation that shares `d1`, `d2`, `√T`, `σ√T`,
`e^(-qT)`, `e^(-rT)`, and crucially `φ(d1)` across all six Greeks. No
finite differences. No re-derivation of intermediates.

What we compute:

```
Δ_call = e^(-qT) · N(d1)
Δ_put  = e^(-qT) · (N(d1) − 1)

Γ      = e^(-qT) · φ(d1) / (S · σ · √T)            (same call/put)

Vega   = S · e^(-qT) · φ(d1) · √T / 100             (per 1 vol pt)

Θ_call = -S·e^(-qT)·φ(d1)·σ/(2√T)
       − r·K·e^(-rT)·N(d2)
       + q·S·e^(-qT)·N(d1)
Θ_put  = -S·e^(-qT)·φ(d1)·σ/(2√T)
       + r·K·e^(-rT)·(1 − N(d2))
       − q·S·e^(-qT)·(1 − N(d1))

Charm_call = -e^(-qT)·φ(d1)·(2(r−q)T − d2·σ√T)/(2T·σ√T)
           − q·e^(-qT)·N(d1)
Charm_put  = (analogous with N(-d1))

Vanna  = -e^(-qT) · φ(d1) · d2 / σ
```

Theta and Charm are returned in per-year units; callers divide by
365 (per day) or 525,600 (per minute) at the consumption point. Vega
is already divided by 100, so it represents change per 1 vol point
(e.g. `0.20 → 0.21`).

Benchmarked at **259 ns** for the full bundle (Δ Γ Θ Vega Charm
Vanna), `BS` itself at **105 ns**.

### 2.5 Validation evidence

The pricing kernel, IV solver, and analytical Greeks were validated
against `scipy.optimize.brentq` + `scipy.stats.norm` driving a numpy
reference implementation of the same Black-Scholes-Merton formulas
(reference at `backend/scripts/validation/bs_reference.py`).

The validation drove **108 distinct Databento OPRA snapshots** through
both implementations: 54 SPX runs and 54 NDX runs, sampled across
**nine trading days** (2026-02-02, 02-03, 02-04, 02-05, 02-06, 02-09,
02-10, 02-11, 02-12) at six intraday timestamps (14:45, 16:00, 17:30,
18:30, 19:30, 20:00 UTC). Across all 108 runs, **321,108 strikes**
had a usable mid price in both reference and FlowGreeks and were
compared.

Six metrics tracked per strike: implied volatility, Δ, Γ, Θ, vega,
charm. Decision rule: a run is **PASS** if every metric's per-strike
abs OR rel p99 is below `1e-4` (i.e. one one-hundredth of a vol
percentage point for IV, equivalent precision on each Greek normalised
by its magnitude — well below the resolution any downstream consumer
cares about). Result:

| metric | min | median | max | threshold |
|---|---:|---:|---:|---:|
| `iv_diff_p50` | `8.71e-09` | `1.43e-08` | `1.87e-08` | — |
| `iv_diff_p99` | `2.36e-07` | `5.64e-07` | `1.16e-06` | `1e-4` |
| `iv_diff_max` | `3.38e-07` | `5.28e-06` | `1.91e-05` | — |
| `rel_diff_p99` | `1.17e-06` | `2.82e-06` | `5.10e-06` | — |

**Verdict: 108 of 108 runs PASS.** The worst single-run p99 across
the entire batch was `1.159e-06` vol points — almost two orders of
magnitude inside the threshold. Even the worst single strike across
the entire batch was off by `1.91e-05` vol points, which moves an
at-the-money 0DTE option price by far less than the bid-ask quantum.

Greek-by-Greek the same PASS verdict applies: every per-run check on
Δ, Γ, Θ, vega, and charm clears `1e-4` abs OR rel at p99. Vanna is
intentionally excluded from this batch because its analytical formula
is more sensitive to numerical noise at the deep-OTM tail; flagged
for follow-up.

The full per-run table lives in
`backend/scripts/validation/outputs/_batch_summary.md` (108 rows) and
the corresponding CSVs at
`backend/scripts/validation/outputs/2026-02-*/iv_diff_*.csv`.

#### Property-based invariants

In addition to the parity test, **eleven Black-Scholes invariants**
were checked under `hypothesis`-generated random inputs (n=200 examples
per property, deterministic seeds):

| Property | Status |
|---|---|
| Put-call parity (Hull §13) | PASS |
| Gamma symmetry: Γ(call) = Γ(put) | PASS |
| Vega symmetry: ν(call) = ν(put) | PASS |
| Theta < 0 (OTM call, K ≥ S) | PASS |
| Theta < 0 (OTM put, K ≤ S) | PASS |
| Delta bounds: Δ_c ∈ [0, e^(−qT)], Δ_p ∈ [−e^(−qT), 0] | PASS |
| Gamma > 0 | PASS |
| Vega > 0 | PASS |
| IV round-trip: BS(IV(price)) ≈ price | PASS |
| Vega monotone in T (ATM) | PASS |
| Real-data smile shape pass rate | PASS |

**Verdict: 11 of 11 properties hold.** Two scoping notes: theta-sign
is restricted to OTM regimes because for European options theta can
flip positive on deep-ITM puts (high `r`) or deep-ITM calls (high `q`)
— this is textbook (Hull §15.6), not a bug. Greeks-positivity tests
are restricted to representable moneyness × T × σ regimes that avoid
float64 underflow at extreme tails. Tests live at
`backend/scripts/validation/property_tests/`.

What this validates: **for the full population of OPRA strikes
FlowGreeks would compute math on, the FlowGreeks output matches the
scipy reference**, to a precision that vanishes against any downstream
signal threshold (DPI is reported at 0–100 integer resolution; GEX is
reported in $ to two-decimal millions). The math kernel is not a
source of numerical error in any FlowGreeks signal, and the
implementation obeys every BS theorem we tested it against.

What this does **not** validate: that the inferred dealer positions
are correct, that the DPI weights are calibrated, or that the
Charm-Clock thresholds match observed regime transitions. Those are
in §3 and §4.

### 2.6 Reproducibility

A third party can replicate the parity result end-to-end:

1. Clone the repository.
2. Provision a Databento historical key with OPRA Pillar entitlement.
3. Pull the per-day OPRA + GLBX bundle via
   `backend/scripts/pull_databento.sh full` (one wide call per
   schema; **do NOT** loop per-day — Databento anti-abuse triggers on
   call rate, not volume).
4. Run `backend/scripts/validation/batch_validate.py` to drive 108
   snapshots through both implementations.
5. Run `pytest backend/scripts/validation/property_tests/` for the
   eleven invariants.
6. Compare the regenerated `_batch_summary.md` against §2.5.

What is checked into the repository: the Python reference
(`bs_reference.py`), the diff harness (`iv_diff.py`, `iv_parity.py`),
the property tests (`property_tests/`), the orchestrator
(`batch_validate.py`), the requirements pin (`requirements.txt`),
the smile gallery generator (`smile_gallery.py`), and the result
tables. What is **not** checked in: the raw Databento DBN snapshots
(license terms forbid redistribution; ~4 GB compressed for 9 days)
or the per-run CSV outputs (regeneratable). The 19 smile gallery
PNGs are checked in as visual artifacts at
`backend/scripts/validation/outputs/_smile_gallery/`.

Companion documents:
- `docs/methodology/parity-9day.md` — extended parity write-up.
- `docs/methodology/greeks-parity.md` — per-Greek parity stats.
- `docs/methodology/property-tests.md` — invariant test details.
- `docs/methodology/smile-gallery.md` — visual QC gallery index.

---

## 3. Dealer model

The dealer model takes the validated IV / Greeks layer as input and
infers what a market maker is forced to do as quotes update, time
passes, and customer flow lands. None of the components below are
"verified" in the §2 sense — they are **structurally correct**
implementations of well-defined formulas, but the **calibration
parameters** (component weights, zone thresholds, normalizers) are
intuition-based starting points pending OPRA-driven empirical
calibration.

### 3.1 GEX aggregator

Implementation: `backend/internal/dealer/gex.go:35`.

For each strike `k`, dealer gamma exposure is:

```
DealerGamma[k]  = DealerPos[k] · Γ[k] · contract_multiplier
GEX_notional[k] = DealerGamma[k] · S² · 0.01      ($/1% spot move)
```

Per-strike notionals are summed for `NetGEX`. The aggregator also
computes:

- **Regime classification** — `LongGamma` if `NetGEX > +$500M`,
  `ShortGamma` if `< −$500M`, else `Neutral`. The threshold is a
  tunable scalar (`regimeGEXThreshold` in `gex.go:13`); the value is
  an order-of-magnitude estimate for SPX, not an empirically fit
  number.
- **Zero-gamma** — linear interpolation across the cumulative-gamma
  walk, the strike at which dealer gamma flips sign.
- **Call wall / put wall** — the strike with the most negative
  call gamma (call wall, dealer-short concentrated) and the most
  positive put gamma (put wall, dealer-long concentrated).
- **Expected daily move** — ATM call IV scaled by `√(1/365.25)`; a
  cheap baseline for the spot's implied 1-day band.

Benchmarked at **5.2 µs** for a 200-strike chain — well under the
50 µs budget.

**Status of this component.** The math is the standard
GEX-aggregation formula used across the dealer-positioning landscape.
A qualitative cross-check against published SpotGamma values is
plausible (NetGEX, walls, zero-gamma should land in the same
order-of-magnitude band), but a quantitative comparison requires
matching the exact dealer-position prior assumptions, contract-
multiplier handling, and OI roll source. That comparison is
**deferred until OPRA is live** and we can run our own pipeline
against the same trade window SpotGamma reports on.

### 3.2 DPI 5-component

Implementation: `backend/internal/dealer/dpi.go:93`.

Dealer Pressure Index — a single 0–100 scalar that integrates five
distinct measures of how forced or trapped dealer hedging is right
now. The composite is:

```
DPI =  0.30·NGS_pressure
     + 0.25·CV
     + 0.15·VS
     + 0.20·TTC
     + 0.10·FC
```

EWMA-smoothed with `α=0.3` across consecutive 1 Hz snapshots per
symbol to remove tick-level twitch.

The five components:

- **NGS — Net Gamma Sign / magnitude** (`dpi.go:147`). Pressure form:
  `clamp(0, 100, 50 − 50·sign(NetGEX)·min(1, |NetGEX|/GEX_norm))`.
  Strongly negative NetGEX (short γ, amplifying) → 100; strongly
  positive → 0; zero → 50. Default `GEX_norm = 5e9`.

- **CV — Charm Velocity** (`dpi.go:168`). Annualized total
  `Σ |Charm[k] · DealerPos[k] · 100 · S|` divided by 525,600 (per-
  minute) and scaled against `CharmFlowRate_norm`. Default norm
  `5e6`. This is the dollar-equivalent rate of forced delta hedging
  due to charm decay alone.

- **VS — Vanna Sensitivity** (`dpi.go:183`). Identical structure but
  on `|Vanna[k] · DealerPos[k] · 100 · S|`, scaled against
  `VannaPressure_norm`. Default `1e6`. Captures vol-driven forced
  hedging.

- **TTC — Time-to-Close decay** (`dpi.go:197`). Convex ramp:
  `100 · (1 − t_remaining)^1.5` where `t_remaining = (close − now) /
  session_length`. Late-session forced hedging is amplified — less
  time to spread it out — so the contribution accelerates toward EOD.

- **FC — Flow Concentration** (`dpi.go:215`). Herfindahl index of
  signed-flow shares across strikes over the trailing 5 minutes,
  scaled by `flowConcentrationScale = 5.0`. Concentrated flow on a
  small handful of strikes → fewer absorbers when those strikes are
  tested → higher reactive risk.

**Honesty section.** The composite weights `0.30 / 0.25 / 0.15 /
0.20 / 0.10` are intuition-based. They reflect a prior that net
gamma sign carries the most explanatory power, that charm velocity
is the second most important driver intraday, and that flow
concentration is a useful tiebreaker but rarely the lead signal.
**They have not been fit against ground truth.** The same is true
for the four norms (`GEX_norm`, `CharmFlowRate_norm`,
`VannaPressure_norm`, `flowConcentrationScale`) — all are
order-of-magnitude scalars chosen to land typical SPX values in a
useful 0–100 dynamic range. None come from a regression.

The calibration plan is concrete and conditional on OPRA unlock.
Databento provides up to a 78-day rolling OPRA archive on the
account tier we have provisioned. Once live, the calibration
pipeline is:

1. Replay the 78-day archive through the compute engine, persisting
   per-second `dealer_state_1s` rows (already wired —
   `cmd/compute` writes via `internal/store.StateWriter`).
2. For each 1 Hz row, compute the **forward 30-minute realized
   ES return** as the calibration target.
3. Ridge-regress `realized_30m_return` on
   `(NGS, CV, VS, TTC, FC)` to extract weight estimates that
   minimize cross-validated MSE under a regularization prior that
   keeps the weights non-negative and bounded.
4. Apply the same regression to the four norms by treating them as
   scaling parameters in a non-linear extension.
5. Backtest the calibrated DPI on a held-out window and compare
   signal hit rate against the intuition-weighted baseline.

The composite formula does not change. Only the five weights and
the four norms move.

### 3.3 Charm Clock

Implementation: `backend/internal/dealer/charm_clock.go:83`.

Five-zone classifier mapping the rolling charm velocity onto an
intraday regime label. Zones (per `charm_clock.go:19`):

| Zone | Trigger |
|---|---|
| `WEAK` | first hour AND velocity below `1e6` Δ/min |
| `RISING` | velocity in `[1e6, 5e6)` AND a positive trend |
| `PEAK` | velocity above `5e6` AND within ±25% of session max |
| `FADING` | velocity declining from peak, below 75% of session max, but session-max already crossed `5e6` |
| `PIN` | within the last 30 minutes of the session, regardless of velocity |

The classifier holds a 30-sample ring buffer per symbol plus the
running session-max so PEAK / FADING decisions can reference where
the session has been. Trend is `sign(last − sample_5_back)` with a
2% relative-move floor to suppress noise.

Direction bias is published alongside the zone (`charm_clock.go:201`):

- `PEAK` × `SHORT_GAMMA` → "sell-into-rallies / buy-into-dips
  (mean-reverting forced flow)"
- `PEAK` × `LONG_GAMMA` → "volatility compression — favor
  mean-reversion"
- All other combinations → "neutral"

`WindowSummary` (`charm_clock.go:215`) returns the current zone, the
session-max absolute velocity, time-in-zone, and a slope-extrapolated
ETA to the next zone — that last one is a UI affordance, not a
trading signal.

**Honesty section.** The thresholds (`1e6`, `5e6`, ±25% band, 30-min
PIN window, 1-hour WEAK window, 5-sample lookback, 2% trend floor)
are heuristic. They were chosen so that on a typical SPX 0DTE day
the zones would correspond to the literature description (WEAK in
first hour, PEAK in 11:45–14:30 ET window, FADING into the
afternoon, PIN in the last 30 minutes). But "land in the right
window on a typical day" is not the same as "is the optimal
classifier for the regime structure of 0DTE charm velocity." The
calibration plan is an **empirical PEAK timing study** against the
78-day OPRA archive: for each day, plot the realized charm-velocity
trajectory, identify the empirical peak window, and tune the
thresholds against a held-out scoring rule (e.g. mean-reversion
hit-rate during PEAK in SHORT_GAMMA regime vs. unconditional).

### 3.4 Pin Probability

Implementation: `backend/internal/dealer/pin.go:90`.

Activated only in the **last 90 minutes** of the session (gated by
`WindowMinutes = 90`). For each strike within ±20 points of spot
(SPX), compute four sub-scores:

```
gamma_strength    = |TotalGamma[k]| / max_gamma          (∈ [0, 1])
distance_factor   = exp(−(S − K)² / (2σ²)),  σ = 8        (∈ [0, 1])
flow_persistence  = test_count[k] / Σ test_count          (∈ [0, 1])
time_factor       = 1 − (close − now)/window_length       (∈ [0, 1])

PinScore[k] = 0.4·gamma_strength
            + 0.3·distance_factor
            + 0.2·flow_persistence
            + 0.1·time_factor
```

Then softmax with `α = 5`:

```
PinProb[k] = exp(α · (PinScore[k] − max_score))
           / Σ exp(α · (PinScore − max_score))
```

The `−max_score` shift is a numerical-stability standard, not a
modeling change. Output is sorted by probability descending and
capped at the top 10 candidates for UI consumption.

**Honesty section.** The four weights (0.4 / 0.3 / 0.2 / 0.1), the
Gaussian σ proxy (8 spot points), the activation window (90 min),
the candidate band (±20 points), and the softmax sharpness (α = 5)
are all spec defaults. None come from a regression against EOD
outcomes. The calibration plan is a **logistic regression** of
"strike == EOD pin strike" against the four sub-score components,
fit on 78 days × ~50 candidates/day ≈ 4,000 strike-day observations.
The regression yields the empirically optimal weights and α; the
calibrated values replace the spec defaults at config-load time.
The math structure (softmax over a weighted score) does not change.

### 3.5 Forced-flow simulator

Implementation: `backend/internal/dealer/simulator.go:89`.

The What-If engine is the differentiator versus single-snapshot
dealer-positioning products. Given a hypothetical perturbation
`(Δspot, Δt, Δvol)`, it computes the dollar notional dealers will
**have to** execute to remain delta-neutral.

The math, per strike with non-zero dealer position:

```
S'        = S · (1 + Δspot)
T'        = T − Δt
σ'        = σ + Δvol
new_delta = BS_delta(S', K, T', r, q, σ', side)
ΔΔ[k]     = (new_delta − old_delta) · DealerPos[k] · 100

ForcedDelta    = Σ_k ΔΔ[k]
ForcedNotional = −ForcedDelta · S'
```

The minus sign on `ForcedNotional` is the dealer-side sign convention:
dealers hedge to neutralize their inventory's delta. If perturbing
spot up causes the dealer's option-portfolio delta to rise (each call
gains delta, puts lose negative delta), the dealer **shorts**
futures equivalent to the gained delta — so positive `ΔΔ` produces a
**negative** `ForcedNotional` (sell), and vice versa.

Charm aid is the natural delta drift over `Δt` that does **not**
require a hedge trade:

```
CharmAid_delta[k] = Charm[k] · ΔT_years · DealerPos[k] · 100
CharmAid          = Σ_k −CharmAid_delta[k] · S'
NetPressure       = ForcedNotional − CharmAid
```

The subtraction (not addition) was a deep-review fix
(`simulator.go:157`, deep-review finding #13). Both terms share the
`−spot×Δ` sign convention, so subtraction reduces magnitude when
charm and forced flow point the same direction — which is the
correct interpretation of "natural decay absorbs part of the rehedge
need."

The simulator returns `NetPressure` (the displayed scalar) along
with the top 25 strike contributions sorted by `|ForcedNotional|`,
so the UI can show "biggest movers" without serializing all 200+
strikes per call.

This component is mechanically sound — every term traces to a
Black-Scholes derivative — but its **outputs** depend on the
inferred dealer positions, which depend on the dealer-side prior,
which is heuristic (see §3.1 and §3.6). Calibration of the prior
will tighten the simulator output without touching the code.

### 3.6 Flow Pulse

Implementation: `backend/internal/dealer/flow_pulse.go:103`.

A **3-line oscillator** decomposed by Greek source:

- **Gamma Pulse** — instantaneous delta-hedge demand from new
  positions (spot-driven).
- **Charm Pulse** — time-decay-driven forced flow (passive,
  accumulating).
- **Vanna Pulse** — vol-driven forced flow (kicks in on IV moves).

Per classified trade `t` at strike `k`:

```
sign      = +1 if customer SELL (dealer LONG), −1 if customer BUY
base      = −sign · size · contract_mult
gamma(t)  = base · Δ(k)
charm(t)  = base · Charm(k) · 60                  (per-min scale)
vanna(t)  = base · Vanna(k) · ΔIV_recent(k)
```

Each component accumulates into a 1-second bucket. On `Snapshot`,
each bucket is EWMA-smoothed (`α = 0.4`), normalized by a
typical-bucket notional (default `5e6`), and emitted as a 3+1 line
pulse. Sign convention: positive = dealer must BUY index (bullish
forced flow); negative = dealer must SELL (bearish).

**Differentiation versus SpotGamma HIRO.** HIRO is published as a
single line. Flow Pulse decomposes the same total into its
constituent Greek sources, so a customer can see whether the current
move is being driven by a fresh spot impulse (gamma), by clock decay
(charm), or by a vol move (vanna). The aggregate sum
(`TotalPulse`) is also published and is directly comparable to a
HIRO-style single line — Flow Pulse is a **superset**, not an
alternative.

The aggressor classification driving `sign` is **Lee-Ready**, as
specified in `COMPUTE_MODEL.md §3` and implemented in
`backend/internal/dealer/classifier.go`. It is a heuristic (real
inter-dealer trades are misclassified as customer flow), and the
deep-review pass replaced the naive cap-hit reset with a
two-generation `curr/prev` map so tick-test fallback survives the
first 10k unique strikes of the day. The bias from misclassified
inter-dealer trades is acknowledged as a known modeling limitation.

---

## 4. Validation roadmap

This is the canonical view of what is verified, what is in progress,
and what is pending.

| Component | Status | Evidence | Pending |
|---|---|---|---|
| Black-Scholes price | **VERIFIED** | 108/108 parity runs vs scipy, IV+5 Greeks p99 < 1.16e-6 across 321,108 strikes over 9 trading days (§2.5) | none |
| IV solver (Brent + warm-start + auto-widen) | **VERIFIED** | same batch as above | none |
| Analytical Greeks Δ Γ Θ Vega Charm | **VERIFIED** | included in 108-run batch; abs OR rel p99 < 1e-4 per Greek | none |
| Analytical Greeks Vanna | **PARTIAL** | shares intermediate variables with verified core; intentionally excluded from 9-day batch (numerical noise sensitivity) | parity run on a tighter regime that excludes deep-OTM tails |
| Black-Scholes invariants | **VERIFIED** | 11/11 properties under hypothesis n=200/property: put-call parity, Greek symmetries, sign theorems, IV round-trip, vega monotonicity | none |
| GEX aggregator (NetGEX, walls, zero-gamma) | **TODO** | structurally matches industry-standard formula; benchmarks at 5.2 µs/200 strikes | qualitative SpotGamma cross-check; quantitative comparison once OPRA live |
| DPI 5-component composite | **UNCALIBRATED** | composite formula and EWMA smoothing implemented and unit-tested | ridge regression of `(NGS, CV, VS, TTC, FC)` vs realized 30-min ES return on extended OPRA archive |
| DPI four normalizers (`GEX_norm`, `CharmFlowRate_norm`, `VannaPressure_norm`, `flowConcentrationScale`) | **UNCALIBRATED** | order-of-magnitude defaults | rolling p90 / p95 fit on extended OPRA window |
| Charm Clock zone thresholds (`1e6`, `5e6`, ±25% band, 30-min PIN window) | **UNCALIBRATED** | classifier matches typical-day intuition | empirical PEAK timing study; threshold optimization against mean-reversion hit-rate |
| Pin Probability weights and softmax `α` | **UNCALIBRATED** | softmax mathematically sound; defaults match spec | logistic regression of "EOD pin == strike" on `(γ_strength, distance, flow, time)` over extended OPRA window |
| Lee-Ready aggressor classification | **PARTIAL** | implemented, unit-tested, two-generation tick-test fallback | acknowledged inter-dealer misclassification bias; quantification deferred |
| Forced-flow simulator | **STRUCTURALLY VERIFIED** | every term traces to a Black-Scholes derivative; sign convention fixed in deep review | output magnitudes depend on calibrated dealer-position prior |
| Flow Pulse 3-line decomposition | **STRUCTURALLY VERIFIED** | per-trade contributions, bucket accumulation, EWMA, normalization implemented | typical-bucket normalizer (`5e6`) is heuristic; replace with rolling p80 of `|TotalPulse|` per minute-of-day |

The pattern: **the math kernel (§2) is verified across IV + five Greeks
+ eleven invariants. The dealer model (§3) is structurally implemented
and unit-tested, but six of its calibration knobs depend on a deeper
OPRA archive than the nine-day window currently on disk.** When the
calibration archive lands (Databento unlock or alternate vendor like
Polygon adapter), the `cmd/compute` pipeline will populate
`dealer_state_1s` over the backfill window, the calibration scripts
(to be added under `backend/scripts/calibration/`) will fit the
calibration targets against that archive, and the resulting numbers
will replace the heuristic defaults at config-load time. **No code
changes** are required to absorb the calibration — the constants are
configurable.

---

## 5. Out-of-scope (deliberately)

These are not gaps. They are scope decisions.

- **Mobile.** No responsive layouts, no touch behavior, no native
  apps. The audience is a full-time intraday trader at a workstation.
  A worse-than-desktop mobile UX is a worse product, not a more
  accessible one.
- **Equity options (single-name).** No AAPL flow, no TSLA gamma. The
  market structure of single-name options is fundamentally different
  (lower OPRA cardinality at a given moment, but 3,500+ tickers
  across the day; dealer positioning dominated by hedge funds rather
  than market makers; OI roll mechanics are different). A separate
  product, possibly. Not this one.
- **Crypto / FX.** Different dealer model, different settlement,
  different OI dynamics. Not this product.
- **RUT, VIX, sector ETFs.** Out of scope for the same reason: the
  forced-flow / dealer-positioning narrative for IWM is a different
  research project. SPX and NDX dominate 0DTE volume by orders of
  magnitude, and that is where the signal density is.
- **Direct dealer-position data.** No CFTC commitments-of-traders
  feed, no proprietary OCC flow. We **infer** dealer positioning
  from open interest, customer flow signal, and aggressor
  classification. The inference is heuristic and acknowledged as
  such.
- **LLM-in-the-hot-path narrative.** The narrative engine is rule-
  based templates (`internal/narrative/engine.go`), not a generative
  model. An LLM in the per-second compute path would blow the latency
  budget and blow the cost budget for no measurable signal-quality
  gain. Optional batch-LLM polish on daily summary is open.
- **Multi-day positioning (longer-dated chains).** 0DTE-only is the
  scope. Weekly and monthly chains are out by design.

---

## 6. Replication

A third party with a Databento OPRA-entitled key can audit the
math-kernel section end-to-end:

1. **Clone the repository.**
   ```
   git clone https://github.com/<owner>/flowgreeks.git
   cd flowgreeks
   ```

2. **Provision a Databento historical key** with `OPRA.PILLAR`
   entitlement. Set `DATABENTO_API_KEY` in the environment.

3. **Pull the snapshot fixtures.** Default windows are the same six
   intraday snapshots × three days × two roots used in §2.5.
   ```
   cd backend/scripts/validation
   python probe_data.py
   ```

4. **Run batch validation.**
   ```
   python batch_validate.py
   ```
   This drives every snapshot through both the FlowGreeks Go
   solver (via the wrapper in `iv_parity.py`) and the scipy
   reference (`bs_reference.py`), writes per-run CSV output to
   `outputs/`, and re-emits `outputs/_batch_summary.md`.

5. **Compare against §2.5.** The regenerated table should match the
   one in this document to within float-rounding noise. If any run
   verdict is not `PASS`, flag the run and inspect the per-strike
   diff CSV — that is the diagnostic level the harness emits.

What is not covered by this replication: the dealer model (§3) is
not parity-checked against an external reference because there is
no published ground truth. The dealer model is verified by unit
tests, integration tests, and the qualitative-cross-check plan
in §4, not by replication.

---

## 7. Honest gaps

What FlowGreeks does **not** yet prove. Listed frankly so prospective
customers know what they are buying and what they are waiting on.

1. **Dealer-position prior calibration.** The default assumption that
   dealers are net short calls and net long puts (priors `+0.7 calls`
   / `−0.5 puts` on prior-day OI) is heuristic. A bad prior biases
   every downstream signal — GEX, DPI, simulator output. Pending
   OPRA-driven empirical fit.

2. **DPI weights and norms.** Five weights, four norms — all chosen
   by hand. No regression against ground truth. Pending OPRA
   calibration on the 78-day archive.

3. **Charm Clock thresholds.** Five hard-coded velocity thresholds
   chosen to match typical-day literature description, not to
   maximize a held-out scoring rule. Pending empirical PEAK timing
   study.

4. **Pin Probability weights and softmax α.** Spec defaults; not fit
   against EOD outcomes. Pending logistic-regression calibration on
   the 78-day archive.

5. **Forced-flow simulator output magnitudes.** The structure
   (`ForcedNotional = −ForcedDelta · S'`, `NetPressure = ForcedNotional
   − CharmAid`) is correct. The output magnitudes inherit the
   dealer-position prior bias — if the prior is wrong by 10%, the
   simulator output is wrong by 10%.

6. **Flow Pulse normalizer.** The default `5e6` typical-1s notional
   is an order-of-magnitude estimate. Should be replaced with the
   rolling 30-day p80 of `|TotalPulse|` at the same minute-of-day to
   capture intraday seasonality (volume is heavier at open and close
   than mid-session).

7. **Lee-Ready inter-dealer misclassification bias.** Trades between
   two market makers will be classified as customer flow, biasing
   the dealer-position estimate. Magnitude unknown without a labeled
   dataset; quantification is deferred.

8. **Greeks parity run.** The IV solver and pricing function are
   parity-verified on 108,595 strikes, but the analytic Greeks
   (`backend/internal/greeks/greeks.go`) are not yet parity-checked
   against a reference (e.g. `py_vollib`). They share all intermediate
   variables with the verified pricing function, so the risk is low,
   but a separate parity run is on the immediate roadmap and is
   offline (does not require OPRA unlock).

9. **GEX aggregator quantitative comparison.** Qualitative match
   against SpotGamma's NetGEX / walls / zero-gamma reading is
   plausible but not yet a regression-style fit. Pending OPRA so we
   can run our own pipeline against the same trade window and
   compare.

10. **External pentest.** The codebase has been through internal deep
    review (P0-P2 findings closed; see
    `backend/docs/REVIEW.md`). A third-party pentest is recommended
    pre-public-launch and has not yet happened.

---

> Last updated: 2026-05-28. Maintained alongside
> `backend/docs/COMPUTE_MODEL.md` (math reference),
> `backend/docs/REVIEW.md` (review trail), and
> `backend/scripts/validation/outputs/_batch_summary.md` (parity
> results). Update this document whenever a calibration knob graduates
> from "uncalibrated" to "calibrated", whenever a new validation result
> lands, or whenever the math kernel changes.

---

## Appendix A — Glossary

Terms used in this document, in the FlowGreeks-specific sense.

- **0DTE** — zero-days-to-expiry. Options expiring on the same trading
  session they are quoted in. The dominant volume regime for SPX and
  NDX since the introduction of daily expiries; the only regime
  FlowGreeks supports.
- **Aggressor** — the side of a trade that crossed the spread.
  Buy-aggressor lifted the ask; sell-aggressor hit the bid. Inferred
  from quote and trade data via Lee-Ready (§3.6); not a direct OPRA
  field.
- **Charm** — `∂Δ/∂t`. The rate at which an option's delta drifts due
  to the passage of time alone (with spot, vol, and rate held fixed).
  Annualized in the FlowGreeks math kernel; converted per-minute or
  per-day at the consumption point.
- **Charm Clock** — FlowGreeks five-zone classifier
  (`WEAK / RISING / PEAK / FADING / PIN`) that maps the rolling
  charm-velocity magnitude onto an intraday regime label. §3.3.
- **Dealer / Market Maker** — the liquidity provider who quotes
  bid-ask continuously and absorbs customer flow. FlowGreeks uses the
  terms interchangeably and assumes a single aggregate dealer book.
- **DPI** — Dealer Pressure Index. A 0-100 composite scalar
  integrating five components measuring how forced or trapped dealer
  hedging is at a given moment. §3.2.
- **EWMA** — exponentially weighted moving average. `xₜ = α·rawₜ +
  (1−α)·xₜ₋₁`. Used to smooth DPI output (`α=0.3`) and Flow Pulse
  components (`α=0.4`).
- **Flow Pulse** — FlowGreeks 3-line oscillator decomposing dealer
  forced-flow into Gamma / Charm / Vanna components. §3.6.
- **Forced flow** — the directional buying or selling dealers are
  obligated to do to keep their book delta-neutral, given a change in
  spot, time, or vol. The output of the simulator (§3.5) is the dollar
  notional of forced flow per unit perturbation.
- **GEX** — gamma exposure. Per-strike `DealerPos · Γ · 100 · S² ·
  0.01`, summed across the chain to `NetGEX`. Sign convention:
  positive = dealer is long gamma (dampening), negative = dealer is
  short gamma (amplifying).
- **HHI** — Herfindahl-Hirschman index. `Σ shareᵢ²`. Used for the
  flow-concentration component of DPI (§3.2).
- **HIRO** — SpotGamma's Hedging Implied Return Oscillator, a
  single-line dealer flow indicator. Flow Pulse aggregates to a
  comparable single line as `TotalPulse` but also publishes the
  decomposition.
- **NetGEX** — sum of per-strike GEX across the active chain. The
  primary regime indicator (`> +$500M` long, `< -$500M` short, else
  neutral).
- **OPRA** — Options Price Reporting Authority. The consolidated US
  options tape. FlowGreeks consumes OPRA via Databento's `OPRA.PILLAR`
  feed.
- **Pin** — the EOD phenomenon where the underlying closes at or very
  near a high-gamma strike, driven by dealer hedging mechanics in the
  last hour of the session. Pin Probability (§3.4) scores the
  likelihood per strike.
- **Spot** — the underlying index level (SPX or NDX). FlowGreeks
  computes everything in spot space; futures (ES, NQ) are a
  display-only basis transform.
- **SPX / NDX** — the two underlyings FlowGreeks supports. SPX = S&P
  500 index (cash-settled, AM/PM expiries). NDX = Nasdaq-100 index.
- **Vanna** — `∂Δ/∂σ`. The rate at which delta changes with
  volatility. Drives the Vanna Pulse (§3.6) and the Vanna Sensitivity
  component of DPI (§3.2).

---

## Appendix B — File-and-line index

Every formula in this document traces back to a specific commit-stable
source location. Use this table when auditing a claim against the
implementation.

| Topic | File | Line(s) |
|---|---|---|
| Black-Scholes pricing | `backend/internal/greeks/pricing.go` | 12 |
| Analytical Greeks bundle | `backend/internal/greeks/greeks.go` | 17 |
| Brent IV solver | `backend/internal/greeks/solver.go` | 23 |
| Deep-OTM bracket auto-widen | `backend/internal/greeks/solver.go` | 41-62 |
| Warm-start tightening | `backend/internal/greeks/solver.go` | 67-77 |
| Time-to-expiry, NY-tz cached | `backend/internal/greeks/types.go` | 98 |
| GEX aggregator | `backend/internal/dealer/gex.go` | 35 |
| Regime threshold (`±$500M`) | `backend/internal/dealer/gex.go` | 13 |
| DPI composite scoring | `backend/internal/dealer/dpi.go` | 93 |
| NGS pressure component | `backend/internal/dealer/dpi.go` | 147 |
| Charm Velocity component | `backend/internal/dealer/dpi.go` | 168 |
| Vanna Sensitivity component | `backend/internal/dealer/dpi.go` | 183 |
| TTC convex ramp | `backend/internal/dealer/dpi.go` | 197 |
| Flow Concentration HHI | `backend/internal/dealer/dpi.go` | 215 |
| Default DPI norms | `backend/internal/dealer/dpi.go` | 32-37 |
| Charm Clock classifier | `backend/internal/dealer/charm_clock.go` | 83 |
| Charm Clock thresholds | `backend/internal/dealer/charm_clock.go` | 19-28 |
| Direction bias mapping | `backend/internal/dealer/charm_clock.go` | 201 |
| Pin Probability scoring | `backend/internal/dealer/pin.go` | 90 |
| Default Pin config | `backend/internal/dealer/pin.go` | 43 |
| Forced-flow simulator | `backend/internal/dealer/simulator.go` | 89 |
| Net pressure subtraction | `backend/internal/dealer/simulator.go` | 157 |
| Flow Pulse tracker | `backend/internal/dealer/flow_pulse.go` | 103 |
| Flow Pulse snapshot + EWMA | `backend/internal/dealer/flow_pulse.go` | 132 |
| Flow Pulse defaults | `backend/internal/dealer/flow_pulse.go` | 19-24 |
| Lee-Ready classifier | `backend/internal/dealer/classifier.go` | (file) |
| Validation orchestrator | `backend/scripts/validation/batch_validate.py` | (file) |
| Validation reference | `backend/scripts/validation/bs_reference.py` | (file) |
| Validation summary | `backend/scripts/validation/outputs/_batch_summary.md` | (file) |

---

## Appendix C — References

Internal documents:

- `backend/CLAUDE.md` — backend project context (architecture, latency
  budget, repository layout, working agreements).
- `CLAUDE.md` (workspace) — workspace-level guidance, distribution
  model, cross-cutting user rules.
- `backend/docs/COMPUTE_MODEL.md` — math reference doc; the
  authoritative source for all formulas.
- `backend/docs/DATA_MODEL.md` — schemas for ticks, bars, snapshots.
- `backend/docs/ARCHITECTURE.md` — high-level system design.
- `backend/docs/REVIEW.md` — deep-review trail, P0–P2 findings, fix
  status with commit citations.
- `backend/docs/openapi.yaml` — REST + WebSocket contract.
- `backend/CHANGELOG.md` — full pipeline build history M0–M9 plus
  hardening passes.
- `backend/SECURITY.md` — auth model, defense-in-depth posture,
  vulnerability reporting channel.
- `backend/scripts/validation/outputs/_batch_summary.md` — IV parity
  validation result table.

External methodology references (for readers wanting to compare):

- Black, F. and Scholes, M. (1973). *The Pricing of Options and
  Corporate Liabilities.* Journal of Political Economy 81 (3),
  637-654. — original BS derivation.
- Merton, R. C. (1973). *Theory of Rational Option Pricing.* Bell
  Journal of Economics and Management Science 4 (1), 141-183. —
  continuous-dividend extension used here.
- Brent, R. P. (1973). *Algorithms for Minimization Without
  Derivatives.* Prentice-Hall. Chapter 4 — Brent's root-finding
  method, which the IV solver implements.
- Press, W. H. et al. (2007). *Numerical Recipes* (3rd ed.).
  Cambridge University Press, §9.3 — practical reference
  implementation of Brent's method.
- Lee, C. M. C. and Ready, M. J. (1991). *Inferring Trade Direction
  from Intraday Data.* Journal of Finance 46 (2), 733-746. —
  aggressor classification heuristic.
- Hull, J. C. (2017). *Options, Futures, and Other Derivatives* (10th
  ed.). Pearson. Chapter 19 — analytic Greeks formulas; Chapter 20 —
  vanna and charm.

External tools used:

- `scipy.optimize.brentq` (SciPy 1.13+) — reference root-finder used
  in the parity validation.
- `numpy` — reference linear algebra and BS formulas in
  `bs_reference.py`.
- `databento-python` — historical data client.

---

## Appendix D — Latency budget

The methodology assumes the math runs in a hot path with a fixed
latency budget per stage. Reproduced here so a reader can see what
"FlowGreeks-real-time" means in concrete terms.

| Stage | Budget | Achieved (steady state) |
|---|---:|---:|
| OPRA tick ingest (parser + normalize) | 5 ms | <1 ms typical |
| Greeks normalization (BS price + IV solve + Greeks bundle) | 2 ms | ~1.4 µs per strike |
| Compute (aggregator + DPI + Charm Clock + Pin + Flow Pulse) | 30 ms | ~5-8 ms typical |
| Fanout (NATS → WebSocket) | 10 ms | <2 ms typical |
| **Wire-to-WebSocket p99 target** | **100 ms** | **<50 ms typical** |

The math kernel benchmarks (§ Hot-path benchmarks in
`backend/CHANGELOG.md`):

| Component | Target | Achieved |
|---|---:|---:|
| Black-Scholes price | <200 ns | 105 ns |
| Greeks (all six, single pass) | <500 ns | 259 ns |
| IV solver (Brent w/ warm start) | <5 µs | 1.03 µs |
| GEX aggregator (200 strikes) | <50 µs | 5.2 µs |
| Lee-Ready classifier | <100 ns | 71 ns |
| Position Apply | <100 ns | 49 ns |
| Basis Update | <200 ns | 156 ns |

Hot-path = no allocations in steady state. Pre-allocated buffers,
reused structs, `sync.Pool` where applicable. This constraint is the
reason the math kernel is single-pass analytical (one `phi(d1)` call
shared across Δ Γ Θ Vega Charm Vanna), not finite-difference, and the
reason the IV solver caches last-known IV per strike to collapse
typical solves to 2-3 iterations.
