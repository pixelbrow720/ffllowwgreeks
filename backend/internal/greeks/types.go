// Package greeks provides Black-Scholes pricing, implied-volatility solving,
// and analytical first/second-order option Greeks (Δ, Γ, Θ, vega, charm,
// vanna) for SPX/NDX-style European cash-settled index options.
//
// All computations follow COMPUTE_MODEL.md §1-§2. Hot-path discipline:
// no allocations, no panics, all functions are deterministic and reentrant.
//
// Conventions:
//   - Spot, strike: same currency unit (e.g. USD).
//   - Time T: years to expiry. Use TimeToExpiryYears() for ns→years conversion.
//   - Rate r: continuously-compounded risk-free rate (e.g. 0.045 for 4.5%).
//   - Yield q: continuous dividend yield for the index (e.g. 0.013).
//   - Vol σ: annualized implied vol (e.g. 0.18 for 18%).
//   - Side: feed.SideCall or feed.SidePut.
package greeks

import (
	"time"
)

// ─── Solver outcomes ──────────────────────────────────────────────────────

// IVResult carries the solver outcome and metadata for diagnostics.
type IVResult struct {
	IV         float64 // 0 on failure
	Iterations int     // iterations consumed
	Converged  bool    // true iff |f(x)| < tol within MaxIter
	Reason     string  // populated on Converged=false (e.g. "no bracket", "max iter")
}

// SolverConfig tunes the IV solver. Defaults (DefaultSolverConfig) are
// designed for fast convergence on liquid 0DTE options.
type SolverConfig struct {
	Tolerance float64 // |f(σ)| stop criterion (default 1e-5)
	MaxIter   int     // hard cap on iterations (default 50)
	VolMin    float64 // bracket low (default 0.001)
	VolMax    float64 // bracket high (default 5.0)
	InitGuess float64 // initial guess (default 0.20). Caller may pass last-known IV.
}

// DefaultSolverConfig is the recommended config for production hot path.
var DefaultSolverConfig = SolverConfig{
	Tolerance: 1e-5,
	MaxIter:   50,
	VolMin:    0.001,
	VolMax:    5.0,
	InitGuess: 0.20,
}

// ─── Greeks bundle ────────────────────────────────────────────────────────

// Greeks bundles the analytical first/second-order sensitivities for one
// (spot, strike, T, r, q, σ, side) point. All are per-contract (multiplier
// applied downstream in dealer aggregation).
type Greeks struct {
	Delta float64 // ∂price/∂spot
	Gamma float64 // ∂²price/∂spot²
	Theta float64 // ∂price/∂t (per year — divide by 365 for per-day, by 525600 for per-min)
	Vega  float64 // ∂price/∂σ per 1 vol pt (already scaled /100)
	Charm float64 // ∂Δ/∂t (per year)
	Vanna float64 // ∂Δ/∂σ
}

// ─── Public surface (defined in pricing.go / solver.go / greeks.go) ───────
//
// BS(spot, strike, t, r, q, sigma float64, side feed.Side) float64
//   prices a European option under Black-Scholes with continuous yield q.
//   Returns 0 for invalid inputs (T ≤ 0, σ ≤ 0, spot ≤ 0, strike ≤ 0).
//
// ImpliedVol(mid, spot, strike, t, r, q float64, side feed.Side, cfg SolverConfig) IVResult
//   solves for σ such that BS(spot, strike, t, r, q, σ, side) = mid.
//   Uses Brent's method bracketed by [cfg.VolMin, cfg.VolMax].
//
// All(spot, strike, t, r, q, sigma float64, side feed.Side) Greeks
//   computes the full Greeks bundle in one analytical pass — shares d1, d2,
//   N(d1) across formulas. Returns zero Greeks if inputs invalid.

// ─── Time helpers ─────────────────────────────────────────────────────────

// SecondsPerYear is the year length used to convert ns timestamps into the
// fractional-year T parameter. Calendar year (365.25 d × 86400 s).
const SecondsPerYear = 365.25 * 86400.0

// nyLoc caches the America/New_York Location once at package init so
// TimeToExpiryYears doesn't hit tzdata on every tick. Falls back to
// UTC when tzdata is missing (e.g. minimal docker images).
var nyLoc = func() *time.Location {
	loc, err := time.LoadLocation("America/New_York")
	if err != nil {
		return time.UTC
	}
	return loc
}()

// TimeToExpiryYears converts an exchange-event timestamp (ns since epoch)
// and an expiry encoded as YYYYMMDD uint32 into fractional years to a
// 16:00 ET cutoff (PM-settled SPX is 4pm ET; AM-settled SPX is 9:30 ET
// but those are not 0DTE — for 0DTE we use 4pm).
//
// Returns 0 if expiry is invalid or has already passed.
func TimeToExpiryYears(tsEventNanos uint64, expiryYYYYMMDD uint32) float64 {
	if expiryYYYYMMDD == 0 {
		return 0
	}
	y := int(expiryYYYYMMDD / 10000)
	m := int((expiryYYYYMMDD / 100) % 100)
	d := int(expiryYYYYMMDD % 100)
	expTime := time.Date(y, time.Month(m), d, 16, 0, 0, 0, nyLoc).UTC()
	now := time.Unix(0, int64(tsEventNanos)).UTC()
	if !expTime.After(now) {
		return 0
	}
	return expTime.Sub(now).Seconds() / SecondsPerYear
}
