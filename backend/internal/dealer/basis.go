// Live spot/futures basis tracker. Implements COMPUTE_MODEL.md §11.
//
// Subscribes to MDP3 front-month ES/NQ ticks. Maintains EWMA-smoothed basis
// (futures_front_mid - spot) per symbol and surfaces a snapshot view that
// downstream code copies into AggregateState.BasisFront. During the rollover
// window (front <8d from expiry) the back-month contract is also tracked.
package dealer

import (
	"bytes"
	"math"
	"sync"
	"time"

	"flowgreeks/internal/feed"
)

// DefaultBasisAlpha is the EWMA smoothing factor recommended by §11.2.
const DefaultBasisAlpha = 0.1

// rolloverThresholdDays defines the front-month rollover window in days.
const rolloverThresholdDays = 8.0

// BasisSnapshot is the immutable per-symbol view returned by Snapshot.
// Fields with zero values indicate "not yet observed" (e.g. FutBackSym == ""
// outside the rollover window).
type BasisSnapshot struct {
	Symbol      feed.Symbol
	Spot        float64
	FutFrontSym string
	FutFrontMid float64
	Basis       float64 // raw: front - spot
	BasisSmooth float64 // EWMA(α)
	FutBackSym  string  // populated only during rollover
	FutBackMid  float64
	BasisBack   float64
	InRollover  bool
	TsUpdated   uint64 // last tick ns
}

// contractState is the per-contract slot held inside a symbolState.
type contractState struct {
	sym       string
	sig       [12]byte // raw FuturesContract bytes for fast equality check
	year      int
	month     int
	expiry    time.Time
	mid       float64
	tsUpdated uint64
}

// symbolState holds the front/back contract slots and basis EWMA for one
// underlying symbol.
type symbolState struct {
	spot        float64
	front       *contractState
	back        *contractState
	basisSmooth float64
	basisInited bool
	tsUpdated   uint64
}

// BasisTracker maintains the live basis estimate for every subscribed
// symbol. Safe for concurrent use.
type BasisTracker struct {
	mu     sync.RWMutex
	alpha  float64
	states map[feed.Symbol]*symbolState
	nowFn  func() time.Time // overridable for testing
}

// NewBasisTracker constructs a tracker with the given EWMA smoothing factor.
// alpha must satisfy 0 < alpha <= 1.
func NewBasisTracker(alpha float64) *BasisTracker {
	if !(alpha > 0 && alpha <= 1) || math.IsNaN(alpha) {
		panic("dealer: BasisTracker alpha must be in (0, 1]")
	}
	return &BasisTracker{
		alpha:  alpha,
		states: make(map[feed.Symbol]*symbolState),
		nowFn:  time.Now,
	}
}

// UpdateSpot records the latest spot index level for the given symbol.
// Non-positive, NaN, or Inf values are ignored.
func (b *BasisTracker) UpdateSpot(symbol feed.Symbol, spot float64) {
	if !(spot > 0) || math.IsInf(spot, 0) {
		return
	}
	b.mu.Lock()
	s := b.getOrCreateLocked(symbol)
	s.spot = spot
	b.mu.Unlock()
}

// UpdateFuture consumes a futures tick (caller must ensure t.IsFuture()).
// The tracker classifies the contract as front or back month based on
// expiry, computes mid, and updates the EWMA basis when both spot and the
// front contract mid are present.
func (b *BasisTracker) UpdateFuture(t feed.Tick) {
	if !t.IsFuture() {
		return
	}
	mid := tickMid(t)
	if !(mid > 0) {
		return
	}

	b.mu.Lock()
	s := b.getOrCreateLocked(t.Symbol)

	// Hot path: contract matches the cached front signature.
	if s.front != nil && s.front.sig == t.FuturesContract {
		s.front.mid = mid
		s.front.tsUpdated = t.TsEvent
		s.tsUpdated = t.TsEvent
		if s.spot > 0 {
			raw := mid - s.spot
			if !s.basisInited {
				s.basisSmooth = raw
				s.basisInited = true
			} else {
				s.basisSmooth = b.alpha*raw + (1-b.alpha)*s.basisSmooth
			}
		}
		b.mu.Unlock()
		return
	}

	// Back-month update.
	if s.back != nil && s.back.sig == t.FuturesContract {
		s.back.mid = mid
		s.back.tsUpdated = t.TsEvent
		s.tsUpdated = t.TsEvent
		b.mu.Unlock()
		return
	}

	// Slow path: brand-new contract. Parse, route, install.
	sym := contractSym(t.FuturesContract[:])
	if sym == "" {
		b.mu.Unlock()
		return
	}
	year, month, ok := parseFuturesContract(sym)
	if !ok {
		b.mu.Unlock()
		return
	}
	expiry := thirdFriday(year, month)
	cs := s.installContract(sym, t.FuturesContract, year, month, expiry)
	if cs == nil {
		b.mu.Unlock()
		return
	}
	cs.mid = mid
	cs.tsUpdated = t.TsEvent
	s.tsUpdated = t.TsEvent
	if cs == s.front && s.spot > 0 {
		raw := mid - s.spot
		if !s.basisInited {
			s.basisSmooth = raw
			s.basisInited = true
		} else {
			s.basisSmooth = b.alpha*raw + (1-b.alpha)*s.basisSmooth
		}
	}
	b.mu.Unlock()
}

// Snapshot returns an immutable view of the tracker state for symbol.
func (b *BasisTracker) Snapshot(symbol feed.Symbol) BasisSnapshot {
	b.mu.RLock()
	defer b.mu.RUnlock()

	snap := BasisSnapshot{Symbol: symbol}
	s, ok := b.states[symbol]
	if !ok {
		return snap
	}
	snap.Spot = s.spot
	snap.TsUpdated = s.tsUpdated

	if s.front != nil {
		snap.FutFrontSym = s.front.sym
		snap.FutFrontMid = s.front.mid
		if s.spot > 0 && s.front.mid > 0 {
			snap.Basis = s.front.mid - s.spot
		}
		days := s.front.expiry.Sub(b.nowFn()).Hours() / 24
		snap.InRollover = days < rolloverThresholdDays
	}
	if s.basisInited {
		snap.BasisSmooth = s.basisSmooth
	}
	if s.back != nil {
		snap.FutBackSym = s.back.sym
		snap.FutBackMid = s.back.mid
		if s.spot > 0 && s.back.mid > 0 {
			snap.BasisBack = s.back.mid - s.spot
		}
	}
	return snap
}

// getOrCreateLocked returns the symbolState for sym, creating it if absent.
// Caller must hold b.mu in write mode.
func (b *BasisTracker) getOrCreateLocked(sym feed.Symbol) *symbolState {
	s, ok := b.states[sym]
	if !ok {
		s = &symbolState{}
		b.states[sym] = s
	}
	return s
}

// installContract routes a brand-new contract into the front or back slot.
// Maintains the invariant that front.expiry <= back.expiry. Returns the slot
// the contract was placed into, or nil if the contract is further out than
// both existing slots (in which case it is dropped).
func (s *symbolState) installContract(sym string, sig [12]byte, year, month int, expiry time.Time) *contractState {
	cs := &contractState{
		sym:    sym,
		sig:    sig,
		year:   year,
		month:  month,
		expiry: expiry,
	}
	if s.front == nil {
		s.front = cs
		return cs
	}
	if expiry.Before(s.front.expiry) {
		// New contract expires sooner — promote it to front, demote old front.
		s.back = s.front
		s.front = cs
		return cs
	}
	if s.back == nil || expiry.Before(s.back.expiry) {
		s.back = cs
		return cs
	}
	return nil
}

// tickMid extracts a mid price from a futures tick. Returns 0 when the tick
// carries no usable price (one-sided quote, missing trade price, etc.).
func tickMid(t feed.Tick) float64 {
	switch t.TickType {
	case feed.TickTypeQuote:
		if t.Bid > 0 && t.Ask > 0 {
			return (t.Bid + t.Ask) / 2
		}
	case feed.TickTypeTrade:
		if t.Price > 0 {
			return t.Price
		}
	}
	return 0
}

// contractSym trims trailing NULs from a 12-byte FuturesContract field and
// returns the printable contract symbol (e.g. "ESM6").
func contractSym(b []byte) string {
	n := bytes.IndexByte(b, 0)
	if n < 0 {
		n = len(b)
	}
	return string(b[:n])
}

// monthCodes maps CME month-letter codes to calendar months.
var monthCodes = [256]int8{
	'F': 1, 'G': 2, 'H': 3, 'J': 4, 'K': 5, 'M': 6,
	'N': 7, 'Q': 8, 'U': 9, 'V': 10, 'X': 11, 'Z': 12,
}

// parseFuturesContract decodes a CME-style symbol of the form RR<M><Y> where
// RR is a 2-letter root (ES, NQ), M is a single month-code letter, and Y is
// a single year digit. The single-digit year is disambiguated against
// time.Now().Year(): the smallest year >= the current calendar year whose
// last digit matches Y is chosen.
//
// Examples (current year = 2026):
//
//	"ESM6" -> 2026, 6
//	"ESU6" -> 2026, 9
//	"NQH7" -> 2027, 3
func parseFuturesContract(sym string) (year int, month int, ok bool) {
	if len(sym) != 4 {
		return 0, 0, false
	}
	mc := sym[2]
	yd := sym[3]
	if yd < '0' || yd > '9' {
		return 0, 0, false
	}
	m := int(monthCodes[mc])
	if m == 0 {
		return 0, 0, false
	}
	digit := int(yd - '0')
	now := time.Now().Year()
	decade := now - (now % 10)
	y := decade + digit
	if y < now {
		y += 10
	}
	return y, m, true
}

// thirdFriday returns the third Friday of the given month at 00:00 UTC. CME
// equity index futures (ES, NQ) settle on this date.
func thirdFriday(year, month int) time.Time {
	first := time.Date(year, time.Month(month), 1, 0, 0, 0, 0, time.UTC)
	weekday := int(first.Weekday()) // Sun=0, Mon=1, ... Fri=5, Sat=6
	offset := (5 - weekday + 7) % 7
	day := 1 + offset + 14
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
}
