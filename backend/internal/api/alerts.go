package api

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"flowgreeks/internal/alerts"
	"flowgreeks/internal/apikey"
	"flowgreeks/internal/feed"

	"github.com/go-chi/chi/v5"
	"github.com/nats-io/nats.go"
)

// AlertHandlers wires the alerts engine into the api router.
//
// Endpoints (all rooted under /api/alerts):
//
//	GET    /api/alerts/rules           — list rules for current key
//	POST   /api/alerts/rules           — create or replace a rule
//	DELETE /api/alerts/rules/{id}      — remove a rule
//
// Owner identity is taken from the resolved APIKey in context (set by
// apikey.Middleware) — the parent_user_id field if present, falling
// back to the key id stringified. Rules are scoped to that owner so
// one tenant can't see / mutate another's rules.
type AlertHandlers struct {
	Engine *alerts.Engine
	Audit  apikey.AuditSink // optional; nil = no audit logging
}

// Mount registers the alerts REST surface.
func (h *AlertHandlers) Mount(r chi.Router) {
	r.Get("/api/alerts/rules", h.list)
	r.Post("/api/alerts/rules", h.upsert)
	r.Delete("/api/alerts/rules/{id}", h.delete)
}

// callerOwnerID resolves the requesting tenant's id from the resolved
// API key. parent_user_id wins when set (correlation with flowjob.id);
// otherwise falls back to the stringified key id.
//
// X-User-ID header is honoured ONLY when no API key was resolved, as
// a development escape hatch when APIKEY_ENABLED=false. A logged-in
// caller cannot spoof someone else's tenant by setting the header.
func callerOwnerID(r *http.Request) string {
	if k, ok := apikey.FromContext(r.Context()); ok {
		if k.ParentUserID != "" {
			return k.ParentUserID
		}
		return strconv.FormatInt(k.ID, 10)
	}
	return r.Header.Get("X-User-ID")
}

// list serves the rules belonging to the requesting owner, paginated.
//
// Query params:
//   ?limit=N   default 50, max 200
//   ?offset=N  default 0
//
// Response shape:
//   {"rules": [...], "total": N, "offset": N, "limit": N}
//
// The shape is consistent across pages so a frontend can render
// "showing 1-25 of 312" without juggling header parsing.
func (h *AlertHandlers) list(w http.ResponseWriter, r *http.Request) {
	owner := callerOwnerID(r)
	limit := parseQueryInt(r, "limit", 50, 1, 200)
	offset := parseQueryInt(r, "offset", 0, 0, 1<<31-1)
	rules, total := h.Engine.ListRulesPage(owner, offset, limit)
	resp := struct {
		Rules  []alerts.Rule `json:"rules"`
		Total  int           `json:"total"`
		Offset int           `json:"offset"`
		Limit  int           `json:"limit"`
	}{
		Rules:  rules,
		Total:  total,
		Offset: offset,
		Limit:  limit,
	}
	if resp.Rules == nil {
		resp.Rules = []alerts.Rule{}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

// parseQueryInt parses ?key=N from the request, falling back to def if
// missing or malformed, and clamping to [min, max] so a hostile client
// can't ask for limit=999999 and force a giant allocation.
func parseQueryInt(r *http.Request, key string, def, min, max int) int {
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

func (h *AlertHandlers) upsert(w http.ResponseWriter, r *http.Request) {
	owner := callerOwnerID(r)
	if owner == "" {
		writeJSONError(w, http.StatusUnauthorized, "missing API key")
		return
	}
	body, err := io.ReadAll(io.LimitReader(r.Body, 16*1024))
	if err != nil {
		writeJSONError(w, http.StatusBadRequest, "read body: "+err.Error())
		return
	}
	defer r.Body.Close()
	var rule alerts.Rule
	if err := json.Unmarshal(body, &rule); err != nil {
		writeJSONError(w, http.StatusBadRequest, "decode rule: "+err.Error())
		return
	}
	if rule.ID == "" || rule.Symbol == feed.SymbolUnknown {
		writeJSONError(w, http.StatusBadRequest, "id and symbol are required")
		return
	}
	if err := h.Engine.UpsertRuleForOwner(rule, owner); err != nil {
		// ErrRuleNotOwned → 404 so the existence of another tenant's
		// rule with this id isn't probeable.
		writeJSONError(w, http.StatusNotFound, "rule not found")
		return
	}
	h.audit(r, "alert.rule.upsert", "id="+rule.ID+" symbol="+rule.Symbol.String())
	w.WriteHeader(http.StatusNoContent)
}

func (h *AlertHandlers) delete(w http.ResponseWriter, r *http.Request) {
	owner := callerOwnerID(r)
	if owner == "" {
		writeJSONError(w, http.StatusUnauthorized, "missing API key")
		return
	}
	id := chi.URLParam(r, "id")
	if id == "" {
		writeJSONError(w, http.StatusBadRequest, "missing id")
		return
	}
	if !h.Engine.RemoveRuleForOwner(id, owner) {
		writeJSONError(w, http.StatusNotFound, "rule not found")
		return
	}
	h.audit(r, "alert.rule.delete", "id="+id)
	w.WriteHeader(http.StatusNoContent)
}

// audit emits a structured event for alert-rule mutations. Same sink
// the apikey middleware uses, so auth + rule-edit history live on the
// same log stream and ship to one SIEM rule.
func (h *AlertHandlers) audit(r *http.Request, kind string, detail string) {
	if h.Audit == nil {
		return
	}
	var keyID int64
	var parent string
	if k, ok := apikey.FromContext(r.Context()); ok {
		keyID = k.ID
		parent = k.ParentUserID
	}
	h.Audit.Emit(r.Context(), apikey.AuditEvent{
		Kind:         apikey.AuditKind(kind),
		KeyID:        keyID,
		ParentUserID: parent,
		IP:           clientIP(r),
		UserAgent:    r.UserAgent(),
		Detail:       detail,
		OccurredAt:   timeNow(),
	})
}

// clientIP returns the host portion of r.RemoteAddr. The router-level
// TrustAwareRealIP middleware rewrites RemoteAddr from X-Forwarded-For
// only when the inbound peer is a trusted proxy, so this function does
// not read XFF directly — preventing spoofing on direct-to-internet
// deployments.
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

// timeNow is the package-level clock; tests may override it.
var timeNow = func() time.Time { return time.Now() }

// BrokerSink delivers Triggers to the api Broker as StateKindAlert
// snapshots. WS clients subscribed to /ws/live with kind=alert (or no
// filter) receive them.
type BrokerSink struct {
	Broker *Broker
}

// Deliver wraps the trigger as a Snapshot and publishes.
func (b *BrokerSink) Deliver(t alerts.Trigger) error {
	data, err := json.Marshal(t)
	if err != nil {
		return err
	}
	sym := feed.ParseSymbol(strings.ToUpper(t.Symbol))
	b.Broker.Publish(Snapshot{
		Symbol: sym,
		Kind:   StateKindAlert,
		Data:   data,
		TsNs:   t.TsNs,
	})
	return nil
}

// SubscribeAlertsToNATS wires the alerts engine to the same `state.>`
// stream the cache+broker subscriber consumes. Each gex snapshot is
// decoded into the alerts package's trimmed Snapshot and fed through
// the engine. Returns when ctx is cancelled.
func SubscribeAlertsToNATS(ctx context.Context, nc *nats.Conn, eng *alerts.Engine) error {
	sub, err := nc.Subscribe("state.>", func(m *nats.Msg) {
		sym, kind, ok := parseStateSubject(m.Subject)
		if !ok || kind != StateKindGEX {
			return
		}
		s, err := alerts.DecodeSnapshot(sym, m.Data)
		if err != nil {
			return
		}
		eng.OnSnapshot(s)
	})
	if err != nil {
		return fmt.Errorf("alerts subscribe state.>: %w", err)
	}
	go func() {
		<-ctx.Done()
		_ = sub.Drain()
	}()
	return nil
}
