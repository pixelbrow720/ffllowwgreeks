package api

import (
	"net/http"
	"strconv"
	"time"

	"flowgreeks/internal/feed"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	httpRequestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_http_requests_total",
		Help: "Total HTTP requests served, by method/route/status_class.",
	}, []string{"method", "route", "status_class"})

	httpRequestDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "flowgreeks_http_request_duration_seconds",
		Help:    "HTTP request latency, by method/route. Excludes WebSocket upgrades.",
		Buckets: prometheus.ExponentialBuckets(0.001, 2, 14), // 1ms .. ~16s
	}, []string{"method", "route"})

	httpResponseBytes = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "flowgreeks_http_response_bytes",
		Help:    "HTTP response body size, by method/route.",
		Buckets: []float64{128, 1024, 8192, 65536, 524288, 4194304},
	}, []string{"method", "route"})

	wsSubscribers = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "flowgreeks_ws_subscribers",
		Help: "Current number of WebSocket subscribers attached to the Broker.",
	})

	wsPublished = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_ws_published_total",
		Help: "Total Snapshot events fanned out by the Broker, by symbol/kind.",
	}, []string{"symbol", "kind"})

	wsDrops = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_ws_drops_total",
		Help: "Total Snapshot events dropped because a subscriber's channel was full.",
	}, []string{"symbol", "kind"})
)

// symLabel returns a stable Prometheus label for a Symbol, keeping
// cardinality bounded — only "spx", "ndx", or "unknown".
func symLabel(s feed.Symbol) string {
	switch s {
	case feed.SymbolSPX:
		return "spx"
	case feed.SymbolNDX:
		return "ndx"
	}
	return "unknown"
}

// MetricsMiddleware records per-request Prometheus metrics. Mount it
// AFTER chi has resolved the route so RouteContext().RoutePattern()
// returns a useful label rather than the raw path. Place after
// middleware.Recoverer so panics are still counted as 500s.
//
// WebSocket upgrades hijack the connection; the wrapped writer's
// status/bytes will read 0 in that case so they're skipped to avoid
// distorting the histograms.
func MetricsMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		ww := middleware.NewWrapResponseWriter(w, r.ProtoMajor)
		next.ServeHTTP(ww, r)

		route := chiRoute(r)
		method := r.Method
		status := ww.Status()
		if status == 0 {
			// WS upgrade or connection hijacked. Don't record.
			return
		}
		httpRequestsTotal.WithLabelValues(method, route, statusClass(status)).Inc()
		httpRequestDuration.WithLabelValues(method, route).Observe(time.Since(start).Seconds())
		httpResponseBytes.WithLabelValues(method, route).Observe(float64(ww.BytesWritten()))
	})
}

// chiRoute returns the matched chi route pattern, falling back to
// "unmatched" when the router didn't find a handler. Using the pattern
// (e.g. "/api/snapshot/{symbol}") keeps cardinality bounded — the raw
// path would explode on the symbol axis.
func chiRoute(r *http.Request) string {
	if rc := chi.RouteContext(r.Context()); rc != nil {
		if p := rc.RoutePattern(); p != "" {
			return p
		}
	}
	return "unmatched"
}

func statusClass(status int) string {
	if status < 100 || status >= 600 {
		return "xxx"
	}
	return strconv.Itoa(status/100) + "xx"
}
