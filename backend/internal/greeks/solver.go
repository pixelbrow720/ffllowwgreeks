package greeks

import (
	"math"

	"flowgreeks/internal/feed"
)

// ImpliedVol solves for σ such that BS(spot, strike, t, r, q, σ, side) = mid
// using Brent's method on the bracket [cfg.VolMin, cfg.VolMax].
//
// If cfg.InitGuess lies inside the bracket and gives a residual of the same
// sign as one bracket end, that end is tightened to the guess — this is the
// "warm start" optimization for caching last-known IV per strike.
//
// On convergence: IVResult.IV is the solved vol, Converged=true.
// On failure: IV=0, Converged=false, Reason explains why.
//
// Deep-OTM fallback: if the initial bracket fails (residual same sign at
// both ends), we widen by an order of magnitude in the indicated
// direction once before giving up. This catches the deep-OTM 0DTE case
// where the default [0.001, 5.0] band leaves BS price floored above mid.
func ImpliedVol(mid, spot, strike, t, r, q float64, side feed.Side, cfg SolverConfig) IVResult {
	if t <= 0 || spot <= 0 || strike <= 0 || mid <= 0 {
		return IVResult{Reason: "invalid inputs"}
	}
	if side != feed.SideCall && side != feed.SidePut {
		return IVResult{Reason: "unknown side"}
	}
	if cfg.VolMin <= 0 || cfg.VolMax <= cfg.VolMin {
		return IVResult{Reason: "invalid bracket"}
	}

	f := func(sigma float64) float64 {
		return BS(spot, strike, t, r, q, sigma, side) - mid
	}

	a, b := cfg.VolMin, cfg.VolMax
	fa, fb := f(a), f(b)

	if fa*fb > 0 {
		// Same-signed residual = no bracket. Widen once in the
		// direction the residual indicates and retry.
		//   fa>0, fb>0 → BS too high in entire bracket → true σ < VolMin
		//   fa<0, fb<0 → BS too low in entire bracket  → true σ > VolMax
		if fa > 0 && fb > 0 {
			a = cfg.VolMin / 10
			if a < 1e-6 {
				a = 1e-6
			}
			fa = f(a)
		} else if fa < 0 && fb < 0 {
			b = cfg.VolMax * 2
			if b > 10 {
				b = 10
			}
			fb = f(b)
		}
		if fa*fb > 0 {
			return IVResult{Reason: "no bracket"}
		}
	}

	// Warm start: if InitGuess is interior and same-signed as a bracket end,
	// tighten that end. BS price is monotonic in σ for vanilla European
	// options, so this is safe and never inverts the bracket.
	if cfg.InitGuess > a && cfg.InitGuess < b {
		fg := f(cfg.InitGuess)
		if fg == 0 {
			return IVResult{IV: cfg.InitGuess, Iterations: 1, Converged: true}
		}
		if fg*fa > 0 {
			a, fa = cfg.InitGuess, fg
		} else if fg*fb > 0 {
			b, fb = cfg.InitGuess, fg
		}
	}

	// Brent's method: combines inverse-quadratic interpolation, secant,
	// and bisection with rigorous bracket-preservation. Reference:
	// Numerical Recipes 3e §9.3, Brent (1973).
	if math.Abs(fa) < math.Abs(fb) {
		a, b = b, a
		fa, fb = fb, fa
	}
	c, fc := a, fa
	mflag := true
	var d float64
	tol := cfg.Tolerance
	iter := 0
	for iter = 1; iter <= cfg.MaxIter; iter++ {
		if math.Abs(fb) < tol {
			return IVResult{IV: b, Iterations: iter, Converged: true}
		}
		var s float64
		if fa != fc && fb != fc {
			// Inverse quadratic interpolation.
			s = a*fb*fc/((fa-fb)*(fa-fc)) +
				b*fa*fc/((fb-fa)*(fb-fc)) +
				c*fa*fb/((fc-fa)*(fc-fb))
		} else {
			// Secant step.
			s = b - fb*(b-a)/(fb-fa)
		}

		lo, hi := (3*a+b)/4, b
		if lo > hi {
			lo, hi = hi, lo
		}
		cond1 := s < lo || s > hi
		cond2 := mflag && math.Abs(s-b) >= math.Abs(b-c)/2
		cond3 := !mflag && math.Abs(s-b) >= math.Abs(c-d)/2
		cond4 := mflag && math.Abs(b-c) < tol
		cond5 := !mflag && math.Abs(c-d) < tol
		if cond1 || cond2 || cond3 || cond4 || cond5 {
			s = (a + b) / 2
			mflag = true
		} else {
			mflag = false
		}

		fs := f(s)
		d, c, fc = c, b, fb
		if fa*fs < 0 {
			b, fb = s, fs
		} else {
			a, fa = s, fs
		}
		if math.Abs(fa) < math.Abs(fb) {
			a, b = b, a
			fa, fb = fb, fa
		}
	}

	if math.Abs(fb) < tol {
		return IVResult{IV: b, Iterations: iter, Converged: true}
	}
	return IVResult{Iterations: cfg.MaxIter, Reason: "max iter"}
}
