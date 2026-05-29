// Package bus defines the NATS subject hierarchy and helpers used to route
// events through the FlowGreeks internal message bus.
//
// Subjects are typed strings — keep them centralized so cross-service
// changes are mechanical (rename in one place).
package bus

import (
	"fmt"
	"strings"

	"flowgreeks/internal/feed"
)

// Stream names (NATS JetStream).
const (
	StreamTicks = "TICKS" // raw normalized feed events
	StreamState = "STATE" // computed state deltas (memory storage, short retention)
	StreamFlow  = "FLOW"  // flow tape items + narrative (file storage, 7d)
)

// ─── Subject builders ─────────────────────────────────────────────────────

// SubjectTicks returns the wildcard for all tick events of a symbol.
//
//	ticks.spx.>
//	ticks.ndx.>
func SubjectTicks(sym feed.Symbol) string {
	return fmt.Sprintf("ticks.%s.>", strings.ToLower(sym.String()))
}

// SubjectTickQuote returns the subject for a specific quote event.
//
//	ticks.spx.quote.20260620.5810000.C
func SubjectTickQuote(sym feed.Symbol, expiry, strike uint32, side feed.Side) string {
	return fmt.Sprintf("ticks.%s.quote.%d.%d.%s",
		strings.ToLower(sym.String()), expiry, strike, side.String())
}

// SubjectTickTrade returns the subject for a specific trade event.
//
//	ticks.spx.trade.20260620.5810000.C
func SubjectTickTrade(sym feed.Symbol, expiry, strike uint32, side feed.Side) string {
	return fmt.Sprintf("ticks.%s.trade.%d.%d.%s",
		strings.ToLower(sym.String()), expiry, strike, side.String())
}

// SubjectTickOI returns the subject for an open-interest update event.
//
//	ticks.spx.oi.20260620.5810000.C
func SubjectTickOI(sym feed.Symbol, expiry, strike uint32, side feed.Side) string {
	return fmt.Sprintf("ticks.%s.oi.%d.%d.%s",
		strings.ToLower(sym.String()), expiry, strike, side.String())
}

// SubjectFutureTick returns the subject for a futures tick (basis tracking).
//
//	ticks.spx.future.ESM6
//	ticks.ndx.future.NQM6
func SubjectFutureTick(sym feed.Symbol, contract string) string {
	return fmt.Sprintf("ticks.%s.future.%s",
		strings.ToLower(sym.String()), contract)
}

// SubjectState returns the subject for a computed state stream.
//
//	state.spx.dpi
//	state.spx.gex
//	state.spx.charm
//	state.spx.flow_pulse
//	state.spx.basis
func SubjectState(sym feed.Symbol, kind StateKind) string {
	return fmt.Sprintf("state.%s.%s",
		strings.ToLower(sym.String()), kind)
}

// SubjectNarrative returns the subject for AI narrative events.
//
//	narrative.spx
func SubjectNarrative(sym feed.Symbol) string {
	return fmt.Sprintf("narrative.%s", strings.ToLower(sym.String()))
}

// StateKind identifies a kind of computed state stream.
type StateKind string

const (
	StateKindDPI       StateKind = "dpi"
	StateKindGEX       StateKind = "gex"
	StateKindCharm     StateKind = "charm"
	StateKindVanna     StateKind = "vanna"
	StateKindFlow      StateKind = "flow"        // flow tape items
	StateKindFlowPulse StateKind = "flow_pulse"  // 3-line oscillator
	StateKindBasis     StateKind = "basis"       // spot/future basis
	StateKindRegime    StateKind = "regime"      // gamma regime label
	StateKindPin       StateKind = "pin"         // EOD pin probability
)

// SubjectControlReplay is the control plane subject for replay session
// playback control (pause, seek, speed).
func SubjectControlReplay(sessionID string) string {
	return fmt.Sprintf("control.replay.%s", sessionID)
}
