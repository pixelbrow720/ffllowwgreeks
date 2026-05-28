# API_POLICY

This document is the contract between the FlowGreeks engine and any
external consumer (partner integration, codegen client, uptime monitor,
trader UI). It is the canonical stability policy for `/v1/*`.

The machine-readable changelog lives in `CHANGELOG.md`. This file
governs what counts as additive vs breaking, when consumers must be
warned, and how new behaviour is negotiated.

---

## 1. Stability contract for `/v1/`

The URL prefix `/v1/` is a stability contract. Within `/v1/`:

**Non-breaking (allowed without consumer notice):**

- Adding a new optional field to a response payload.
- Adding a new endpoint.
- Adding a new optional query parameter with a back-compat default.
- Tightening a response field's range *within* its declared type
  (e.g. `int >= 0` → `int >= 1` only if no consumer ever observed `0`).
- Adding a new value to a discriminated union *only if* the union is
  documented as open (`string` rather than a closed enum). All public
  unions in this repo are currently `string`.
- Loosening request validation (accepting more inputs than before).
- Performance-only changes (latency, batching, caching).

**Breaking (require a `/v2/` prefix or a documented migration window):**

- Removing a response field.
- Renaming a response field.
- Narrowing a response field's type (`number | null` → `number`,
  `string` → enum, `T[] | null` → `T[]`).
- Changing a field's units or sign convention.
- Removing or renaming an endpoint.
- Tightening request validation in a way that previously-valid
  payloads now 4xx.
- Changing an HTTP status code that consumers may branch on (e.g.
  the Rev 8 401/403 collapse — that one shipped without a `/v2/`
  bump and is documented retroactively in `CHANGELOG.md`).
- Changing a WebSocket frame's discriminator semantics.

When a breaking change is unavoidable on `/v1/` (legal, security,
data-correctness), the change MUST land alongside:

1. A `CHANGELOG.md` entry under the next version bump, marking the
   change `Breaking`.
2. `Deprecation:` and `Sunset:` HTTP headers on every response that
   exposes the deprecated shape (see § 4).
3. At least 30 days between the first deprecated response and any
   removal.

Two real exceptions to this policy already shipped without warning
(Rev 8 `/health` thinning and the 401/403 collapse). Both are
catalogued in `CHANGELOG.md` v1.1 as the policy's birth event.

### 1a. Permanent v1 stability exemptions

The following inconsistencies are **frozen for the lifetime of v1**.
Fixing them mid-v1 would itself be breaking. Each is tracked here
explicitly so consumers can rely on the current shape and `/v2/`
implementers know what to unify:

- **`/v1/{symbol}/flow` and `/v1/{symbol}/hiro` are NOT wrapped in
  the standard `{symbol, computed_at, next_update_in_seconds, data}`
  envelope** (Rev 12 BC-15). They emit their own bare shape:
  `{symbol, event_type, since, limit, events}` and
  `{symbol, bucket, since, cumulative, series}` respectively. Every
  other `/v1/{symbol}/*` data response IS wrapped. `/v2/` MUST
  unify all response envelopes; source-level
  `# REV12-V2: wrap envelope` comments mark the wrap site.
- **`flow.limit` overflow returns HTTP 422, not a silent clamp**
  (BC-18). Pydantic `Query(ge=1, le=1000)` rejects out-of-range
  values eagerly so caller-side bugs (e.g. asking for 100000
  events) fail loudly rather than scanning a hot hypertable. `/v2/`
  MAY change to silent clamping; v1 does not.
- **`since` overflow on `/flow` and `/hiro` returns HTTP 400, not a
  silent clamp** (BC-19). Values older than 24h or in the future
  are rejected at the endpoint rather than absorbed by the query.
  Same rationale as BC-18.

## 2. Pydantic `extra="allow"` policy

Every typed response model in `app/api/schemas.py` is configured
with `model_config = ConfigDict(extra="allow")`. This means the
emitter is permitted to forward additional fields beyond what the
schema declares — typically provenance keys (`weight_source`,
`regime_label`) or extras that haven't yet been promoted into the
typed schema.

**Consumer contract:**

- Codegen clients (TypeScript, Python, OpenAPI codegen) MUST be
  configured with `additionalProperties: true` on the response
  models, otherwise emitter additions will fail strict-mode
  validation.
- A field that exists on the wire but is missing from the served
  OpenAPI document is NOT a contract violation — it is a
  documentation gap. Consumers should treat unknown fields
  permissively and surface them as opaque.
- The reverse — a field declared in OpenAPI but absent on
  the wire — IS a contract violation and should be reported.

When a recurring extra is observed in production, the emitter team
should promote it into the typed schema (see Rev 9 CT-14 / BC-7
for the canonical pattern). This is non-breaking.

## 3. Codegen consumers

The FastAPI emitter exposes the OpenAPI document at `/openapi.json`
when `ENABLE_OPENAPI_DOCS=true`. Codegen clients should pull the
running server's spec rather than relying on a checked-in artefact —
the document is regenerated automatically by FastAPI from the typed
schemas in `app/api/schemas.py` and is always in sync with the live
emitter.

WS frames are NOT in OpenAPI (FastAPI does not introspect WS routes).
The shapes and close-code semantics are documented in
`docs/api_reference.md` and pinned in this file (§ 6).

## 4. Deprecation + Sunset header policy

When a `/v1/` field or endpoint is being phased out:

1. The response (or one of its responses) MUST include a
   `Deprecation:` HTTP header carrying an HTTP-date marking when the
   deprecation took effect.
2. A `Sunset:` HTTP header carries an HTTP-date no earlier than 30
   days after the `Deprecation:` date, marking the earliest
   permissible removal.
3. The `CHANGELOG.md` entry MUST list both dates.
4. The deprecation note belongs in the field's `description=` on the
   Pydantic model so OpenAPI codegen consumers see it.

Active deprecations (as of v1.1):

| Surface | Field / endpoint | Deprecation | Sunset | Replacement |
|---|---|---|---|---|
| `/v1/{symbol}/iv` | `IvResponse.skew` | 2027-01-01 | 2027-07-01 | `IvResponse.skew_per_expiry` |

Field removal must wait until the `/v2/` rollout — `Sunset:`
indicates the earliest the field MAY disappear, not the earliest
it WILL.

## 5. Version negotiation (forward-looking)

Consumers MAY send an `Accept-Version: v1.1` request header. Servers
MUST ignore unknown values and respond on the requested major. When
`/v2/` ships, the same prefix-driven URL versioning continues to be
the primary mechanism; `Accept-Version` is reserved for behavioural
toggles within a major (e.g. opting into pre-release additive
fields).

This header is currently a no-op for behaviour selection — but
v1.2+ servers DO read it for telemetry (Rev 12 BC-13). The value
is logged at debug level via the `accept_version` structlog field
and stashed on `request.state.accept_version` for any future
toggle. v1.1 servers ignore the header entirely, so consumers can
begin echoing it (e.g. `Accept-Version: v1.2`) without breaking
against pre-Rev-12 servers.

## 6. WebSocket frame discriminator policy

Frames on `WS /v1/{symbol}/stream` and `WS /v1/{symbol}/stream/ticks`
follow these rules:

- **Snapshot frames** carry `type: "snapshot"` AND `data`. Pre-v1.2
  servers emitted snapshot frames without a `type` field; consumers
  should still treat absence of `type` (with `data` present) as a
  snapshot frame so they continue to interoperate with any
  pre-v1.2 emitter still in flight. v1.2+ servers always emit
  `type: "snapshot"` (Rev 12 BC-9 — additive).
- **All other frames** carry a `type` discriminator
  (`tick | heartbeat | error`).
- Adding a new `type` value is **additive**, not breaking. Consumers
  MUST tolerate unknown `type` values rather than treating them as
  a hard error. Any new `type` added to the public emitter will land
  in `CHANGELOG.md` under `Added`.
- Removing or repurposing an existing `type` is breaking and follows
  the `/v2/` rule from § 1.
- `WsErrorFrame` (`{type: "error", code, message}`) is emitted on
  pre-existing fatal-ish conditions only — initial-snapshot prime
  failure (HTTP-style code 503) and unexpected handler exception
  (code 500). Rev 12 BC-10 wires the emit; the TS contract has
  declared the shape since v1.1.
- Close codes are part of the contract: 1008 (revoked / unauthorised
  during stream), 1012 (service restart at lifespan teardown), and
  4401 (mid-stream revocation) are documented in
  `docs/api_reference.md`. Adding a new close code is additive;
  removing or repurposing one is breaking.

## 7. Numeric serialisation: NaN, +Infinity, -Infinity

NaN, +Infinity, and -Infinity are serialised as JSON `null` in
BOTH REST and WebSocket transports (Rev 12 BC-16). Consumers MUST
treat `null` numeric values as "not computable" rather than as zero.

The contract is enforced by the choice of serialiser — `orjson` (WS
hot path) and FastAPI's `jsonable_encoder` (REST) both coerce
non-finite numbers to `null`. There is no opt-out; passing a Python
`float('nan')` to a Pydantic response model results in `null` on the
wire regardless of whether the field is typed `float` or
`float | None`. Calculators that have a meaningful "not computed"
sentinel SHOULD prefer `None` directly over NaN to make the contract
intent explicit at the source.

## 8. Anonymous discovery surface

Anonymous (no-auth) routes deliberately expose only the minimum
information required to bootstrap a credentialed integration:

| Path | Returns | Since |
| --- | --- | --- |
| `GET /health` | `{status, ts}` (liveness only) | v1.1 (Rev 8 SEC-9) |
| `GET /ready` | `{ready, last_tick_age_seconds, threshold_seconds, ts}` (HTTP 503 when stale) | v1.1 (Rev 11 SRE-3) |
| `GET /v1/symbols` | `{symbols: string[]}` | v1.2 (Rev 12 BC-17) |

All other operational telemetry (DB connectivity, live-feed
booleans, last-compute timestamps, per-symbol pipeline run history)
requires either `X-API-Key` (`/health/detail`) or admin JWT
(`/admin/system/status`).

## 9. References

- `CHANGELOG.md` — chronological log of every consumer-visible
  change, in markdown.
- `CHANGELOG.json` — same structure, JSON-typed (Rev 12 BC-14).
  Codegen consumers and CI guards can parse this without lexing
  markdown. Schema: top-level `current_version`, `versions[]` with
  `{version, date, rev, breaking, added, changed, documented}`,
  and `active_deprecations[]`.
- `docs/api_reference.md` — endpoint reference, payload shapes,
  WebSocket frame examples and close-code reference.
- `/openapi.json` — served by the running app when
  `ENABLE_OPENAPI_DOCS=true`. Canonical REST schema for codegen.
