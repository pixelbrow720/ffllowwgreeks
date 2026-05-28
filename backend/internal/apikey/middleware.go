package apikey

import (
	"context"
	"net/http"
	"strings"
	"time"
)

// Middleware authenticates inbound requests against an API key. Order
// of credential resolution:
//
//   1. Authorization: Bearer <secret>
//   2. X-API-Key: <secret>
//
// On success, the resolved APIKey is attached to the request context
// (read via FromContext) and the request continues. On failure the
// handler writes 401 directly and the chain short-circuits.
//
// The lookup runs against the configured Store with a tight 2s timeout
// so a degraded Postgres can't tail-latency every request.
type Middleware struct {
	Store     Store
	Audit     AuditSink // optional; nil = no audit logging
	Now       func() time.Time
}

// NewMiddleware constructs a middleware. Audit may be nil; the time
// source falls back to time.Now when nil.
func NewMiddleware(s Store, audit AuditSink) *Middleware {
	return &Middleware{Store: s, Audit: audit}
}

// Handler returns the actual http middleware. Use as:
//
//   protected := chi.NewRouter()
//   protected.Use(mw.Handler)
func (m *Middleware) Handler(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		secret := extractSecret(r)
		if secret == "" {
			recordAuth("missing")
			m.audit(r, AuditAuthMissing, 0, "")
			writeErr(w, http.StatusUnauthorized, ErrNoCredentials.Error())
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), LookupTimeout)
		defer cancel()
		key, err := m.Store.LookupByHash(ctx, HashSecret(secret))
		now := m.now()
		switch {
		case err == ErrUnknownKey:
			recordAuth("unknown")
			m.audit(r, AuditAuthUnknown, 0, "")
			writeErr(w, http.StatusUnauthorized, ErrUnknownKey.Error())
			return
		case err != nil:
			recordAuth("lookup_error")
			m.audit(r, AuditAuthLookupFailed, 0, err.Error())
			writeErr(w, http.StatusInternalServerError, ErrLookupFailed.Error())
			return
		}
		if !key.IsActive(now) {
			if key.RevokedAt != nil {
				recordAuth("revoked")
				m.audit(r, AuditAuthRevoked, key.ID, "")
				writeErr(w, http.StatusUnauthorized, ErrRevokedKey.Error())
				return
			}
			recordAuth("expired")
			m.audit(r, AuditAuthExpired, key.ID, "")
			writeErr(w, http.StatusUnauthorized, ErrExpiredKey.Error())
			return
		}
		// Best-effort touch — coalesced to one update per minute per key
		// so a hot client doesn't hammer the DB on every request.
		if shouldTouch(key.LastUsedAt, now) {
			go func(id int64) {
				bg, cancel := context.WithTimeout(context.Background(), LookupTimeout)
				defer cancel()
				_ = m.Store.TouchLastUsed(bg, id)
			}(key.ID)
		}
		recordAuth("ok")
		m.audit(r, AuditAuthOK, key.ID, "")
		next.ServeHTTP(w, r.WithContext(withAPIKey(r.Context(), key)))
	})
}

func (m *Middleware) now() time.Time {
	if m.Now != nil {
		return m.Now()
	}
	return time.Now()
}

func (m *Middleware) audit(r *http.Request, kind AuditKind, keyID int64, detail string) {
	if m.Audit == nil {
		return
	}
	m.Audit.Emit(r.Context(), AuditEvent{
		Kind:       kind,
		KeyID:      keyID,
		IP:         clientIP(r),
		UserAgent:  r.UserAgent(),
		Detail:     detail,
		OccurredAt: m.now(),
	})
}

// extractSecret parses the inbound credential. Bearer wins over
// X-API-Key when both are present (the more standard form).
func extractSecret(r *http.Request) string {
	if h := r.Header.Get("Authorization"); h != "" {
		const prefix = "Bearer "
		if len(h) > len(prefix) && strings.EqualFold(h[:len(prefix)], prefix) {
			return strings.TrimSpace(h[len(prefix):])
		}
	}
	return strings.TrimSpace(r.Header.Get("X-API-Key"))
}

// clientIP returns the host portion of r.RemoteAddr. Upstream the api
// binary's TrustAwareRealIP middleware has already rewritten RemoteAddr
// from X-Forwarded-For when (and only when) the inbound peer is in the
// configured trusted-proxy CIDRs — this function never reads XFF on its
// own, so a hostile client cannot spoof their IP for audit logs or
// per-IP rate limiting by setting the header themselves.
func clientIP(r *http.Request) string {
	ra := r.RemoteAddr
	if ra == "" {
		return ""
	}
	if ra[0] == '[' {
		if end := strings.IndexByte(ra, ']'); end > 0 {
			return ra[1:end]
		}
	}
	if i := strings.LastIndexByte(ra, ':'); i > 0 {
		return ra[:i]
	}
	return ra
}

func writeErr(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(`{"error":"` + msg + `"}`))
}
