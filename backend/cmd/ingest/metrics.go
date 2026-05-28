package main

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Ingest dispatch metrics. The archive writer already publishes
// flowgreeks_archive_ticks_{written,dropped}_total; these add the
// NATS-publish side of the same fork plus feed error visibility.
var (
	publishedTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_ingest_published_total",
		Help: "Ticks successfully published to NATS by the ingest dispatcher, by tick_type.",
	}, []string{"tick_type"})

	publishErrorsTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_ingest_publish_errors_total",
		Help: "NATS publish failures during ingest dispatch.",
	})

	feedErrorsTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_ingest_feed_errors_total",
		Help: "Non-fatal errors surfaced by the upstream feed adapter.",
	})
)

// tickTypeLabel returns a stable Prometheus label, bounded to a small
// set so cardinality stays predictable.
func tickTypeLabel(isFuture bool, t uint8) string {
	if isFuture {
		return "future"
	}
	switch t {
	case 1:
		return "quote"
	case 2:
		return "trade"
	case 3:
		return "oi"
	}
	return "other"
}
