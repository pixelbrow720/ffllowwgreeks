package dealer

import (
	"flowgreeks/internal/feed"
)

// maxClassifierStrikes caps the per-strike last-trade-price map. Once the
// cap is reached we reset the map; expired contracts age out at day
// rollover anyway and the reset is cheap relative to the steady-state
// hot-path cost.
const maxClassifierStrikes = 10000

// strikeKey identifies a (symbol, expiry, strike, side) tuple. Used by
// both Classifier and PositionTracker. Fixed-size, no pointers — cheap to
// hash.
type strikeKey struct {
	Symbol feed.Symbol
	Side   feed.Side
	Expiry uint32
	Strike uint32
}

// Classifier implements Lee-Ready trade aggressor classification per
// docs/COMPUTE_MODEL.md §3.
//
// State: two-generation last-trade-price map. When the active map hits
// maxClassifierStrikes we rotate (curr → prev, fresh curr), so a hot
// strike's price is still recoverable until the next rotation. The
// previous design wiped ALL history on reset, which made every other
// strike's tick-test fallback return UNKNOWN until it traded again — on
// a busy SPX day with thousands of strikes that regressed classification
// quality the moment we crossed the cap.
//
// Concurrency: single-threaded by design. The compute service drives the
// classifier from one event loop. If that invariant changes, wrap calls
// in a mutex at the call site rather than paying the cost on every Tick.
type Classifier struct {
	curr map[strikeKey]float64
	prev map[strikeKey]float64
}

// NewClassifier returns an empty Classifier.
func NewClassifier() *Classifier {
	return &Classifier{curr: make(map[strikeKey]float64, 1024)}
}

// Classify returns the aggressor side for a trade tick following the
// Lee-Ready rules. Non-trade ticks, non-option ticks, and ticks with
// missing or crossed quotes return AggressorUnknown without touching
// state. State is updated with the trade price after classification so
// subsequent mid-trade ticks can use the tick-test fallback.
func (c *Classifier) Classify(t feed.Tick) feed.Aggressor {
	if t.TickType != feed.TickTypeTrade || t.AssetClass != feed.AssetClassOption {
		return feed.AggressorUnknown
	}
	if t.Bid <= 0 || t.Ask <= 0 || t.Ask < t.Bid {
		return feed.AggressorUnknown
	}

	k := strikeKey{Symbol: t.Symbol, Side: t.Side, Expiry: t.Expiry, Strike: t.Strike}
	mid := (t.Bid + t.Ask) * 0.5

	var ag feed.Aggressor
	switch {
	case t.Price >= t.Ask:
		ag = feed.AggressorBuy
	case t.Price <= t.Bid:
		ag = feed.AggressorSell
	case t.Price > mid:
		ag = feed.AggressorBuy
	case t.Price < mid:
		ag = feed.AggressorSell
	default:
		last, ok := c.lookupLast(k)
		switch {
		case !ok:
			ag = feed.AggressorUnknown
		case last < t.Price:
			ag = feed.AggressorBuy
		case last > t.Price:
			ag = feed.AggressorSell
		default:
			ag = feed.AggressorUnknown
		}
	}

	if len(c.curr) >= maxClassifierStrikes {
		c.prev = c.curr
		c.curr = make(map[strikeKey]float64, maxClassifierStrikes)
	}
	c.curr[k] = t.Price
	return ag
}

// lookupLast checks the active generation first, then the previous
// generation. The two-generation design preserves the most recent
// trade price for any strike that traded in the last 1-2 cap rotations.
func (c *Classifier) lookupLast(k strikeKey) (float64, bool) {
	if v, ok := c.curr[k]; ok {
		return v, true
	}
	if c.prev != nil {
		if v, ok := c.prev[k]; ok {
			return v, true
		}
	}
	return 0, false
}
