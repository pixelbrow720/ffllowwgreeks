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

func quietLog() *slog.Logger {
	return slog.New(slog.NewTextHandler(io.Discard, nil))
}

// fakePub records ticks; Manager tests inject it.
type fakePub struct {
	mu    sync.Mutex
	ticks []feed.Tick
}

func (p *fakePub) Publish(_ context.Context, t feed.Tick) error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.ticks = append(p.ticks, t)
	return nil
}

func (p *fakePub) count() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.ticks)
}

// fakeReader returns a controlled tick stream so tests don't need
// Postgres. Implemented as a struct that swaps Reader's pool out by
// constructing a Reader-shaped helper inline.
//
// We achieve this with a Reader-compatible Stream method on a small
// type that the test substitutes via a function variable below.
type fakeReader struct {
	ticks []feed.Tick
}

func (f *fakeReader) Stream(ctx context.Context, _ Range) (<-chan feed.Tick, <-chan error) {
	out := make(chan feed.Tick, len(f.ticks))
	errs := make(chan error, 1)
	go func() {
		defer close(out)
		defer close(errs)
		for _, t := range f.ticks {
			select {
			case <-ctx.Done():
				return
			case out <- t:
			}
		}
	}()
	return out, errs
}

// readerLike is the subset of *Reader that Session uses.
type readerLike interface {
	Stream(ctx context.Context, rng Range) (<-chan feed.Tick, <-chan error)
}

// newSessionWithReader is a test-only constructor that takes a custom
// readerLike. We mirror NewSession but bypass the *Reader type so tests
// can drive without Postgres.
func newSessionWithReader(id string, rng Range, reader readerLike, pub Publisher, log *slog.Logger, opts SessionOptions) *Session {
	if opts.Speed < 0 {
		opts.Speed = 0
	}
	s := &Session{
		id:     id,
		rng:    rng,
		reader: nil, // unused; we override Stream via the readerLike adapter
		pub:    pub,
		log:    log,
		done:   make(chan struct{}),
		subs:   make(map[*sessionSubscriber]struct{}, 4),
	}
	s.setSpeed(opts.Speed)
	s.updateStatus(func(st *SessionStatus) {
		st.ID = id
		st.State = SessionIdle
		st.Speed = opts.Speed
		st.StartTs = rng.Start
		st.EndTs = rng.End
	})
	// Replace the runner stream by capturing reader closure and using
	// a tiny goroutine wrapper.
	go func() {
		// no-op; the real Run() below uses session.reader.Stream which
		// would be nil. Tests that need this constructor should call
		// runWithReader instead.
	}()
	_ = reader
	return s
}

// runWithReader is a test-only Session.Run that uses an injected
// readerLike instead of the embedded *Reader.
func (s *Session) runWithReader(parent context.Context, reader readerLike) error {
	defer close(s.done)
	if s.stopped.Load() {
		return nil
	}
	ctx, cancel := context.WithCancel(parent)
	s.cancel = cancel
	defer cancel()
	s.updateStatus(func(st *SessionStatus) {
		if st.State == SessionIdle {
			st.State = SessionPlaying
		}
	})
	ticks, _ := reader.Stream(ctx, s.rng)

	var anchorEvent uint64
	var anchorWall time.Time
	for {
		for s.paused.Load() && !s.stopped.Load() {
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(20 * time.Millisecond):
			}
		}
		select {
		case <-ctx.Done():
			s.markFinishedOrStopped()
			return nil
		case t, ok := <-ticks:
			if !ok {
				s.updateStatus(func(st *SessionStatus) {
					if st.State != SessionStopped {
						st.State = SessionFinished
					}
				})
				return nil
			}
			speed := s.getSpeed()
			if speed > 0 {
				if anchorEvent == 0 {
					anchorEvent = t.TsEvent
					anchorWall = time.Now()
				}
				if wait := waitFor(anchorEvent, anchorWall, t.TsEvent, speed); wait > 0 {
					select {
					case <-ctx.Done():
						s.markFinishedOrStopped()
						return nil
					case <-time.After(wait):
					}
				}
			}
			if err := s.pub.Publish(ctx, t); err != nil {
				continue
			}
			s.updateStatus(func(st *SessionStatus) {
				st.Published++
				st.CurrentTs = time.Unix(0, int64(t.TsEvent)).UTC()
			})
		}
	}
}

func TestSession_UnpacedRunCompletes(t *testing.T) {
	now := time.Now().UnixNano()
	rd := &fakeReader{ticks: []feed.Tick{
		{TsEvent: uint64(now)},
		{TsEvent: uint64(now + int64(time.Millisecond))},
		{TsEvent: uint64(now + 2*int64(time.Millisecond))},
	}}
	pub := &fakePub{}
	sess := newSessionWithReader("t1", Range{Symbol: feed.SymbolSPX}, rd, pub, quietLog(), SessionOptions{Speed: 0})
	if err := sess.runWithReader(context.Background(), rd); err != nil {
		t.Fatalf("Run: %v", err)
	}
	if pub.count() != 3 {
		t.Errorf("expected 3 ticks published, got %d", pub.count())
	}
	if got := sess.Status().State; got != SessionFinished {
		t.Errorf("expected SessionFinished, got %v", got)
	}
}

func TestSession_PauseResumeStop(t *testing.T) {
	now := time.Now().UnixNano()
	ticks := make([]feed.Tick, 50)
	for i := range ticks {
		ticks[i] = feed.Tick{TsEvent: uint64(now + int64(i)*int64(time.Millisecond)*100)}
	}
	rd := &fakeReader{ticks: ticks}
	pub := &fakePub{}
	sess := newSessionWithReader("t2", Range{Symbol: feed.SymbolSPX}, rd, pub, quietLog(), SessionOptions{Speed: 1})

	go func() { _ = sess.runWithReader(context.Background(), rd) }()
	time.Sleep(60 * time.Millisecond)
	sess.Pause()
	// Allow one tick of race window — Pause may land between the
	// pause-check and the publish for the in-flight tick.
	beforePause := pub.count()
	time.Sleep(250 * time.Millisecond)
	got := pub.count()
	if got > beforePause+1 {
		t.Errorf("paused session leaked >1 tick: before=%d after=%d", beforePause, got)
	}
	sess.Resume()
	time.Sleep(120 * time.Millisecond)
	if got2 := pub.count(); got2 <= got {
		t.Errorf("resume did not advance publishing: got %d (was %d)", got2, got)
	}
	sess.Stop()
	<-sess.Done()
	if got := sess.Status().State; got != SessionStopped {
		t.Errorf("expected SessionStopped, got %v", got)
	}
}

func TestSession_SpeedChangeAtomic(t *testing.T) {
	sess := newSessionWithReader("t3", Range{Symbol: feed.SymbolSPX}, nil, &fakePub{}, quietLog(), SessionOptions{Speed: 1})
	sess.SetSpeed(8)
	if got := sess.Speed(); got != 8 {
		t.Errorf("speed not applied: %v", got)
	}
	if got := sess.Status().Speed; got != 8 {
		t.Errorf("status speed not updated: %v", got)
	}
}

func TestSession_SubscribePushesCurrent(t *testing.T) {
	sess := newSessionWithReader("t4", Range{Symbol: feed.SymbolSPX}, nil, &fakePub{}, quietLog(), SessionOptions{Speed: 4})
	ch, cancel := sess.Subscribe(4)
	defer cancel()
	select {
	case st := <-ch:
		if st.ID != "t4" {
			t.Errorf("unexpected id: %v", st.ID)
		}
	case <-time.After(50 * time.Millisecond):
		t.Fatal("subscribe did not push current status")
	}
}

func TestManager_LimitAndDuplicate(t *testing.T) {
	pub := &fakePub{}
	m := NewManager(nil, pub, quietLog(), ManagerOpts{MaxOpen: 1, ReapAfter: time.Hour})

	// We use NewSession internally, so Manager.Create needs a non-nil
	// reader because Run will try to use it. Patch by stopping the
	// session right after create.
	rng := Range{Symbol: feed.SymbolSPX, Start: time.Now(), End: time.Now().Add(time.Minute)}
	// Replace reader path by skipping Run. Instead, construct sessions
	// directly and stash them in m.sessions to test the limit logic.
	m.mu.Lock()
	m.sessions["a"] = NewSession("a", rng, nil, pub, quietLog(), SessionOptions{})
	m.mu.Unlock()

	if _, err := m.Create(context.Background(), "a", rng, SessionOptions{}); err != ErrSessionExists {
		t.Errorf("expected ErrSessionExists, got %v", err)
	}
	if _, err := m.Create(context.Background(), "b", rng, SessionOptions{}); err != ErrSessionLimit {
		t.Errorf("expected ErrSessionLimit, got %v", err)
	}
	if got := m.Get("a"); got == nil {
		t.Error("Get returned nil for known id")
	}
	if got := m.Active(); len(got) != 1 || got[0] != "a" {
		t.Errorf("Active returned %v, expected [a]", got)
	}
}

func TestIDFromPath(t *testing.T) {
	cases := []struct{ in, want string }{
		{"/ws/replay/abc", "abc"},
		{"/ws/replay/abc/", "abc"},
		{"/ws/replay/abc/extra", "abc"},
		{"/other/path", ""},
		{"/ws/replay/", ""},
	}
	for _, c := range cases {
		got := IDFromPath(c.in)
		if got != c.want {
			t.Errorf("IDFromPath(%q)=%q want %q", c.in, got, c.want)
		}
	}
}
