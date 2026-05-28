package replay

import (
	"context"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

type recordingPub struct {
	mu    sync.Mutex
	ticks []feed.Tick
	when  []time.Time
}

func (p *recordingPub) Publish(_ context.Context, t feed.Tick) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.ticks = append(p.ticks, t)
	p.when = append(p.when, time.Now())
	return nil
}

func quietLogger() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

func TestRunnerUnpacedPreservesOrder(t *testing.T) {
	in := make(chan feed.Tick, 16)
	for i := 0; i < 8; i++ {
		in <- feed.Tick{TsEvent: uint64(i), Strike: uint32(i)}
	}
	close(in)

	pub := &recordingPub{}
	r := NewRunner(pub, quietLogger(), 0)
	if err := r.Run(context.Background(), in); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(pub.ticks) != 8 {
		t.Fatalf("expected 8 published, got %d", len(pub.ticks))
	}
	for i, p := range pub.ticks {
		if p.Strike != uint32(i) {
			t.Errorf("order broken at %d: strike=%d", i, p.Strike)
		}
	}
}

func TestRunnerPacedRespectsSpeed(t *testing.T) {
	// Two ticks, 100ms apart in event time. At speed=10× that's 10ms wall
	// clock. We allow generous tolerance — the test asserts the runner
	// did NOT release them in the same instant (which would happen if
	// pacing were broken).
	const eventDeltaMs = 100
	const speed = 10.0

	now := time.Now().UnixNano()
	in := make(chan feed.Tick, 4)
	in <- feed.Tick{TsEvent: uint64(now)}
	in <- feed.Tick{TsEvent: uint64(now + eventDeltaMs*int64(time.Millisecond))}
	close(in)

	pub := &recordingPub{}
	r := NewRunner(pub, quietLogger(), speed)
	if err := r.Run(context.Background(), in); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if len(pub.when) != 2 {
		t.Fatalf("expected 2 timestamps, got %d", len(pub.when))
	}
	gap := pub.when[1].Sub(pub.when[0])
	expected := time.Duration(eventDeltaMs) * time.Millisecond / speed
	if gap < expected/2 {
		t.Errorf("paced gap too small: got %v, want >= %v", gap, expected/2)
	}
	if gap > expected*5 {
		t.Errorf("paced gap too large: got %v, want <= %v", gap, expected*5)
	}
}

func TestRunnerCancellable(t *testing.T) {
	in := make(chan feed.Tick) // never closed; emits one tick then blocks
	go func() {
		in <- feed.Tick{TsEvent: 1}
	}()
	pub := &recordingPub{}
	r := NewRunner(pub, quietLogger(), 1.0)
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Millisecond)
	defer cancel()
	err := r.Run(ctx, in)
	if err != context.DeadlineExceeded && err != context.Canceled {
		t.Errorf("expected ctx error, got %v", err)
	}
}
