package apikey

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// helper to build a memory-backed middleware + a seeded key.
func newTestMW(t *testing.T) (*Middleware, *MemoryStore, string) {
	t.Helper()
	store := NewMemoryStore()
	secret, hash, err := Generate()
	if err != nil {
		t.Fatalf("generate: %v", err)
	}
	if _, err := store.Create(context.Background(), APIKey{
		Name:         "test",
		Hash:         hash,
		ParentUserID: "u-123",
		RateLimitRPS: 1.0,
		RateBurst:    30,
	}); err != nil {
		t.Fatalf("seed: %v", err)
	}
	return NewMiddleware(store, NoopAuditSink{}), store, secret
}

func TestMiddleware_AcceptsBearer(t *testing.T) {
	mw, _, secret := newTestMW(t)
	called := false
	h := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		k, ok := FromContext(r.Context())
		if !ok {
			t.Error("APIKey not in context")
		}
		if k.ParentUserID != "u-123" {
			t.Errorf("parent_user_id = %q, want u-123", k.ParentUserID)
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx", nil)
	r.Header.Set("Authorization", "Bearer "+secret)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if !called {
		t.Fatal("downstream handler never reached")
	}
	if w.Code != http.StatusNoContent {
		t.Errorf("status %d", w.Code)
	}
}

func TestMiddleware_AcceptsXAPIKey(t *testing.T) {
	mw, _, secret := newTestMW(t)
	h := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx", nil)
	r.Header.Set("X-API-Key", secret)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusNoContent {
		t.Errorf("status %d", w.Code)
	}
}

func TestMiddleware_RejectsMissing(t *testing.T) {
	mw, _, _ := newTestMW(t)
	h := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("handler reached on missing creds")
	}))
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("status %d, want 401", w.Code)
	}
}

func TestMiddleware_RejectsUnknown(t *testing.T) {
	mw, _, _ := newTestMW(t)
	h := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("handler reached on unknown key")
	}))
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx", nil)
	r.Header.Set("Authorization", "Bearer not-a-real-secret")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("status %d, want 401", w.Code)
	}
}

func TestMiddleware_RejectsRevoked(t *testing.T) {
	mw, store, secret := newTestMW(t)
	// Revoke the seeded key.
	rows := store.rows
	if len(rows) == 0 {
		t.Fatal("no seeded key")
	}
	if err := store.Revoke(context.Background(), rows[0].ID); err != nil {
		t.Fatalf("revoke: %v", err)
	}
	h := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("handler reached on revoked key")
	}))
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx", nil)
	r.Header.Set("Authorization", "Bearer "+secret)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("status %d, want 401", w.Code)
	}
	var body map[string]string
	_ = json.Unmarshal(w.Body.Bytes(), &body)
	if got := body["error"]; got != ErrRevokedKey.Error() {
		t.Errorf("error body %q, want revoked", got)
	}
}

func TestMiddleware_RejectsExpired(t *testing.T) {
	store := NewMemoryStore()
	secret, hash, _ := Generate()
	past := time.Now().Add(-time.Hour)
	if _, err := store.Create(context.Background(), APIKey{
		Name:      "expired",
		Hash:      hash,
		ExpiresAt: &past,
	}); err != nil {
		t.Fatalf("seed: %v", err)
	}
	mw := NewMiddleware(store, NoopAuditSink{})
	h := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("handler reached on expired key")
	}))
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx", nil)
	r.Header.Set("Authorization", "Bearer "+secret)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("status %d, want 401", w.Code)
	}
}

func TestExtractSecret_BearerWinsOverXAPIKey(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	r.Header.Set("Authorization", "Bearer abc")
	r.Header.Set("X-API-Key", "xyz")
	if got := extractSecret(r); got != "abc" {
		t.Errorf("got %q, want abc (Bearer should win)", got)
	}
}

func TestExtractSecret_TolerantOfBearerCase(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	r.Header.Set("Authorization", "bearer lowercase")
	if got := extractSecret(r); got != "lowercase" {
		t.Errorf("got %q, want lowercase", got)
	}
}

// Browsers cannot set Authorization or X-API-Key on a WebSocket
// upgrade handshake, so for /ws/* the api_key query parameter is the
// only credential channel. The fallback must NOT fire on regular HTTP
// requests — that would push secrets into proxy logs and Referer
// headers.
func TestExtractSecret_QueryParamHonoredOnWSUpgrade(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/ws/live?api_key=ws-secret", nil)
	r.Header.Set("Connection", "Upgrade")
	r.Header.Set("Upgrade", "websocket")
	if got := extractSecret(r); got != "ws-secret" {
		t.Errorf("ws upgrade got %q, want ws-secret", got)
	}
}

func TestExtractSecret_QueryParamIgnoredOnPlainHTTP(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx?api_key=should-not-fire", nil)
	if got := extractSecret(r); got != "" {
		t.Errorf("plain http got %q, want empty", got)
	}
}

func TestExtractSecret_HeadersWinOverQueryOnWSUpgrade(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "/ws/live?api_key=from-query", nil)
	r.Header.Set("Connection", "Upgrade")
	r.Header.Set("Upgrade", "websocket")
	r.Header.Set("Authorization", "Bearer from-header")
	if got := extractSecret(r); got != "from-header" {
		t.Errorf("got %q, want from-header (header should win)", got)
	}
}

func TestExtractSecret_QueryParamRequiresUpgradeHeaderPair(t *testing.T) {
	// Connection: Upgrade alone (no Upgrade: websocket) must not unlock the fallback.
	r := httptest.NewRequest(http.MethodGet, "/ws/live?api_key=should-not-fire", nil)
	r.Header.Set("Connection", "Upgrade")
	if got := extractSecret(r); got != "" {
		t.Errorf("connection-only got %q, want empty", got)
	}
	// Upgrade: websocket alone (no Connection: Upgrade) must not unlock either.
	r2 := httptest.NewRequest(http.MethodGet, "/ws/live?api_key=should-not-fire", nil)
	r2.Header.Set("Upgrade", "websocket")
	if got := extractSecret(r2); got != "" {
		t.Errorf("upgrade-only got %q, want empty", got)
	}
}

func TestExtractSecret_ConnectionHeaderTokenList(t *testing.T) {
	// Real browsers often send "Connection: keep-alive, Upgrade".
	// We must walk the comma-separated token list, not byte-compare.
	r := httptest.NewRequest(http.MethodGet, "/ws/live?api_key=ws-secret", nil)
	r.Header.Set("Connection", "keep-alive, Upgrade")
	r.Header.Set("Upgrade", "websocket")
	if got := extractSecret(r); got != "ws-secret" {
		t.Errorf("token-list got %q, want ws-secret", got)
	}
}

func TestMiddleware_AcceptsQueryParamOnWSUpgrade(t *testing.T) {
	mw, _, secret := newTestMW(t)
	called := false
	h := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		called = true
		w.WriteHeader(http.StatusSwitchingProtocols)
	}))
	r := httptest.NewRequest(http.MethodGet, "/ws/live?api_key="+secret, nil)
	r.Header.Set("Connection", "Upgrade")
	r.Header.Set("Upgrade", "websocket")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if !called {
		t.Fatal("downstream handler never reached")
	}
	if w.Code != http.StatusSwitchingProtocols {
		t.Errorf("status %d, want 101", w.Code)
	}
}

func TestMiddleware_RejectsQueryParamOnPlainHTTP(t *testing.T) {
	mw, _, secret := newTestMW(t)
	h := mw.Handler(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("downstream reached — query-param auth must NOT work on plain HTTP")
	}))
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx?api_key="+secret, nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if w.Code != http.StatusUnauthorized {
		t.Errorf("status %d, want 401", w.Code)
	}
}

func TestGenerate_DistinctSecrets(t *testing.T) {
	a, _, _ := Generate()
	b, _, _ := Generate()
	if a == b {
		t.Error("two Generate() calls returned identical secrets")
	}
	if len(a) != 64 {
		t.Errorf("hex secret length %d, want 64", len(a))
	}
}

func TestHashSecret_Deterministic(t *testing.T) {
	a := HashSecret("same-secret")
	b := HashSecret("same-secret")
	if string(a) != string(b) {
		t.Error("HashSecret not deterministic")
	}
	c := HashSecret("different")
	if string(a) == string(c) {
		t.Error("HashSecret collided on different inputs")
	}
}
