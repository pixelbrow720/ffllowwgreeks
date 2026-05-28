package api

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"time"

	"flowgreeks/internal/feed"

	"github.com/coder/websocket"
)

// WSMessage is what we accept from clients. The protocol is small —
// only "subscribe" and "unsubscribe" actions for now. Server-pushed
// snapshots use the raw compute Data plus a {symbol, kind} envelope.
type WSMessage struct {
	Action  string   `json:"action"`            // "subscribe" | "unsubscribe"
	Symbols []string `json:"symbols,omitempty"` // ["spx", "ndx"]
	Kinds   []string `json:"kinds,omitempty"`   // ["gex"]
}

// WSEvent wraps a Snapshot for wire delivery.
type WSEvent struct {
	Type   string          `json:"type"` // "snapshot" | "ack" | "error" | "heartbeat"
	Symbol string          `json:"symbol,omitempty"`
	Kind   string          `json:"kind,omitempty"`
	TsNs   uint64          `json:"ts_ns,omitempty"`
	Data   json.RawMessage `json:"data,omitempty"`
	Error  string          `json:"error,omitempty"`
}

// LiveHandler serves the /ws/live endpoint.
//
// Origins must be set explicitly. An empty Origins list means "deny
// any cross-origin upgrade" so a logged-in user visiting a malicious
// site can't have JS open a WS to /ws/live and exfiltrate live state.
// To run a same-origin dev server (frontend served by the api itself),
// the upgrade still succeeds because Accept skips the Origin check
// when Origin == Host. To allow a separate dev origin (e.g. Next.js
// on :3000), set Origins explicitly via API_CORS_ORIGINS.
type LiveHandler struct {
	Broker  *Broker
	Cache   *Cache
	Log     *slog.Logger
	Origins []string
}

// maxInboundMessageBytes caps a single client → server payload. The
// only inbound shape we accept is a small subscribe/unsubscribe JSON;
// anything beyond ~4KiB is either a malformed client or an attempt to
// pin server memory. The connection is killed on overflow.
const maxInboundMessageBytes = 4096

// ServeHTTP upgrades the connection and runs the read+write loops.
func (h *LiveHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	acceptOpts := &websocket.AcceptOptions{
		// Empty -> coder/websocket only allows same-origin upgrades.
		// Non-empty -> match against this allowlist (supports glob).
		OriginPatterns: h.Origins,
	}

	c, err := websocket.Accept(w, r, acceptOpts)
	if err != nil {
		h.Log.Warn("ws accept failed", "err", err, "origin", r.Header.Get("Origin"))
		return
	}
	c.SetReadLimit(maxInboundMessageBytes)
	defer c.CloseNow()

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()

	sub := h.Broker.Subscribe(256, SubFilter{})
	defer h.Broker.Unsubscribe(sub)

	// Read loop: drains incoming control messages.
	go h.readLoop(ctx, cancel, c, sub)

	// Write loop: serves snapshots + heartbeat.
	heartbeat := time.NewTicker(15 * time.Second)
	defer heartbeat.Stop()

	for {
		select {
		case <-ctx.Done():
			return

		case <-heartbeat.C:
			if err := writeJSONWS(ctx, c, WSEvent{Type: "heartbeat", TsNs: uint64(time.Now().UnixNano())}); err != nil {
				return
			}

		case ev, ok := <-sub.Events():
			if !ok {
				return
			}
			if err := writeJSONWS(ctx, c, WSEvent{
				Type:   "snapshot",
				Symbol: strings.ToLower(ev.Symbol.String()),
				Kind:   string(ev.Kind),
				TsNs:   ev.TsNs,
				Data:   ev.Data,
			}); err != nil {
				return
			}
		}
	}
}

func (h *LiveHandler) readLoop(ctx context.Context, cancel context.CancelFunc, c *websocket.Conn, sub *Subscriber) {
	defer cancel()
	for {
		_, data, err := c.Read(ctx)
		if err != nil {
			if !errors.Is(err, context.Canceled) {
				h.Log.Debug("ws read end", "err", err)
			}
			return
		}
		var msg WSMessage
		if err := json.Unmarshal(data, &msg); err != nil {
			_ = writeJSONWS(ctx, c, WSEvent{Type: "error", Error: "bad message: " + err.Error()})
			continue
		}
		switch msg.Action {
		case "subscribe":
			applyFilter(sub, msg)
			// Push last-known snapshots so the client sees state
			// immediately instead of waiting for the next compute
			// publish (up to 1s gap). Marked type=snapshot.replay so
			// clients can distinguish a primer from a live tick.
			if h.Cache != nil {
				for _, snap := range h.Cache.SnapshotsFor(sub.snapshotFilter()) {
					_ = writeJSONWS(ctx, c, WSEvent{
						Type:   "snapshot.replay",
						Symbol: strings.ToLower(snap.Symbol.String()),
						Kind:   string(snap.Kind),
						TsNs:   snap.TsNs,
						Data:   snap.Data,
					})
				}
			}
			_ = writeJSONWS(ctx, c, WSEvent{Type: "ack"})
		case "unsubscribe":
			applyFilter(sub, msg)
			_ = writeJSONWS(ctx, c, WSEvent{Type: "ack"})
		default:
			_ = writeJSONWS(ctx, c, WSEvent{Type: "error", Error: "unknown action: " + msg.Action})
		}
	}
}

// applyFilter mutates the Subscriber's filter — additive on subscribe,
// subtractive on unsubscribe. Empty filter means "everything". Holds
// filterMu so a concurrent Broker.Publish probing matches() doesn't
// race the map writes.
func applyFilter(sub *Subscriber, m WSMessage) {
	sub.filterMu.Lock()
	defer sub.filterMu.Unlock()
	if sub.filter.Symbols == nil {
		sub.filter.Symbols = make(map[feed.Symbol]struct{})
	}
	if sub.filter.Kinds == nil {
		sub.filter.Kinds = make(map[StateKind]struct{})
	}
	for _, s := range m.Symbols {
		sym := feed.ParseSymbol(strings.ToUpper(s))
		if sym == feed.SymbolUnknown {
			continue
		}
		if m.Action == "subscribe" {
			sub.filter.Symbols[sym] = struct{}{}
		} else {
			delete(sub.filter.Symbols, sym)
		}
	}
	for _, k := range m.Kinds {
		kind := StateKind(strings.ToLower(k))
		if m.Action == "subscribe" {
			sub.filter.Kinds[kind] = struct{}{}
		} else {
			delete(sub.filter.Kinds, kind)
		}
	}
}

func writeJSONWS(ctx context.Context, c *websocket.Conn, ev WSEvent) error {
	wctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	b, err := json.Marshal(ev)
	if err != nil {
		return err
	}
	return c.Write(wctx, websocket.MessageText, b)
}
