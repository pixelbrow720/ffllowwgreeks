package replay

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"time"
)

// Manager owns the set of active replay sessions for an api binary
// instance. It enforces a soft cap on concurrent sessions and reaps
// finished ones after a grace period so /ws/replay/{id} can still fetch
// the final status briefly after the session ends.
type Manager struct {
	reader   *Reader
	pub      Publisher
	log      *slog.Logger
	maxOpen  int
	reapAfter time.Duration

	mu       sync.Mutex
	sessions map[string]*Session
}

// ManagerOpts tunes the manager.
type ManagerOpts struct {
	MaxOpen   int           // soft cap on concurrent sessions; default 32
	ReapAfter time.Duration // delay before deleting finished sessions; default 5m
}

// NewManager constructs a manager.
func NewManager(reader *Reader, pub Publisher, log *slog.Logger, opts ManagerOpts) *Manager {
	if opts.MaxOpen <= 0 {
		opts.MaxOpen = 32
	}
	if opts.ReapAfter <= 0 {
		opts.ReapAfter = 5 * time.Minute
	}
	return &Manager{
		reader:    reader,
		pub:       pub,
		log:       log,
		maxOpen:   opts.MaxOpen,
		reapAfter: opts.ReapAfter,
		sessions:  make(map[string]*Session, opts.MaxOpen),
	}
}

// ErrSessionExists is returned when Create is called with an id that's
// still active.
var ErrSessionExists = errors.New("replay: session id already exists")

// ErrSessionLimit is returned when the active-session cap is reached.
var ErrSessionLimit = errors.New("replay: session limit reached")

// Create starts a new session under the given id. The Run loop is
// launched in a background goroutine; caller can subscribe to status
// via the returned Session.
func (m *Manager) Create(ctx context.Context, id string, rng Range, opts SessionOptions) (*Session, error) {
	m.mu.Lock()
	if _, ok := m.sessions[id]; ok {
		m.mu.Unlock()
		sessionsRejected.WithLabelValues("exists").Inc()
		return nil, ErrSessionExists
	}
	if len(m.sessions) >= m.maxOpen {
		m.mu.Unlock()
		sessionsRejected.WithLabelValues("limit").Inc()
		return nil, ErrSessionLimit
	}
	sess := NewSession(id, rng, m.reader, m.pub, m.log.With("session", id), opts)
	m.sessions[id] = sess
	count := len(m.sessions)
	m.mu.Unlock()
	sessionsCreated.Inc()
	sessionsActive.Set(float64(count))

	go func() {
		if err := sess.Run(ctx); err != nil {
			m.log.Warn("session run exited with error", "session", id, "err", err)
		}
		st := sess.Status()
		sessionsFinishedByState.WithLabelValues(string(st.State)).Inc()
		// Schedule reap after grace period. Status() still works during
		// the window so a late /status fetch returns the final outcome.
		t := time.NewTimer(m.reapAfter)
		defer t.Stop()
		select {
		case <-t.C:
		case <-ctx.Done():
		}
		m.mu.Lock()
		// Only delete if it's still our entry — defensive against
		// id reuse if reaping races a Create call.
		if existing, ok := m.sessions[id]; ok && existing == sess {
			delete(m.sessions, id)
		}
		count := len(m.sessions)
		m.mu.Unlock()
		sessionsActive.Set(float64(count))
	}()
	return sess, nil
}

// Get returns the session by id, or nil if not present.
func (m *Manager) Get(id string) *Session {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.sessions[id]
}

// Stop ends a session and removes it from the active set. Returns false
// if the session wasn't found.
func (m *Manager) Stop(id string) bool {
	sess := m.Get(id)
	if sess == nil {
		return false
	}
	sess.Stop()
	return true
}

// Active returns a snapshot of the current session ids. Useful for
// diagnostic /admin endpoints.
func (m *Manager) Active() []string {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]string, 0, len(m.sessions))
	for id := range m.sessions {
		out = append(out, id)
	}
	return out
}
