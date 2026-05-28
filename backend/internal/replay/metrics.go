package replay

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// Replay manager + session metrics. Operators alert on:
//
//   - flowgreeks_replay_sessions_active near MaxOpen → likely
//     orphaned sessions (clients disconnected without Stop)
//   - rate(flowgreeks_replay_session_errors_total) elevated →
//     historical reader or publisher is degraded
//   - rate(flowgreeks_replay_ticks_published_total) at 0 while
//     sessions are active → reader stuck
var (
	sessionsActive = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "flowgreeks_replay_sessions_active",
		Help: "Number of replay sessions currently registered on the manager.",
	})

	sessionsCreated = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_replay_sessions_created_total",
		Help: "Total replay sessions ever created.",
	})

	sessionsRejected = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_replay_sessions_rejected_total",
		Help: "Replay session creation rejections, by reason.",
	}, []string{"reason"})

	sessionsFinishedByState = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "flowgreeks_replay_sessions_finished_total",
		Help: "Replay sessions that exited their Run loop, by terminal state.",
	}, []string{"state"})

	ticksPublishedTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_replay_ticks_published_total",
		Help: "Total ticks re-published onto NATS by replay sessions.",
	})

	publishErrorsTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "flowgreeks_replay_publish_errors_total",
		Help: "Replay publish failures (NATS publish or downstream). Bumped per offending tick.",
	})
)
