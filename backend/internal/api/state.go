// Package api implements the FlowGreeks REST + WebSocket frontend.
//
// State flow:
//
//	NATS state.<sym>.gex (compute publisher)
//	  → Cache.Update (latest snapshot per symbol+kind)
//	  → Broker.Publish (fan-out to subscribed WS connections)
//
// REST handlers read from Cache. WS handlers register a Subscriber on
// the Broker for the streams they care about.
package api

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"

	"flowgreeks/internal/feed"

	"github.com/nats-io/nats.go"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// stateHeadParseErrors counts NATS state/narrative payloads whose ts_ns
// header could not be parsed. Previously swallowed silently — a
// corrupted compute publish would strip ts_ns from broker fanout with
// no operator signal. Bounded cardinality (one label).
var stateHeadParseErrors = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "flowgreeks_state_head_parse_errors_total",
	Help: "NATS state/narrative payloads with malformed ts_ns header.",
}, []string{"subject"})

// StateKind identifies a stream emitted by the compute service.
type StateKind string

const (
	StateKindGEX       StateKind = "gex"
	StateKindNarrative StateKind = "narrative"
	StateKindAlert     StateKind = "alert"
)

// Snapshot is a raw JSON snapshot from the compute service. We keep it as
// a json.RawMessage so the frontend deals with the shape directly without
// us re-marshalling on every read.
type Snapshot struct {
	Symbol feed.Symbol
	Kind   StateKind
	Data   json.RawMessage
	TsNs   uint64
}

// Cache holds the latest snapshot per (symbol, kind). Reads via Get are
// lock-free relative to writes (RWMutex). Writes happen at compute's
// publish rate (~1 Hz per symbol).
type Cache struct {
	mu      sync.RWMutex
	entries map[string]Snapshot
}

func NewCache() *Cache {
	return &Cache{entries: make(map[string]Snapshot, 8)}
}

func (c *Cache) Update(s Snapshot) {
	c.mu.Lock()
	c.entries[cacheKey(s.Symbol, s.Kind)] = s
	c.mu.Unlock()
}

func (c *Cache) Get(sym feed.Symbol, kind StateKind) (Snapshot, bool) {
	c.mu.RLock()
	s, ok := c.entries[cacheKey(sym, kind)]
	c.mu.RUnlock()
	return s, ok
}

// SnapshotsFor returns the cached snapshots that match a SubFilter, in
// no particular order. Used to seed a freshly-subscribed WS client with
// last-known state so they don't have to wait for the next compute
// publish (typically up to 1s gap).
func (c *Cache) SnapshotsFor(filter SubFilter) []Snapshot {
	c.mu.RLock()
	out := make([]Snapshot, 0, len(c.entries))
	for _, s := range c.entries {
		if !matchesFilter(filter, s.Symbol, s.Kind) {
			continue
		}
		out = append(out, s)
	}
	c.mu.RUnlock()
	return out
}

// matchesFilter is the canonical SubFilter predicate, shared by
// Cache.SnapshotsFor and Subscriber.matches so seeding and live fan-out
// agree on what counts as "in scope".
func matchesFilter(f SubFilter, sym feed.Symbol, kind StateKind) bool {
	if len(f.Symbols) > 0 {
		if _, ok := f.Symbols[sym]; !ok {
			return false
		}
	}
	if len(f.Kinds) > 0 {
		if _, ok := f.Kinds[kind]; !ok {
			return false
		}
	}
	return true
}

func cacheKey(sym feed.Symbol, kind StateKind) string {
	return fmt.Sprintf("%d:%s", sym, kind)
}

// Broker fans out Snapshot events to subscribed WS connections.
//
// Concurrency contract: Publish is called from the NATS subscriber
// goroutine; Subscribe / Unsubscribe from HTTP handler goroutines.
// Each Subscriber owns its own bounded channel and a "drop on full"
// policy so a slow client cannot block the publisher.
type Broker struct {
	mu          sync.RWMutex
	subscribers map[*Subscriber]struct{}
}

func NewBroker() *Broker {
	b := &Broker{subscribers: make(map[*Subscriber]struct{}, 64)}
	wsSubscribers.Set(0)
	return b
}

// Publish fans out the Snapshot to every Subscriber whose filter matches.
// Drops to slow subscribers are surfaced both on the Subscriber's Dropped
// counter (per-connection visibility) and the package-level
// flowgreeks_ws_drops_total counter (aggregate health).
func (b *Broker) Publish(s Snapshot) {
	wsPublished.WithLabelValues(symLabel(s.Symbol), string(s.Kind)).Inc()
	b.mu.RLock()
	defer b.mu.RUnlock()
	for sub := range b.subscribers {
		if !sub.matches(s) {
			continue
		}
		select {
		case sub.ch <- s:
		default:
			sub.dropped.Add(1)
			wsDrops.WithLabelValues(symLabel(s.Symbol), string(s.Kind)).Inc()
		}
	}
}

// Subscribe registers a new Subscriber and returns it. Caller must call
// Unsubscribe when done.
func (b *Broker) Subscribe(buf int, filter SubFilter) *Subscriber {
	if buf <= 0 {
		buf = 64
	}
	sub := &Subscriber{
		ch:     make(chan Snapshot, buf),
		filter: filter,
	}
	b.mu.Lock()
	b.subscribers[sub] = struct{}{}
	count := len(b.subscribers)
	b.mu.Unlock()
	wsSubscribers.Set(float64(count))
	return sub
}

func (b *Broker) Unsubscribe(sub *Subscriber) {
	b.mu.Lock()
	delete(b.subscribers, sub)
	count := len(b.subscribers)
	b.mu.Unlock()
	wsSubscribers.Set(float64(count))
	close(sub.ch)
}

// SubFilter narrows what a subscriber receives. Empty Symbols means "any
// symbol"; empty Kinds means "any kind".
type SubFilter struct {
	Symbols map[feed.Symbol]struct{}
	Kinds   map[StateKind]struct{}
}

// Subscriber is one WS connection's view onto the broker.
//
// filter is mutated by the WS read loop on subscribe/unsubscribe while
// Broker.Publish reads it from the publisher goroutine. The filterMu
// guards both ends; the lock is fine-grained enough that a slow
// publisher only blocks filter mutations for the matches() probe, not
// for the channel send.
//
// dropped is atomic so Dropped() callers (metrics, /admin) don't have
// to synchronize with the publisher.
type Subscriber struct {
	ch       chan Snapshot
	filterMu sync.RWMutex
	filter   SubFilter
	dropped  atomic.Uint64
}

func (s *Subscriber) Events() <-chan Snapshot { return s.ch }

func (s *Subscriber) Dropped() uint64 { return s.dropped.Load() }

func (s *Subscriber) matches(snap Snapshot) bool {
	s.filterMu.RLock()
	defer s.filterMu.RUnlock()
	return matchesFilter(s.filter, snap.Symbol, snap.Kind)
}

// snapshotFilter copies the filter under lock so the caller can iterate
// it (e.g. seeding from Cache) without holding the lock across slow ops.
func (s *Subscriber) snapshotFilter() SubFilter {
	s.filterMu.RLock()
	defer s.filterMu.RUnlock()
	out := SubFilter{}
	if len(s.filter.Symbols) > 0 {
		out.Symbols = make(map[feed.Symbol]struct{}, len(s.filter.Symbols))
		for k := range s.filter.Symbols {
			out.Symbols[k] = struct{}{}
		}
	}
	if len(s.filter.Kinds) > 0 {
		out.Kinds = make(map[StateKind]struct{}, len(s.filter.Kinds))
		for k := range s.filter.Kinds {
			out.Kinds[k] = struct{}{}
		}
	}
	return out
}

// SubscribeNATS wires the cache + broker to the NATS state + narrative
// streams. Runs until ctx is cancelled. Caller is expected to invoke this
// once at api startup.
//
// Subjects subscribed:
//
//	state.>        — full per-second snapshots from compute (cached + fanned out)
//	narrative.>    — narrative events from compute (fanned out, NOT cached
//	                 because they're append events, not snapshots)
func SubscribeNATS(ctx context.Context, nc *nats.Conn, cache *Cache, broker *Broker) error {
	stateSub, err := nc.Subscribe("state.>", func(m *nats.Msg) {
		sym, kind, ok := parseStateSubject(m.Subject)
		if !ok {
			return
		}
		// Peek ts_ns for staleness reporting; meter parse failures so a
		// corrupted compute publish doesn't silently strip the timestamp.
		var head struct {
			TsNs uint64 `json:"ts_ns"`
		}
		if err := json.Unmarshal(m.Data, &head); err != nil {
			stateHeadParseErrors.WithLabelValues("state").Inc()
		}

		snap := Snapshot{
			Symbol: sym,
			Kind:   kind,
			Data:   append(json.RawMessage(nil), m.Data...), // own the bytes
			TsNs:   head.TsNs,
		}
		cache.Update(snap)
		broker.Publish(snap)
	})
	if err != nil {
		return fmt.Errorf("nats subscribe state.>: %w", err)
	}

	narrSub, err := nc.Subscribe("narrative.>", func(m *nats.Msg) {
		sym, ok := parseNarrativeSubject(m.Subject)
		if !ok {
			return
		}
		var head struct {
			TsNs uint64 `json:"ts_ns"`
		}
		if err := json.Unmarshal(m.Data, &head); err != nil {
			stateHeadParseErrors.WithLabelValues("narrative").Inc()
		}
		// Narrative events are time-series, not snapshots — don't cache.
		broker.Publish(Snapshot{
			Symbol: sym,
			Kind:   StateKindNarrative,
			Data:   append(json.RawMessage(nil), m.Data...),
			TsNs:   head.TsNs,
		})
	})
	if err != nil {
		return fmt.Errorf("nats subscribe narrative.>: %w", err)
	}

	go func() {
		<-ctx.Done()
		_ = stateSub.Drain()
		_ = narrSub.Drain()
	}()
	return nil
}

// parseNarrativeSubject parses `narrative.<sym>` into our typed pair.
func parseNarrativeSubject(subj string) (feed.Symbol, bool) {
	parts := strings.Split(subj, ".")
	if len(parts) != 2 || parts[0] != "narrative" {
		return 0, false
	}
	sym := feed.ParseSymbol(strings.ToUpper(parts[1]))
	if sym == feed.SymbolUnknown {
		return 0, false
	}
	return sym, true
}

// parseStateSubject parses `state.<sym>.<kind>` into our typed pair.
func parseStateSubject(subj string) (feed.Symbol, StateKind, bool) {
	parts := strings.Split(subj, ".")
	if len(parts) != 3 || parts[0] != "state" {
		return 0, "", false
	}
	sym := feed.ParseSymbol(strings.ToUpper(parts[1]))
	if sym == feed.SymbolUnknown {
		return 0, "", false
	}
	return sym, StateKind(parts[2]), true
}
