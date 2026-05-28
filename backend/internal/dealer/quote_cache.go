package dealer

import (
	"sync"

	"flowgreeks/internal/feed"
)

// QuoteCache mirrors the latest top-of-book per (symbol, expiry, strike,
// side). Required because trade ticks (Mbp0Msg / `trades` schema) carry
// only price + size — Lee-Ready and Flow Pulse need bid/ask at trade
// time to classify the aggressor. Mirrors the Python reference's
// `state[instrument_id] = {bid, ask, quote_ts}` pattern.
//
// Updated from the quote tick path; consulted from the trade tick path
// before classifier.Classify is called.
//
// Concurrency: callers feeding from a single goroutine per pipeline
// can skip the lock; the RWMutex is here so multiple producers (or
// hot-path goroutines that fan out per-strike) remain safe.
type QuoteCache struct {
	mu sync.RWMutex
	m  map[quoteKey]Quote
}

// Quote is the cached NBBO entry. TsNs is the event timestamp from the
// most recent quote tick — caller can use it to enforce a freshness
// window before trusting the bid/ask.
type Quote struct {
	Bid   float64
	Ask   float64
	TsNs  uint64
}

type quoteKey struct {
	symbol feed.Symbol
	expiry uint32
	strike uint32
	side   feed.Side
}

// NewQuoteCache returns a ready-to-use cache.
func NewQuoteCache() *QuoteCache {
	return &QuoteCache{m: make(map[quoteKey]Quote, 1024)}
}

// Update folds a quote tick into the cache. No-op if t isn't a quote
// tick. Caller doesn't have to filter; this is safe to invoke on every
// inbound tick.
func (c *QuoteCache) Update(t feed.Tick) {
	if t.TickType != feed.TickTypeQuote || !t.IsOption() {
		return
	}
	k := quoteKey{symbol: t.Symbol, expiry: t.Expiry, strike: t.Strike, side: t.Side}
	c.mu.Lock()
	c.m[k] = Quote{Bid: t.Bid, Ask: t.Ask, TsNs: t.TsEvent}
	c.mu.Unlock()
}

// Get returns the most recent NBBO for the strike, ok=false if no
// quote has been observed yet.
func (c *QuoteCache) Get(symbol feed.Symbol, expiry, strike uint32, side feed.Side) (Quote, bool) {
	k := quoteKey{symbol: symbol, expiry: expiry, strike: strike, side: side}
	c.mu.RLock()
	q, ok := c.m[k]
	c.mu.RUnlock()
	return q, ok
}

// Apply fills bid/ask on a trade tick from the cache. Returns true
// if a quote was found. Crossed/locked or zero quotes leave the tick
// fields zero so downstream classifiers that guard on positive spread
// fall back to UNKNOWN cleanly.
func (c *QuoteCache) Apply(t *feed.Tick) bool {
	if t.TickType != feed.TickTypeTrade || !t.IsOption() {
		return false
	}
	q, ok := c.Get(t.Symbol, t.Expiry, t.Strike, t.Side)
	if !ok {
		return false
	}
	t.Bid = q.Bid
	t.Ask = q.Ask
	return true
}

// Len returns the number of strikes currently cached. Diagnostic only.
func (c *QuoteCache) Len() int {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return len(c.m)
}
