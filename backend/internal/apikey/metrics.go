package apikey

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Prometheus metrics for the API-key auth surface. Cardinality is
// bounded by `result` enum only — no per-key labels.
var (
	authAttempts = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_apikey_auth_attempts_total",
		Help: "Total API-key auth attempts, by outcome.",
	}, []string{"result"}) // ok | missing | unknown | revoked | expired | lookup_error

	rateLimited = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_apikey_rate_limited_total",
		Help: "Total requests rejected with 429 by the per-key rate limiter.",
	})
)

func recordAuth(result string) { authAttempts.WithLabelValues(result).Inc() }
func recordRateLimited()       { rateLimited.Inc() }
