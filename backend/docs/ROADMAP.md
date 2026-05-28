# Roadmap

> Phased milestones from zero to revenue. Each milestone has a clear DEFINITION OF DONE so it's unambiguous when to move on.

This is a solo dev plan with realistic timing. Estimates assume ~20-30 hrs/week pairing with Claude Code.

## Overview

```
M0 ─ Project foundation         (1-2 weeks)
M1 ─ Live tick ingest           (2-3 weeks)    ← biggest learning curve
M2 ─ Greeks + dealer engine     (2-3 weeks)
M3 ─ DPI + Charm Clock signals  (2 weeks)
M4 ─ API + WebSocket            (2 weeks)
M5 ─ Frontend integration       (3-4 weeks)
M6 ─ Auth + billing + landing   (2 weeks)      ← LAUNCH possible here
M7 ─ Replay + backtest          (3 weeks)
M8 ─ Pin engine + simulator     (2-3 weeks)
M9 ─ AI narrative + polish      (2 weeks)
─────────────────────────────────────────────
Total to launch (M6):  ~14-18 weeks
Total to full V1:      ~22-28 weeks
```

---

## M0 — Project foundation (1-2 weeks)

**Goal:** Repo is alive, basic tooling works, can hello-world a Go binary.

- [ ] Init git repo + `.gitignore`
- [ ] `go mod init github.com/<you>/flowgreeks`
- [ ] Wire `golangci-lint` config + Makefile
- [ ] Set up `cmd/api/main.go` with chi router serving `/health`
- [ ] Docker compose: Postgres+Timescale, Redis, NATS — all healthy locally
- [ ] golang-migrate + first migration creating `schema_version` table
- [ ] Logging via `log/slog` with JSON handler
- [ ] Basic Prometheus `/metrics` endpoint on api binary
- [ ] First commit + GitHub repo (private)

**DoD:** `make up` starts the stack, `curl localhost:8080/health` returns 200, `make test` runs (no tests yet, but framework works).

---

## M1 — Live tick ingest (2-3 weeks)

**Goal:** Connect to OPRA Pillar + MDP3, parse messages, filter SPX/NDX, publish normalized ticks to NATS.

- [ ] Read OPRA Pillar protocol spec (SBE) — confirm vendor delivery format
- [ ] Read CME MDP 3.0 spec — front-month ES/NQ for spot proxy
- [ ] Implement SBE decoder for option quote + trade messages (`internal/feed/opra/`)
- [ ] Implement SBE decoder for futures quote + trade (`internal/feed/mdp3/`)
- [ ] Symbol filter: drop everything except SPX/SPXW/NDX/NDXP roots
- [ ] Normalizer: vendor → internal `Tick` struct
- [ ] NATS publisher: `ticks.<symbol>` subject
- [ ] Throughput test: replay 1 hour of historical OPRA at 5x speed, measure decode rate
- [ ] Latency test: from fake feed input → NATS publish, p99 < 5ms
- [ ] Archive writer: separate worker, batched 1s flushes to TS `ticks` table
- [ ] Backfill script: load 1y historical OPRA archive into TS

**DoD:** Live SPX 0DTE ticks visible via `nats sub 'ticks.spx.>'`. Historical day queryable with `SELECT count(*) FROM ticks WHERE symbol=1 AND expiry='2026-05-21'`. Decode throughput > 500k msg/sec on test box.

**Risk areas:**
- Vendor protocol learning curve (largest risk)
- SBE decoding performance (might need Rust sidecar if Go too slow)
- 1y backfill time (could be days; design for resumable)

---

## M2 — Greeks + dealer engine (2-3 weeks)

**Goal:** Per-tick IV solve, analytical Greeks, per-strike dealer position estimate, net GEX.

- [ ] BS pricing function + Brent IV solver in `internal/greeks/`
- [ ] Unit tests vs `py_vollib` parity for known inputs
- [ ] Analytical Greeks: delta, gamma, theta, vega, charm, vanna
- [ ] Lee-Ready aggressor classifier
- [ ] Dealer position estimator with prior-day OI seeding
- [ ] Per-strike GEX notional
- [ ] Net GEX, zero gamma strike, call wall, put wall computation
- [ ] **Basis tracker:** consume MDP3 ES/NQ front-month, compute live basis with EWMA, publish to `state.<sym>.basis` and Redis hash. Detect rollover window (< 8d).
- [ ] Compute service consumes NATS `ticks.>`, publishes `state.<sym>.gex` + writes Redis
- [ ] Hot path benchmarks: full per-strike Greeks compute < 30ms p99 for active 0DTE chain

**DoD:** Watch `state.spx.gex` flow on a live session, sanity-check Net GEX matches GEXBot's reading within ~10%. Charm + vanna values match Bloomberg OVDV for sample strikes.

---

## M3 — DPI + Charm Clock signals (2 weeks)

**Goal:** Full DPI composite, charm velocity tracking, charm zone classifier.

- [ ] Implement DPI 5 components (NGS, CV, VS, TTC, FC) in `internal/dealer/dpi.go`
- [ ] Composite weighting + EWMA smoothing
- [ ] Charm velocity rolling window
- [ ] Charm zone classifier (WEAK/RISING/PEAK/FADING/PIN)
- [ ] **Flow Pulse oscillator (HIRO-style, decomposed):** gamma/charm/vanna pulse per 1s bucket, EWMA smoothing, intraday seasonality normalizer. Persisted to `flow_pulse_1s` hypertable. See COMPUTE_MODEL.md §10.
- [ ] Persist `dealer_state_1s` rows
- [ ] CI lint + test pipeline (GitHub Actions)
- [ ] Backtest replay: run last 60 days of historical ticks through compute, verify DPI track plausibility
- [ ] Calibrate DPI normalizers (`GEX_norm`, `CharmFlowRate_norm`) from 90d historical
- [ ] Calibrate Flow Pulse intraday seasonality (per-minute-of-day p80 from 30d historical)

**DoD:** DPI series for last 30 days plotted, peaks correlate with known event days (FOMC, CPI). Charm zones cluster correctly (PEAK ~11:45-14:30 ET).

---

## M4 — API + WebSocket (2 weeks)

**Goal:** Frontend can subscribe and receive live state.

- [ ] REST endpoints:
  - `GET /api/snapshot/:symbol` — current state
  - `GET /api/levels/:symbol` — gamma walls + key levels
  - `GET /api/history/:symbol/:date` — for replay
  - `GET /api/strikes/:symbol/:expiry` — strike matrix
- [ ] WebSocket `/ws/live` with subscription model
- [ ] WS handles: subscribe, unsubscribe, heartbeat, drop-on-slow
- [ ] Per-connection bounded send channel
- [ ] CORS for development
- [ ] Postman collection / OpenAPI spec
- [ ] Stress test: 1000 concurrent WS clients, no memory leak over 1 hour

**DoD:** Curl WS with `wscat`, see DPI updates flow. REST snapshot returns within 50ms p99.

---

## M5 — Frontend integration (3-4 weeks)

**Goal:** Convert HTML mockups to live React/Svelte app consuming WS.

- [ ] Decide framework (SvelteKit recommended — see STACK.md)
- [ ] Project setup with TypeScript + Vite
- [ ] Convert dashboard mockup → component tree
- [ ] WS client + reconnect/backoff + state store
- [ ] Charm Clock page live data
- [ ] Flow tape with virtualized list
- [ ] Strike matrix heatmap (canvas-rendered for performance)
- [ ] What-If simulator (UI shell — backend in M8)
- [ ] **User preferences panel:** timezone selector (IANA TZ list, default detected from browser), spot/futures view toggle (global + per-symbol override), default symbol, number format
- [ ] **Timezone display:** all UI timestamps formatted via `Intl.DateTimeFormat` with user TZ. Backend always sends UTC nanosecond.
- [ ] **Spot/Futures view:** single store flag, all level components apply `+ basis_smooth` when in FUTURES mode. Show subtle "ES" or "NQ" suffix on prices, basis indicator in header.
- [ ] Theme + palette already locked: red/teal on black

**DoD:** Open browser to localhost:3000, see live SPX dashboard with all panels rendering real data.

---

## M6 — Add-on integration → SOFT LAUNCH (2 weeks)

**Goal:** Wire FlowGreeks into flowjob.id as a paid add-on.

- [ ] flowjob.id ↔ FlowGreeks API-key provisioning protocol (parent site mints + revokes via the `apikey.Generate` helper or equivalent)
- [ ] Tier-based rate budgets (`api_keys.rate_limit_rps` + `rate_burst`) populated from flowjob.id subscription tier on activation
- [ ] Landing page from mockup → live deployment (handled by flowjob.id main-site track)
- [ ] Add-on activation flow on flowjob.id surfaces secret to user once (one-shot reveal)
- [ ] Marketing copy + screenshots
- [ ] Discord server for community
- [ ] First 10 friendly beta users
- [ ] Stripe webhook handler for subscription events

**DoD:** Stranger can sign up, pay, log in, see dashboard. First $1 of revenue earned.

---

## M7 — Replay + backtest (3 weeks)

**Goal:** Time machine + signal validation.

- [ ] Replay worker `cmd/replay/`
- [ ] WS topic `/ws/replay/<session_id>` with playback control
- [ ] Speed control (0.5x → 60x)
- [ ] Frontend Replay page from mockup → live
- [ ] Date picker with event day flagging (FOMC, CPI, OPEX)
- [ ] Backtest engine: run signal across N days, output PnL/winrate/Sharpe
- [ ] Backtest UI: configure entry/exit rules from DPI/charm/zone
- [ ] Edge stats panel populated by backtest

**DoD:** Replay 21 May 2026 (CPI day) at 4x, see all panels animate. Backtest "Long when DPI>80 in PEAK zone" returns realistic PnL stats.

---

## M8 — Pin engine + What-If simulator (2-3 weeks)

**Goal:** Two more hero features fully wired.

- [ ] Pin probability scoring per strike
- [ ] Activates last 90 min of session
- [ ] Calibration vs historical EOD outcomes
- [ ] What-If simulator backend: scenario API, perturbation engine
- [ ] Probability cone via historical analog matching
- [ ] Alerts: pin candidate, regime flip, DPI threshold, charm zone enter

**DoD:** EOD pin prediction has measurable accuracy on holdout days. Simulator output matches manual calculation for sanity scenarios.

---

## M9 — AI narrative + polish (2 weeks)

**Goal:** Final hero polish + revenue acceleration.

- [ ] Rule-based narrative generator
- [ ] Narrative log persistence + display
- [ ] Optional LLM polish for daily summary (Anthropic API, cached)
- [ ] Performance pass: profile hot path, eliminate top 5 allocations
- [ ] Documentation site (docs.flowgreeks.com)
- [ ] Public API for Quant tier
- [ ] First post-launch retention/conversion analysis

**DoD:** Narrative feed feels useful (not noisy). Performance metrics published. Documentation comprehensive enough that users self-onboard.

---

## Risks & dependencies

| Risk | Mitigation |
|---|---|
| OPRA decode performance insufficient in Go | Fallback: Rust sidecar service for SBE decode only |
| 1y backfill takes too long | Design resumable, do incremental; can launch with shorter history initially |
| Dealer model inaccurate | Validate against known regimes; tune weights with backtest; accept v1 imperfection |
| Solo dev burnout | This is the biggest risk. Ship M6 (revenue) before M7-M9 perfection |
| Vendor feed reliability | Have ingest replay buffer; alert on disconnect; spot-check daily |

## Anti-roadmap (deferred indefinitely)

- Mobile app
- More than 2 underlyings (SPX, NDX only)
- Equity options support (different dealer dynamics)
- Crypto / FX
- Fundamental data
- Insider/political flow
- Discord/Slack bot
- Native desktop app (web is fine)
