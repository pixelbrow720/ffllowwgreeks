// DPI composite scorer per docs/COMPUTE_MODEL.md §5. Combines the five
// pressure components (NGS_pressure, Charm Velocity, Vanna Sensitivity,
// Time-to-Close, Flow Concentration) into a 0-100 dealer pressure index,
// EWMA-smoothed across calls per symbol.
package dealer

import (
	"math"
	"sync"
	"time"

	"flowgreeks/internal/feed"
)

const (
	// minutesPerYear converts annualized charm to per-minute hedge demand
	// per §5.1(b).
	minutesPerYear = 525600.0

	// flowConcentrationScale is the empirical multiplier on HHI per
	// §5.1(e). HHI ∈ [1/N, 1]; with this factor an HHI ≥ 0.20 saturates
	// FC at 100. Calibrated against typical 0DTE chains where >0.2 means
	// ≥40% of recent flow is sitting on a few strikes. Tunable in M7.
	flowConcentrationScale = 5.0

	// ttcExponent gives the convex ramp toward EOD per §5.1(d).
	ttcExponent = 1.5
)

// Default normalizers — order-of-magnitude starting points for SPX 0DTE.
// Replace in production with rolling p90/p95 sourced from the store.
const (
	DefaultDPIGEXNorm           = 5e9
	DefaultDPICharmFlowRateNorm = 5e6
	DefaultDPIVannaPressureNorm = 1e6
	DefaultDPIEWMAAlpha         = 0.3
)

// DPIConfig parameterizes the scorer. SessionStart and SessionEnd bound
// the regular trading session used by the TTC component; their delta is
// the session length. Zero-valued numeric fields fall back to package
// defaults; zero-valued session times yield TTC = 0.
type DPIConfig struct {
	GEXNorm           float64
	CharmFlowRateNorm float64
	VannaPressureNorm float64
	EWMAAlpha         float64
	SessionStart      time.Time
	SessionEnd        time.Time
}

// dpiState holds the smoothed-component snapshot for one symbol.
type dpiState struct {
	prev   DPIBreakdown
	inited bool
}

// DPIScorer computes the DPI breakdown per Score() call and applies
// per-symbol EWMA smoothing across consecutive calls. Safe for
// concurrent use.
type DPIScorer struct {
	cfg    DPIConfig
	mu     sync.Mutex
	states map[feed.Symbol]*dpiState
}

// NewDPIScorer constructs a scorer. Zero-valued config fields fall back
// to package defaults.
func NewDPIScorer(cfg DPIConfig) *DPIScorer {
	if !(cfg.GEXNorm > 0) {
		cfg.GEXNorm = DefaultDPIGEXNorm
	}
	if !(cfg.CharmFlowRateNorm > 0) {
		cfg.CharmFlowRateNorm = DefaultDPICharmFlowRateNorm
	}
	if !(cfg.VannaPressureNorm > 0) {
		cfg.VannaPressureNorm = DefaultDPIVannaPressureNorm
	}
	if !(cfg.EWMAAlpha > 0 && cfg.EWMAAlpha <= 1) {
		cfg.EWMAAlpha = DefaultDPIEWMAAlpha
	}
	return &DPIScorer{
		cfg:    cfg,
		states: make(map[feed.Symbol]*dpiState),
	}
}

// Score evaluates the DPI breakdown for a symbol. rows must already
// carry populated Greeks, DealerPos, and GEXNotional (i.e. Aggregate
// has been called). signedFlow5min maps strike → net signed volume over
// the trailing 5 minutes; nil or empty yields FC = 0. Output components
// are EWMA(α)-smoothed across consecutive calls per symbol.
func (s *DPIScorer) Score(
	symbol feed.Symbol,
	view AggregateView,
	rows []StrikeRow,
	signedFlow5min map[uint32]int64,
	now time.Time,
) DPIBreakdown {
	spot := deriveSpotFromRows(rows)

	raw := DPIBreakdown{
		NetGammaSign:      ngsPressure(view.NetGEX, s.cfg.GEXNorm),
		CharmVelocity:     charmVelocity(rows, spot, s.cfg.CharmFlowRateNorm),
		VannaSensitivity:  vannaSensitivity(rows, spot, s.cfg.VannaPressureNorm),
		TimeToCloseDecay:  ttcDecay(now, s.cfg.SessionStart, s.cfg.SessionEnd),
		FlowConcentration: flowConcentration(signedFlow5min),
	}

	s.mu.Lock()
	st, ok := s.states[symbol]
	if !ok {
		st = &dpiState{}
		s.states[symbol] = st
	}
	if !st.inited {
		st.prev = raw
		st.inited = true
	} else {
		a := s.cfg.EWMAAlpha
		st.prev = DPIBreakdown{
			NetGammaSign:      a*raw.NetGammaSign + (1-a)*st.prev.NetGammaSign,
			CharmVelocity:     a*raw.CharmVelocity + (1-a)*st.prev.CharmVelocity,
			VannaSensitivity:  a*raw.VannaSensitivity + (1-a)*st.prev.VannaSensitivity,
			TimeToCloseDecay:  a*raw.TimeToCloseDecay + (1-a)*st.prev.TimeToCloseDecay,
			FlowConcentration: a*raw.FlowConcentration + (1-a)*st.prev.FlowConcentration,
		}
	}
	out := st.prev
	s.mu.Unlock()
	return out
}

// Reset clears EWMA state for the given symbol — call at session start
// so a fresh day does not inherit yesterday's smoothed components.
func (s *DPIScorer) Reset(symbol feed.Symbol) {
	s.mu.Lock()
	delete(s.states, symbol)
	s.mu.Unlock()
}

// ─── components ───────────────────────────────────────────────────────

// ngsPressure returns the dealer-pressure form of NGS per §5.1(a):
// strongly negative NetGEX (short γ, forced amplifying hedge) → 100,
// strongly positive (long γ, dampening) → 0, zero NetGEX → 50.
func ngsPressure(netGEX, norm float64) float64 {
	if norm <= 0 {
		return 50
	}
	mag := math.Abs(netGEX) / norm
	if mag > 1 {
		mag = 1
	}
	var sign float64
	switch {
	case netGEX > 0:
		sign = 1
	case netGEX < 0:
		sign = -1
	}
	return clamp0to100(50 - 50*sign*mag)
}

// charmVelocity sums |Charm·DealerPos·100·S| across strikes, converts
// the annualized total to per-minute hedge demand by dividing by
// 525,600, then scales against the rolling p95 norm.
func charmVelocity(rows []StrikeRow, spot, norm float64) float64 {
	if len(rows) == 0 || spot <= 0 || norm <= 0 {
		return 0
	}
	var sum float64
	for i := range rows {
		sum += math.Abs(rows[i].Charm * float64(rows[i].DealerPos) * contractMultiplier * spot)
	}
	rate := sum / minutesPerYear
	return clamp0to100(100 * rate / norm)
}

// vannaSensitivity sums |Vanna·DealerPos·100·S| across strikes and
// scales against the rolling p95 norm — magnitude of forced delta
// hedging per 1-vol-pt move.
func vannaSensitivity(rows []StrikeRow, spot, norm float64) float64 {
	if len(rows) == 0 || spot <= 0 || norm <= 0 {
		return 0
	}
	var sum float64
	for i := range rows {
		sum += math.Abs(rows[i].Vanna * float64(rows[i].DealerPos) * contractMultiplier * spot)
	}
	return clamp0to100(100 * sum / norm)
}

// ttcDecay implements 100·(1 - tRemaining)^1.5 per §5.1(d). At
// SessionStart returns 0, at SessionEnd returns 100, and is convex in
// between.
func ttcDecay(now, start, end time.Time) float64 {
	sessionLen := end.Sub(start).Seconds()
	if sessionLen <= 0 {
		return 0
	}
	remaining := end.Sub(now).Seconds() / sessionLen
	switch {
	case remaining < 0:
		remaining = 0
	case remaining > 1:
		remaining = 1
	}
	return 100 * math.Pow(1-remaining, ttcExponent)
}

// flowConcentration computes the Herfindahl index of |signedFlow5min|
// shares across strikes, then scales to 0-100 with the empirical
// concentration factor. Empty / zero-total maps return 0.
func flowConcentration(flow map[uint32]int64) float64 {
	if len(flow) == 0 {
		return 0
	}
	var total float64
	for _, v := range flow {
		total += math.Abs(float64(v))
	}
	if total <= 0 {
		return 0
	}
	var hhi float64
	for _, v := range flow {
		share := math.Abs(float64(v)) / total
		hhi += share * share
	}
	return clamp0to100(100 * hhi * flowConcentrationScale)
}

// ─── helpers ──────────────────────────────────────────────────────────

func clamp0to100(x float64) float64 {
	if math.IsNaN(x) {
		return 0
	}
	switch {
	case x < 0:
		return 0
	case x > 100:
		return 100
	}
	return x
}

// deriveSpotFromRows recovers spot from the GEXNotional populated by
// Aggregate: GEXNotional = DealerPos·Γ·S² so any row with non-zero
// state yields the same S. Returns 0 when no row carries usable state,
// in which case CV and VS produce 0.
func deriveSpotFromRows(rows []StrikeRow) float64 {
	for i := range rows {
		denom := float64(rows[i].DealerPos) * rows[i].Gamma
		if denom == 0 || rows[i].GEXNotional == 0 {
			continue
		}
		sq := rows[i].GEXNotional / denom
		if sq > 0 {
			return math.Sqrt(sq)
		}
	}
	return 0
}
