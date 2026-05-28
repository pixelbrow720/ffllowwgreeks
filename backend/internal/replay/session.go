package replay

import (
	"context"
	"errors"
	"log/slog"
	"math"
	"sync"
	"sync/atomic"
	"time"
)

// SessionState is the lifecycle phase of a Session.
type SessionState string

const (
	SessionIdle     SessionState = "idle"
	SessionPlaying  SessionState = "playing"
	SessionPaused   SessionState = "paused"
	SessionFinished SessionState = "finished"
	SessionStopped  SessionState = "stopped"
	SessionFailed   SessionState = "failed"
)

// SessionOptions tunes a single replay session.
type SessionOptions struct {
	Speed float64 // 1.0 real-time, N× faster, 0 = unpaced
}

// SessionStatus is the snapshot the WS layer streams to clients.
type SessionStatus struct {
	ID         string       `json:"id"`
	State      SessionState `json:"state"`
	Speed      float64      `json:"speed"`
	StartTs    time.Time    `json:"start_ts"`    // range start
	EndTs      time.Time    `json:"end_ts"`      // range end
	CurrentTs  time.Time    `json:"current_ts"`  // event time of last published tick
	Published  uint64       `json:"published"`
	Errors     uint64       `json:"errors"`
	UpdatedAt  time.Time    `json:"updated_at"`
	Error      string       `json:"error,omitempty"`
}

// Session orchestrates one replay job. Created via Manager.Create, then
// driven by a separate goroutine launched by Start.
//
// Thread safety: state-mutating control methods (Pause/Resume/SetSpeed/
// Stop) are safe to call from any goroutine. Status() is RLock-cheap.
type Session struct {
	id     string
	rng    Range
	reader *Reader
	pub    Publisher
	log    *slog.Logger

	speed   atomic.Uint64 // float64 bits
	paused  atomic.Bool
	stopped atomic.Bool

	statusMu sync.RWMutex
	status   SessionStatus

	subsMu sync.Mutex
	subs   map[*sessionSubscriber]struct{}

	// cancelMu guards cancel. Run writes; Stop reads. Without this lock
	// Stop arriving before Run has stored its cancel func would be
	// silently dropped (the ctx-cancel never fires, the loop keeps
	// publishing until the parent ctx tells it to quit).
	cancelMu sync.Mutex
	cancel   context.CancelFunc

	// doneOnce gates close(done) so an accidental double Run can't
	// panic on close-of-closed-channel.
	doneOnce sync.Once
	done     chan struct{}
}

type sessionSubscriber struct {
	ch chan SessionStatus
}

// NewSession constructs an idle session. Call Run to start the loop.
func NewSession(id string, rng Range, reader *Reader, pub Publisher, log *slog.Logger, opts SessionOptions) *Session {
	if opts.Speed < 0 {
		opts.Speed = 0
	}
	s := &Session{
		id:     id,
		rng:    rng,
		reader: reader,
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
	return s
}

// ID returns the session id.
func (s *Session) ID() string { return s.id }

// Status returns a copy of the current status. Cheap.
func (s *Session) Status() SessionStatus {
	s.statusMu.RLock()
	defer s.statusMu.RUnlock()
	return s.status
}

// Subscribe returns a channel that receives status updates as the session
// progresses. Caller must call the returned cancel to stop the
// subscription. Buffer drops on full.
func (s *Session) Subscribe(buf int) (<-chan SessionStatus, func()) {
	if buf <= 0 {
		buf = 32
	}
	sub := &sessionSubscriber{ch: make(chan SessionStatus, buf)}
	s.subsMu.Lock()
	s.subs[sub] = struct{}{}
	s.subsMu.Unlock()

	// Push current status so a fresh subscriber doesn't have to wait
	// for the next event.
	cur := s.Status()
	select {
	case sub.ch <- cur:
	default:
	}

	cancel := func() {
		s.subsMu.Lock()
		if _, ok := s.subs[sub]; ok {
			delete(s.subs, sub)
			close(sub.ch)
		}
		s.subsMu.Unlock()
	}
	return sub.ch, cancel
}

// Pause flips the session into the paused state. Idempotent.
func (s *Session) Pause() {
	if s.stopped.Load() {
		return
	}
	s.paused.Store(true)
	s.updateStatus(func(st *SessionStatus) {
		if st.State == SessionPlaying {
			st.State = SessionPaused
		}
	})
}

// Resume flips the session out of paused. Idempotent.
func (s *Session) Resume() {
	if s.stopped.Load() {
		return
	}
	s.paused.Store(false)
	s.updateStatus(func(st *SessionStatus) {
		if st.State == SessionPaused {
			st.State = SessionPlaying
		}
	})
}

// SetSpeed updates the playback speed atomically. Takes effect on the
// next tick boundary. 0 = unpaced (max throughput).
func (s *Session) SetSpeed(speed float64) {
	if speed < 0 {
		speed = 0
	}
	s.setSpeed(speed)
	s.updateStatus(func(st *SessionStatus) {
		st.Speed = speed
	})
}

// Stop ends the session cleanly. Idempotent.
func (s *Session) Stop() {
	if s.stopped.Swap(true) {
		return
	}
	s.cancelMu.Lock()
	cancel := s.cancel
	s.cancelMu.Unlock()
	if cancel != nil {
		cancel()
	}
	s.updateStatus(func(st *SessionStatus) {
		if st.State != SessionFinished && st.State != SessionFailed {
			st.State = SessionStopped
		}
	})
}

// Run drives the replay loop. Returns when the source channel closes,
// the session is stopped, or ctx is cancelled. Should be invoked once
// per session, typically in its own goroutine.
func (s *Session) Run(parent context.Context) error {
	defer s.doneOnce.Do(func() { close(s.done) })
	if s.stopped.Load() {
		return errors.New("session already stopped")
	}

	ctx, cancel := context.WithCancel(parent)
	defer cancel()
	s.cancelMu.Lock()
	s.cancel = cancel
	s.cancelMu.Unlock()

	// Stop() may have raced this assignment — re-check after publishing
	// the cancel func so a Stop that arrived just before sees the
	// updated pointer (but did not invoke ours), short-circuit instead.
	if s.stopped.Load() {
		return nil
	}

	s.updateStatus(func(st *SessionStatus) {
		if st.State == SessionIdle {
			st.State = SessionPlaying
		}
	})

	ticks, errs := s.reader.Stream(ctx, s.rng)

	var anchorEvent uint64
	var anchorWall time.Time

	go func() {
		for err := range errs {
			s.log.Warn("replay reader error", "session", s.id, "err", err)
			publishErrorsTotal.Inc()
			s.updateStatus(func(st *SessionStatus) {
				st.Errors++
				st.Error = err.Error()
			})
		}
	}()

	for {
		// Pause loop. We poll instead of using a condvar so SetSpeed/
		// Stop also wake us up promptly via ctx.
		for s.paused.Load() && !s.stopped.Load() {
			select {
			case <-ctx.Done():
				return nil
			case <-time.After(50 * time.Millisecond):
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
				publishErrorsTotal.Inc()
				s.updateStatus(func(st *SessionStatus) {
					st.Errors++
					st.Error = err.Error()
				})
				continue
			}
			ticksPublishedTotal.Inc()
			s.updateStatus(func(st *SessionStatus) {
				st.Published++
				st.CurrentTs = time.Unix(0, int64(t.TsEvent)).UTC()
			})
		}
	}
}

// Done returns a channel closed when the run loop exits.
func (s *Session) Done() <-chan struct{} { return s.done }

func (s *Session) markFinishedOrStopped() {
	if s.stopped.Load() {
		s.updateStatus(func(st *SessionStatus) {
			st.State = SessionStopped
		})
	} else {
		s.updateStatus(func(st *SessionStatus) {
			st.State = SessionFinished
		})
	}
}

func (s *Session) updateStatus(mut func(*SessionStatus)) {
	s.statusMu.Lock()
	mut(&s.status)
	s.status.UpdatedAt = time.Now().UTC()
	snap := s.status
	s.statusMu.Unlock()

	s.subsMu.Lock()
	for sub := range s.subs {
		select {
		case sub.ch <- snap:
		default:
		}
	}
	s.subsMu.Unlock()
}

func (s *Session) setSpeed(v float64) {
	s.speed.Store(math.Float64bits(v))
}

func (s *Session) getSpeed() float64 {
	return math.Float64frombits(s.speed.Load())
}

// waitFor returns how long to sleep before publishing the next tick at
// the given event time. Identical to Runner's pacing arithmetic.
func waitFor(anchorEvent uint64, anchorWall time.Time, tsEvent uint64, speed float64) time.Duration {
	if speed == 0 {
		return 0
	}
	elapsedEventNs := int64(tsEvent - anchorEvent)
	elapsedWall := time.Duration(float64(elapsedEventNs) / speed)
	due := anchorWall.Add(elapsedWall)
	wait := time.Until(due)
	if wait < 0 {
		return 0
	}
	if wait > 30*time.Second {
		// Cap so a forward-jump in event time doesn't strand the loop
		// for minutes.
		return 30 * time.Second
	}
	return wait
}

// Touch returns the current speed for diagnostic logging.
func (s *Session) Speed() float64 { return s.getSpeed() }

// IsPaused reports the current paused state.
func (s *Session) IsPaused() bool { return s.paused.Load() }
