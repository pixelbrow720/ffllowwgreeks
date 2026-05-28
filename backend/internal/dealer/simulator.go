// What-If Dealer Simulator per docs/COMPUTE_MODEL.md §7.
//
// Given a hypothetical (spot move, time elapsed, vol shock), computes
// the forced dealer hedge in delta + dollar notional. Pure function over
// a snapshot of StrikeRow + market state; no goroutines, no external
// services.
//
// Sign convention for ForcedNotional:
//   > 0  → dealer must BUY index (futures) to rehedge
//   < 0  → dealer must SELL
//
// Derivation: in a short-gamma regime, a spot rise increases the dealer's
// option-portfolio delta (each call gains delta, puts lose negative
// delta). To stay delta-neutral the dealer SHORTS futures equivalent to
// the delta gain — so positive ΔDelta → negative ForcedNotional.
package dealer

import (
	"time"

	"flowgreeks/internal/feed"
	"flowgreeks/internal/greeks"
)

// ScenarioInput is what the user / API supplies.
//
// SpotPctChange is a fraction (0.005 = +0.5%, -0.01 = -1%).
// DurationMinutes is how far into the future the scenario looks.
// VolPtChange is in vol points (0.02 = +2 vol pts on the IV surface).
type ScenarioInput struct {
	SpotPctChange   float64
	DurationMinutes float64
	VolPtChange     float64
}

// ScenarioResult is the simulator's output.
type ScenarioResult struct {
	// Inputs echoed back for diagnostic UI.
	NewSpot       float64
	DurationYears float64

	// Aggregate hedge demand. ForcedDelta is in raw delta-equivalent
	// (sum of Δdelta × position × multiplier). ForcedNotional applies
	// the sign convention above.
	ForcedDelta    float64 // sum of Δ-changes the dealer must absorb
	ForcedNotional float64 // dollar notional, signed (>0 BUY, <0 SELL)

	// CharmAid: portion of the rehedge that the natural decay of charm
	// over DurationMinutes will absorb on its own. Reduces the magnitude
	// of NetPressure relative to ForcedNotional.
	CharmAid float64

	// NetPressure = ForcedNotional − CharmAid. The number to display.
	NetPressure float64

	// Per-strike contributions for "biggest movers" UI. Sorted by
	// |ForcedNotional| descending, capped at 25 entries to keep
	// payloads small.
	TopContributions []StrikeContribution
}

// StrikeContribution is one row in the per-strike breakdown.
type StrikeContribution struct {
	Expiry         uint32
	Strike         uint32
	Side           feed.Side
	OldDelta       float64
	NewDelta       float64
	DeltaChange    float64
	ForcedNotional float64
}

const (
	// Charm in greeks.All is annualized — convert to per-minute by
	// dividing by 525600. This must match COMPUTE_MODEL.md §2.
	minutesPerYearForCharm = 525600.0

	// Maximum per-strike rows surfaced. UI doesn't need more than this
	// for the breakdown panel.
	maxTopContributions = 25
)

// Simulate runs the What-If scenario.
//
// rows MUST already carry populated DealerPos, IV, Delta, Charm — the
// same shape produced by the M2 aggregator (greeks.All filled them).
// spot is the current underlying. now and rate/yield are needed to
// compute time-to-expiry at the perturbed timestamp.
func Simulate(
	rows []StrikeRow,
	spot float64,
	now time.Time,
	rate float64,
	dividendYield float64,
	in ScenarioInput,
) ScenarioResult {
	res := ScenarioResult{
		NewSpot:       spot * (1 + in.SpotPctChange),
		DurationYears: in.DurationMinutes / minutesPerYearForCharm,
	}

	if len(rows) == 0 {
		return res
	}

	// Future evaluation point (used by greeks.TimeToExpiryYears via
	// adjusted timestamp).
	futureNs := uint64(now.Add(time.Duration(in.DurationMinutes * float64(time.Minute))).UnixNano())

	contributions := make([]StrikeContribution, 0, len(rows))

	for _, r := range rows {
		if r.DealerPos == 0 || r.IV <= 0 {
			continue
		}
		yearsNew := greeks.TimeToExpiryYears(futureNs, r.Expiry)
		if yearsNew <= 0 {
			continue
		}
		strike := feed.DecodeStrike(r.Strike)
		ivNew := r.IV + in.VolPtChange
		if ivNew <= 0 {
			continue
		}

		gNew := greeks.All(res.NewSpot, strike, yearsNew, rate, dividendYield, ivNew, r.Side)
		dDelta := gNew.Delta - r.Delta
		dPortfolio := dDelta * float64(r.DealerPos) * 100

		res.ForcedDelta += dPortfolio

		// Charm aid: charm is annualized, so charm × duration_years gives
		// the natural delta drift over DurationMinutes for this strike.
		// COMPUTE_MODEL.md §7.2 expresses the aid in dollar notional.
		charmDeltaContrib := r.Charm * res.DurationYears * float64(r.DealerPos) * 100

		strikeNotional := -dPortfolio * res.NewSpot
		res.CharmAid += -charmDeltaContrib * res.NewSpot

		contributions = append(contributions, StrikeContribution{
			Expiry:         r.Expiry,
			Strike:         r.Strike,
			Side:           r.Side,
			OldDelta:       r.Delta,
			NewDelta:       gNew.Delta,
			DeltaChange:    dDelta,
			ForcedNotional: strikeNotional,
		})
	}

	res.ForcedNotional = -res.ForcedDelta * res.NewSpot
	// Charm naturally moves part of the required delta over the
	// scenario window without dealers having to trade. Subtract
	// CharmAid so NetPressure is the residual notional dealers
	// actually need to execute. Both terms share the -spot×Δ sign
	// convention, so subtraction (not addition) reduces magnitude.
	res.NetPressure = res.ForcedNotional - res.CharmAid

	// Sort by |ForcedNotional| desc, cap to maxTopContributions.
	sortContribsByMagnitudeDesc(contributions)
	if len(contributions) > maxTopContributions {
		contributions = contributions[:maxTopContributions]
	}
	res.TopContributions = contributions

	return res
}

// sortContribsByMagnitudeDesc sorts in place by |ForcedNotional|
// descending. Insertion sort is fine — N is bounded by the active
// chain size (~200) and we cap output at 25.
func sortContribsByMagnitudeDesc(c []StrikeContribution) {
	for i := 1; i < len(c); i++ {
		for j := i; j > 0; j-- {
			if absF(c[j].ForcedNotional) > absF(c[j-1].ForcedNotional) {
				c[j], c[j-1] = c[j-1], c[j]
			} else {
				break
			}
		}
	}
}

func absF(x float64) float64 {
	if x < 0 {
		return -x
	}
	return x
}
