# Contract drift audit — backend/docs/openapi.yaml vs internal/api/

Generated: 2026-05-28
Method: read openapi.yaml + grep handlers + compare struct fields/tags

## Summary
- paths in spec: 9 (11 operations: GET/POST/DELETE)
- paths in code: 9 REST + 2 WS (`/ws/live`, `/ws/replay/*` intentionally out of scope per spec `info.description`)
- drift findings: 8 (P0=1, P1=0, P2=3, P3=4)

## Path inventory

| Spec path | Method | Spec security | Code file:line | Code mount group |
|---|---|---|---|---|
| /health | GET | none | cmd/api/main.go:116 | root |
| /health/live | GET | none | cmd/api/main.go:117 | root |
| /health/ready | GET | none | cmd/api/main.go:119 | root |
| /metrics | GET | none | cmd/api/main.go:120 | root |
| /api/snapshot/{symbol} | GET | none | internal/api/rest.go:31 | MountPublic |
| /api/levels/{symbol} | GET | none | internal/api/rest.go:32 | MountPublic |
| /api/simulate/{symbol} | POST | apiKeyAuth+bearerAuth | internal/api/rest.go:40 | MountProtected (main.go:158) |
| /api/backtest/run | POST | apiKeyAuth+bearerAuth | internal/api/backtest.go:29 | protected (main.go:185) |
| /api/alerts/rules | GET | apiKeyAuth+bearerAuth | internal/api/alerts.go:40 | protected (main.go:159) |
| /api/alerts/rules | POST | apiKeyAuth+bearerAuth | internal/api/alerts.go:41 | protected (main.go:159) |
| /api/alerts/rules/{id} | DELETE | **NONE** | internal/api/alerts.go:42 | protected (main.go:159) |

Code-only routes (acknowledged in spec `info.description`, not part of REST contract): `GET /ws/live` (main.go:168), `GET /ws/replay/*` (main.go:175).

## Findings

### F1 [P0]: DELETE /api/alerts/rules/{id} — auth missing in spec, present in code
**Spec** (openapi.yaml:299-315): operation has no `security` block.
**Code** (internal/api/alerts.go:42): route registered on `protected` router; `protected.Use(apiKeyMW.Handler)` (main.go:145) and `apiKeyLimiter.Middleware` (main.go:156) are applied when `APIKEY_ENABLED=true`.
**Drift**: TS codegen will produce an unauthenticated client for DELETE; live calls will 401 when the gate is on. Spec also omits `401` and `429` responses that other protected operations enumerate.
**Fix**: add `security: [{apiKeyAuth: []}, {bearerAuth: []}]` and `401` + `429` responses to the `delete` operation. Mirror the structure used by POST /api/alerts/rules.

### F2 [P2]: BacktestRequest.cooldown_min — default mismatch
**Spec** (openapi.yaml:553): `cooldown_min: { type: number, format: double, default: 5 }`.
**Code** (internal/api/backtest.go:44): `CooldownMin float64 json:"cooldown_min"`. No defaulting before the field is passed to `backtest.Strategy{CooldownMin: req.CooldownMin}` (line 139). Go zero-value is `0`, not `5`.
**Drift**: documented default of 5 minutes is never applied server-side. Frontend may rely on it.
**Fix**: either set `if req.CooldownMin == 0 { req.CooldownMin = 5 }` after decode, or remove the `default: 5` from the spec. Verify upstream `backtest.Run` behavior.

### F3 [P2]: POST /api/alerts/rules — 404 response not declared in spec
**Spec** (openapi.yaml:290-297): `204`, `400`, `401` declared. No `404`.
**Code** (internal/api/alerts.go:138-143): on `Engine.UpsertRuleForOwner` returning `ErrRuleNotOwned`, handler emits `404 "rule not found"` (deliberate to avoid leaking other-tenant rule IDs).
**Drift**: TS codegen will not include 404 as a possible failure; clients may surface it as an unexpected error.
**Fix**: add `404` to the `post` responses with the existing `Error` schema. Document the rationale (cross-tenant probing).

### F4 [P2]: SimulateResponse.symbol — spec lacks enum constraint
**Spec** (openapi.yaml:521): `symbol: { type: string }` (no enum).
**Code** (internal/api/simulate.go:129): `resp.Symbol = strings.ToLower(sym.String())` — only ever `"spx"` or `"ndx"`.
**Drift**: cosmetic in JSON, but TS type is `string` instead of a discriminated union. Frontend can't switch-narrow on the value.
**Fix**: change spec to `symbol: { type: string, enum: [spx, ndx] }` to match the actual surface.

### F5 [P3]: AlertRule — `kind` required in spec, not enforced in code
**Spec** (openapi.yaml:614-628): `required: [id, symbol, kind]`.
**Code** (internal/api/alerts.go:134): only validates `rule.ID == "" || rule.Symbol == feed.SymbolUnknown`. An empty `kind` is accepted and propagates to the engine.
**Drift**: spec stricter than code. A client sending a rule with empty `kind` gets a successful 204 even though the predicate will never fire.
**Fix**: extend the upsert validation: `if rule.Kind == "" { writeJSONError(w, 400, "kind is required"); return }`.

### F6 [P3]: SimulateRequest — spec marks body required, code accepts empty
**Spec** (openapi.yaml:174-179): `requestBody: required: true` referencing `SimulateRequest`.
**Code** (internal/api/simulate.go:71-77): `if len(body) > 0 { json.Unmarshal(...) }` — an empty body is silently treated as zero-value `SimulateRequest`.
**Drift**: caller can POST with no body and get a 200 echoing the current state with all-zero scenario inputs. Spec implies a 400.
**Fix**: either require `len(body) > 0` and 400 on empty, or relax the spec to `required: false` and document the no-op semantics.

### F7 [P3]: GET /api/alerts/rules — 401 absent from responses
**Spec** (openapi.yaml:271-277): only `200` declared.
**Code**: route is on `protected` router so 401 is produced by `apikey.Middleware` before the handler runs, plus 429 from `apiKeyLimiter`.
**Drift**: undeclared response codes for an operation that has a `security` block.
**Fix**: add `401` and `429` responses to mirror POST /api/alerts/rules.

### F8 [P3]: AlertRuleList.rules — spec says required, code can omit
**Spec** (openapi.yaml:632): `required: [rules, total, offset, limit]`.
**Code** (internal/api/alerts.go:84-91): handler explicitly forces `Rules` to `[]alerts.Rule{}` if nil — this part is correct.
**Drift**: none functionally. Confirmed compliant; flagged here only to mark it as checked.
**Fix**: no action.

## Verified-clean (no drift)

- `StateSnapshot` (openapi.yaml:421-445) vs the JSON written to NATS by `cmd/compute` and forwarded verbatim by `rest.go:59`. Spec's `symbol: integer` matches `feed.Symbol uint8`; `side: 1=CALL, 2=PUT` matches `feed.SideCall=1`, `feed.SidePut=2` (internal/feed/types.go:57-58). UNCLEAR: full strike-row coverage requires reading the compute publisher (`cmd/compute/main.go`); manual check needed if compute is changed.
- `LevelsResponse` (openapi.yaml:407-419) vs anonymous struct in rest.go:78-87 — fields and JSON tags match 1:1.
- `BacktestResponse` (openapi.yaml:580-600) vs `runResponse` (backtest.go:67-84) — field-by-field match including `total_return`, `snapshots_evaluated`, `mean_return`, `stddev`, `sharpe`, `sortino`, `max_dd`.
- `Trade` (openapi.yaml:602-612) vs `tradeOut` (backtest.go:56-65) — match.
- `RuleKind` enum (openapi.yaml:569-578) vs `alerts.RuleKind` constants (internal/alerts/types.go:26-39) — all 7 values match exactly.
- `AlertRule.cooldown_ns` is `time.Duration` (int64 ns) → spec `integer/int64`. Match.
- `AlertRule.symbol` is `feed.Symbol` (uint8) → spec `integer`. Match. Note frontend must encode as 1 or 2; spec's description is the only contract.
- Auth headers: spec accepts `X-API-Key` and `Authorization: Bearer` (openapi.yaml:336-349); code reads both with Bearer winning (internal/apikey/middleware.go:114-120). Match.
- `/health/ready` 503 path: spec lists 503 with same schema as 200; code (cmd/api/main.go:340-345) returns 503 with the same body shape. Match.
- Symbol path param case-insensitivity: spec enum `[spx, ndx, SPX, NDX]`; code does `strings.ToUpper(...)` then `feed.ParseSymbol` which also accepts both cases. Match.

## Recommendations (priority order)

1. **F1 first.** A missing `security` block on a destructive endpoint is the single change with the highest likelihood of producing a silent integration bug once `web/` codegens its client.
2. **F2 + F3 + F4 together** — small spec-side edits, no code change required for F4. F2 needs a one-line code fix; F3 is purely additive in spec.
3. **F5 + F6** — close the validation holes in code; both are one-liners and improve API hygiene before any frontend work depends on them.
4. **F7** — bulk-edit pass to ensure every protected operation lists `401` and `429`.
