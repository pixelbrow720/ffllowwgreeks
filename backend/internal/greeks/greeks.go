package greeks

import (
	"math"

	"flowgreeks/internal/feed"
)

// All computes the full Greeks bundle (Δ, Γ, Θ, Vega, Charm, Vanna) in one
// analytical pass, sharing d1/d2/φ(d1) across formulas. Returns zero Greeks
// for invalid inputs. Formulas match COMPUTE_MODEL.md §2 exactly.
//
// Conventions:
//   - Vega scaled per 1 vol pt (already divided by 100).
//   - Theta and Charm in per-year (caller divides by 365 / 525600 for
//     per-day / per-min as needed).
func All(spot, strike, t, r, q, sigma float64, side feed.Side) Greeks {
	if t <= 0 || sigma <= 0 || spot <= 0 || strike <= 0 {
		return Greeks{}
	}
	if side != feed.SideCall && side != feed.SidePut {
		return Greeks{}
	}

	sqrtT := math.Sqrt(t)
	sigSqrtT := sigma * sqrtT
	d1 := (math.Log(spot/strike) + (r-q+0.5*sigma*sigma)*t) / sigSqrtT
	d2 := d1 - sigSqrtT

	dfQ := math.Exp(-q * t)
	dfR := math.Exp(-r * t)
	pd1 := phid(d1) // shared across Γ, Vega, Θ, Charm, Vanna

	g := Greeks{
		Gamma: dfQ * pd1 / (spot * sigSqrtT),
		Vega:  spot * dfQ * pd1 * sqrtT / 100,
		Vanna: -dfQ * pd1 * d2 / sigma,
	}

	// Charm common factor: -e^(-qT) · φ(d1) · (2(r-q)T - d2·σ√T) / (2T·σ√T)
	charmCommon := -dfQ * pd1 * (2*(r-q)*t - d2*sigSqrtT) / (2 * t * sigSqrtT)
	// Theta common factor: -S·e^(-qT)·φ(d1)·σ / (2√T)
	thetaCommon := -spot * dfQ * pd1 * sigma / (2 * sqrtT)

	switch side {
	case feed.SideCall:
		Nd1 := phi(d1)
		Nd2 := phi(d2)
		g.Delta = dfQ * Nd1
		g.Theta = thetaCommon - r*strike*dfR*Nd2 + q*spot*dfQ*Nd1
		g.Charm = charmCommon - q*dfQ*Nd1
	case feed.SidePut:
		// N(-x) = 1 - N(x): two phi calls suffice for Delta + Theta + Charm.
		Nd1 := phi(d1)
		Nd2 := phi(d2)
		g.Delta = dfQ * (Nd1 - 1)
		g.Theta = thetaCommon + r*strike*dfR*(1-Nd2) - q*spot*dfQ*(1-Nd1)
		g.Charm = charmCommon + q*dfQ*(1-Nd1)
	}
	return g
}
