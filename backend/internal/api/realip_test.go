package api

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestTrustAwareRealIP_NoTrustedListIsNoop(t *testing.T) {
	mw := TrustAwareRealIP(nil)
	got := ""
	h := mw(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		got = r.RemoteAddr
	}))
	r := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.RemoteAddr = "9.9.9.9:1234"
	r.Header.Set("X-Forwarded-For", "1.2.3.4")
	h.ServeHTTP(httptest.NewRecorder(), r)
	if got != "9.9.9.9:1234" {
		t.Errorf("got %q, want untouched 9.9.9.9:1234 — XFF must be ignored without an allowlist", got)
	}
}

func TestTrustAwareRealIP_HonoursXFFWhenPeerTrusted(t *testing.T) {
	mw := TrustAwareRealIP([]string{"10.0.0.0/8"})
	got := ""
	h := mw(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		got = r.RemoteAddr
	}))
	r := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.RemoteAddr = "10.0.0.5:443"
	r.Header.Set("X-Forwarded-For", "203.0.113.7")
	h.ServeHTTP(httptest.NewRecorder(), r)
	if got != "203.0.113.7:443" {
		t.Errorf("got %q, want 203.0.113.7:443", got)
	}
}

func TestTrustAwareRealIP_IgnoresXFFFromUntrustedPeer(t *testing.T) {
	mw := TrustAwareRealIP([]string{"10.0.0.0/8"})
	got := ""
	h := mw(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		got = r.RemoteAddr
	}))
	r := httptest.NewRequest(http.MethodGet, "/x", nil)
	// Hostile client connecting directly, claiming to be someone else.
	r.RemoteAddr = "203.0.113.7:9000"
	r.Header.Set("X-Forwarded-For", "1.2.3.4")
	h.ServeHTTP(httptest.NewRecorder(), r)
	if got != "203.0.113.7:9000" {
		t.Errorf("got %q, want untouched 203.0.113.7:9000 — XFF from untrusted peer must be ignored", got)
	}
}

func TestTrustAwareRealIP_PeelsTrustedHopsRightToLeft(t *testing.T) {
	// Two layers of trusted proxy in front (e.g. CDN → ingress).
	mw := TrustAwareRealIP([]string{"10.0.0.0/8", "172.16.0.0/12"})
	got := ""
	h := mw(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		got = r.RemoteAddr
	}))
	r := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.RemoteAddr = "10.0.0.5:443"
	// XFF chain: real client → CDN edge (172.x) → ingress (10.x, our peer)
	r.Header.Set("X-Forwarded-For", "203.0.113.7, 172.16.5.5")
	h.ServeHTTP(httptest.NewRecorder(), r)
	if got != "203.0.113.7:443" {
		t.Errorf("got %q, want first untrusted hop 203.0.113.7:443", got)
	}
}

func TestTrustAwareRealIP_BareIPInAllowlist(t *testing.T) {
	// "1.2.3.4" without /32 should still match exactly that host.
	mw := TrustAwareRealIP([]string{"1.2.3.4"})
	got := ""
	h := mw(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		got = r.RemoteAddr
	}))
	r := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.RemoteAddr = "1.2.3.4:80"
	r.Header.Set("X-Forwarded-For", "9.9.9.9")
	h.ServeHTTP(httptest.NewRecorder(), r)
	if got != "9.9.9.9:80" {
		t.Errorf("got %q, want 9.9.9.9:80", got)
	}
}

func TestTrustAwareRealIP_NoXFFLeavesAddrAlone(t *testing.T) {
	mw := TrustAwareRealIP([]string{"10.0.0.0/8"})
	got := ""
	h := mw(http.HandlerFunc(func(_ http.ResponseWriter, r *http.Request) {
		got = r.RemoteAddr
	}))
	r := httptest.NewRequest(http.MethodGet, "/x", nil)
	r.RemoteAddr = "10.0.0.5:443"
	h.ServeHTTP(httptest.NewRecorder(), r)
	if got != "10.0.0.5:443" {
		t.Errorf("got %q, want untouched 10.0.0.5:443 when no XFF present", got)
	}
}
