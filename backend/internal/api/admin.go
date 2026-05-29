package api

import (
	"context"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"flowgreeks/internal/apikey"

	"github.com/go-chi/chi/v5"
)

// Admin is the operator-only key management surface.
//
// Auth: a single shared secret presented as Authorization: Bearer <token>
// and compared in constant time. No mTLS / OIDC / per-operator identity —
// this is an internal control plane bound to loopback by default; flowjob.id
// (the parent product) reaches it via tunnel/SSH/internal mesh.
//
// Endpoints (all rooted under /admin):
//
//	GET  /admin/keys                — paginated list (cursor + limit).
//	GET  /admin/keys/{id}           — single key detail.
//	POST /admin/keys/{id}/revoke    — idempotent revoke.
//
// None of the responses ever include the secret or its hash — only the
// metadata flowjob.id needs for an "active keys" dashboard plus the
// short hex prefix of the hash for visual identification.
type Admin struct {
	Store apikey.Store
	Token string
	Audit apikey.AuditSink
	Now   func() time.Time
}

// adminMaxLimit caps the per-page row count regardless of what the
// client asks for, so a confused operator can't request limit=1000000
// and force a giant allocation. 200 matches the durable rule from the
// task spec.
const adminMaxLimit = 200

// adminDefaultLimit is the fallback when ?limit is missing or invalid.
const adminDefaultLimit = 50

// Mount attaches every admin route on r. r is expected to be a fresh
// router served on a separate listener (loopback by default) — never
// the public mux.
func (a *Admin) Mount(r chi.Router) {
	r.Use(a.authMiddleware)
	r.Get("/admin/keys", a.list)
	r.Get("/admin/keys/{id}", a.get)
	r.Post("/admin/keys/{id}/revoke", a.revoke)
}

// authMiddleware gates every admin endpoint behind a constant-time
// comparison against the configured shared token. 401 on mismatch.
//
// The empty-token case is handled at startup (cmd/api refuses to mount
// the admin server when ADMIN_TOKEN is unset), so reaching here with
// an empty configured token is a programming bug rather than a runtime
// state — we still 401 to be safe.
func (a *Admin) authMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if a.Token == "" {
			adminWriteErr(w, http.StatusUnauthorized, "admin token not configured")
			return
		}
		got := bearerFromHeader(r.Header.Get("Authorization"))
		if got == "" || subtle.ConstantTimeCompare([]byte(got), []byte(a.Token)) != 1 {
			adminWriteErr(w, http.StatusUnauthorized, "invalid admin token")
			return
		}
		next.ServeHTTP(w, r)
	})
}

// bearerFromHeader extracts the token from Authorization: Bearer <token>.
// Empty when the header is missing or malformed. Tolerant of "bearer"
// case so an operator copy-pasting from docs doesn't get a surprise 401.
func bearerFromHeader(h string) string {
	const prefix = "Bearer "
	if len(h) <= len(prefix) {
		return ""
	}
	if !strings.EqualFold(h[:len(prefix)], prefix) {
		return ""
	}
	return strings.TrimSpace(h[len(prefix):])
}

// adminKeyView is the wire shape returned to admin callers. We never
// include the hash bytes verbatim and never include the secret — only
// a short hex prefix of the hash for at-a-glance identification across
// the operator UI.
type adminKeyView struct {
	ID           int64      `json:"id"`
	Label        string     `json:"label"`
	Prefix       string     `json:"prefix"`
	CreatedAt    time.Time  `json:"created_at"`
	LastUsedAt   *time.Time `json:"last_used_at,omitempty"`
	RevokedAt    *time.Time `json:"revoked_at,omitempty"`
	ExpiresAt    *time.Time `json:"expires_at,omitempty"`
	ParentUserID string     `json:"parent_user_id,omitempty"`
	RateLimitRPS float64    `json:"rate_limit_rps"`
	RateBurst    int        `json:"rate_burst"`
}

// toAdminView maps a stored APIKey to its outbound shape.
func toAdminView(k apikey.APIKey) adminKeyView {
	return adminKeyView{
		ID:           k.ID,
		Label:        k.Name,
		Prefix:       hashPrefix(k.Hash),
		CreatedAt:    k.CreatedAt,
		LastUsedAt:   k.LastUsedAt,
		RevokedAt:    k.RevokedAt,
		ExpiresAt:    k.ExpiresAt,
		ParentUserID: k.ParentUserID,
		RateLimitRPS: k.RateLimitRPS,
		RateBurst:    k.RateBurst,
	}
}

// hashPrefix returns the first 8 hex chars of the SHA-256 hash for
// at-a-glance visual ID. Never the full hash — exposing the hash on a
// shared-token-protected control plane lowers the bar for a token leak
// to escalate into a key takeover (one ProvisioningGenerate + one DB
// write away). 8 chars × 4 bits = 32 bits of identifier — enough that
// operators can disambiguate a ~100 row page, not so much that hash
// material is meaningfully leaked.
func hashPrefix(h []byte) string {
	if len(h) == 0 {
		return ""
	}
	const n = 4 // 4 bytes → 8 hex chars
	if len(h) < n {
		return hex.EncodeToString(h)
	}
	return hex.EncodeToString(h[:n])
}

// list serves the paginated admin key listing.
//
//	GET /admin/keys?cursor=<id>&limit=<n>
//
// Response: { "items": [...], "next_cursor": <id|0> }
func (a *Admin) list(w http.ResponseWriter, r *http.Request) {
	cursor := parseAdminInt64(r, "cursor", 0)
	if cursor < 0 {
		cursor = 0
	}
	limit := parseAdminInt(r, "limit", adminDefaultLimit, 1, adminMaxLimit)

	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	rows, next, err := a.Store.ListPaged(ctx, cursor, limit)
	if err != nil {
		adminWriteErr(w, http.StatusInternalServerError, "list failed")
		return
	}

	out := struct {
		Items      []adminKeyView `json:"items"`
		NextCursor int64          `json:"next_cursor"`
	}{
		Items:      make([]adminKeyView, 0, len(rows)),
		NextCursor: next,
	}
	for _, k := range rows {
		out.Items = append(out.Items, toAdminView(k))
	}

	a.audit(r, apikey.AuditAdminList, 0, "cursor="+strconv.FormatInt(cursor, 10)+" limit="+strconv.Itoa(limit)+" returned="+strconv.Itoa(len(out.Items)))
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(out)
}

// get serves a single key by id.
//
//	GET /admin/keys/{id}
func (a *Admin) get(w http.ResponseWriter, r *http.Request) {
	id, ok := adminIDParam(r)
	if !ok {
		adminWriteErr(w, http.StatusBadRequest, "invalid id")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	k, err := a.Store.GetByID(ctx, id)
	if err != nil {
		if errors.Is(err, apikey.ErrUnknownKey) {
			adminWriteErr(w, http.StatusNotFound, "not found")
			return
		}
		adminWriteErr(w, http.StatusInternalServerError, "get failed")
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(toAdminView(k))
}

// revoke is idempotent: revoking an already-revoked key returns 204
// without re-stamping revoked_at (the underlying SQL is gated by
// `revoked_at IS NULL`). 404 when the id is unknown.
//
//	POST /admin/keys/{id}/revoke
func (a *Admin) revoke(w http.ResponseWriter, r *http.Request) {
	id, ok := adminIDParam(r)
	if !ok {
		adminWriteErr(w, http.StatusBadRequest, "invalid id")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	// 404 first so we don't silently accept a no-op for a non-existent id.
	if _, err := a.Store.GetByID(ctx, id); err != nil {
		if errors.Is(err, apikey.ErrUnknownKey) {
			adminWriteErr(w, http.StatusNotFound, "not found")
			return
		}
		adminWriteErr(w, http.StatusInternalServerError, "revoke lookup failed")
		return
	}
	if err := a.Store.Revoke(ctx, id); err != nil {
		adminWriteErr(w, http.StatusInternalServerError, "revoke failed")
		return
	}
	a.audit(r, apikey.AuditAdminRevoke, id, "")
	w.WriteHeader(http.StatusNoContent)
}

// adminIDParam parses {id} into int64. Returns (0, false) when missing
// or non-numeric.
func adminIDParam(r *http.Request) (int64, bool) {
	raw := chi.URLParam(r, "id")
	if raw == "" {
		return 0, false
	}
	v, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || v <= 0 {
		return 0, false
	}
	return v, true
}

func parseAdminInt(r *http.Request, key string, def, min, max int) int {
	raw := r.URL.Query().Get(key)
	if raw == "" {
		return def
	}
	v, err := strconv.Atoi(raw)
	if err != nil {
		return def
	}
	if v < min {
		return min
	}
	if v > max {
		return max
	}
	return v
}

func parseAdminInt64(r *http.Request, key string, def int64) int64 {
	raw := r.URL.Query().Get(key)
	if raw == "" {
		return def
	}
	v, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return def
	}
	return v
}

func (a *Admin) audit(r *http.Request, kind apikey.AuditKind, keyID int64, detail string) {
	if a.Audit == nil {
		return
	}
	a.Audit.Emit(r.Context(), apikey.AuditEvent{
		Kind:       kind,
		KeyID:      keyID,
		IP:         clientIP(r),
		UserAgent:  r.UserAgent(),
		Detail:     detail,
		OccurredAt: a.now(),
	})
}

func (a *Admin) now() time.Time {
	if a.Now != nil {
		return a.Now()
	}
	return time.Now()
}

func adminWriteErr(w http.ResponseWriter, status int, msg string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_, _ = w.Write([]byte(`{"error":` + jsonString(msg) + `}`))
}
