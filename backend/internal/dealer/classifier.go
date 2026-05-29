package dealer

import (
	"flowgreeks/internal/feed"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// classifierUnknown counts Lee-Ready fallthroughs by reason so operators
// can spot a degraded classifier (e.g. a flood of crossed quotes from a
// venue glitch, or a strike that's never traded so tick-test has no
// anchor). Cardinality is bounded — three reasons by construction.
var classifierUnknown = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "flowgreeks_classifier_aggressor_unknown_total",
	Help: "Lee-Ready trades that returned AggressorUnknown, by reason.",
}, []string{"reason"})

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
		classifierUnknown.WithLabelValues("crossed_or_missing_quote").Inc()
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
			classifierUnknown.WithLabelValues("no_prior_trade").Inc()
			ag = feed.AggressorUnknown
		case last < t.Price:
			ag = feed.AggressorBuy
		case last > t.Price:
			ag = feed.AggressorSell
		default:
			classifierUnknown.WithLabelValues("equal_to_prior").Inc()
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

// PruneExpired removes every last-trade-price entry whose expiry is
// strictly before today (YYYYMMDD format). Returns the number of
// entries evicted across both generations. Caller drives this from a
// session-boundary tick so the maps don't accumulate dead 0DTE
// contracts on a long-running ingest. Single-threaded by the same
// invariant as Classify.
func (c *Classifier) PruneExpired(today uint32) int {
	var n int
	for k := range c.curr {
		if k.Expiry < today {
			delete(c.curr, k)
			n++
		}
	}
	if c.prev != nil {
		for k := range c.prev {
			if k.Expiry < today {
				delete(c.prev, k)
				n++
			}
		}
	}
	return n
}
