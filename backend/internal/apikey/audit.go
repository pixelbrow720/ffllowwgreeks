package apikey

import (
	"context"
	"log/slog"
	"time"
)

// AuditEvent is a structured record of an authentication event.
//
// Cardinality stays low here: the kind enum is small, key_id and
// parent_user_id are bounded by the size of the api_keys table. SIEM
// rules can grep on level (WARN for anomalies) without trawling
// millions of unique labels.
type AuditEvent struct {
	Kind         AuditKind
	KeyID        int64  // 0 when no key was identified yet (e.g. missing creds)
	ParentUserID string // copied from APIKey.ParentUserID when known
	IP           string // best-effort client address
	UserAgent    string // truncated to 256 chars by the sink
	Detail       string // free-form; never include secret material
	OccurredAt   time.Time
}

// AuditKind enumerates the events the apikey surface emits. Keep the
// list small — large taxonomies leak implementation detail into
// telemetry.
type AuditKind string

const (
	AuditAuthOK            AuditKind = "apikey.auth.ok"
	AuditAuthMissing       AuditKind = "apikey.auth.missing"
	AuditAuthUnknown       AuditKind = "apikey.auth.unknown"
	AuditAuthRevoked       AuditKind = "apikey.auth.revoked"
	AuditAuthExpired       AuditKind = "apikey.auth.expired"
	AuditAuthLookupFailed  AuditKind = "apikey.auth.lookup_failed"
	AuditAuthRateLimited   AuditKind = "apikey.auth.rate_limited"

	// Operator-only admin surface (loopback listener, shared-token gated).
	// AdminList is INFO; AdminRevoke escalates to WARN because key
	// revocation is a security-meaningful mutation worth a SIEM rule.
	AuditAdminList         AuditKind = "admin.list"
	AuditAdminRevoke       AuditKind = "admin.revoke"
)

// AuditSink receives events. Implementations must not block the
// calling goroutine — log + return is the contract.
type AuditSink interface {
	Emit(ctx context.Context, ev AuditEvent)
}

// SlogAuditSink writes events as structured slog records.
// AuthMissing / Unknown / Revoked / Expired escalate to WARN so SIEM
// rules can grep on level.
type SlogAuditSink struct {
	Logger *slog.Logger
}

func NewSlogAuditSink(l *slog.Logger) *SlogAuditSink {
	if l == nil {
		l = slog.Default()
	}
	return &SlogAuditSink{Logger: l}
}

func (s *SlogAuditSink) Emit(ctx context.Context, ev AuditEvent) {
	level := slog.LevelInfo
	switch ev.Kind {
	case AuditAuthMissing, AuditAuthUnknown, AuditAuthRevoked,
		AuditAuthExpired, AuditAuthLookupFailed, AuditAuthRateLimited,
		AuditAdminRevoke:
		level = slog.LevelWarn
	}
	ua := ev.UserAgent
	if len(ua) > 256 {
		ua = ua[:256]
	}
	s.Logger.LogAttrs(ctx, level, "audit",
		slog.String("kind", string(ev.Kind)),
		slog.Int64("key_id", ev.KeyID),
		slog.String("parent_user_id", ev.ParentUserID),
		slog.String("ip", ev.IP),
		slog.String("user_agent", ua),
		slog.String("detail", ev.Detail),
		slog.Time("occurred_at", ev.OccurredAt),
	)
}

// NoopAuditSink discards events. Useful in tests where audit isn't
// the system under test.
type NoopAuditSink struct{}

func (NoopAuditSink) Emit(_ context.Context, _ AuditEvent) {}
