package greeks

import (
	"math"
	"testing"

	"flowgreeks/internal/feed"
)

// TestImpliedVol_RoundTrip prices an option at a known σ then solves IV
// from that price; recovered σ must match within tolerance. Checks both
// sides and ITM/OTM/ATM strike configurations.
func TestImpliedVol_RoundTrip(t *testing.T) {
	type scenario struct {
		spot, strike, tt, r, q float64
		side                   feed.Side
		label                  string
	}
	scenarios := []scenario{
		{5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall, "atm-call-0dte"},
		{5810, 5810, 0.0027, 0.045, 0.013, feed.SidePut, "atm-put-0dte"},
		{5810, 5750, 0.0027, 0.045, 0.013, feed.SideCall, "itm-call-0dte"},
		{5810, 5870, 0.0027, 0.045, 0.013, feed.SideCall, "otm-call-0dte"},
		{5810, 5870, 0.0027, 0.045, 0.013, feed.SidePut, "itm-put-0dte"},
		{5810, 5750, 0.0027, 0.045, 0.013, feed.SidePut, "otm-put-0dte"},
		{100, 100, 0.5, 0.05, 0.02, feed.SideCall, "atm-call-6mo"},
		{100, 110, 0.5, 0.05, 0.02, feed.SidePut, "itm-put-6mo"},
	}
	vols := []float64{0.05, 0.10, 0.20, 0.50, 1.0}
	for _, s := range scenarios {
		for _, sigma := range vols {
			price := BS(s.spot, s.strike, s.tt, s.r, s.q, sigma, s.side)
			if price <= 0 {
				continue // some deep-OTM combinations underflow; skip
			}
			res := ImpliedVol(price, s.spot, s.strike, s.tt, s.r, s.q, s.side, DefaultSolverConfig)
			if !res.Converged {
				t.Errorf("%s σ=%.2f: not converged (%s)", s.label, sigma, res.Reason)
				continue
			}
			if d := math.Abs(res.IV - sigma); d > 1e-4 {
				t.Errorf("%s σ=%.2f: solved %.6f, diff %.2e iters=%d", s.label, sigma, res.IV, d, res.Iterations)
			}
		}
	}
}

// TestImpliedVol_WarmStart verifies that supplying a near-true InitGuess
// reduces iteration count vs default guess.
func TestImpliedVol_WarmStart(t *testing.T) {
	const sigmaTrue = 0.18
	price := BS(5810, 5810, 0.0027, 0.045, 0.013, sigmaTrue, feed.SideCall)

	cold := DefaultSolverConfig
	cold.InitGuess = 0.20
	resCold := ImpliedVol(price, 5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall, cold)

	warm := DefaultSolverConfig
	warm.InitGuess = 0.181
	resWarm := ImpliedVol(price, 5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall, warm)

	if !resCold.Converged || !resWarm.Converged {
		t.Fatalf("convergence failed: cold=%+v warm=%+v", resCold, resWarm)
	}
	if resWarm.Iterations > resCold.Iterations {
		t.Errorf("warm start (%d iters) slower than cold (%d iters)", resWarm.Iterations, resCold.Iterations)
	}
}

func TestImpliedVol_NoBracket(t *testing.T) {
	// Mid above maximum producible price (intrinsic + huge vol premium).
	// 1e9 is past even the widened bracket (σ=10), so we still expect failure.
	res := ImpliedVol(1e9, 5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall, DefaultSolverConfig)
	if res.Converged || res.Reason != "no bracket" {
		t.Errorf("expected no-bracket failure, got %+v", res)
	}
}

// TestImpliedVol_HighVolAutoWiden covers the deep-OTM 0DTE case where
// the true σ exceeds the default VolMax=5.0 but stays under the widened
// bound. Previously these chains returned "no bracket" and dropped out
// of the snapshot. Now the solver widens once and converges.
func TestImpliedVol_HighVolAutoWiden(t *testing.T) {
	// Far OTM 0DTE call priced at σ=6.0 — outside default bracket but
	// inside the widened [VolMin/10, VolMax*2] = [0.0001, 10] band.
	const sigmaTrue = 6.0
	price := BS(5810, 6800, 0.0027, 0.045, 0.013, sigmaTrue, feed.SideCall)
	if price <= 0 {
		t.Skip("price underflowed")
	}
	res := ImpliedVol(price, 5810, 6800, 0.0027, 0.045, 0.013, feed.SideCall, DefaultSolverConfig)
	if !res.Converged {
		t.Fatalf("expected widened-bracket convergence, got %+v", res)
	}
	if d := math.Abs(res.IV - sigmaTrue); d > 1e-3 {
		t.Errorf("σ=%.4f recovered %.4f (diff %.2e)", sigmaTrue, res.IV, d)
	}
}

func TestImpliedVol_InvalidInputs(t *testing.T) {
	cases := []struct {
		name                          string
		mid, spot, strike, tt, r, q   float64
		side                          feed.Side
		cfg                           SolverConfig
	}{
		{"mid<=0", 0, 5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall, DefaultSolverConfig},
		{"T<=0", 5, 5810, 5810, 0, 0.045, 0.013, feed.SideCall, DefaultSolverConfig},
		{"spot<=0", 5, 0, 5810, 0.0027, 0.045, 0.013, feed.SideCall, DefaultSolverConfig},
		{"strike<=0", 5, 5810, 0, 0.0027, 0.045, 0.013, feed.SideCall, DefaultSolverConfig},
		{"unknown side", 5, 5810, 5810, 0.0027, 0.045, 0.013, feed.SideUnknown, DefaultSolverConfig},
		{"bad bracket", 5, 5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall, SolverConfig{Tolerance: 1e-5, MaxIter: 50, VolMin: 0.5, VolMax: 0.1}},
	}
	for _, c := range cases {
		res := ImpliedVol(c.mid, c.spot, c.strike, c.tt, c.r, c.q, c.side, c.cfg)
		if res.Converged {
			t.Errorf("%s: expected failure, got IV=%v", c.name, res.IV)
		}
	}
}

func BenchmarkImpliedVol(b *testing.B) {
	price := BS(5810, 5810, 0.0027, 0.045, 0.013, 0.18, feed.SideCall)
	cfg := DefaultSolverConfig
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = ImpliedVol(price, 5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall, cfg)
	}
}

func BenchmarkImpliedVol_WarmStart(b *testing.B) {
	price := BS(5810, 5810, 0.0027, 0.045, 0.013, 0.18, feed.SideCall)
	cfg := DefaultSolverConfig
	cfg.InitGuess = 0.181
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = ImpliedVol(price, 5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall, cfg)
	}
}
