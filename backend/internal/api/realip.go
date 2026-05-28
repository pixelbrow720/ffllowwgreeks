package api

import (
	"net"
	"net/http"
	"strings"
)

// TrustAwareRealIP rewrites r.RemoteAddr from X-Forwarded-For only when
// the inbound RemoteAddr matches one of the configured trusted-proxy
// CIDRs. Without an allowlist, XFF is ignored entirely so a hostile
// client can't spoof source IP for audit logging or per-IP rate limiting.
//
// XFF is parsed right-to-left, peeling trusted hops off the tail until
// we reach the first untrusted address — that's the real client. RFC 7239
// would be more precise but XFF is what every load balancer in front of
// us actually emits.
//
// trustedCIDRs is a slice of strings (e.g. "10.0.0.0/8", "1.2.3.4/32").
// Invalid entries are skipped silently at startup; the caller is expected
// to log them once when constructing the middleware.
//
// Returns a no-op middleware when trustedCIDRs is empty — the safer
// default for direct-to-internet deployments.
func TrustAwareRealIP(trustedCIDRs []string) func(http.Handler) http.Handler {
	nets := parseCIDRs(trustedCIDRs)
	if len(nets) == 0 {
		return func(next http.Handler) http.Handler { return next }
	}
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if real := resolveRealIP(r, nets); real != "" {
				r.RemoteAddr = real
			}
			next.ServeHTTP(w, r)
		})
	}
}

func parseCIDRs(raw []string) []*net.IPNet {
	out := make([]*net.IPNet, 0, len(raw))
	for _, s := range raw {
		s = strings.TrimSpace(s)
		if s == "" {
			continue
		}
		if !strings.Contains(s, "/") {
			// Treat bare IP as a /32 or /128 host.
			if ip := net.ParseIP(s); ip != nil {
				if ip4 := ip.To4(); ip4 != nil {
					s += "/32"
				} else {
					s += "/128"
				}
			}
		}
		_, n, err := net.ParseCIDR(s)
		if err == nil && n != nil {
			out = append(out, n)
		}
	}
	return out
}

func resolveRealIP(r *http.Request, trusted []*net.IPNet) string {
	host, port, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	ip := net.ParseIP(host)
	if ip == nil || !cidrContains(trusted, ip) {
		return ""
	}
	xff := r.Header.Get("X-Forwarded-For")
	if xff == "" {
		return ""
	}
	hops := strings.Split(xff, ",")
	// Walk right-to-left peeling trusted hops; the first untrusted hop
	// is the real client. If every hop is trusted (unusual, but possible
	// behind nested proxies), the leftmost hop wins.
	for i := len(hops) - 1; i >= 0; i-- {
		h := strings.TrimSpace(hops[i])
		hopIP := net.ParseIP(h)
		if hopIP == nil {
			continue
		}
		if !cidrContains(trusted, hopIP) {
			return joinHostPort(h, port)
		}
	}
	first := strings.TrimSpace(hops[0])
	if first == "" {
		return ""
	}
	return joinHostPort(first, port)
}

func cidrContains(nets []*net.IPNet, ip net.IP) bool {
	for _, n := range nets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

func joinHostPort(host, port string) string {
	if port == "" {
		return host
	}
	return net.JoinHostPort(host, port)
}
