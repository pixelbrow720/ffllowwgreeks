package alerts

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var webhookAsyncErrors = promauto.NewCounter(prometheus.CounterOpts{
	Name: "flowgreeks_alerts_webhook_async_errors_total",
	Help: "Webhook deliveries that failed in the background (Deliver returned nil but the POST errored).",
})

// ErrWebhookBlockedTarget is returned by NewWebhookSink when the URL
// resolves to a private / loopback / link-local address. We refuse to
// turn the api binary into an open SSRF gateway against the cloud
// metadata service, internal LAN, or sibling services on the same host.
var ErrWebhookBlockedTarget = errors.New("alerts: webhook URL points to a blocked target (loopback / private / link-local)")

// Sink is the delivery contract. Implementations must be non-blocking
// or self-bound — Engine.dispatch holds an RLock while calling.
type Sink interface {
	Deliver(t Trigger) error
}

// WebhookSink POSTs each Trigger to the configured URL. Failures are
// surfaced via flowgreeks_alerts_webhook_async_errors_total since
// delivery is async (so a slow endpoint can't block the engine's
// hot path).
type WebhookSink struct {
	URL    string
	Client *http.Client
}

// NewWebhookSink constructs a sink with sensible HTTP client defaults.
// Refuses URLs that resolve to a loopback, private, link-local, or
// unspecified address — those would let alert rules turn the api
// binary into an SSRF gateway against the cloud metadata service
// (169.254.169.254), localhost services, or the LAN.
//
// Returns ErrWebhookBlockedTarget on failure. Caller decides whether
// to surface that to the rule creator or fall back silently.
//
// The HTTP client uses a custom transport that re-runs the IP allow-list
// check at dial time. Without this, a malicious DNS server could pass
// validation (returning a public IP) and then flip to 169.254.169.254
// for the actual connect — classic DNS rebinding TOCTOU. The dial-time
// hook makes the round-trip end-to-end safe even against hostile DNS.
func NewWebhookSink(rawURL string) (*WebhookSink, error) {
	if err := validateWebhookURL(rawURL); err != nil {
		return nil, err
	}
	transport := &http.Transport{
		Proxy: http.ProxyFromEnvironment,
		DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
			return safeDial(ctx, network, addr)
		},
		TLSHandshakeTimeout:   3 * time.Second,
		ResponseHeaderTimeout: 5 * time.Second,
		IdleConnTimeout:       30 * time.Second,
		MaxIdleConns:          16,
		ForceAttemptHTTP2:     true,
	}
	return &WebhookSink{
		URL: rawURL,
		Client: &http.Client{
			Timeout:   5 * time.Second,
			Transport: transport,
		},
	}, nil
}

// safeDial resolves the target host and refuses the connection if any
// resolved address belongs to the blocked set. Acts as the dial-time
// half of the SSRF defense — pairs with validateWebhookURL's parse-time
// half so even a TOCTOU-rebound DNS answer cannot reach loopback /
// metadata / RFC 1918 hosts.
func safeDial(ctx context.Context, network, addr string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		return nil, fmt.Errorf("alerts: webhook split addr: %w", err)
	}
	ips, err := (&net.Resolver{}).LookupIPAddr(ctx, host)
	if err != nil {
		return nil, fmt.Errorf("alerts: webhook resolve %q: %w", host, err)
	}
	if len(ips) == 0 {
		return nil, fmt.Errorf("alerts: webhook host %q resolved to no addresses", host)
	}
	for _, ip := range ips {
		if isBlockedIP(ip.IP) {
			return nil, fmt.Errorf("%w: %s -> %s (dial-time)", ErrWebhookBlockedTarget, host, ip.IP)
		}
	}
	d := &net.Dialer{Timeout: 3 * time.Second, KeepAlive: 30 * time.Second}
	return d.DialContext(ctx, network, net.JoinHostPort(ips[0].IP.String(), port))
}

// validateWebhookURL parses the URL, requires http(s), and checks every
// resolved IP against the blocklist. Done at sink construction so a
// rule that points at 169.254.169.254 fails on save, not on first fire.
func validateWebhookURL(rawURL string) error {
	u, err := url.Parse(rawURL)
	if err != nil {
		return fmt.Errorf("alerts: webhook URL parse: %w", err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("alerts: webhook scheme %q not allowed", u.Scheme)
	}
	host := u.Hostname()
	if host == "" {
		return fmt.Errorf("alerts: webhook URL has empty host")
	}
	addrs, err := net.LookupIP(host)
	if err != nil {
		// DNS failure at validation time is itself suspicious — refuse.
		return fmt.Errorf("alerts: webhook host %q resolve: %w", host, err)
	}
	for _, ip := range addrs {
		if isBlockedIP(ip) {
			return fmt.Errorf("%w: %s -> %s", ErrWebhookBlockedTarget, host, ip)
		}
	}
	return nil
}

// isBlockedIP reports whether the address belongs to a range we refuse
// to deliver to. Called both at sink construction (validateWebhookURL)
// and at dial time (safeDial), so a DNS rebinding answer that flipped
// after parse-time validation still gets caught before any bytes go
// over the wire.
func isBlockedIP(ip net.IP) bool {
	if ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() ||
		ip.IsInterfaceLocalMulticast() || ip.IsMulticast() || ip.IsUnspecified() {
		return true
	}
	if ip4 := ip.To4(); ip4 != nil {
		// RFC 1918 private ranges + cloud metadata (169.254.169.254 is
		// already link-local) + CGNAT.
		_, c10, _ := net.ParseCIDR("10.0.0.0/8")
		_, c172, _ := net.ParseCIDR("172.16.0.0/12")
		_, c192, _ := net.ParseCIDR("192.168.0.0/16")
		_, c100, _ := net.ParseCIDR("100.64.0.0/10")
		for _, n := range []*net.IPNet{c10, c172, c192, c100} {
			if n != nil && n.Contains(ip4) {
				return true
			}
		}
		return false
	}
	// IPv6 ULA fc00::/7 + site-local fec0::/10 (deprecated but blocked).
	_, ula, _ := net.ParseCIDR("fc00::/7")
	_, sl, _ := net.ParseCIDR("fec0::/10")
	for _, n := range []*net.IPNet{ula, sl} {
		if n != nil && n.Contains(ip) {
			return true
		}
	}
	return false
}

// Deliver fires the webhook in a goroutine so a slow endpoint can't
// block the engine's hot path. Returns nil even on async failure;
// background errors bump webhookAsyncErrors so operators still see them.
func (w *WebhookSink) Deliver(t Trigger) error {
	go func() {
		body, err := json.Marshal(t)
		if err != nil {
			webhookAsyncErrors.Inc()
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, w.URL, bytes.NewReader(body))
		if err != nil {
			webhookAsyncErrors.Inc()
			return
		}
		req.Header.Set("Content-Type", "application/json")
		resp, err := w.Client.Do(req)
		if err != nil {
			webhookAsyncErrors.Inc()
			return
		}
		_ = resp.Body.Close()
		// Treat 4xx/5xx as failures — a 200/204 endpoint is the only
		// success signal we have without consuming the body.
		if resp.StatusCode >= 400 {
			webhookAsyncErrors.Inc()
		}
	}()
	return nil
}

// FanoutSink broadcasts each Trigger to every subscribed channel. Used
// to feed the dashboard "Alerts" panel via WS without an extra moving
// part. Drop-on-full per subscriber so a slow client cannot back up
// the engine.
type FanoutSink struct {
	mu   sync.RWMutex
	subs map[*fanoutSub]struct{}
}

type fanoutSub struct {
	ch     chan Trigger
	userID string
}

// NewFanoutSink returns an empty broadcaster.
func NewFanoutSink() *FanoutSink {
	return &FanoutSink{subs: make(map[*fanoutSub]struct{}, 16)}
}

// Subscribe registers a new in-process subscriber. userID lets the
// caller filter to one user's alerts; empty string means "all".
func (f *FanoutSink) Subscribe(buf int, userID string) (<-chan Trigger, func()) {
	if buf <= 0 {
		buf = 32
	}
	sub := &fanoutSub{ch: make(chan Trigger, buf), userID: userID}
	f.mu.Lock()
	f.subs[sub] = struct{}{}
	f.mu.Unlock()
	cancel := func() {
		f.mu.Lock()
		if _, ok := f.subs[sub]; ok {
			delete(f.subs, sub)
			close(sub.ch)
		}
		f.mu.Unlock()
	}
	return sub.ch, cancel
}

// Deliver fans out to all subscribers; matches userID when set.
func (f *FanoutSink) Deliver(t Trigger) error {
	f.mu.RLock()
	defer f.mu.RUnlock()
	for sub := range f.subs {
		if sub.userID != "" && sub.userID != t.UserID {
			continue
		}
		select {
		case sub.ch <- t:
		default:
		}
	}
	return nil
}

// String returns a short label for diagnostics.
func (f *FanoutSink) String() string { return fmt.Sprintf("fanout(subs=%d)", len(f.subs)) }
