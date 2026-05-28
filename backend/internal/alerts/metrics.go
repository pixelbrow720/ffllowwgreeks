package alerts

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Alerts engine metrics. Operators alert on:
//
//   - rate(flowgreeks_alerts_evaluations_total) drops to 0 →
//     compute is no longer publishing or alerts subscriber dropped
//   - rate(flowgreeks_alerts_delivery_errors_total) elevated →
//     a sink (webhook, broker) is degraded
var (
	rulesGauge = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "flowgreeks_alerts_rules",
		Help: "Number of alert rules registered (enabled + disabled).",
	})

	evaluationsTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_alerts_evaluations_total",
		Help: "Total Snapshot evaluations across all rules.",
	})

	firedTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_alerts_fired_total",
		Help: "Triggers that passed the predicate AND cooldown gate, by rule kind.",
	}, []string{"kind"})

	cooldownSuppressedTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_alerts_cooldown_suppressed_total",
		Help: "Triggers suppressed by cooldown, by rule kind.",
	}, []string{"kind"})

	deliveriesTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_alerts_deliveries_total",
		Help: "Trigger deliveries to sinks.",
	}, []string{"sink"})

	deliveryErrorsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_alerts_delivery_errors_total",
		Help: "Sink delivery failures.",
	}, []string{"sink"})
)
