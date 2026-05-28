// Package narrative emits human-readable text events for state changes
// in the dealer pipeline. Used to drive the "Live Dealer Narrative"
// panel in the dashboard.
//
// Per docs/COMPUTE_MODEL.md §9 the generator is RULE-BASED, not an LLM —
// templates trigger on state transitions detected by comparing
// successive aggregator snapshots. LLM polish for daily summaries is a
// separate post-revenue feature.
//
// Engine is stateful (one instance per symbol). Caller threads a single
// goroutine in / out so no internal locking is needed.
package narrative

import (
	"fmt"
	"strings"

	"flowgreeks/internal/dealer"
	"flowgreeks/internal/feed"
)

// Tag classifies the narrative event type for filtering / styling in
// the UI. Mirrors the chips on the dashboard mockup.
type Tag string

const (
	TagRegime Tag = "REGIME"
	TagCharm  Tag = "CHARM"
	TagPin    Tag = "PIN"
	TagFlow   Tag = "FLOW"
	TagDPI    Tag = "DPI"
	TagVol    Tag = "VOL"
)

// Event is one emitted narrative item.
type Event struct {
	TsNs   uint64
	Symbol feed.Symbol
	Tag    Tag
	Text   string

	// Refs is a structured payload: numbers/levels referenced in the
	// text. Lets the UI hyperlink {strike} → strike row, etc.
	Refs map[string]any
}

// Snapshot is the subset of dealer state the narrative engine looks at
// each aggregator tick. Caller passes one per second; the engine
// compares against the previously held snapshot to detect transitions.
type Snapshot struct {
	TsNs        uint64
	Spot        float64
	NetGEX      float64
	ZeroGamma   float64
	Regime      dealer.Regime
	DPI         float64
	CharmZone   dealer.CharmZone
	PinActive   bool
	PinTopStrike float64
	PinTopProb  float64

	// Sweep flag (one per call): caller sets when a single sweep print
	// is detected on this tick. Strike + size + side describe it.
	SweepStrike float64
	SweepSize   uint32
	SweepSide   feed.Side
	SweepBuy    bool // aggressor BUY (else SELL)
}

// Engine carries the per-symbol prior snapshot and DPI threshold-cross
// state.
type Engine struct {
	symbol feed.Symbol
	prev   *Snapshot

	// crossed[threshold] = direction (-1 = below, +1 = above) the last
	// time we observed it. Lets us emit "DPI crossed 80 (rising)" only
	// when the cross actually happens, not every tick we're above 80.
	crossed map[int]int8
}

// DPI thresholds we explicitly call out in the narrative.
var dpiThresholds = []int{40, 60, 80}

// NewEngine constructs a narrative engine for one symbol.
func NewEngine(sym feed.Symbol) *Engine {
	return &Engine{
		symbol:  sym,
		crossed: make(map[int]int8, len(dpiThresholds)),
	}
}

// Step compares the new snapshot against the prior one and returns any
// narrative events triggered by the transition. Always non-nil; empty
// when nothing notable changed. Caller is expected to publish + persist
// the events.
func (e *Engine) Step(s Snapshot) []Event {
	out := make([]Event, 0, 4)

	if e.prev == nil {
		// First tick — seed prior state, don't emit boot noise.
		e.prev = &s
		for _, th := range dpiThresholds {
			if s.DPI >= float64(th) {
				e.crossed[th] = +1
			} else {
				e.crossed[th] = -1
			}
		}
		return out
	}

	// Regime flip.
	if s.Regime != e.prev.Regime && s.Regime != dealer.RegimeUnknown {
		out = append(out, Event{
			TsNs: s.TsNs, Symbol: e.symbol, Tag: TagRegime,
			Text: fmt.Sprintf("Regime flipped to %s at %.0f. Forced flow direction: %s.",
				s.Regime, s.ZeroGamma, regimeDirection(s.Regime)),
			Refs: map[string]any{
				"regime":     s.Regime.String(),
				"zero_gamma": s.ZeroGamma,
				"prev":       e.prev.Regime.String(),
			},
		})
	}

	// DPI threshold cross.
	for _, th := range dpiThresholds {
		thF := float64(th)
		switch {
		case s.DPI >= thF && e.prev.DPI < thF && e.crossed[th] != +1:
			out = append(out, Event{
				TsNs: s.TsNs, Symbol: e.symbol, Tag: TagDPI,
				Text: fmt.Sprintf("DPI crossed %d (rising) — pressure %s.",
					th, dpiQualifier(s.DPI)),
				Refs: map[string]any{
					"threshold": th, "direction": "rising", "value": s.DPI,
				},
			})
			e.crossed[th] = +1
		case s.DPI < thF && e.prev.DPI >= thF && e.crossed[th] != -1:
			out = append(out, Event{
				TsNs: s.TsNs, Symbol: e.symbol, Tag: TagDPI,
				Text: fmt.Sprintf("DPI crossed %d (falling) — pressure %s.",
					th, dpiQualifier(s.DPI)),
				Refs: map[string]any{
					"threshold": th, "direction": "falling", "value": s.DPI,
				},
			})
			e.crossed[th] = -1
		}
	}

	// Charm zone enter.
	if s.CharmZone != e.prev.CharmZone && s.CharmZone != dealer.CharmZoneUnknown {
		out = append(out, Event{
			TsNs: s.TsNs, Symbol: e.symbol, Tag: TagCharm,
			Text: fmt.Sprintf("Entered %s window. %s",
				s.CharmZone, charmZoneCommentary(s.CharmZone, s.Regime)),
			Refs: map[string]any{
				"zone": s.CharmZone.String(), "prev": e.prev.CharmZone.String(),
			},
		})
	}

	// Pin candidate first appearance / strike change.
	if s.PinActive && s.PinTopStrike > 0 {
		if !e.prev.PinActive || e.prev.PinTopStrike != s.PinTopStrike {
			out = append(out, Event{
				TsNs: s.TsNs, Symbol: e.symbol, Tag: TagPin,
				Text: fmt.Sprintf("Pin candidate forming at %.0f. Prob %.0f%%.",
					s.PinTopStrike, s.PinTopProb*100),
				Refs: map[string]any{
					"strike": s.PinTopStrike, "probability": s.PinTopProb,
				},
			})
		}
	}

	// Sweep detection (caller-flagged).
	if s.SweepSize > 0 && s.SweepStrike > 0 {
		side := "C"
		if s.SweepSide == feed.SidePut {
			side = "P"
		}
		dirText := "buy"
		if !s.SweepBuy {
			dirText = "sell"
		}
		action := dealerAction(s.SweepBuy, s.SweepSide)
		out = append(out, Event{
			TsNs: s.TsNs, Symbol: e.symbol, Tag: TagFlow,
			Text: fmt.Sprintf("Sweep %s %.0f%s × %s. Dealer must %s.",
				dirText, s.SweepStrike, side, formatSize(s.SweepSize), action),
			Refs: map[string]any{
				"strike": s.SweepStrike, "size": s.SweepSize,
				"side": side, "buy": s.SweepBuy,
			},
		})
	}

	prev := s
	prev.SweepSize = 0
	prev.SweepStrike = 0
	e.prev = &prev
	return out
}

func regimeDirection(r dealer.Regime) string {
	switch r {
	case dealer.RegimeShortGamma:
		return "amplifying (sell rallies, buy dips)"
	case dealer.RegimeLongGamma:
		return "dampening (mean-reverting flow)"
	default:
		return "neutral"
	}
}

func dpiQualifier(d float64) string {
	switch {
	case d >= 80:
		return "HIGH"
	case d >= 60:
		return "elevated"
	case d >= 40:
		return "moderate"
	default:
		return "muted"
	}
}

func charmZoneCommentary(z dealer.CharmZone, r dealer.Regime) string {
	switch z {
	case dealer.CharmZonePeak:
		if r == dealer.RegimeShortGamma {
			return "Trade with momentum — fade walls."
		}
		return "Vol compression — favor mean-reversion."
	case dealer.CharmZonePin:
		return "EOD pin/squeeze regime."
	case dealer.CharmZoneFading:
		return "Charm decay easing off peak."
	case dealer.CharmZoneRising:
		return "Charm decay ramping toward peak window."
	case dealer.CharmZoneWeak:
		return "Warm-up — charm low."
	default:
		return ""
	}
}

func dealerAction(buyAggressor bool, side feed.Side) string {
	// Customer BUY a CALL → dealer sold the call → dealer is short
	// gamma → must hedge by buying spot (long Δ).
	// Customer BUY a PUT  → dealer sold the put  → dealer must hedge
	// by selling spot (negative Δ on a short put).
	customerBoughtCall := buyAggressor && side == feed.SideCall
	customerSoldCall := !buyAggressor && side == feed.SideCall
	customerBoughtPut := buyAggressor && side == feed.SidePut
	customerSoldPut := !buyAggressor && side == feed.SidePut
	switch {
	case customerBoughtCall:
		return "BUY spot to hedge long Δ"
	case customerSoldCall:
		return "SELL spot to unwind hedge"
	case customerBoughtPut:
		return "SELL spot to hedge short Δ"
	case customerSoldPut:
		return "BUY spot to unwind hedge"
	}
	return "rehedge"
}

func formatSize(n uint32) string {
	switch {
	case n >= 1000:
		return fmt.Sprintf("%.1fk", float64(n)/1000)
	default:
		return fmt.Sprintf("%d", n)
	}
}

// Helper used by tests.
func (e Event) String() string {
	return strings.Join([]string{string(e.Tag), e.Text}, " ")
}
