// Package trace provides request-scoped trace ID propagation across
// binaries.
//
// FlowGreeks has multiple processes (api, ingest, compute, replay) that
// communicate via NATS. A single user-facing operation —
// "run a backtest", "start a replay session", "evaluate a simulator
// scenario" — can touch several binaries. Without a shared id, slog
// lines from each binary can't be correlated to the originating request.
//
// Scope: request-level operations only. We deliberately do NOT tag every
// tick or per-second state publish — those are too high-volume and the
// trace id would dominate log size for no debugging value. Use this for:
//
//   - HTTP requests on the api binary (chi RequestID ↔ trace ID)
//   - Replay session lifecycle (one trace per session)
//   - Backtest runs (one trace per run)
//   - Auth flows (one trace per signup/login)
//   - Alerts engine actions (rule eval is per-snapshot; only CRUD)
//
// On NATS, propagation is via the "X-Trace-ID" header (NATS 2.x+ support
// nats.Header on Msg). Subscribers attach the header value to their
// context before processing.
package trace

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"net/http"
	"strings"
)

// HeaderName is the convention used on both HTTP and NATS messages.
const HeaderName = "X-Trace-ID"

type ctxKey struct{}

// NewID generates a fresh 8-byte hex trace ID. Short enough to read in
// logs, large enough that collisions over a typical session are
// astronomically unlikely.
func NewID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "00000000"
	}
	return hex.EncodeToString(b[:])
}

// WithID attaches id to ctx so downstream code can read it via FromContext.
// Empty id is a no-op so callers can pipe through optional inputs without
// a guard.
func WithID(ctx context.Context, id string) context.Context {
	id = strings.TrimSpace(id)
	if id == "" {
		return ctx
	}
	return context.WithValue(ctx, ctxKey{}, id)
}

// FromContext returns the trace id attached to ctx, or "" if none.
func FromContext(ctx context.Context) string {
	if ctx == nil {
		return ""
	}
	v := ctx.Value(ctxKey{})
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

// EnsureID returns ctx with a trace id; if none is present, generates a
// fresh one. Always returns a non-empty id alongside the context.
func EnsureID(ctx context.Context) (context.Context, string) {
	if id := FromContext(ctx); id != "" {
		return ctx, id
	}
	id := NewID()
	return WithID(ctx, id), id
}

// Logger returns log with a "trace_id" attribute pulled from ctx, or
// the original logger unchanged if no trace id is set. Use everywhere
// a *slog.Logger is needed inside a request scope.
func Logger(ctx context.Context, log *slog.Logger) *slog.Logger {
	id := FromContext(ctx)
	if id == "" || log == nil {
		return log
	}
	return log.With("trace_id", id)
}

// FromHTTP pulls the trace id from an HTTP request. Honors X-Trace-ID
// header from upstream callers; falls back to chi's request_id when
// available. Empty when neither is present — caller decides whether to
// generate a fresh one (typically yes via EnsureID).
func FromHTTP(r *http.Request) string {
	if r == nil {
		return ""
	}
	if v := r.Header.Get(HeaderName); v != "" {
		return v
	}
	if v := r.Header.Get("X-Request-ID"); v != "" {
		return v
	}
	return ""
}

// Inject sets the trace id from ctx onto an HTTP header set, so outbound
// requests keep the chain. No-op when no trace id is set.
func Inject(ctx context.Context, h http.Header) {
	if id := FromContext(ctx); id != "" && h != nil {
		h.Set(HeaderName, id)
	}
}
