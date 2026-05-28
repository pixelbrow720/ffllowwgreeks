package greeks

import (
	"math"

	"flowgreeks/internal/feed"
)

// BS prices a European option under Black-Scholes with continuous dividend
// yield q. Returns 0 for invalid inputs (T ≤ 0, σ ≤ 0, spot ≤ 0, strike ≤ 0,
// unknown side). Follows COMPUTE_MODEL.md §1.
func BS(spot, strike, t, r, q, sigma float64, side feed.Side) float64 {
	if t <= 0 || sigma <= 0 || spot <= 0 || strike <= 0 {
		return 0
	}
	sqrtT := math.Sqrt(t)
	sigSqrtT := sigma * sqrtT
	d1 := (math.Log(spot/strike) + (r-q+0.5*sigma*sigma)*t) / sigSqrtT
	d2 := d1 - sigSqrtT
	dfQ := math.Exp(-q * t)
	dfR := math.Exp(-r * t)
	switch side {
	case feed.SideCall:
		return spot*dfQ*phi(d1) - strike*dfR*phi(d2)
	case feed.SidePut:
		return strike*dfR*phi(-d2) - spot*dfQ*phi(-d1)
	default:
		return 0
	}
}
