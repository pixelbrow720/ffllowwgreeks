package replay

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"flowgreeks/internal/feed"

	"github.com/coder/websocket"
)

// WSHandler serves /ws/replay/{session_id}.
//
// Connection protocol:
//
//   1. Client dials ws://.../ws/replay/{session_id}?symbol=spx&date=2026-05-21&speed=4
//      If the session_id is not active, the handler creates it from
//      query params and starts it. If active, the handler attaches as
//      an additional status subscriber.
//
//   2. Server immediately sends current SessionStatus as a "status" event.
//
//   3. Client may send control messages:
//        {"action":"pause"}
//        {"action":"resume"}
//        {"action":"set_speed","speed":2}
//        {"action":"stop"}
//
//   4. Server pushes status events as the session changes state, plus
//      a 15s heartbeat.
//
// Multiple WS clients per session are supported — each attaches its
// own status subscription.
//
// Origins must be set explicitly. Empty list means same-origin only —
// matches the /ws/live default-deny posture.
type WSHandler struct {
	Manager *Manager
	Log     *slog.Logger
	Origins []string
}

// WSEvent is the wire envelope.
type WSEvent struct {
	Type   string         `json:"type"` // "status" | "ack" | "error" | "heartbeat"
	Status *SessionStatus `json:"status,omitempty"`
	Error  string         `json:"error,omitempty"`
	TsNs   uint64         `json:"ts_ns,omitempty"`
}

// WSControl is what we accept from clients.
type WSControl struct {
	Action string  `json:"action"`
	Speed  float64 `json:"speed,omitempty"`
}

// replayMaxInboundBytes caps a single client → server payload. The
// only inbound shape is a small control JSON; anything beyond ~1KiB
// is malformed or hostile.
const replayMaxInboundBytes = 1024

// IDFromPath returns the session id segment from a request path of
// shape /ws/replay/{id}. Empty when no segment is present.
func IDFromPath(p string) string {
	const prefix = "/ws/replay/"
	if !strings.HasPrefix(p, prefix) {
		return ""
	}
	rest := p[len(prefix):]
	if i := strings.Index(rest, "/"); i >= 0 {
		rest = rest[:i]
	}
	return rest
}

// ServeHTTP upgrades the connection and runs the read+write loops.
func (h *WSHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	id := IDFromPath(r.URL.Path)
	if id == "" {
		http.Error(w, "missing session id", http.StatusBadRequest)
		return
	}

	c, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		OriginPatterns: h.Origins,
	})
	if err != nil {
		h.Log.Warn("ws accept failed", "err", err, "session", id)
		return
	}
	c.SetReadLimit(replayMaxInboundBytes)
	defer c.CloseNow()

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()

	// Look up or create the session.
	sess := h.Manager.Get(id)
	if sess == nil {
		rng, opts, err := parseSessionParams(r)
		if err != nil {
			_ = writeJSON(ctx, c, WSEvent{Type: "error", Error: err.Error()})
			return
		}
		sess, err = h.Manager.Create(ctx, id, rng, opts)
		if err != nil {
			_ = writeJSON(ctx, c, WSEvent{Type: "error", Error: err.Error()})
			return
		}
	}

	statusCh, unsub := sess.Subscribe(64)
	defer unsub()

	// Read loop: control messages.
	go func() {
		defer cancel()
		for {
			_, data, err := c.Read(ctx)
			if err != nil {
				if !errors.Is(err, context.Canceled) {
					h.Log.Debug("ws read end", "err", err)
				}
				return
			}
			var msg WSControl
			if err := json.Unmarshal(data, &msg); err != nil {
				_ = writeJSON(ctx, c, WSEvent{Type: "error", Error: "bad message: " + err.Error()})
				continue
			}
			switch msg.Action {
			case "pause":
				sess.Pause()
			case "resume":
				sess.Resume()
			case "set_speed":
				sess.SetSpeed(msg.Speed)
			case "stop":
				sess.Stop()
			default:
				_ = writeJSON(ctx, c, WSEvent{Type: "error", Error: "unknown action: " + msg.Action})
				continue
			}
			_ = writeJSON(ctx, c, WSEvent{Type: "ack"})
		}
	}()

	hb := time.NewTicker(15 * time.Second)
	defer hb.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-hb.C:
			if err := writeJSON(ctx, c, WSEvent{Type: "heartbeat", TsNs: uint64(time.Now().UnixNano())}); err != nil {
				return
			}
		case st, ok := <-statusCh:
			if !ok {
				return
			}
			stCopy := st
			if err := writeJSON(ctx, c, WSEvent{Type: "status", Status: &stCopy}); err != nil {
				return
			}
			if st.State == SessionFinished || st.State == SessionStopped || st.State == SessionFailed {
				return
			}
		}
	}
}

// parseSessionParams extracts a Range + SessionOptions from the WS query
// string. Required: symbol + (date OR start+end). Optional: speed.
func parseSessionParams(r *http.Request) (Range, SessionOptions, error) {
	q := r.URL.Query()
	symStr := q.Get("symbol")
	if symStr == "" {
		return Range{}, SessionOptions{}, errors.New("missing symbol")
	}
	sym := feed.ParseSymbol(strings.ToUpper(symStr))
	if sym == feed.SymbolUnknown {
		return Range{}, SessionOptions{}, errors.New("unknown symbol")
	}

	var rng Range
	rng.Symbol = sym

	if startStr, endStr := q.Get("start"), q.Get("end"); startStr != "" && endStr != "" {
		s, err := time.Parse(time.RFC3339, startStr)
		if err != nil {
			return Range{}, SessionOptions{}, errors.New("parse start: " + err.Error())
		}
		e, err := time.Parse(time.RFC3339, endStr)
		if err != nil {
			return Range{}, SessionOptions{}, errors.New("parse end: " + err.Error())
		}
		rng.Start = s
		rng.End = e
	} else if dateStr := q.Get("date"); dateStr != "" {
		d, err := time.Parse("2006-01-02", dateStr)
		if err != nil {
			return Range{}, SessionOptions{}, errors.New("parse date: " + err.Error())
		}
		rng.Start = time.Date(d.Year(), d.Month(), d.Day(), 13, 30, 0, 0, time.UTC)
		rng.End = time.Date(d.Year(), d.Month(), d.Day(), 20, 15, 0, 0, time.UTC)
	} else {
		return Range{}, SessionOptions{}, errors.New("missing date or start+end")
	}

	speed := 4.0
	if s := q.Get("speed"); s != "" {
		f, err := strconv.ParseFloat(s, 64)
		if err != nil {
			return Range{}, SessionOptions{}, fmt.Errorf("parse speed: %w", err)
		}
		speed = f
	}
	return rng, SessionOptions{Speed: speed}, nil
}

func writeJSON(ctx context.Context, c *websocket.Conn, ev WSEvent) error {
	wctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	b, err := json.Marshal(ev)
	if err != nil {
		return err
	}
	return c.Write(wctx, websocket.MessageText, b)
}
