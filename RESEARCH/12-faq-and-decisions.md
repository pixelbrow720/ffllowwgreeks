# 12 · FAQ + Decisions

## Why SPX + NDX only?

The math is calibrated for index 0DTE. Cross-product (single-stock options, ETFs, RUT) breaks the calibration without compensating signal value. SPX + NDX cover ~90% of US 0DTE options volume by notional. Scope creep is the #1 way niche tools die.

## Why desktop only?

Audience is at a desk with 2-3 monitors. Mobile gestures don't help when scanning a 23-strike ladder. Responsive code doubles maintenance for zero customer value.

## Why 1Hz aggregation, not faster?

Latency budget says < 100ms wire-to-WS. Aggregation runs once per second to amortize cost across multiple ticks. Sub-second ticks could be supported but the math is noise-dominated below 1s for 0DTE.

## Why throttle frontend updates to 1/min?

A trader looking at the dashboard for 6 hours doesn't need 21,600 redraws. Once-per-minute updates with force-flush on regime/zone/pin transitions covers the actionable signal density. Alternatives tested:
- 1Hz (default Recharts cadence): values un-readable, panel "fights" the eye.
- 5s: still flickers.
- 1min + transition force-flush: stable enough to read, never miss a regime change.

Possible improvement: per-panel throttle (some need faster than others).

## Why NATS JetStream?

Decouples ingest from compute. Replay swaps in trivially. Per-subject sharding when scaling. Subscribers can lag without ingest backpressure. We considered Kafka (heavier, harder ops) and Redis Streams (less feature-complete). NATS is the right size.

## Why TimescaleDB?

Standard Postgres tooling + native time-series compression + hypertable chunk pruning. Saved ~10x storage vs raw Postgres on the 9-day archive. Considered ClickHouse (better analytics, worse OLTP) and InfluxDB (limited SQL). Timescale wins for FlowGreeks's read pattern (point-in-time + small ranges + occasional aggregations).

## Why Go for backend?

Hot-path zero-allocation discipline is enforceable in Go. Wire-to-WS p99 < 100ms is much harder in Python or Node. Considered Rust (steeper learning curve, slower to iterate) and C++ (we're not building a HFT firm). Go is the pragmatic choice.

## Why Next.js for frontend?

Server components for fast first paint. Tailwind for rapid design iteration. App router for clean route segmentation. Considered SvelteKit (less recharts ecosystem) and pure React+Vite (more build setup). Next.js + Tailwind is industry-standard enough that hiring-time is short.

## Why Recharts?

Tested vs visx, react-financial-charts, lightweight-charts, d3-direct. Recharts wins on:
- Composition (mix areas + lines + reference lines + tooltips trivially).
- Responsive sizing (when wired correctly via `useMeasuredBox`).
- Tailwind-friendly stroke / fill via inline style.
- Familiar API for any React dev.

Trade-off: bundle is ~120kb. Acceptable.

## Why brand pink as decorative-only?

Color discipline rule from CLAUDE.md: monochrome 90% default, three earned semantic accents. Brand pink is product identity but on data it would compete with the three semantic colors. Reserving it for chrome (CTAs, ambient backdrop, hero gradients) preserves both brand identity AND data legibility.

## Why throttle replay to unpaced (Speed=0)?

For development: get to "real numbers in dashboard" fastest. For demo / Brow viewing: this is wrong, use Speed=10 or 60.

The replay has a `-Speed` flag. The default in `tmp/run-replay.ps1` is whatever the last invocation passed. Should be Speed=1 or 10 by default; Speed=0 for batch backfill only.

## Why is the dashboard chart filtered to start at 13:30 UTC?

Brow asked for "20:30 WIB" which is `13:30 UTC`. That's about an hour before SPX cash open during EST. Catches the pre-RTH futures activity that drives early dealer positioning, then the cash session. The OI seed at 11:30 UTC is loaded by the backend regardless; only the chart cuts off pre-13:30.

## Why 8h history backfill?

Covers a full RTH session (6.5h) plus an hour of pre-market overlap. 8h × 1 sample/min = 480 samples, well under any payload limit.

## Why 64 strikes in the wire format?

NATS max payload is 1 MiB. Full chain JSON is ~1.5 MiB once cache is warm. 64 strikes by `|dealer_pos|` keeps payload < 32 KB. Display layer filters further to ±5% of spot for the visible band, drops far-OTM LEAPS that the backend top-N picker can include due to massive OI but irrelevant intraday gamma.

## Why per-key rate limit?

DDoS vector: a malicious or buggy client can saturate `pgxpool` with `LookupByHash` calls. Per-IP limit at the root + per-key limit on the protected mux gives 5-layer defense.

## Why admin endpoints on a separate loopback port?

Defense in depth. The public API has its own concerns; admin operations (list/revoke keys) shouldn't share the public request path. Loopback by default means a misconfigured firewall doesn't expose the admin token path. Operator must explicitly bind to a non-loopback address.

## Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-05-29 | Drop multi-component DPI Timeline → composite-only | Audit confirmed multi-line was unreadable; composite tells the story |
| 2026-05-29 | Throttle frontend snapshot to 1/min with regime force-flush | 1Hz redraws made the dashboard unreadable |
| 2026-05-29 | Default alert rules seeded on startup | Without them, signal log was empty by design |
| 2026-05-29 | Spot history endpoint reads from `dealer_state_1s` | Backend already had `QueryStates`; reuse |
| 2026-05-29 | Filter spot < 1000 in history endpoint + frontend | Early-replay rows have basis-seed garbage |
| 2026-05-29 | Walls overlap collapsed to single label when equal | Common on OPEX days, two labels on same Y unreadable |
| 2026-05-29 | KeyLevels RES dot = accent-short, SUP = accent-long | Per data semantics: RES = call wall = short-side dealer pressure |
| 2026-05-29 | Charm zone tone explicit per zone | Audit suggested FADING was rendering pink — clarify by code, document why |
| 2026-05-29 | Top-64 strikes by `|dealer_pos|` in wire format | NATS payload cap |
| 2026-05-29 | Pin engine `min_probability` parsed but not applied | Engine has no clean trigger gate; design call deferred |
| 2026-05-29 | RTH cutoff 13:30 UTC (= 20:30 WIB) | User request, also catches pre-RTH activity |
| 2026-05-29 | Vendor frontend-design + ui-ux-pro-max skills | Need consistent design reference across sessions |

## Unresolved

- Pin engine trigger probability gate design.
- NDX strategy until OPRA unlock.
- Real calibration walk vs 9-day archive (not yet executed).
- Backtest engine architecture.
- Time-machine GEX strike replay (`/api/history-strikes`).
- WCAG contrast audit.
- External pentest pre-launch.
- Subscription tier definition with parent product (flowjob.id).
