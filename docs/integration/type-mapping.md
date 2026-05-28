# Frontend type generation — openapi.yaml → api-types.ts

Generated: 2026-05-28
Tool: openapi-typescript v7.13.0
Source: `backend/docs/openapi.yaml`
Output: `web/src/lib/api-types.ts` (969 lines)

## Schemas exported

`paths` keys (10):
- `/health`, `/health/live`, `/health/ready`, `/metrics`
- `/api/snapshot/{symbol}`, `/api/levels/{symbol}`, `/api/simulate/{symbol}`
- `/api/backtest/run`, `/api/alerts/rules`, `/api/alerts/rules/{id}`
- `/auth/signup`, `/auth/login`, `/auth/me`, `/auth/refresh`, `/auth/logout` (declared, no operations — flowjob.id-side)

`components.schemas` (20):
- Error, HealthResponse, ReadinessResponse
- LevelsResponse
- StateSnapshot, DPIBreakdown, FlowPulse, PinResult, PinCandidate, StrikeRow
- SimulateRequest, SimulateResponse, StrikeContribution
- BacktestRequest, BacktestResponse, RuleSpec, RuleKind, Trade
- AlertRule, AlertRuleList

`components.parameters`: Symbol (`"spx" | "ndx" | "SPX" | "NDX"`)
`components.responses`: BadRequest, NoStateYet
`operations` (10): getHealth, getLive, getReadiness, getMetrics, getSnapshot, getLevels, postSimulate, postBacktestRun, listAlertRules, upsertAlertRule, deleteAlertRule

## Mock files in `web/src/lib`

Single mock file: `web/src/lib/mock.ts` (226 lines).

Type aliases / interfaces declared:
- `CharmZone` — string union `"WEAK" | "RISING" | "PEAK" | "FADING" | "PIN"`
- `Regime` — string union `"SHORT_GAMMA" | "LONG_GAMMA" | "NEUTRAL"`
- `DPIBreakdown`, `FlowPulse`, `PinCandidate`, `StrikeRow`
- `FlowEvent` — frontend-only (option flow tape row)
- `Alert` — frontend-only (UI alert display row, distinct from spec `AlertRule`)
- `Snapshot` — frontend variant of `StateSnapshot`

Const fixtures: `SNAPSHOT`, `DPI_HISTORY`, `SPOT_HISTORY`, `FLOW_TAPE`, `ALERTS`, `CHARM_CLOCK_HOURS`, `KEY_LEVELS`, `FORCED_FLOW_SCENARIOS`.

Mock is consumed by 10 dashboard components (see `web/src/components/dashboard/*.tsx`). No file imports `api-types.ts` yet.

## Coverage matrix

| Spec schema | Mock equivalent | Match? | Notes |
|---|---|---|---|
| StateSnapshot | `Snapshot` / `SNAPSHOT` | partial | mock fields all required; spec all optional. Enum encoding diverges (see Type-encoding mismatches). |
| DPIBreakdown | `DPIBreakdown` / `SNAPSHOT.dpi` | yes | field-for-field; mock requires all fields, spec has them optional. |
| FlowPulse | `FlowPulse` / `SNAPSHOT.flow_pulse` | yes | same shape; required vs optional only. |
| PinResult | inline on `Snapshot.pin` | yes | mock inlines instead of named type — equivalent. |
| PinCandidate | `PinCandidate` | yes | identical fields. |
| StrikeRow | `StrikeRow` | partial | `side` encoded differently (string vs number). |
| LevelsResponse | `KEY_LEVELS` (UI fixture) | NO | mock invented different shape (label/dist/strength rows for visualization). Spec gives flat numeric levels. |
| SimulateRequest | — | NO | not used in frontend yet. |
| SimulateResponse | `FORCED_FLOW_SCENARIOS` | NO | mock is an array of pre-baked scenarios with `label`, `forced_notional`, `charm_aid`, `net_pressure`. Spec returns a single result with `top_contributions`. |
| StrikeContribution | — | NO | not represented in mocks. |
| BacktestRequest / BacktestResponse / Trade | — | NO | backtest UI not built yet. |
| RuleKind / RuleSpec | — | NO | rule builder UI not built yet. |
| AlertRule | `Alert` (different concept) | NO | mock `Alert` is a UI feed row (`ts`, `kind`, `message`, `severity`); spec `AlertRule` is a stored rule definition (`id`, `symbol`, `kind`, `threshold`, `cooldown_ns`, `sinks`, `enabled`). Different domain objects. |
| AlertRuleList | — | NO | rule management UI not built. |
| HealthResponse / ReadinessResponse | — | NO | no health UI. |
| Error | — | NO | error envelope not modeled in frontend yet. |

Mock-only shapes (no spec equivalent — frontend-invented):
- `FlowEvent` / `FLOW_TAPE` — option print tape. Backend has no flow-tape endpoint in current spec.
- `DPI_HISTORY` — DPI time series for chart. Backend exposes only point-in-time snapshot.
- `SPOT_HISTORY` — spot time series for chart. Same — no historical endpoint.
- `CHARM_CLOCK_HOURS` — synthetic intraday charm intensity for radial chart.
- `KEY_LEVELS` — UI projection of LevelsResponse with `label`, `dist`, `type`, `strength` columns.

## Type-encoding mismatches (load-bearing)

These will require a converter at the API boundary, not just a type swap:

1. `regime` — spec `number` (0=UNKNOWN, 1=SHORT_GAMMA, 2=LONG_GAMMA, 3=NEUTRAL); mock string union.
2. `charm_zone` — spec `number` (1=WEAK, 2=RISING, 3=PEAK, 4=FADING, 5=PIN); mock string union.
3. `symbol` on `StateSnapshot` — spec `number` (1=SPX, 2=NDX); mock `"SPX" | "NDX"`.
4. `side` on `StrikeRow` — spec `number` (1=CALL, 2=PUT); mock `"C" | "P"`.
5. `strike` on `StrikeRow` — spec `int32` × 1000 encoding (per schema description); mock uses raw float (e.g. `5850`, not `5850000`). Decode helper required.
6. `expiry` — spec int32 YYYYMMDD; mock matches (`20251128`).
7. All `StateSnapshot` fields are optional in spec — frontend must handle undefined or rely on a guard at the fetch layer.

## tsc --noEmit results

Errors: **0**. `api-types.ts` compiles clean against the existing tsconfig. No file imports it yet, so no cascading errors from mismatched mocks. The mismatches above will surface when components are migrated off `mock.ts`.

## Recommendations for frontend integration

1. Add a thin wire-decoder module (e.g. `web/src/lib/api-decode.ts`) that converts spec types → UI types: enum-int to string union, `strike × 1000` to float, optional-everywhere to required-with-defaults. Keep `api-types.ts` as the wire contract; export a `DecodedSnapshot` for component consumption.
2. Replace `Snapshot` in `mock.ts` with `import type { components } from "@/lib/api-types"` and alias `type WireSnapshot = components["schemas"]["StateSnapshot"]`. Build `Snapshot` as the decoded shape.
3. `LevelsResponse` is the smallest/cheapest endpoint — wire it first as a vertical slice; `KEY_LEVELS` becomes a derived view (compute `dist`, `strength`, `type` client-side from spec fields).
4. Treat `Alert` (UI feed row) as separate from `AlertRule` (stored rule). Keep the distinction in two files: `lib/alerts-feed.ts` (WS-driven, frontend-shaped) vs `lib/api-types.ts` (rule CRUD).
5. `FORCED_FLOW_SCENARIOS` is a UI demo fixture; the simulator endpoint takes a single `SimulateRequest` and returns a single `SimulateResponse`. Replace the mock with a state model that issues N requests and stores N responses keyed by scenario label.
6. Time-series shapes (`DPI_HISTORY`, `SPOT_HISTORY`) have no spec coverage. Either (a) keep client-side rolling buffer over WS frames, or (b) request a new historical endpoint from backend before promoting these views past mock.
7. Extend Datadog/Sentry-style error envelope by importing `components["schemas"]["Error"]` for fetch-layer parsing; currently no frontend handler exists.
