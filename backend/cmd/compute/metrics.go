package main

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Compute pipeline metrics. Operators alert on:
//
//   - rate(flowgreeks_compute_ticks_processed_total) drops to 0 →
//     ingest is dead or NATS is partitioned
//   - rate(flowgreeks_compute_iv_solver_failures_total) high →
//     stale or bad quote book
//   - flowgreeks_compute_aggregator_iterations not increasing →
//     aggregator stuck
var (
	ticksProcessedTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_compute_ticks_processed_total",
		Help: "Total ticks the compute pipeline ingested, by symbol/tick_type.",
	}, []string{"symbol", "tick_type"})

	ivSolverAttemptsTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_compute_iv_solver_attempts_total",
		Help: "Total IV solver invocations.",
	})
	ivSolverFailuresTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_compute_iv_solver_failures_total",
		Help: "IV solver invocations that did not converge.",
	})

	aggregatorIterations = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_compute_aggregator_iterations_total",
		Help: "Aggregator loop iterations completed.",
	})
	aggregatorDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "flowgreeks_compute_aggregator_duration_seconds",
		Help:    "Wall-clock time taken by one aggregator pass over both symbols.",
		Buckets: prometheus.ExponentialBuckets(0.001, 2, 12),
	})
	aggregatorActiveStrikes = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "flowgreeks_compute_active_strikes",
		Help: "Number of active strikes the IV cache holds, by symbol.",
	}, []string{"symbol"})
)

// tickTypeLabel returns a stable Prometheus label for a tick type.
// Cardinality bounded to {quote, trade, oi, future, other}.
func tickTypeLabel(isFuture bool, t uint8) string {
	if isFuture {
		return "future"
	}
	switch t {
	case 1: // feed.TickTypeQuote
		return "quote"
	case 2: // feed.TickTypeTrade
		return "trade"
	case 3: // feed.TickTypeOI
		return "oi"
	}
	return "other"
}

// computeSymLabel mirrors api.symLabel locally so cmd/compute does
// not import internal/api just for a label string.
func computeSymLabel(id uint8) string {
	switch id {
	case 1:
		return "spx"
	case 2:
		return "ndx"
	}
	return "unknown"
}
