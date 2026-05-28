package trace

import (
	"context"

	"github.com/nats-io/nats.go"
)

// FromNATS pulls the trace id from a NATS message header, returning "" if
// either the header set is nil or the key is absent. NATS messages
// without a Header field (older clients) return "" gracefully.
func FromNATS(m *nats.Msg) string {
	if m == nil || m.Header == nil {
		return ""
	}
	return m.Header.Get(HeaderName)
}

// InjectNATS prepares a *nats.Msg with the trace id from ctx attached as
// a header. Caller is responsible for setting m.Subject and m.Data;
// this only ensures the header set exists and contains the trace id.
// Returns the message unchanged if no trace id is set on ctx.
func InjectNATS(ctx context.Context, m *nats.Msg) *nats.Msg {
	if m == nil {
		return nil
	}
	id := FromContext(ctx)
	if id == "" {
		return m
	}
	if m.Header == nil {
		m.Header = nats.Header{}
	}
	m.Header.Set(HeaderName, id)
	return m
}

// PublishMsg builds a *nats.Msg with subject + data + trace header from
// ctx, and publishes it via nc. This is the canonical replacement for
// `nc.Publish(subject, data)` whenever the call site has a request-scoped
// ctx — the ergonomics stay one-line.
func PublishMsg(ctx context.Context, nc *nats.Conn, subject string, data []byte) error {
	if nc == nil {
		return nil
	}
	m := &nats.Msg{Subject: subject, Data: data}
	InjectNATS(ctx, m)
	return nc.PublishMsg(m)
}
