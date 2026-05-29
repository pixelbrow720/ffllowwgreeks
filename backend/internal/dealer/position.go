package dealer

import (
	"sync"

	"flowgreeks/internal/feed"
)

// Dealer-side prior coefficients applied to prior-day OI when seeding
// dealer position. Per docs/COMPUTE_MODEL.md §4: dealers run net SHORT
// calls (customer demand for upside) and net LONG puts (customer hedge
// demand). Refine empirically post-MVP.
const (
	dealerCallPrior = -0.7 // each contract of call OI → −0.7 dealer position
	dealerPutPrior  = 0.5  // each contract of put OI → +0.5 dealer position
)

// PositionTracker maintains a running net signed dealer-side position
// per (symbol, expiry, strike, side) per docs/COMPUTE_MODEL.md §4.
// SeedFromOI sets the prior; Apply mutates it from classified trades.
//
// Concurrency: writers (SeedFromOI / Apply / PruneExpired) and readers
// (Snapshot / Get) run on different goroutines in cmd/compute (NATS
// callback vs aggregator loop). Guard the map with an RWMutex so the
// aggregator's Snapshot can read concurrently with itself but is fenced
// against writers.
type PositionTracker struct {
	mu  sync.RWMutex
	pos map[strikeKey]int64
}

// NewPositionTracker returns an empty tracker.
func NewPositionTracker() *PositionTracker {
	return &PositionTracker{pos: make(map[strikeKey]int64, 1024)}
}

// SeedFromOI initializes the dealer-position prior from an OI tick. Only
// option ticks of TickTypeOI with a known side are honored; other input
// is silently ignored. Repeated SeedFromOI calls overwrite the prior —
// callers should seed once at session start before any Apply call.
func (p *PositionTracker) SeedFromOI(t feed.Tick) {
	if t.TickType != feed.TickTypeOI || t.AssetClass != feed.AssetClassOption {
		return
	}
	var coeff float64
	switch t.Side {
	case feed.SideCall:
		coeff = dealerCallPrior
	case feed.SidePut:
		coeff = dealerPutPrior
	default:
		return
	}
	k := strikeKey{Symbol: t.Symbol, Side: t.Side, Expiry: t.Expiry, Strike: t.Strike}
	p.mu.Lock()
	p.pos[k] = int64(coeff * float64(t.OpenInterest))
	p.mu.Unlock()
}

// Apply updates the dealer position for a classified trade tick. Customer
// BUY (lifted ask) implies dealer sold, so position decreases by Size;
// customer SELL (hit bid) implies dealer bought, so position increases.
// Unknown aggressor, zero size, non-option, or non-trade ticks are
// no-ops.
func (p *PositionTracker) Apply(t feed.Tick) {
	if t.TickType != feed.TickTypeTrade || t.AssetClass != feed.AssetClassOption {
		return
	}
	if t.Size == 0 {
		return
	}
	k := strikeKey{Symbol: t.Symbol, Side: t.Side, Expiry: t.Expiry, Strike: t.Strike}
	switch t.Aggressor {
	case feed.AggressorBuy:
		p.mu.Lock()
		p.pos[k] -= int64(t.Size)
		p.mu.Unlock()
	case feed.AggressorSell:
		p.mu.Lock()
		p.pos[k] += int64(t.Size)
		p.mu.Unlock()
	default:
		// AggressorUnknown — leave position unchanged.
	}
}

// Get returns the current dealer position for the given strike. Returns
// zero for an unknown strike.
func (p *PositionTracker) Get(symbol feed.Symbol, expiry, strike uint32, side feed.Side) int64 {
	p.mu.RLock()
	v := p.pos[strikeKey{Symbol: symbol, Side: side, Expiry: expiry, Strike: strike}]
	p.mu.RUnlock()
	return v
}

// Snapshot returns a slice of StrikeRow for every strike of the given
// symbol with non-zero state. Greeks/IV/volume fields are left at zero
// — they are filled by other components (greeks pipeline). The returned
// slice is freshly allocated; callers may retain or mutate it.
func (p *PositionTracker) Snapshot(symbol feed.Symbol) []StrikeRow {
	p.mu.RLock()
	out := make([]StrikeRow, 0, len(p.pos))
	for k, v := range p.pos {
		if k.Symbol != symbol {
			continue
		}
		out = append(out, StrikeRow{
			Expiry:    k.Expiry,
			Strike:    k.Strike,
			Side:      k.Side,
			DealerPos: v,
		})
	}
	p.mu.RUnlock()
	return out
}

// PruneExpired removes every dealer-position entry whose expiry is
// strictly before today (YYYYMMDD format, matching feed.Tick.Expiry).
// Returns the number of entries evicted. Caller drives this from a
// session-boundary tick — typically once per day at SOD — so the
// per-strike map doesn't accumulate dead 0DTE contracts on a
// long-running ingest. Safe under the same RWMutex as the rest of the
// tracker.
func (p *PositionTracker) PruneExpired(today uint32) int {
	var n int
	p.mu.Lock()
	for k := range p.pos {
		if k.Expiry < today {
			delete(p.pos, k)
			n++
		}
	}
	p.mu.Unlock()
	return n
}
