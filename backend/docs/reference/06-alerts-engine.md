# 06 — Alerts engine

> Validated against commit `3e5b0ec`.
> Source:
> - [`internal/alerts/`](../../internal/alerts/) — engine.go, delivery.go, types.go, metrics.go
> - [`internal/api/alerts.go`](../../internal/api/alerts.go) — REST handlers + NATS subscriber
> - [`internal/narrative/engine.go`](../../internal/narrative/engine.go) — rule-based narrative generator (separate concern)

## Two engines, similar shape

| | Alerts engine | Narrative engine |
|---|---|---|
| Lives in | `internal/alerts/` | `internal/narrative/` |
| Trigger source | User-defined `Rule` | Hardcoded story rules per §9 |
| Output | `Trigger` → sinks (broker, webhook) | `Narrative` → NATS `narrative.<sym>` |
| User scoping | Per `UserID` | Global |

This doc covers the **alerts** engine. The narrative engine is referenced in passing because it shares the `Snapshot` type.

## Data flow

```
NATS state.<sym>.gex publish
   │
   ▼
api.SubscribeAlertsToNATS                       ← cmd/api/main.go
   │  decode JSON → alerts.DecodeSnapshot
   │
   ▼
Engine.OnSnapshot(snap)
   │
   ▼
   for rule in rules:
       if rule.Symbol != snap.Symbol → skip
       if not rule.Match(snap)        → skip          ← predicate eval
       if now < rule.lastFired+Cooldown → skip        ← cooldown gate
       rule.lastFired = now
       trigger := buildTrigger(rule, snap)
       evaluations++ / fires++
       dispatch(trigger)
   │
   ▼
   for sink in sinks:
       sink.Deliver(trigger)                          ← BrokerSink, WebhookSink, ...
       deliveries++ / delivery_errors++
```

Engine state (`Engine` struct, [`engine.go:14`](../../internal/alerts/engine.go#L14)):

```
mu      sync.RWMutex   guards rules map
rules   map[string]*Rule

sinksMu sync.RWMutex   guards sinks map
sinks   map[string]Sink
```

`OnSnapshot` is called from one goroutine (the NATS subscriber) so the hot path doesn't hold the rules lock during `dispatch` — it snapshots a slice + releases the lock first.

## Rule shape

```go
type Rule struct {
    ID         string         // user-supplied unique key
    UserID     string         // creator's id (from JWT)
    Symbol     feed.Symbol    // SPX | NDX
    Kind       RuleKind       // dpi | gex | charm | pin | regime
    Predicate  Predicate      // func(snap Snapshot) bool
    Cooldown   time.Duration  // default 60s if zero
    Severity   string         // "info" | "warn" | "critical"
    Message    string         // template body (with {field} subs)
}
```

`RuleKind` is mostly for UI grouping + metric label cardinality bounding ([`types.go`](../../internal/alerts/types.go)).

`Predicate` is shared with the backtest engine (`alerts.Predicate` / `alerts.Snapshot` are reused there — see [`05-time-machine.md`](05-time-machine.md)). Same logic, two contexts.

## REST API

```
GET    /api/alerts/rules                 list (paginated)
POST   /api/alerts/rules                 create or upsert
DELETE /api/alerts/rules/{id}            remove
```

User identity priority ([`api/alerts.go`](../../internal/api/alerts.go) — `callerOwnerID`):

```go
func callerOwnerID(r *http.Request) string {
    if k, ok := apikey.FromContext(r.Context()); ok {
        if k.ParentUserID != "" {
            return k.ParentUserID
        }
        return strconv.FormatInt(k.ID, 10)
    }
    return r.Header.Get("X-User-ID")    // dev escape hatch
}
```

The resolved API key wins when `apikey.Middleware` is mounted (parent_user_id preferred, falling back to the stringified key id); the `X-User-ID` header is only honoured when `APIKEY_ENABLED=false` so a logged-in caller can't spoof someone else's tenant by setting the header.

Pagination ([`api/alerts.go:63`](../../internal/api/alerts.go#L63)):

```
GET /api/alerts/rules?limit=N&offset=M

  limit  ∈ [1, 200], default 50
  offset ∈ [0, 2^31-1], default 0

  Response:
    {
      "rules":  [Rule, ...],
      "total":  int,
      "offset": int,
      "limit":  int
    }
```

`Engine.ListRulesPage(userID, offset, limit)` is the canonical paginated reader; `Engine.ListRules(userID)` keeps the bare-slice signature for callers that don't want pages.

Audit emission ([`api/alerts.go`](../../internal/api/alerts.go)) on upsert + delete uses the same `auth.AuditSink` the auth handlers use, so login + rule mutation events live on one log stream.

## Sinks

Two ship in-tree:

### `BrokerSink`

[`internal/api/alerts.go:149`](../../internal/api/alerts.go#L149):

```go
func (b *BrokerSink) Deliver(t alerts.Trigger) error {
    data, _ := json.Marshal(t)
    sym := feed.ParseSymbol(strings.ToUpper(t.Symbol))
    b.Broker.Publish(Snapshot{
        Symbol: sym,
        Kind:   StateKindAlert,
        Data:   data,
        TsNs:   t.TsNs,
    })
    return nil
}
```

So WebSocket clients subscribed to `kind=alert` (or no filter) receive triggers as part of their normal stream. Same dispatch path as state snapshots — no separate channel.

### `WebhookSink` — with SSRF guard

[`internal/alerts/delivery.go`](../../internal/alerts/delivery.go) `NewWebhookSink(url)` validates the URL **at construction time**:

```
reject if scheme != http | https
reject if host is empty
resolve host → []net.IP
for each ip:
    reject if ip.IsLoopback()              ← 127.0.0.0/8, ::1
    reject if ip.IsPrivate()               ← RFC 1918
    reject if ip.IsLinkLocalUnicast()      ← 169.254.0.0/16 (cloud metadata!)
    reject if ip.IsLinkLocalMulticast()
    reject if ip.IsInterfaceLocalMulticast()
    reject if ip.IsUnspecified()           ← 0.0.0.0
    reject if ip is in 100.64.0.0/10       ← CGNAT
    reject if ip is in fc00::/7            ← IPv6 ULA
    reject if ip is "site-local"
```

Returns `ErrWebhookBlockedTarget` if any blocklist hit. **The validation runs at sink construction**, not on first fire — so a hostile rule fails at the API layer (`POST /api/alerts/rules` returns 400), not at delivery time. The user gets immediate feedback.

`Deliver` itself is async: the engine calls `Deliver`, which queues a goroutine to POST. Failures bump `flowgreeks_alerts_webhook_async_errors_total` since the synchronous `error` return only signals queue submission, not endpoint success.

## Cooldown gate

```go
if r.Cooldown <= 0 {
    r.Cooldown = 60 * time.Second  // default
}
if now.Before(r.lastFired.Add(r.Cooldown)) {
    cooldownSuppressed.WithLabelValues(string(r.Kind)).Inc()
    continue
}
r.lastFired = now
```

Prevents a noisy rule from spamming. Default 60s; overridable per rule.

The check `s.TsNs == 0 → use wall clock` ([`engine.go:118-121`](../../internal/alerts/engine.go#L118)) was a deep-review fix — `time.Unix(0, 0).IsZero()` returns FALSE (epoch is 1970-01-01, not Go's zero time), so the old code evaluated cooldown against 1970 for any snapshot with `TsNs == 0`. Now we check `TsNs` directly and substitute `time.Now()` when it's zero.

## Metrics

[`internal/alerts/metrics.go`](../../internal/alerts/metrics.go):

```
flowgreeks_alerts_rules                          gauge
flowgreeks_alerts_evaluations_total              counter
flowgreeks_alerts_fires_total{kind=...}          counter
flowgreeks_alerts_cooldown_suppressed_total{kind=...}
flowgreeks_alerts_deliveries_total{sink=...}
flowgreeks_alerts_delivery_errors_total{sink=...}
flowgreeks_alerts_webhook_async_errors_total
```

Cardinality stays bounded: `kind` is a small enum, `sink` is a fixed list. No per-user / per-rule labels.

## Trigger shape

```go
type Trigger struct {
    RuleID    string
    UserID    string
    Symbol    string
    Kind      RuleKind
    Severity  string
    Message   string  // template-expanded
    TsNs      uint64  // copied from snap
    Snapshot  Snapshot
}
```

The `Message` template supports `{field}` placeholders that resolve from `Snapshot` — e.g. `"NetGEX flipped to {netgex} at spot {spot}"`. Field set: `spot, netgex, dpi, charm_velocity, pin_top_strike, pin_top_prob`, regime + zone enums by name. Implementation in `internal/alerts/engine.go` `expandMessage`.

## Concurrency contract

| Operation | Concurrency |
|---|---|
| `AddRule`, `RemoveRule` | Lock; cheap |
| `ListRules`, `ListRulesPage` | RLock; copies under lock then sorts |
| `OnSnapshot` | Single hot goroutine (NATS subscriber); RLock to copy `*Rule` slice, release, evaluate |
| `dispatch` | Inherits RLock from caller? **No** — `dispatch` re-takes `sinksMu.RLock` separately so a slow sink can't block rule CRUD |

`Sink.Deliver` is called synchronously from `dispatch` but every shipped sink is non-blocking (broker = chan send with default; webhook = goroutine fan-out). A future blocking sink would need its own dispatcher.

## Test coverage map

| Test | Covers |
|---|---|
| `TestEngine_AddListRemove` | CRUD + sorted order |
| `TestEngine_ListRulesPage` | pagination total + offset + limit |
| `TestEngine_FiresOnce` | basic match path |
| `TestEngine_CooldownSuppresses` | cooldown gate |
| `TestEngine_TsNsZeroUsesWallClock` | regression for the `time.Unix(0,0)` epoch trap |
| `TestEngine_PerUserScoping` | rules scoped to UserID |
| `TestNewWebhookSink_RejectsLoopback` | SSRF guard on 127.x |
| `TestNewWebhookSink_RejectsPrivate` | RFC 1918 |
| `TestNewWebhookSink_RejectsCloudMetadata` | 169.254.169.254 |
| `TestNewWebhookSink_RejectsCGNAT` | 100.64/10 |
| `TestNewWebhookSink_RejectsIPv6ULA` | fc00::/7 |
| `TestNewWebhookSink_AcceptsPublic` | normal URL accepted |
| `TestBrokerSink_PublishesAsAlertSnapshot` | wire shape |

All in `internal/alerts/*_test.go`.

## What this section does **not** cover

- Predicate semantics for backtest reuse → see [`05-time-machine.md`](05-time-machine.md).
- WS broker fanout mechanics → see [`01-data-pipeline.md`](01-data-pipeline.md) §8.
- `auth.AuditSink` — the audit infrastructure that the alerts handler also uses → see [`02-auth.md`](02-auth.md).
