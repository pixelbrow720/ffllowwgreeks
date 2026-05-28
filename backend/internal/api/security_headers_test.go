package api

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestSecurityHeaders_BasicSet(t *testing.T) {
	h := SecurityHeaders(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	r := httptest.NewRequest(http.MethodGet, "/api/snapshot/spx", nil)
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)

	want := map[string]string{
		"X-Content-Type-Options":       "nosniff",
		"X-Frame-Options":              "DENY",
		"Referrer-Policy":              "no-referrer",
		"Content-Security-Policy":      "default-src 'none'; frame-ancestors 'none'",
		"Cross-Origin-Resource-Policy": "same-origin",
	}
	for k, v := range want {
		if got := w.Header().Get(k); got != v {
			t.Errorf("%s = %q, want %q", k, got, v)
		}
	}
	// HSTS must NOT be set on plain HTTP — telling a non-TLS client to
	// "always upgrade" is meaningless and breaks local dev.
	if got := w.Header().Get("Strict-Transport-Security"); got != "" {
		t.Errorf("HSTS leaked on plain HTTP: %q", got)
	}
}

func TestSecurityHeaders_HSTSOnHTTPSForwarded(t *testing.T) {
	h := SecurityHeaders(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {}))
	r := httptest.NewRequest(http.MethodGet, "/", nil)
	r.Header.Set("X-Forwarded-Proto", "https")
	w := httptest.NewRecorder()
	h.ServeHTTP(w, r)
	if got := w.Header().Get("Strict-Transport-Security"); got == "" {
		t.Fatal("HSTS missing when request arrived via X-Forwarded-Proto=https")
	}
}
