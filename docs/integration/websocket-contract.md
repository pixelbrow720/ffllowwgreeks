# WebSocket contract reference

Source files: `backend/internal/api/{state,ws,alerts}.go`,
`backend/internal/replay/{ws,session}.go`, `backend/internal/bus/subjects.go`,
`backend/cmd/compute/main.go`, `backend/cmd/api/main.go`.

Validated against commit: `e5edb6da854b705291034818101b55e899862abf` (branch `main`).

Endpoints (both `coder/websocket`, text frames, JSON):
`/ws/live` — live state snapshots + narrative + alerts.
`/ws/replay/{session_id}` — control plane for one historical replay.

---

## /ws/live

### Connection

- URL: `ws://<host>:<port>/ws/live` (registered at `backend/cmd/api/main.go:168`).
- Auth: **NOT behind `apiKeyMW`**. The handler is mounted on the root router,
  not the `protected` subrouter (`backend/cmd/api/main.go:160-168`). Treat as
  open today; expect this to change once the flowjob.id key gate is wired.
- Origin policy (`backend/internal/api/ws.go:60-65`): the handler passes
  `Origins` straight to `websocket.AcceptOptions.OriginPatterns`. Empty
  list = same-origin only (request `Origin` must equal `Host`). Configure
  cross-origin allowlist via `API_CORS_ORIGINS` (env → `cfg.API.CORSOrigins`,
  glob patterns supported).
- Read limit: **4096 bytes** per client → server frame
  (`backend/internal/api/ws.go:56,71`). Overflow closes the connection.
- Per-connection broker buffer: 256 events (`backend/internal/api/ws.go:77`).
- Write timeout: 5 s per outbound frame (`backend/internal/api/ws.go:191-198`).

### Client → server messages

Schema (`backend/internal/api/ws.go:20-24`):

```json
{ "action": "subscribe" | "unsubscribe",
  "symbols": ["spx", "ndx"],
  "kinds":   ["gex", "narrative", "alert"] }
```

Symbols are case-insensitive; only `SPX` and `NDX` resolve, others are silently
ignored (`backend/internal/api/ws.go:170-174`). Kinds are lower-cased into a
`StateKind` string (no validation — unknown kinds simply never match).

Filter semantics (`backend/internal/api/state.go:90-102`,
`backend/internal/api/ws.go:161-189`):

- Empty `Symbols` map ⇒ "any symbol". Empty `Kinds` map ⇒ "any kind".
- `subscribe` is **additive** to the current filter sets.
- `unsubscribe` is **subtractive** (removes those entries from the sets).
- A connection starts with empty filters, i.e. receives everything until
  it narrows.

#### action: `subscribe`

1. Server merges `symbols` + `kinds` into the subscriber's filter.
2. Server immediately replays cached snapshots that match the new filter as
   `type=snapshot.replay` events (one per cached `(symbol, kind)`)
   (`backend/internal/api/ws.go:136-146`). The cache holds the last
   `state.<sym>.<kind>` per pair (`backend/internal/api/state.go:74-85`);
   `narrative.<sym>` events are **not** cached.
3. Server then writes `{"type":"ack"}`.

#### action: `unsubscribe`

1. Server removes the supplied entries from the filter.
2. Server writes `{"type":"ack"}`. No replay.

Any unknown `action` ⇒ `{"type":"error","error":"unknown action: <name>"}`.
Malformed JSON ⇒ `{"type":"error","error":"bad message: ..."}`. Connection
stays open in both cases.

### Server → client envelope

Single shape (`backend/internal/api/ws.go:27-34`):

```json
{ "type":   "snapshot" | "snapshot.replay" | "ack" | "error" | "heartbeat",
  "symbol": "spx",
  "kind":   "gex",
  "ts_ns":  1717000000000000000,
  "data":   { ... },
  "error":  "..." }
```

`symbol` is lower-cased (`backend/internal/api/ws.go:103,140`). `kind` is the
raw `StateKind` string. `data` is the raw JSON published on NATS — the api
does not re-marshal it (`backend/internal/api/state.go:38-43,252`).

#### type=`snapshot` and type=`snapshot.replay`

Same envelope. `replay` is emitted only as the immediate primer after a
subscribe; `snapshot` is the live fan-out. Frontends can dedupe on
`(symbol, kind, ts_ns)` if they want to ignore the primer.

##### kind=`gex`

- Triggered by NATS subject `state.<sym>.gex`
  (`backend/internal/bus/subjects.go:64-67`,
   `backend/internal/api/state.go:238-260`).
- `data` is the struct published at `backend/cmd/compute/main.go:618-673`:

```json
{
  "ts_ns": 1717000000000000000,
  "symbol": 1, "spot": 5832.10,
  "basis_smooth": -1.84, "fut_front_sym": "ESM6",
  "net_gex": -1234567890.0, "zero_gamma": 5810.0,
  "call_wall": 5850.0, "put_wall": 5780.0,
  "expected_mv": 0.42, "regime": 0,
  "dpi": { "composite": 0.71, "net_gamma_sign": -1,
           "charm_velocity": 0.34, "vanna_sensitivity": 0.12,
           "time_to_close_decay": 0.55, "flow_concentration": 0.41 },
  "flow_pulse": { "gamma": 0.1, "charm": 0.0, "vanna": -0.2, "total": -0.1 },
  "charm_zone": 2, "charm_velocity_raw": 0.34,
  "pin": {
    "active": true, "window_mins": 45.0,
    "top_strike": 5825.0, "top_probability": 0.62,
    "candidates": [
      { "strike": 5825.0, "probability": 0.62, "gamma_strength": 1.0,
        "distance_factor": 0.9, "flow_persistence": 0.8, "time_factor": 0.7 }
    ]
  },
  "strikes": [
    { "expiry": 20260620, "strike": 5810000, "side": 0,
      "dealer_pos": -1234, "iv": 0.18, "gamma": 0.0034,
      "charm": -0.0001, "vanna": 0.002, "gex_notional": -1.2e8 }
  ]
}
```

`symbol` (the int code inside `data`) is `feed.Symbol` numeric: `SPX=1`,
`NDX=2`. Use the envelope's `symbol` string for routing.

##### kind=`narrative`

- Triggered by NATS subject `narrative.<sym>`
  (`backend/internal/bus/subjects.go:72-74`,
   `backend/internal/api/state.go:262-282`).
- Not cached: clients only see narrative events that arrive while connected.
- `data` shape (`backend/cmd/compute/main.go:733-742`):

```json
{ "ts_ns": 1717000000000000000,
  "tag":   "PIN",
  "text":  "Pin tightening near 5825 — top prob 0.62, 45m to close.",
  "refs":  { "strike": 5825.0, "probability": 0.62 } }
```

Tags emitted by `internal/narrative` today: `PIN`, `REGIME`, `WALL`, `FLOW`,
`DPI`, `VOL` (`backend/internal/narrative/engine.go:25-33`).

##### kind=`alert`

- Triggered **in-process** by `alerts.Engine` evaluating `state.<sym>.gex`
  snapshots and calling `BrokerSink.Deliver`
  (`backend/internal/api/alerts.go:218-236`,
   `backend/internal/api/alerts.go:242-262`).
- `data` is an `alerts.Trigger` (`backend/internal/alerts/types.go:58-66`):

```json
{ "rule_id": "dpi-hot",
  "user_id": "u_42",
  "symbol":  "SPX",
  "kind":    "dpi_above",
  "ts_ns":   1717000000000000000,
  "text":    "DPI 0.83 > threshold 0.75",
  "refs":    { "dpi": 0.83 } }
```

Note: the inner `symbol` is upper-case (raw owner-facing field); the envelope
`symbol` is lower-case ("spx"). Use the envelope for routing.

#### type=`ack`

`{"type":"ack"}`. No other fields. Sent after every accepted subscribe /
unsubscribe.

#### type=`error`

`{"type":"error","error":"<reason>"}`. Connection stays open.

#### type=`heartbeat`

Every 15 s (`backend/internal/api/ws.go:84,92-95`).
`{"type":"heartbeat","ts_ns":<server unix nanos>}`. Server-initiated only;
clients do not need to ack. Used both as keepalive and to surface server
liveness when no live ticks are flowing.

### Drop-on-slow-client

`Broker.Publish` non-blocking sends into each subscriber's 256-buffer channel
(`backend/internal/api/state.go:129-144`). On a full buffer:

- The event is **dropped silently** for that subscriber (no disconnect).
- The subscriber's `dropped` atomic counter increments.
- The Prometheus counter `flowgreeks_ws_drops_total{symbol,kind}` ticks.

There is no resume / replay-after-drop mechanism beyond reconnecting (which
gets a fresh `snapshot.replay` for the cached kinds — narrative events
between the drop and reconnect are lost).

---

## /ws/replay/{session_id}

### Connection

- URL: `ws://<host>:<port>/ws/replay/<session_id>` (mounted at
  `backend/cmd/api/main.go:174-175` with chi pattern `/ws/replay/*`).
- Available only when the api booted with a working Postgres pool
  (`backend/cmd/api/main.go:173,178`); otherwise the route is not registered
  and clients see 404.
- Auth: same posture as `/ws/live` — not on the `protected` subrouter.
- Origin policy: same as `/ws/live` (`OriginPatterns: cfg.API.CORSOrigins`).
- Read limit: **1024 bytes** per inbound frame
  (`backend/internal/replay/ws.go:67,98`).
- Status subscriber buffer: 64 (`backend/internal/replay/ws.go:119`).

If the `session_id` is unknown, the handler creates a new session from the
query string and starts it (`backend/internal/replay/ws.go:104-117`):

| Param   | Required | Format             | Notes |
| ------- | -------- | ------------------ | ----- |
| symbol  | yes      | `spx` / `ndx`      | Case-insensitive. |
| date    | one of   | `YYYY-MM-DD`       | Range 13:30→20:15 UTC. |
| start   | one of   | RFC3339            | Pair with `end`. |
| end     | one of   | RFC3339            | Pair with `start`. |
| speed   | no       | float, default 4.0 | 0 = unpaced, 1 = real-time, N× faster. |

If the `session_id` already exists, the handler attaches as an additional
status subscriber — multiple WS clients per session are supported.

### Client → server (control plane)

Schema (`backend/internal/replay/ws.go:59-62`):

```json
{ "action": "pause" | "resume" | "set_speed" | "stop",
  "speed":  2.5 }
```

| action       | extra field | server effect                                            |
| ------------ | ----------- | -------------------------------------------------------- |
| `pause`      | —           | `Session.Pause()` — idempotent, freezes the run loop.    |
| `resume`     | —           | `Session.Resume()` — idempotent.                         |
| `set_speed`  | `speed`     | Atomic speed change; takes effect on next tick boundary. Negative is clamped to 0 (unpaced). |
| `stop`       | —           | `Session.Stop()` — terminal; transitions to `stopped`.   |

Each accepted action is echoed with `{"type":"ack"}`. Unknown action ⇒
`{"type":"error","error":"unknown action: <name>"}`. Malformed JSON ⇒
`{"type":"error","error":"bad message: ..."}`. Connection stays open.

### Server → client envelope

Schema (`backend/internal/replay/ws.go:51-56`):

```json
{ "type":   "status" | "ack" | "error" | "heartbeat",
  "ts_ns":  1717000000000000000,
  "error":  "...",
  "status": { ... SessionStatus ... } }
```

#### type=`status`

Sent immediately on subscribe (current snapshot) and on every state change.
`status` payload (`backend/internal/replay/session.go:31-42`):

```json
{ "id":         "sess-2026-05-21-spx",
  "state":      "playing",
  "speed":      4.0,
  "start_ts":   "2026-05-21T13:30:00Z",
  "end_ts":     "2026-05-21T20:15:00Z",
  "current_ts": "2026-05-21T15:42:13.481Z",
  "published":  124018,
  "errors":     0,
  "updated_at": "2026-05-28T11:30:00.001Z",
  "error":      "" }
```

`state` enum (`backend/internal/replay/session.go:14-23`):
`idle`, `playing`, `paused`, `finished`, `stopped`, `failed`.

The server **closes the connection** after the next status whose `state` is
`finished`, `stopped`, or `failed` (`backend/internal/replay/ws.go:174-176`).

#### type=`ack`, `error`, `heartbeat`

Same shape as `/ws/live`. Heartbeat cadence is 15 s
(`backend/internal/replay/ws.go:155`).

### Drop-on-slow-client

Per-WS status channel buffer is 64; on overflow `Session.updateStatus` drops
the update silently (`backend/internal/replay/session.go:328-334`). The
publish path itself is unaffected — only this client's status feed loses an
update. The next state change will deliver an updated `SessionStatus` so
state ultimately converges; clients should not depend on receiving every
intermediate status.

---

## NATS subject → WS kind mapping

| NATS subject              | Cached? | Envelope `kind`        | Source                                                            |
| ------------------------- | ------- | ---------------------- | ----------------------------------------------------------------- |
| `state.<sym>.gex`         | yes     | `gex`                  | `cmd/compute/main.go:717-720`                                     |
| `state.<sym>.<other>`     | yes     | `<other>` (raw string) | Reserved for `dpi`, `charm`, `vanna`, `flow`, `flow_pulse`, `basis`, `regime`, `pin` (`internal/bus/subjects.go:79-89`). Not currently published. |
| `narrative.<sym>`         | no      | `narrative`            | `cmd/compute/main.go:732-749`                                     |
| (in-process via `BrokerSink`) | no  | `alert`                | `internal/api/alerts.go:218-236` — bridged from `alerts.Engine` reading `state.<sym>.gex`. |

`<sym>` is lower-cased on the wire (`spx`, `ndx`); the envelope's `symbol`
matches.

---

## Frontend integration checklist

- [ ] Reconnect with exponential backoff on close (server has no resume
      protocol; treat every reconnect as a fresh session).
- [ ] On connect, send a single `subscribe` listing the desired symbols and
      kinds. Do not send wildcards; just enumerate.
- [ ] Treat the burst of `type=snapshot.replay` after the `ack` as priming
      state — render it the same way as `snapshot`, but optionally suppress
      animations / transitions for it.
- [ ] Track per-`(symbol, kind)` `ts_ns` and discard out-of-order events
      (the broker is best-effort and a slow-drop can cause apparent reorder
      across kinds).
- [ ] Implement a heartbeat watchdog: if no `heartbeat` or `snapshot` in
      30 s, close the socket and reconnect.
- [ ] Cap inbound subscribe frames ≤ 4 KiB; the server enforces this and
      kills the connection on overflow.
- [ ] Lower-case symbol fields throughout — both envelopes use lower-case;
      API key paths and REST query params should match.
- [ ] For replay clients, expect the connection to terminate cleanly after a
      terminal `status` (`finished`, `stopped`, `failed`) — do not auto-
      reconnect on those.
- [ ] Replay control: clamp `set_speed.speed ≥ 0`. Use `0` for unpaced bulk.
- [ ] Surface drop counts via REST (or Prometheus) for ops visibility — the
      WS does not advertise its own drop count today.
