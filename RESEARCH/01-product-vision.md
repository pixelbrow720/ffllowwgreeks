# 01 · Product Vision

## One-liner

**Real-time 0DTE options flow + dealer positioning intelligence for SPX & NDX, optimized for traders who want to see the forced flow before it hits the tape.**

## What it is

FlowGreeks is a desktop terminal that ingests SPX + NDX option chains from OPRA and CME index futures from MDP3, computes dealer positioning (gamma, charm, vanna), and surfaces the **forced flow** — the dollar-notional dealers MUST hedge in the next minutes/hours given the current chain state and the spot path.

It is NOT a charting platform. It is NOT a backtesting platform (yet). It is a **read-the-dealer** scanner.

## Who it is for

- Solo / small-team day traders who trade 0DTE SPX or NDX (the only two products that matter for 0DTE volume).
- Specifically: traders who already understand gamma exposure (GEX), zero gamma, walls, charm decay, and pin risk. The product sells answers to people who already ask the right questions.
- **NOT** for retail beginners. NOT for portfolio managers. NOT for crypto / FX / equities.

## The one wedge

Every other product (SpotGamma, MenthorQ, etc.) computes EOD/snapshot dealer state and updates every 15s. FlowGreeks computes it **wire-to-WS p99 < 100ms** and exposes a **predictive forced-flow** number: "if spot moves +1%, dealers must buy +$2.91B". That predictive simulation is the wedge. Everyone else shows you yesterday's gamma; FlowGreeks shows you next-hour's required dealer activity.

## Hard scope rules (do not violate without a written reason)

- **SPX + NDX only.** No equities, no crypto, no FX, no RUT. The math is calibrated for index 0DTE; cross-product breaks the calibration.
- **Desktop only.** 1920×1080 baseline. No mobile. No responsive. The audience is at a desk with three monitors.
- **English code + comments. Bahasa Indonesia for chat.**
- **No `git push` automated.** User pushes manually.
- **No mocked DB tests** unless the test is unit-scoped to non-DB logic.
- **Hot path = zero allocations in steady state** (Go).
- **Latency budget per stage:** ingest 5ms · normalize 2ms · compute 30ms · fanout 10ms · total p99 < 100ms wire-to-WS.

## Color discipline

Three earned semantic accents. Everything else is monochrome ink + brand decorative.

| Token | Hex | Use |
|---|---|---|
| `accent-short` | `#ef4444` | Dealer short γ, declining, alerts CRIT |
| `accent-long` | `#10b981` | Dealer long γ, rising, healthy |
| `accent-warn` | `#f59e0b` | Pin candidate, alerts WARN, FORCED-state |
| `brand` / `brand-hi` | `#ff2a5b` / `#ff5b85` | **Decorative ambient ONLY** — backdrop glows, CTAs, hero number callouts. **NEVER on a value, bar, chart line, or data label.** |
| ink scale | `#08080a` → `#f4f4f5` | Everything else |

## Auth model

FlowGreeks is an **add-on inside flowjob.id** (parent product). flowjob owns user accounts, billing, tier provisioning. FlowGreeks issues opaque API keys minted by flowjob; this repo never has signup, login, password reset, or tier UI.

## Latency philosophy

The pipeline IS the product. Every layer must justify its cost.

```
OPRA Pillar / CME MDP3 → ingest (5ms) → normalize (2ms) → NATS JetStream
  → compute Greeks + dealer state (30ms) → fanout to WS (10ms) → dashboard
```

If a stage exceeds its budget, the fix is the stage, not the budget.

## What the product is NOT

- Not a broker / order-routing system. No execution.
- Not a charting / TA platform. RSI, MACD, candle patterns are out.
- Not a portfolio tracker. No positions tab.
- Not a news terminal. No news feed.
- Not a multi-asset terminal. SPX + NDX 0DTE only.

## Success criteria

- A 0DTE trader looking at the dashboard for 6+ hours can identify regime, walls, pin candidates, and forced flow at a glance — without scrolling, without hover, in <2 seconds.
- The math is correct against a known parity test (Black-Scholes, scipy, BBG TERM) on every published snapshot.
- The numbers are stable enough to read. Live updates do not redraw the page faster than the eye can absorb.
