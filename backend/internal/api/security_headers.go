package api

import "net/http"

// SecurityHeaders sets a conservative set of HTTP response headers that
// shield browser-side clients from common attacks. Applied as a global
// chi middleware so every route — including future ones — inherits the
// posture without explicit opt-in.
//
// Header rationale:
//
//   - X-Content-Type-Options: nosniff
//     Stops MIME-sniffing on JSON / text responses.
//
//   - X-Frame-Options: DENY
//     Refuses framing of any api response. We don't serve any UI from
//     this binary today; if that changes, the SPA host should override.
//
//   - Referrer-Policy: no-referrer
//     Prevents leaking the calling URL (which can carry tokens or PII)
//     when a logged-in browser follows a link from our domain.
//
//   - Content-Security-Policy: default-src 'none'; frame-ancestors 'none'
//     The api emits JSON; no client-side execution context is needed.
//     Frame-ancestors backs up X-Frame-Options for modern browsers.
//
//   - Strict-Transport-Security
//     Only set when the request arrived over TLS — telling an HTTP
//     client to switch is meaningless and can break local dev. Behind
//     a TLS-terminating proxy, set X-Forwarded-Proto=https so chi/RealIP
//     and this check see the right scheme.
//
//   - Cross-Origin-Resource-Policy: same-origin
//     A defence-in-depth nudge that complements the WS origin allowlist
//     and CORS policy.
func SecurityHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		h := w.Header()
		h.Set("X-Content-Type-Options", "nosniff")
		h.Set("X-Frame-Options", "DENY")
		h.Set("Referrer-Policy", "no-referrer")
		h.Set("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
		h.Set("Cross-Origin-Resource-Policy", "same-origin")
		if isHTTPS(r) {
			h.Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
		}
		next.ServeHTTP(w, r)
	})
}

// isHTTPS reports whether the inbound request arrived over TLS, taking
// X-Forwarded-Proto into account so reverse-proxy deployments still
// flip the HSTS header on.
func isHTTPS(r *http.Request) bool {
	if r.TLS != nil {
		return true
	}
	if proto := r.Header.Get("X-Forwarded-Proto"); proto == "https" {
		return true
	}
	return false
}
