# Compute Model

> Math and signal definitions for FlowGreeks. The proprietary core of the product. Read after [DATA_MODEL.md](DATA_MODEL.md).

This document specifies WHAT we compute and HOW. Implementation goes in `internal/greeks/` and `internal/dealer/`.

## 1. IV solver

For each option strike with a fresh quote, solve for implied volatility from mid price.

**Method:** Black-Scholes for SPX (European, cash-settled, no dividends on index level — we use SPX's continuous div yield from CBOE).

**Solver:** Brent's method on the BS pricing function. Bracket [0.001, 5.0] (0.1% to 500% vol). Tolerance 1e-5. Cap at 50 iterations (typically converges in <10).

**Optimization:**
- Cache last-known IV per strike, use as initial guess
- Skip strikes >5σ from spot (deep ITM/OTM, no signal value)
- Skip if bid-ask spread > 30% of mid (no usable price)

**Inputs:**
- Mid = (bid + ask) / 2
- Spot = SPX index value (latest from CBOE feed or proxy from ES front month)
- T = time to expiry in years (compute from expiry date + 16:00 ET cutoff for AM-settled vs PM-settled)
- r = risk-free rate (3M T-bill, refreshed daily)
- q = dividend yield (SPX continuous div yield, refreshed daily)

**Output:** `IV` per (expiry, strike, side).

## 2. Greeks (analytical, not finite-diff)

For each (expiry, strike, side) with valid IV, compute Greeks analytically from BS:

```
d1 = [ln(S/K) + (r - q + σ²/2)T] / (σ√T)
d2 = d1 - σ√T

Delta_call = e^(-qT) · N(d1)
Delta_put  = e^(-qT) · (N(d1) - 1)

Gamma      = e^(-qT) · φ(d1) / (S · σ · √T)         # same for call/put

Vega       = S · e^(-qT) · φ(d1) · √T / 100          # per 1 vol pt

Theta_call = -S·e^(-qT)·φ(d1)·σ/(2√T) - r·K·e^(-rT)·N(d2) + q·S·e^(-qT)·N(d1)
Theta_put  = -S·e^(-qT)·φ(d1)·σ/(2√T) + r·K·e^(-rT)·N(-d2) - q·S·e^(-qT)·N(-d1)

Charm_call = -e^(-qT) · φ(d1) · [(2(r-q)T - d2·σ·√T) / (2T·σ·√T)]
           - q·e^(-qT)·N(d1)                                       # ∂Δ/∂t per year, divide by 525,600 for per-min
Charm_put  = (analogous, with N(-d1))

Vanna      = -e^(-qT) · φ(d1) · d2 / σ              # ∂Δ/∂σ
Vomma      = Vega · d1 · d2 / σ                     # ∂Vega/∂σ (M2+ if needed)
```

Where φ = standard normal pdf, N = standard normal cdf.

**Use Cody algorithm for N(x)** (faster than erf on hot path). Pre-tabulate for x in [-8, 8] step 0.001 if profiler says it matters.

## 3. Trade aggressor classification (Lee-Ready)

For each trade, classify whether it was buyer-initiated (lifted ask) or seller-initiated (hit bid):

```
if price >= ask:        return BUY
if price <= bid:        return SELL
if price > mid:         return BUY
if price < mid:         return SELL
if price == mid:        # tick test fallback
    if last_trade_price < price: return BUY
    if last_trade_price > price: return SELL
    return UNKNOWN
```

This drives dealer-side estimation: assume retail = aggressor, dealer = passive (lifted/hit). Therefore:
- Trade aggressed BUY at ask → customer bought, dealer SOLD (dealer is short the option, gamma-negative)
- Trade aggressed SELL at bid → customer sold, dealer BOUGHT (dealer long, gamma-positive)

This is a heuristic. Real dealer flow includes inter-dealer trades. We accept the bias as part of MVP — refinement is post-revenue.

## 4. Dealer positioning estimate (per strike)

For each (expiry, strike, side), maintain a running net signed dealer position:

```
DealerPos[k] = -OI_prior_day[k] · DealerSidePrior[k]   # initial guess
              + Σ (SignedFlow_today[k])

where SignedFlow = +size if customer SOLD (dealer LONG), -size if customer BOUGHT (dealer SHORT)
```

**DealerSidePrior** is the assumption about how prior-day OI is distributed between dealer and customer. For SPX, common heuristic: dealers are net SHORT calls (customer demand for upside), net LONG puts (customer hedge demand). Use `+0.7` for calls, `-0.5` for puts as initial assumption. Refine empirically.

**Net dealer Gamma per strike:**

```
DealerGamma[k] = DealerPos[k] · Γ[k] · 100  # 100 = contract multiplier for SPX
```

Notional GEX:

```
GEX_notional[k] = DealerGamma[k] · S · S · 0.01  # $/1% move
```

## 5. DPI (Dealer Pressure Index)

Composite score 0-100 indicating how forced/trapped dealer hedging is right now.

### 5.1 Components

Each component scaled to 0-100 individually, then weighted.

**a) Net Gamma Sign Magnitude (NGS)**

```
NGS = clamp(0, 100, 50 + 50 · sign(NetGEX) · min(1, |NetGEX| / GEX_norm))
```

where `GEX_norm` is a rolling 30-day percentile-90 of |NetGEX|. `NGS=0` means strongly long-gamma (dampening), `NGS=100` means strongly short-gamma (amplifying).

For dealer pressure, we treat short-gamma as MORE pressure (forced hedging). So:

```
NGS_pressure = clamp(0, 100, 50 - 50 · sign(NetGEX) · min(1, |NetGEX| / GEX_norm))
```

If NetGEX is strongly negative (short γ) → NGS_pressure → 100.

**b) Charm Velocity (CV)**

Sum of |charm × DealerPos| across all active 0DTE strikes, expressed as $/min of forced delta hedging:

```
CharmFlowRate = Σ_k |Charm[k] · DealerPos[k] · 100 · S| / 525600   # $/min equivalent
```

Scaled:

```
CV = clamp(0, 100, 100 · CharmFlowRate / CharmFlowRate_norm)
```

`CharmFlowRate_norm` = rolling 90-day p95.

**c) Vanna Sensitivity (VS)**

```
VannaPressure = Σ_k |Vanna[k] · DealerPos[k] · 100 · S|
VS = clamp(0, 100, 100 · VannaPressure / VannaPressure_norm)
```

**d) Time-to-Close Decay (TTC)**

```
T_remaining = (close_time - now) / regular_session_length    # 0 to 1
TTC = 100 · (1 - T_remaining)^1.5     # exponential ramp toward EOD
```

This captures that late-session forced hedging is amplified (less time to spread it out).

**e) Flow Concentration (FC)**

Herfindahl index of recent (last 5 min) signed flow per strike:

```
shares[k] = |signedflow_5min[k]| / Σ |signedflow_5min|
HHI = Σ shares[k]^2
FC = clamp(0, 100, 100 · HHI · concentration_norm_factor)
```

Concentrated flow → fewer strikes carrying the weight → faster price reaction when those strikes are tested.

### 5.2 Composite

```
DPI = 0.30·NGS_pressure + 0.25·CV + 0.15·VS + 0.20·TTC + 0.10·FC
```

Weights are starting points. Tune with backtest in M7. Smooth with EWMA(α=0.3) over 1s samples to reduce twitch.

## 6. Charm Clock zones

5-zone classification for the current charm decay phase:

| Zone | Trigger | Typical time |
|---|---|---|
| WEAK | charm_velocity < 1M Δ/min · close to open | 09:30 → 10:30 |
| RISING | charm_velocity rising, > 1M, < 5M | 10:30 → 11:45 |
| PEAK | charm_velocity > 5M and within ±25% of session max | 11:45 → 14:30 |
| FADING | charm_velocity declining from peak | 14:30 → 15:30 |
| PIN | t < 30 min, gamma concentration HHI > 0.3 | 15:30 → 16:00 |

Zone is recomputed per second. Persisted in `dealer_state_1s.charm_zone`.

**Direction bias:**
- If regime SHORT_GAMMA: charm in PEAK biases dealer to **sell into rallies / buy into dips** (mean-reverting forced flow). Trade momentum carefully.
- If regime LONG_GAMMA: charm in PEAK dampens. Volatility compresses. Favor mean-reversion trades.

## 7. Forced-flow simulator (What-If)

Given user input (Δ_spot, Δ_t, Δ_vol), output forced dealer hedge in dollar notional.

### 7.1 Method

For each strike with non-zero dealer position:

```
# new Greeks at perturbed (S', T', σ')
S'  = S · (1 + Δ_spot)
T'  = T - Δ_t
σ'  = σ + Δ_vol

new_delta = BS_delta(S', K, T', r, q, σ', side)

# delta change at strike level
Δdelta_total[k] = (new_delta - old_delta) · DealerPos[k] · 100

# total forced hedge in delta:
ForcedDelta = Σ_k Δdelta_total[k]

# dollar notional in spot index:
ForcedNotional = ForcedDelta · S'
```

Sign convention: `ForcedNotional > 0` = dealer must BUY index (futures), `< 0` = SELL.

### 7.2 Charm aid

Time decay during Δ_t reduces some hedge need:

```
CharmAid = Σ_k (Charm[k] · Δ_t_minutes · DealerPos[k] · 100 · S')
```

Net pressure = `ForcedNotional + CharmAid`.

### 7.3 Probability cone

Use historical analog: find similar regimes (DPI band, charm zone, regime, time bucket) in the last 12 months, measure outcome distribution. Approach:

```
# pseudocode
analog_days = query historical sessions where:
  - same charm_zone
  - same regime
  - DPI within ±10
  - time bucket within ±30min
return histogram of next-30-min outcomes (squeeze, mean-revert, pin, reversal)
```

This gives us a non-parametric probability estimate without needing a trained model. Refine in M7.

## 8. Pin probability engine

Activated in last 90 minutes of session.

For each strike near current spot (±20pt SPX):

```
# magnetism score
gamma_strength[k] = |TotalGamma[k]| / max(|TotalGamma|)     # 0-1 normalized
distance_factor[k] = exp(-((S - K)^2) / (2 · σ_proxy^2))    # gaussian decay
flow_persistence[k] = (recent_test_count[k] / 5min_window)
time_factor = (close_time - now) / session_length            # less time → more pin

PinScore[k] = 0.4·gamma_strength + 0.3·distance_factor + 0.2·flow_persistence + 0.1·time_factor

# convert to probabilities (softmax over candidate strikes)
PinProb[k] = exp(α · PinScore[k]) / Σ exp(α · PinScore)
```

α calibrated against historical EOD outcomes (M7).

## 9. AI narrative generation (M4+)

Rule-based templates triggered by state transitions. NOT an LLM in the hot path — too expensive and slow.

Templates:
- Regime flip: `"Regime flipped to {regime} at {strike}. Forced flow direction: {dir}."`
- DPI threshold cross: `"DPI crossed {threshold} ({direction}) — pressure {qualifier}."`
- Charm zone enter: `"Entered {zone} window. Edge: {win_rate}%, expectancy {exp}R (12mo)."`
- Pin candidate: `"Pin candidate forming at {strike}. Prob {p}%."`
- Sweep detected: `"Sweep {side} {strike} × {size}. Dealer must {action}."`

Each emits a `narrative_log` row + NATS publish. Optional LLM polish in M9 for daily summary.

## 10. Numerical safety

- Always check `IV > 0` before Greeks compute
- Always check `T > 0` (skip expired options)
- Clamp `d1, d2` to `[-8, 8]` before `N()` to avoid extreme tails
- Any NaN/Inf in compute path → log + drop strike for that tick
- Aggregate sums use Kahan summation if precision matters at scale (test first)

## 10. Flow Pulse (HIRO-style oscillator, decomposed)

Real-time oscillator that aggregates dealer-side flow impact across all active 0DTE strikes. Visually: 3-line oscillator that oscillates around zero. Above zero = bullish dealer hedging (forced BUY), below zero = bearish (forced SELL).

### 10.1 Why decompose

SpotGamma's HIRO is a single line. We decompose into 3 sub-signals so user can see WHICH greek is driving the move:

- **Gamma Pulse** — instantaneous delta hedge demand from new positions (spot-driven)
- **Charm Pulse** — time-decay-driven forced flow (passive, accumulating)
- **Vanna Pulse** — vol-driven forced flow (kicks in on IV moves)

Sum = total Flow Pulse. Each component plotted as separate line, plus optional aggregate.

### 10.2 Compute

For every classified trade `t` at strike `k`:

```
# Δ-equivalent dealer hedge for this trade (signed)
hedge_delta(t) = -aggressor_sign(t) · size(t) · contract_mult · delta(k, side)

# weighted by greek source
gamma_contrib(t) = hedge_delta(t)                                      # base
charm_contrib(t) = -aggressor_sign(t) · size(t) · contract_mult · charm(k, side) · 60   # per-min charm hedge accumulation
vanna_contrib(t) = -aggressor_sign(t) · size(t) · contract_mult · vanna(k, side) · iv_change_recent(k)
```

Aggregate over rolling 1-second buckets, then smooth with EWMA(α=0.4):

```
GammaPulse[bucket] = Σ gamma_contrib over 1s   (then EWMA)
CharmPulse[bucket] = Σ charm_contrib over 1s   (then EWMA)
VannaPulse[bucket] = Σ vanna_contrib over 1s   (then EWMA)
TotalPulse[bucket] = sum of above
```

Sign convention:
- **Positive (teal in UI)** = dealer must BUY index → bullish flow
- **Negative (red in UI)** = dealer must SELL index → bearish flow

### 10.3 Normalization

Display values normalized as `Δ-notional / typical-1s-notional` ratio so that `+1.0 = "1× typical bullish flow"`, `-2.0 = "2× typical bearish flow"`. Typical normalizer = rolling 30-day p80 of |TotalPulse| at same minute-of-day (intraday seasonality).

### 10.4 Persistence

Per-second snapshots written to `flow_pulse_1s` table:
```sql
CREATE TABLE flow_pulse_1s (
    ts            TIMESTAMPTZ NOT NULL,
    symbol        SMALLINT NOT NULL,
    gamma_pulse   DOUBLE PRECISION,
    charm_pulse   DOUBLE PRECISION,
    vanna_pulse   DOUBLE PRECISION,
    total_pulse   DOUBLE PRECISION,
    norm_factor   DOUBLE PRECISION
);
SELECT create_hypertable('flow_pulse_1s', 'ts', chunk_time_interval => INTERVAL '1 day');
```

### 10.5 Differentiation vs HIRO

| | SpotGamma HIRO | FlowGreeks Flow Pulse |
|---|---|---|
| Single line | ✓ | option (toggle) |
| Greek decomposition | — | ✓ (gamma / charm / vanna) |
| 0DTE-only | partial (broad) | ✓ |
| Methodology disclosed | partial | full (see this doc) |
| Replay-able | limited | ✓ full session |
| Backtestable signal | — | ✓ |

### 10.6 UI integration (M5)

- Dedicated panel in dashboard with 3 lines (or aggregate toggle)
- Optional overlay onto SPX price chart (translucent)
- Zero-cross alerts (configurable: "Total Pulse cross zero from below" → bullish trigger)
- Replay scrub maintains pulse history

---

## 11. Basis tracking (Spot ↔ Futures view)

Compute happens at SPOT (SPX index, NDX index). For display, user can switch to FUTURES view (ES, NQ front month). This is purely a display transform — does NOT affect Greeks/dealer math.

### 11.1 Definition

```
basis = futures_front_mid - spot
```

For SPX → ES: typically positive (cost-of-carry > dividend yield in current rate regime).
For NDX → NQ: same logic.

### 11.2 Live tracking

Subscribe MDP3 front-month ES + NQ. Compute mid every quote update. Smooth with EWMA(α=0.1) over 1s samples to filter noise.

Persisted in Redis:
```
HSET state:<symbol>
  spot 5811.42
  fut_front_mid 5817.28
  basis 5.86
  basis_smooth 5.78
  basis_ts 1716640328123
```

Front-month rollover detection: when ES/NQ contract change is < 8 days away, also publish back-month mid + basis_back. Frontend can warn user during rollover week.

### 11.3 Frontend transform

When user toggles "Futures view":
```
displayed_level = computed_level + basis_smooth     (for SPX/NDX → ES/NQ)
```

Apply uniformly to: call wall, put wall, zero gamma, all strike rows in heatmap, spot marker on charts. Flow tape strike labels stay AS-IS (option strikes are fixed) but a small `≈ESxxxx` annotation can be shown.

### 11.4 What does NOT change

- Greeks (BS computed in spot space, deltas/gammas are dimensionless or per-spot — invariant under shift)
- DPI, charm velocity, vanna sensitivity (signal-level, not price-level)
- Strike numbers themselves (SPX 5810C is always SPX 5810C)
- Backend persistence (always spot)

This means switching view is a 1-frame UI operation. No backend round-trip.

## 12. Validation strategy

- **Greeks:** parity check vs known-good library (py_vollib) for fixed inputs in unit tests
- **DPI:** plot DPI track for 5 historical days, eyeball against known regime narratives, calibrate weights
- **Forced flow simulator:** check at known scenarios (e.g. Vol Mageddon Aug 5 2024) — ensure direction sign matches reality
- **Charm clock:** plot charm velocity for last 30 days, verify peak zone clustering aligns with literature (typically ~1100-1400 ET peak)
- **Basis:** plot ES-SPX basis over last 30 days, verify mean ≈ cost of carry, no spikes > 3 stddev outside rollover weeks
