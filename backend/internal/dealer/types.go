// Package dealer holds dealer-positioning models and computed state types
// for FlowGreeks. The math/heuristics here are the proprietary core — see
// docs/COMPUTE_MODEL.md for the canonical specification.
//
// This file defines shared types only. Implementations live in sibling
// packages: position.go (dealer pos estimate), basis.go (futures basis),
// pulse.go (flow pulse oscillator, M3), dpi.go (DPI composite, M3).
package dealer

import (
	"flowgreeks/internal/feed"
)

// ─── Regime / Zone enums ──────────────────────────────────────────────────

// Regime labels the gamma posture of dealers right now.
type Regime uint8

const (
	RegimeUnknown    Regime = 0
	RegimeShortGamma Regime = 1 // dealers must amplify (sell rallies, buy dips)
	RegimeLongGamma  Regime = 2 // dealers dampen (mean-reverting flow)
	RegimeNeutral    Regime = 3 // near zero gamma
)

// String returns a human-readable label.
func (r Regime) String() string {
	switch r {
	case RegimeShortGamma:
		return "SHORT_GAMMA"
	case RegimeLongGamma:
		return "LONG_GAMMA"
	case RegimeNeutral:
		return "NEUTRAL"
	default:
		return "UNKNOWN"
	}
}

// CharmZone identifies the intraday charm-decay phase.
type CharmZone uint8

const (
	CharmZoneUnknown CharmZone = 0
	CharmZoneWeak    CharmZone = 1 // near-open warm-up
	CharmZoneRising  CharmZone = 2 // ramp toward peak
	CharmZonePeak    CharmZone = 3 // dominant decay window
	CharmZoneFading  CharmZone = 4 // post-peak relaxation
	CharmZonePin     CharmZone = 5 // EOD pin / squeeze
)

// String returns a label for the zone.
func (z CharmZone) String() string {
	switch z {
	case CharmZoneWeak:
		return "WEAK"
	case CharmZoneRising:
		return "RISING"
	case CharmZonePeak:
		return "PEAK"
	case CharmZoneFading:
		return "FADING"
	case CharmZonePin:
		return "PIN"
	default:
		return "UNKNOWN"
	}
}

// ─── Per-strike & aggregate state ─────────────────────────────────────────

// StrikeRow carries everything we need per (expiry, strike, side) for the
// dealer-positioning aggregations (GEX, walls, pulse).
type StrikeRow struct {
	Expiry       uint32
	Strike       uint32
	Side         feed.Side
	OI           uint32
	Volume       uint32
	NetSignedVol int32   // session-cumulative signed flow (customer-positive)
	IV           float64 // implied vol from BS solve (annualized)

	// Greeks per contract (one option contract)
	Delta float64
	Gamma float64
	Theta float64 // per year
	Vega  float64 // per 1 vol pt
	Charm float64 // per year (∂Δ/∂t)
	Vanna float64 // ∂Δ/∂σ

	// Dealer projection. Positive = dealer net long contracts at this strike.
	DealerPos int64

	// Notional GEX at this strike, signed dealer-side.
	GEXNotional float64
}

// AggregateState is the per-symbol per-second snapshot consumed by the API
// layer and persisted to dealer_state_1s. This will grow as M3 lands DPI
// components and Flow Pulse.
type AggregateState struct {
	Symbol  feed.Symbol
	TsEvent uint64 // ns since epoch UTC

	// Underlying.
	Spot       float64
	BasisFront float64 // signed: futures - spot (smoothed)

	// Aggregated GEX.
	NetGEX     float64 // notional, signed dealer-side
	ZeroGamma  float64 // strike level where dealer gamma flips sign
	CallWall   float64 // strike with largest negative dealer-gamma (call side)
	PutWall    float64 // strike with largest positive dealer-gamma (put side)
	ExpectedMv float64 // 1-day expected move %, derived from IV ATM

	// Regime & charm posture (M3 wires zone classifier; here for type stability).
	Regime    Regime
	CharmZone CharmZone

	// Strike matrix snapshot. Caller may mutate after reading; producers
	// must hand off a fresh slice (no in-place reuse across snapshots).
	StrikeMatrix []StrikeRow
}

// DPIBreakdown is the 5-component composite. Wired by M3 dpi.go.
type DPIBreakdown struct {
	NetGammaSign      float64 // 0-100
	CharmVelocity     float64
	VannaSensitivity  float64
	TimeToCloseDecay  float64
	FlowConcentration float64
}

// Composite returns the weighted DPI (0-100). Weights from COMPUTE_MODEL.md
// §5.2; tunable with backtest in M7.
func (b DPIBreakdown) Composite() float64 {
	return 0.30*b.NetGammaSign +
		0.25*b.CharmVelocity +
		0.15*b.VannaSensitivity +
		0.20*b.TimeToCloseDecay +
		0.10*b.FlowConcentration
}
