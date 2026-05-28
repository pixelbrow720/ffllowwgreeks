package trace

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/nats-io/nats.go"
)

func TestNewID_Length(t *testing.T) {
	id := NewID()
	if len(id) != 16 {
		t.Errorf("expected 16 hex chars, got %d (%q)", len(id), id)
	}
}

func TestNewID_Unique(t *testing.T) {
	seen := map[string]bool{}
	for i := 0; i < 100; i++ {
		id := NewID()
		if seen[id] {
			t.Fatalf("collision after %d ids: %s", i, id)
		}
		seen[id] = true
	}
}

func TestWithID_FromContext(t *testing.T) {
	ctx := WithID(context.Background(), "abc123")
	if got := FromContext(ctx); got != "abc123" {
		t.Errorf("got %q, want abc123", got)
	}
}

func TestWithID_EmptyIsNoop(t *testing.T) {
	base := context.Background()
	ctx := WithID(base, "")
	if got := FromContext(ctx); got != "" {
		t.Errorf("empty id should not attach, got %q", got)
	}
}

func TestEnsureID_Generates(t *testing.T) {
	ctx, id := EnsureID(context.Background())
	if id == "" {
		t.Fatal("EnsureID returned empty id on bare context")
	}
	if FromContext(ctx) != id {
		t.Error("EnsureID context did not carry generated id")
	}
}

func TestEnsureID_Preserves(t *testing.T) {
	pre := WithID(context.Background(), "preset")
	ctx, id := EnsureID(pre)
	if id != "preset" {
		t.Errorf("EnsureID overwrote existing id, got %q", id)
	}
	if FromContext(ctx) != "preset" {
		t.Error("EnsureID dropped existing id from ctx")
	}
}

func TestFromHTTP_HeaderPrecedence(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.Header.Set("X-Request-ID", "req-fallback")
	r.Header.Set("X-Trace-ID", "trace-primary")
	if got := FromHTTP(r); got != "trace-primary" {
		t.Errorf("X-Trace-ID should win, got %q", got)
	}
}

func TestFromHTTP_FallsBackToRequestID(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.Header.Set("X-Request-ID", "req-only")
	if got := FromHTTP(r); got != "req-only" {
		t.Errorf("expected fallback to X-Request-ID, got %q", got)
	}
}

func TestInject_HTTP(t *testing.T) {
	ctx := WithID(context.Background(), "abc")
	h := http.Header{}
	Inject(ctx, h)
	if got := h.Get(HeaderName); got != "abc" {
		t.Errorf("expected header %q, got %q", "abc", got)
	}
}

func TestNATS_Roundtrip(t *testing.T) {
	ctx := WithID(context.Background(), "nats-trace")
	m := &nats.Msg{Subject: "x", Data: []byte("y")}
	InjectNATS(ctx, m)
	if got := FromNATS(m); got != "nats-trace" {
		t.Errorf("FromNATS got %q, want nats-trace", got)
	}
}

func TestNATS_EmptyCtxLeavesHeaderNil(t *testing.T) {
	m := &nats.Msg{Subject: "x"}
	InjectNATS(context.Background(), m)
	if m.Header != nil {
		t.Errorf("empty ctx should not allocate Header, got %v", m.Header)
	}
}

func TestFromNATS_NilSafe(t *testing.T) {
	if got := FromNATS(nil); got != "" {
		t.Errorf("expected empty for nil msg, got %q", got)
	}
	m := &nats.Msg{Subject: "x"}
	if got := FromNATS(m); got != "" {
		t.Errorf("expected empty when no header, got %q", got)
	}
}
