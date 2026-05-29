# Product Vision

## One-liner

**Real-time 0DTE options flow + dealer positioning intelligence for SPX & NDX, optimized for traders who want to see the forced flow before it hits the tape.**

## Audience

Solo / small-team day traders who already trade 0DTE SPX or NDX and already understand:

- Gamma exposure (GEX), zero-gamma, walls
- Charm decay near close
- Pin risk
- Dealer hedging mechanics

NOT for retail beginners. NOT for portfolio managers. NOT for crypto / FX / equities.

## The wedge

Every other product (SpotGamma, MenthorQ, etc.) computes EOD/snapshot dealer state and updates every 15s. FlowGreeks computes it **wire-to-WS p99 < 100ms** and exposes a **predictive forced-flow** number:

> "if SPX moves +1% in next 60min, dealers must sell **−$2.91B**"

That predictive simulation is the wedge. Everyone else shows you yesterday's gamma; FlowGreeks shows you next-hour's required dealer hedging.

## Hard scope rules

| Rule | Why |
|---|---|
| **SPX + NDX only** | The math is calibrated for index 0DTE. Cross-product breaks calibration. |
| **Desktop only, 1920×1080 baseline** | Audience trades from 2-3 monitor desk setup. Mobile gestures don't help with strike-ladder scanning. |
| **English code + comments. Bahasa Indonesia for chat.** | |
| **No mocked DB tests** | Unless test is unit-scoped to non-DB logic. |
| **Hot path = zero allocations in steady state** (Go) | Latency budget is the product. |
| **Latency budget**: ingest 5ms · normalize 2ms · compute 30ms · fanout 10ms · **total p99 < 100ms wire-to-WS** | |

## What it is NOT

- Not a broker / order-routing system.
- Not a charting / TA platform (no RSI, MACD, candle patterns).
- Not a portfolio tracker.
- Not a news terminal.
- Not multi-asset.

## Color discipline (non-negotiable)

| Token | Hex | Use |
|---|---|---|
| `accent-short` | `#ef4444` | Dealer short γ, declining, alerts CRIT |
| `accent-long` | `#10b981` | Dealer long γ, rising, healthy |
| `accent-warn` | `#f59e0b` | Pin candidate, alerts WARN, FORCED state |
| `brand` / `brand-hi` | `#ff2a5b` / `#ff5b85` | **Decorative ambient ONLY** — backdrop glows, CTAs, hero number callouts. **NEVER on a value, bar, chart line, or data label.** |
| ink scale | `#08080a` → `#f4f4f5` | Everything else |

## Math jantung produknya

### Dealer Positioning Index (DPI), 0-100 scale

5 components, EWMA-smoothed across 1Hz snapshots:

| Component | What it measures | Direction |
|---|---|---|
| `net_gamma_sign` | magnitude of NetGEX vs `gex_norm` | high = dealers heavily exposed |
| `charm_velocity` | rate of \|Δ flow\| from charm decay | high = strong intraday hedging needed |
| `vanna_sensitivity` | \|Δ flow\| from vol moves | high = vol-driven hedging required |
| `time_to_close_decay` | logistic ramp toward 16:00 ET | high = closer to close = more pressure |
| `flow_concentration` | concentration index of signed 5-min flow | high = flow concentrated at few strikes |

Tier breakpoints: 0-25 STABLE · 25-50 BUILDING · 50-75 ELEVATED · 75-100 FORCED.

### Charm Clock zones

WEAK (first hour, low velocity) → RISING → PEAK (high velocity, near session-max) → FADING (post-peak decline) → PIN (last 30min always).

### Pin Probability Engine

Per-strike `pin_prob[K] = γ_strength × distance_factor × flow_persistence`. Active when top probability > threshold (default 40%).

### Forced-flow simulator

Given current state, simulate "if spot moves +Δ%, what dealer hedge is required?"

```
ForcedHedge(Δ) = Σ_strikes (γ_K × OI_K × DealerSign × Multiplier × S × Δ)
                + Σ_strikes (charm_K × DealerSign × Multiplier × dt)
                + Σ_strikes (vanna_K × DealerSign × Multiplier × dσ)
```

Reported as dollar-notional. This is the wedge.

### Greeks (Black-Scholes + IV solver)

Per (strike, side, expiry, ts): Brent's method bracket `[1e-4, 5.0]`, tolerance `1e-6`. Warm-started from prior IV cache. Outputs Δ, Γ, Θ, ν, charm, vanna.

## Auth model

FlowGreeks is an **add-on inside flowjob.id** (parent product). flowjob.id owns user accounts, billing, tier provisioning. FlowGreeks issues opaque API keys minted by flowjob; this repo never has signup, login, password reset, or tier UI.

## Stack (current implementation)

- **Backend**: Go 1.22+ (api, compute, ingest, replay, calibrate)
- **Pub/sub**: NATS JetStream
- **Storage**: Postgres 16 + TimescaleDB (ticks hypertable, dealer_state_1s, api_keys), Redis (Spot Window Cache)
- **Frontend**: Next.js 14 + Tailwind + Recharts
- **Vendor**: Databento OPRA Pillar (options) + GLBX MDP3 (futures) → see `DATA-9-DAYS.md` for the historical archive

## Success criteria

- A 0DTE trader looking at the dashboard for 6+ hours can identify regime, walls, pin candidates, and forced flow at a glance — without scrolling, without hover, in <2 seconds.
- The math is correct against scipy parity test (Black-Scholes) on every published snapshot.
- The numbers are stable enough to read. Live updates do not redraw faster than the eye can absorb (1-min throttle with regime-flip force-flush).
- Wire-to-WS p99 < 100ms in live mode.
