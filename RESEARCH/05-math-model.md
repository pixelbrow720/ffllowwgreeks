# 05 · Math Model

## Greeks (Black-Scholes + IV solver)

### Inputs per (strike, side, expiry, ts)

- `S` spot (from basis-adjusted ES/NQ front mid)
- `K` strike
- `T` time to expiry in years (event-time, not wall-clock — replay would otherwise collapse to T=0)
- `r` risk-free rate (3-month treasury, refreshed daily; placeholder constant for now)
- `q` dividend yield (SPX index dividend, refreshed daily; placeholder)
- `σ` implied volatility (solved per quote via Brent's method, warm-started from the strike's prior IV)

### Outputs (`internal/greeks`)

- `Delta` ∂Price/∂S
- `Gamma` ∂²Price/∂S²
- `Theta` ∂Price/∂t
- `Vega` ∂Price/∂σ
- `Charm` ∂Δ/∂t (delta decay rate per minute)
- `Vanna` ∂Δ/∂σ (delta sensitivity to vol)

Implementation notes:
- Brent solver bracket: `[1e-4, 5.0]`. Tolerance: `1e-6` on price diff.
- Warm-start: per-(expiry, strike, side) IV cache. Cache miss = bracket the bracket via spot+strike geometry.
- Solver failures (~13% in normal sessions) are silently skipped; deep-OTM noise is the dominant cause.

## Dealer aggregation

Per snapshot:

```
NetGEX[strike, side] = OI[strike, side] × Multiplier × S² × Gamma × DealerSign
```

`DealerSign` = +1 for puts (dealer long γ if customer long puts), -1 for calls (dealer short γ).
`Multiplier` = 100 (US options).
`S` = spot.

Sum across all active strikes:
- `NetGEX_total` (sign carries dealer regime)
- `CallWall` = strike with max |GEX| on the call side
- `PutWall` = strike with max |GEX| on the put side
- `ZeroGamma` = strike where cumulative NetGEX flips sign
- `ExpectedMv` = expected 1σ move to close (depends on session-end vol, simplified)

## DPI 5-component

The Dealer Positioning Index is the focal number. 0-100 scale. Computed per Snapshot, then EWMA-smoothed across snapshots.

| Component | What it measures | Signal direction |
|---|---|---|
| `net_gamma_sign` | magnitude of NetGEX vs `gex_norm` | high = dealers heavily exposed |
| `charm_velocity` | rate of |Δ flow| from charm decay | high = strong intraday hedging needed |
| `vanna_sensitivity` | |Δ flow| from vol moves | high = vol-driven hedging required |
| `time_to_close_decay` | logistic ramp toward 16:00 ET | high = closer to close = more pressure |
| `flow_concentration` | concentration index of signed 5-min flow | high = flow concentrated at few strikes |

**Composite:** weighted sum of the 5, normalized 0-100. Tier breakpoints:
- 0-25 STABLE
- 25-50 BUILDING
- 50-75 ELEVATED
- 75-100 FORCED

EWMA alpha: 0.2 (5-bucket effective window). State per-symbol.

Component values arrive in the wire format on the **0-100 magnitude scale** (not 0-1). The frontend does NOT rescale them. Direction (long γ vs short γ) lives in `regime` + `net_gex` sign, NOT in component values.

## Charm Clock Classifier

Maps aggregated charm velocity → zone:

| Zone | Trigger |
|---|---|
| WEAK | first hour of session AND velocity < weak_ceiling |
| RISING | velocity rising AND in [weak_ceiling, peak_floor) |
| PEAK | velocity > peak_floor AND ≥ 75% of session-max |
| FADING | session-max ≥ peak_floor AND velocity < 75% of session-max AND declining |
| PIN | last 30 min of session, regardless of velocity |

Defaults (Feb 2026 SPX): `weak_ceiling = 1e6`, `peak_floor = 5e6`. Calibration JSON can override via `SetVelocityThresholds`.

## Pin Probability Engine

Computes per-strike pin probability in last hour. Three components:

```
pin_prob[K] = γ_strength × distance_factor × flow_persistence
```

- `γ_strength`: |dealer γ at K| / max |γ across all K|, clamped 0-1.
- `distance_factor`: gaussian falloff from spot, σ ≈ expected_mv.
- `flow_persistence`: fraction of last 5 min where K had a trade test.

Pin **active** when top probability > threshold (default 40%, calibrated).
Pin **top_strike** = argmax over candidates.

## Forced-flow simulator

The product wedge. Given current state, simulate "if spot moves +Δ%, what dealer hedge is required?"

```
ForcedHedge(Δ) = Σ_strikes (γ_K × OI_K × DealerSign × Multiplier × S × Δ)
                + Σ_strikes (charm_K × DealerSign × Multiplier × dt)
                + Σ_strikes (vanna_K × DealerSign × Multiplier × dσ)
```

Reported as dollar-notional. The "if SPX +1% in next 60min" callout in the landing page hero shows `-$2.91B` — this is the forced sell-into-rally for the Feb-2026 session.

## Calibration

Empirical normalizers fit from `dealer_state_1s` rows over a window:

| Constant | Source |
|---|---|
| `gex_norm` | 95th percentile of `|net_gex|` |
| `charm_flow_rate_norm` | 95th percentile of `|charm_velocity|` |
| `vanna_pressure_norm` | 95th percentile of `|dpi_vanna|` |
| `charm_zone_boundaries[0]` | 33rd percentile of raw `charm_velocity` |
| `charm_zone_boundaries[1]` | 66th percentile of raw `charm_velocity` |
| `pin_min_probability` | median of `pin_top_prob` where `pin_active=true` |

R-7 percentile (NumPy default) so analysts comparing in pandas see byte-identical numbers.

Tool: `cmd/calibrate`, output JSON consumed by `cmd/compute --calibration-config`.

**Note:** real calibration walk vs full 9-day archive has NOT been executed yet. `make calibrate` is wired and ready; needs Brow to run it.

## Validation

Black-Scholes parity tests in `internal/greeks/*_test.go`. Cover known scipy parity values to within 1e-6. 17/17 packages green.

No backtest engine yet. M5+ task.

## Open math questions

- Earnings-day calendar effect on charm decay shape — not yet modeled.
- Quarterly OPEX magnetism (third Friday) — anecdotally strong but no measurement layer.
- Cross-product (NDX vs SPX) hedging carry — out of scope until live OPRA.
