package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"flowgreeks/internal/apikey"

	"github.com/go-chi/chi/v5"
)

// recordingAuditSink captures emitted events so tests can assert what
// the admin surface logged.
type recordingAuditSink struct {
	mu     sync.Mutex
	events []apikey.AuditEvent
}

func (s *recordingAuditSink) Emit(_ context.Context, ev apikey.AuditEvent) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.events = append(s.events, ev)
}

func (s *recordingAuditSink) snapshot() []apikey.AuditEvent {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]apikey.AuditEvent, len(s.events))
	copy(out, s.events)
	return out
}

func (s *recordingAuditSink) byKind(k apikey.AuditKind) []apikey.AuditEvent {
	out := []apikey.AuditEvent{}
	for _, ev := range s.snapshot() {
		if ev.Kind == k {
			out = append(out, ev)
		}
	}
	return out
}

// newAdminTestServer wires a fully-stocked admin handler against a
// memory-backed Store with `seed` keys created up front.
func newAdminTestServer(t *testing.T, token string, seed int) (*httptest.Server, *apikey.MemoryStore, *recordingAuditSink, []apikey.APIKey) {
	t.Helper()
	store := apikey.NewMemoryStore()
	keys := make([]apikey.APIKey, 0, seed)
	for i := 0; i < seed; i++ {
		_, hash, err := apikey.Generate()
		if err != nil {
			t.Fatalf("generate %d: %v", i, err)
		}
		k, err := store.Create(context.Background(), apikey.APIKey{
			Name:         "key-" + strconv.Itoa(i),
			Hash:         hash,
			ParentUserID: "u-" + strconv.Itoa(i%3),
			RateLimitRPS: 1.0,
			RateBurst:    30,
		})
		if err != nil {
			t.Fatalf("seed %d: %v", i, err)
		}
		keys = append(keys, k)
	}
	audit := &recordingAuditSink{}
	admin := &Admin{Store: store, Token: token, Audit: audit}
	r := chi.NewRouter()
	admin.Mount(r)
	srv := httptest.NewServer(r)
	t.Cleanup(srv.Close)
	return srv, store, audit, keys
}

func adminGET(t *testing.T, srv *httptest.Server, path, token string) *http.Response {
	t.Helper()
	req, err := http.NewRequest(http.MethodGet, srv.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func adminPOST(t *testing.T, srv *httptest.Server, path, token string) *http.Response {
	t.Helper()
	req, err := http.NewRequest(http.MethodPost, srv.URL+path, nil)
	if err != nil {
		t.Fatal(err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func TestAdmin_RejectsMissingToken(t *testing.T) {
	srv, _, _, _ := newAdminTestServer(t, "secret-token", 1)
	resp := adminGET(t, srv, "/admin/keys", "")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("missing token = %d, want 401", resp.StatusCode)
	}
}

func TestAdmin_RejectsWrongToken(t *testing.T) {
	srv, _, _, _ := newAdminTestServer(t, "secret-token", 1)
	resp := adminGET(t, srv, "/admin/keys", "wrong-token")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("wrong token = %d, want 401", resp.StatusCode)
	}
}

func TestAdmin_AcceptsCorrectToken(t *testing.T) {
	srv, _, audit, _ := newAdminTestServer(t, "secret-token", 3)
	resp := adminGET(t, srv, "/admin/keys", "secret-token")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	var got struct {
		Items      []adminKeyView `json:"items"`
		NextCursor int64          `json:"next_cursor"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if len(got.Items) != 3 {
		t.Errorf("items = %d, want 3", len(got.Items))
	}
	if got.NextCursor != 0 {
		t.Errorf("next_cursor = %d, want 0 (last page)", got.NextCursor)
	}
	for _, item := range got.Items {
		if item.Prefix == "" {
			t.Error("expected non-empty hash prefix")
		}
		if len(item.Prefix) > 16 {
			t.Errorf("hash prefix %q too long — should never expose full hash", item.Prefix)
		}
	}
	if len(audit.byKind(apikey.AuditAdminList)) != 1 {
		t.Errorf("audit.list events = %d, want 1", len(audit.byKind(apikey.AuditAdminList)))
	}
}

func TestAdmin_ListPagination(t *testing.T) {
	srv, _, _, keys := newAdminTestServer(t, "tok", 5)
	// First page with limit=2.
	resp := adminGET(t, srv, "/admin/keys?limit=2", "tok")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	var page1 struct {
		Items      []adminKeyView `json:"items"`
		NextCursor int64          `json:"next_cursor"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&page1); err != nil {
		t.Fatal(err)
	}
	if len(page1.Items) != 2 {
		t.Fatalf("page1 items = %d, want 2", len(page1.Items))
	}
	if page1.Items[0].ID != keys[0].ID || page1.Items[1].ID != keys[1].ID {
		t.Errorf("page1 ids = [%d, %d], want [%d, %d]", page1.Items[0].ID, page1.Items[1].ID, keys[0].ID, keys[1].ID)
	}
	if page1.NextCursor != keys[1].ID {
		t.Errorf("next_cursor = %d, want %d", page1.NextCursor, keys[1].ID)
	}

	// Second page using returned cursor.
	resp2 := adminGET(t, srv, "/admin/keys?limit=2&cursor="+strconv.FormatInt(page1.NextCursor, 10), "tok")
	defer resp2.Body.Close()
	var page2 struct {
		Items      []adminKeyView `json:"items"`
		NextCursor int64          `json:"next_cursor"`
	}
	if err := json.NewDecoder(resp2.Body).Decode(&page2); err != nil {
		t.Fatal(err)
	}
	if len(page2.Items) != 2 {
		t.Fatalf("page2 items = %d, want 2", len(page2.Items))
	}
	if page2.Items[0].ID != keys[2].ID || page2.Items[1].ID != keys[3].ID {
		t.Errorf("page2 ids = [%d, %d], want [%d, %d]", page2.Items[0].ID, page2.Items[1].ID, keys[2].ID, keys[3].ID)
	}
	if page2.NextCursor != keys[3].ID {
		t.Errorf("page2 next_cursor = %d, want %d", page2.NextCursor, keys[3].ID)
	}

	// Final page — should return the last row, no further cursor.
	resp3 := adminGET(t, srv, "/admin/keys?limit=2&cursor="+strconv.FormatInt(page2.NextCursor, 10), "tok")
	defer resp3.Body.Close()
	var page3 struct {
		Items      []adminKeyView `json:"items"`
		NextCursor int64          `json:"next_cursor"`
	}
	if err := json.NewDecoder(resp3.Body).Decode(&page3); err != nil {
		t.Fatal(err)
	}
	if len(page3.Items) != 1 {
		t.Fatalf("page3 items = %d, want 1", len(page3.Items))
	}
	if page3.Items[0].ID != keys[4].ID {
		t.Errorf("page3 id = %d, want %d", page3.Items[0].ID, keys[4].ID)
	}
	if page3.NextCursor != 0 {
		t.Errorf("page3 next_cursor = %d, want 0 (last page)", page3.NextCursor)
	}
}

func TestAdmin_ListLimitClamp(t *testing.T) {
	srv, _, _, _ := newAdminTestServer(t, "tok", 3)
	// limit=999 should clamp to adminMaxLimit (200) — not error.
	resp := adminGET(t, srv, "/admin/keys?limit=999999", "tok")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("status = %d, want 200", resp.StatusCode)
	}
}

func TestAdmin_GetByID(t *testing.T) {
	srv, _, _, keys := newAdminTestServer(t, "tok", 2)
	resp := adminGET(t, srv, "/admin/keys/"+strconv.FormatInt(keys[0].ID, 10), "tok")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	var got adminKeyView
	if err := json.NewDecoder(resp.Body).Decode(&got); err != nil {
		t.Fatal(err)
	}
	if got.ID != keys[0].ID {
		t.Errorf("id = %d, want %d", got.ID, keys[0].ID)
	}
	if got.Label != keys[0].Name {
		t.Errorf("label = %q, want %q", got.Label, keys[0].Name)
	}
}

func TestAdmin_GetUnknown404(t *testing.T) {
	srv, _, _, _ := newAdminTestServer(t, "tok", 1)
	resp := adminGET(t, srv, "/admin/keys/9999", "tok")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
}

func TestAdmin_Revoke204AndIdempotent(t *testing.T) {
	srv, store, audit, keys := newAdminTestServer(t, "tok", 1)
	id := keys[0].ID

	// First revoke: 204.
	resp := adminPOST(t, srv, "/admin/keys/"+strconv.FormatInt(id, 10)+"/revoke", "tok")
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		t.Errorf("first revoke status = %d, want 204", resp.StatusCode)
	}

	got, err := store.GetByID(context.Background(), id)
	if err != nil {
		t.Fatalf("re-read: %v", err)
	}
	if got.RevokedAt == nil {
		t.Fatal("revoked_at not set after revoke")
	}
	firstStamp := *got.RevokedAt

	// Second revoke (idempotent): still 204.
	time.Sleep(2 * time.Millisecond) // ensure NOW() would differ if we re-stamped
	resp2 := adminPOST(t, srv, "/admin/keys/"+strconv.FormatInt(id, 10)+"/revoke", "tok")
	resp2.Body.Close()
	if resp2.StatusCode != http.StatusNoContent {
		t.Errorf("second revoke status = %d, want 204 (idempotent)", resp2.StatusCode)
	}

	got2, err := store.GetByID(context.Background(), id)
	if err != nil {
		t.Fatalf("re-read after second revoke: %v", err)
	}
	if got2.RevokedAt == nil {
		t.Fatal("revoked_at lost after second revoke")
	}
	if !got2.RevokedAt.Equal(firstStamp) {
		t.Errorf("revoked_at re-stamped on idempotent revoke: %v -> %v", firstStamp, *got2.RevokedAt)
	}

	// Audit emitted twice (once per call).
	revokeEvents := audit.byKind(apikey.AuditAdminRevoke)
	if len(revokeEvents) != 2 {
		t.Errorf("admin.revoke audit count = %d, want 2", len(revokeEvents))
	}
	for _, ev := range revokeEvents {
		if ev.KeyID != id {
			t.Errorf("audit key_id = %d, want %d", ev.KeyID, id)
		}
	}
}

func TestAdmin_RevokeUnknown404(t *testing.T) {
	srv, _, audit, _ := newAdminTestServer(t, "tok", 1)
	resp := adminPOST(t, srv, "/admin/keys/9999/revoke", "tok")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("status = %d, want 404", resp.StatusCode)
	}
	if len(audit.byKind(apikey.AuditAdminRevoke)) != 0 {
		t.Error("expected no audit event for unknown id revoke")
	}
}

func TestAdmin_RevokeBadID(t *testing.T) {
	srv, _, _, _ := newAdminTestServer(t, "tok", 1)
	resp := adminPOST(t, srv, "/admin/keys/not-a-number/revoke", "tok")
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", resp.StatusCode)
	}
}

func TestAdmin_NeverLeaksHashOrSecret(t *testing.T) {
	srv, _, _, _ := newAdminTestServer(t, "tok", 2)
	resp := adminGET(t, srv, "/admin/keys", "tok")
	defer resp.Body.Close()
	body := readAll(t, resp)
	// The hash bytes are 32 bytes → 64 hex chars. We allow the 8-char
	// prefix but no full 64-char hex blob should ever appear in a
	// response body.
	if strings.Contains(strings.ToLower(body), "hash") {
		t.Errorf("response includes 'hash' field: %s", body)
	}
	if strings.Contains(strings.ToLower(body), "secret") {
		t.Errorf("response includes 'secret' field: %s", body)
	}
}

func TestAdmin_BearerCaseTolerant(t *testing.T) {
	srv, _, _, _ := newAdminTestServer(t, "tok", 1)
	req, _ := http.NewRequest(http.MethodGet, srv.URL+"/admin/keys", nil)
	req.Header.Set("Authorization", "bearer tok")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("lowercase bearer status = %d, want 200", resp.StatusCode)
	}
}

func readAll(t *testing.T, resp *http.Response) string {
	t.Helper()
	b := make([]byte, 0, 1024)
	buf := make([]byte, 1024)
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 {
			b = append(b, buf[:n]...)
		}
		if err != nil {
			break
		}
	}
	return string(b)
}
