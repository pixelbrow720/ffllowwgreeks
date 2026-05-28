// Pin Probability Engine per docs/COMPUTE_MODEL.md §8.
//
// Activated in the last 90 minutes of the trading session. For each
// strike within ±20pt of spot, scores how likely the underlying is to
// pin to that strike at the close, then softmaxes the scores into
// probabilities.
//
// Components per §8:
//   gamma_strength    |TotalGamma[k]| / max — 0-1 normalized
//   distance_factor   exp(-((S-K)²)/(2σ²)) — Gaussian decay around spot
//   flow_persistence  recent_test_count / 5min_window
//   time_factor       (close - now)/sessionLen — less time → more pin
//
// PinScore = 0.4*γ_strength + 0.3*distance + 0.2*flow + 0.1*time
// PinProb = softmax(α * PinScore) over candidate strikes
//
// Pure function. M9 calibration job tunes α and the recent-test-count
// window against historical EOD outcomes.
package dealer

import (
	"math"
	"sort"
	"time"

	"flowgreeks/internal/feed"
)

// PinConfig tunes the scoring weights and gating window. Defaults are
// the spec values from COMPUTE_MODEL.md §8.
type PinConfig struct {
	WindowMinutes   float64 // pin engine activates within this many min of close (default 90)
	MaxDistance     float64 // strike must be within this many pts of spot (default 20 for SPX)
	SigmaProxy      float64 // distance gaussian σ in spot units (default 8)
	WeightGamma     float64 // weight on gamma_strength (default 0.4)
	WeightDistance  float64 // weight on distance_factor (default 0.3)
	WeightFlow      float64 // weight on flow_persistence (default 0.2)
	WeightTime      float64 // weight on time_factor (default 0.1)
	SoftmaxAlpha    float64 // sharpness of the softmax (default 5)
}

// DefaultPinConfig returns the spec defaults.
func DefaultPinConfig() PinConfig {
	return PinConfig{
		WindowMinutes:  90,
		MaxDistance:    20,
		SigmaProxy:     8,
		WeightGamma:    0.4,
		WeightDistance: 0.3,
		WeightFlow:     0.2,
		WeightTime:     0.1,
		SoftmaxAlpha:   5,
	}
}

// PinCandidate is one scored strike in the result.
type PinCandidate struct {
	Strike      uint32  // strike * 1000
	StrikePrice float64 // decoded float (convenience for UI)
	Score       float64
	Probability float64

	// Components (for diagnostic / "why" tooltip in the UI)
	GammaStrength   float64
	DistanceFactor  float64
	FlowPersistence float64
	TimeFactor      float64
}

// PinResult aggregates the engine output.
type PinResult struct {
	Active       bool           // false if outside the activation window
	WindowMins   float64        // minutes to close at evaluation time
	Candidates   []PinCandidate // sorted by Probability desc, capped to 10
	TopStrike    float64        // highest-probability strike (decoded)
	TopProbability float64
}

// PinFlow describes recent tests of a strike (touch / breach count over
// the last 5 minutes). Caller maintains this window — the engine only
// reads it. Strikes absent from the map default to zero tests.
type PinFlow map[uint32]int

// EvaluatePin scores pin candidates given the current chain.
//
// rows must already carry populated Gamma + DealerPos (i.e. the M2
// aggregator has run). spot is the current underlying price. now/close
// drive the time factor; flow comes from a 5-min sliding window of
// strike-test events kept by the caller.
func EvaluatePin(rows []StrikeRow, spot float64, flow PinFlow, now, sessionStart, sessionEnd time.Time, cfg PinConfig) PinResult {
	if cfg.WindowMinutes <= 0 {
		cfg = DefaultPinConfig()
	}
	timeToClose := sessionEnd.Sub(now).Minutes()
	res := PinResult{WindowMins: timeToClose}
	if timeToClose <= 0 || timeToClose > cfg.WindowMinutes {
		return res
	}
	if spot <= 0 || len(rows) == 0 {
		return res
	}
	res.Active = true

	// Aggregate |TotalGamma| per strike (sum across call + put × dealer pos × multiplier).
	type strikeAgg struct {
		strike   uint32
		strikePx float64
		gamma    float64
	}
	aggMap := make(map[uint32]*strikeAgg, len(rows)/2)
	for _, r := range rows {
		if r.DealerPos == 0 || r.Gamma == 0 {
			continue
		}
		strikePx := feed.DecodeStrike(r.Strike)
		if math.Abs(strikePx-spot) > cfg.MaxDistance {
			continue
		}
		entry, ok := aggMap[r.Strike]
		if !ok {
			entry = &strikeAgg{strike: r.Strike, strikePx: strikePx}
			aggMap[r.Strike] = entry
		}
		entry.gamma += math.Abs(r.Gamma * float64(r.DealerPos) * 100)
	}
	if len(aggMap) == 0 {
		return res
	}

	// Find max for normalization.
	var maxGamma float64
	for _, a := range aggMap {
		if a.gamma > maxGamma {
			maxGamma = a.gamma
		}
	}
	if maxGamma <= 0 {
		return res
	}

	// Time factor: more pin pressure as close approaches.
	timeFactor := 1.0 - timeToClose/cfg.WindowMinutes
	if timeFactor < 0 {
		timeFactor = 0
	}

	// Score components per strike.
	sigma2 := 2 * cfg.SigmaProxy * cfg.SigmaProxy
	if sigma2 <= 0 {
		sigma2 = 128 // σ=8 fallback
	}
	totalFlow := 0
	for _, n := range flow {
		totalFlow += n
	}

	candidates := make([]PinCandidate, 0, len(aggMap))
	for _, a := range aggMap {
		gammaStrength := a.gamma / maxGamma
		dist := spot - a.strikePx
		distanceFactor := math.Exp(-(dist * dist) / sigma2)
		var flowPersistence float64
		if totalFlow > 0 {
			flowPersistence = float64(flow[a.strike]) / float64(totalFlow)
		}
		score := cfg.WeightGamma*gammaStrength +
			cfg.WeightDistance*distanceFactor +
			cfg.WeightFlow*flowPersistence +
			cfg.WeightTime*timeFactor
		candidates = append(candidates, PinCandidate{
			Strike:          a.strike,
			StrikePrice:     a.strikePx,
			Score:           score,
			GammaStrength:   gammaStrength,
			DistanceFactor:  distanceFactor,
			FlowPersistence: flowPersistence,
			TimeFactor:      timeFactor,
		})
	}

	// Softmax probabilities. Numerical-stability: subtract max score
	// before exp.
	var sMax float64
	for i, c := range candidates {
		if i == 0 || c.Score > sMax {
			sMax = c.Score
		}
	}
	var sum float64
	for i := range candidates {
		candidates[i].Probability = math.Exp(cfg.SoftmaxAlpha * (candidates[i].Score - sMax))
		sum += candidates[i].Probability
	}
	if sum > 0 {
		for i := range candidates {
			candidates[i].Probability /= sum
		}
	}

	// Sort by probability descending; cap at 10 entries (UI only shows
	// top candidates anyway).
	sort.Slice(candidates, func(i, j int) bool {
		return candidates[i].Probability > candidates[j].Probability
	})
	if len(candidates) > 10 {
		candidates = candidates[:10]
	}
	res.Candidates = candidates
	if len(candidates) > 0 {
		res.TopStrike = candidates[0].StrikePrice
		res.TopProbability = candidates[0].Probability
	}
	return res
}
