package replay

import (
	"context"
	"fmt"
	"log/slog"
	"time"

	"flowgreeks/internal/feed"
)

// Publisher is the contract Runner uses to emit ticks. Production wires
// this to bus.Publisher; tests substitute a recording fake.
type Publisher interface {
	Publish(ctx context.Context, t feed.Tick) error
}

// Runner paces a stream of historical ticks and publishes them via the
// supplied Publisher.
//
// Speed is the wall-clock multiplier on event time:
//
//	1.0  → real time
//	4.0  → 4× faster (1 minute of session = 15 seconds wall clock)
//	60.0 → 60× faster
//	0    → as fast as the sink can absorb (no pacing)
//
// Pacing aligns the FIRST tick to "now" and offsets every subsequent
// tick's wait time relative to that anchor. Drift accumulates only from
// publish latency, not from sleep error.
type Runner struct {
	pub   Publisher
	log   *slog.Logger
	speed float64
}

func NewRunner(pub Publisher, log *slog.Logger, speed float64) *Runner {
	if speed < 0 {
		speed = 0
	}
	return &Runner{pub: pub, log: log, speed: speed}
}

// Run drains the tick channel, paces each emit, and publishes via the
// shared bus.Publisher (binary 90-byte encoding, identical to live
// ingest). Returns nil when the source channel closes cleanly, or the
// first publish/context error.
//
// Heartbeat log every 10s with progress counters.
func (r *Runner) Run(ctx context.Context, ticks <-chan feed.Tick) error {
	if r.speed == 0 {
		return r.runUnpaced(ctx, ticks)
	}

	var anchorEvent uint64
	var anchorWall time.Time
	var (
		published, errors, consumed uint64
		lastLog                     = time.Now()
	)
	logEvery := 10 * time.Second

	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case t, ok := <-ticks:
			if !ok {
				r.log.Info("replay finished",
					"published", published, "consumed", consumed, "errors", errors)
				return nil
			}
			consumed++
			if anchorEvent == 0 {
				anchorEvent = t.TsEvent
				anchorWall = time.Now()
			}
			// elapsed event time since anchor, scaled by speed
			elapsedEventNs := int64(t.TsEvent - anchorEvent)
			elapsedWall := time.Duration(float64(elapsedEventNs) / r.speed)
			due := anchorWall.Add(elapsedWall)
			now := time.Now()
			if wait := due.Sub(now); wait > 0 {
				select {
				case <-ctx.Done():
					return ctx.Err()
				case <-time.After(wait):
				}
			}
			if err := r.pub.Publish(ctx, t); err != nil {
				errors++
				if errors < 10 || errors%1000 == 0 {
					r.log.Warn("replay publish failed", "err", err)
				}
			} else {
				published++
			}
			if time.Since(lastLog) >= logEvery {
				r.log.Info("replay heartbeat",
					"consumed", consumed, "published", published,
					"errors", errors, "speed", r.speed,
					"event_wall_ts", time.Unix(0, int64(t.TsEvent)).UTC().Format(time.RFC3339))
				lastLog = time.Now()
			}
		}
	}
}

func (r *Runner) runUnpaced(ctx context.Context, ticks <-chan feed.Tick) error {
	var consumed, published, errors uint64
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case t, ok := <-ticks:
			if !ok {
				r.log.Info("replay finished (unpaced)",
					"published", published, "consumed", consumed, "errors", errors)
				return nil
			}
			consumed++
			if err := r.pub.Publish(ctx, t); err != nil {
				errors++
				if errors < 10 || errors%1000 == 0 {
					r.log.Warn("replay publish failed", "err", err)
				}
				continue
			}
			published++
		}
	}
}

// FormatRange is a small helper for CLI logging.
func FormatRange(rng Range) string {
	return fmt.Sprintf("%s [%s → %s]",
		rng.Symbol.String(),
		rng.Start.UTC().Format(time.RFC3339),
		rng.End.UTC().Format(time.RFC3339))
}
