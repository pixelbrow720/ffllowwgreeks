package greeks

import (
	"math"
	"testing"

	"flowgreeks/internal/feed"
)

// TestBS_Parity validates BS against published / hand-computed reference
// values from canonical sources (Hull 9e, py_vollib). Tolerance 1e-4.
func TestBS_Parity(t *testing.T) {
	cases := []struct {
		name                                    string
		spot, strike, tt, r, q, sigma, expected float64
		side                                    feed.Side
	}{
		// Hull 9e Example 15.6: S=42, K=40, T=0.5, r=0.10, q=0, σ=0.20.
		{"hull-call", 42, 40, 0.5, 0.10, 0, 0.20, 4.7594224, feed.SideCall},
		{"hull-put", 42, 40, 0.5, 0.10, 0, 0.20, 0.8085994, feed.SidePut},
		// py_vollib canonical: S=K=100, T=1, r=0.05, q=0, σ=0.20.
		{"vollib-call", 100, 100, 1.0, 0.05, 0, 0.20, 10.4505835721856, feed.SideCall},
		{"vollib-put", 100, 100, 1.0, 0.05, 0, 0.20, 5.5735260222570, feed.SidePut},
		// ATM, zero rate / div: closed-form (2Φ(σ/2)-1)·S.
		{"zero-rate-call", 100, 100, 1.0, 0, 0, 0.20, 7.9655674554058, feed.SideCall},
		// With dividend yield (BS-Merton).
		{"with-div-call", 100, 100, 1.0, 0.05, 0.02, 0.20, 9.227005374159, feed.SideCall},
	}
	for _, c := range cases {
		got := BS(c.spot, c.strike, c.tt, c.r, c.q, c.sigma, c.side)
		if math.Abs(got-c.expected) > 1e-4 {
			t.Errorf("%s: BS = %.10f, want %.10f (diff %.2e)", c.name, got, c.expected, got-c.expected)
		}
	}
}

// TestBS_PutCallParity is a strong invariant: C - P = S·e^(-qT) - K·e^(-rT).
// Holds for any vanilla European pair regardless of σ, T, etc.
func TestBS_PutCallParity(t *testing.T) {
	type p struct{ s, k, tt, r, q, sigma float64 }
	cases := []p{
		{100, 100, 1.0, 0.05, 0.0, 0.20},
		{5810, 5810, 0.0027, 0.045, 0.013, 0.18},
		{4500, 4400, 0.25, 0.04, 0.015, 0.22},
		{4500, 4600, 0.25, 0.04, 0.015, 0.22},
		{120, 80, 0.5, 0.03, 0.0, 0.5},
	}
	for _, c := range cases {
		call := BS(c.s, c.k, c.tt, c.r, c.q, c.sigma, feed.SideCall)
		put := BS(c.s, c.k, c.tt, c.r, c.q, c.sigma, feed.SidePut)
		expected := c.s*math.Exp(-c.q*c.tt) - c.k*math.Exp(-c.r*c.tt)
		if d := call - put - expected; math.Abs(d) > 1e-9 {
			t.Errorf("parity broken at %+v: C-P-(Se^-qT - Ke^-rT) = %.2e", c, d)
		}
	}
}

// TestBS_Invalid covers the guard branches: each must return 0.
func TestBS_Invalid(t *testing.T) {
	type c struct {
		name                            string
		spot, strike, tt, r, q, sigma   float64
		side                            feed.Side
	}
	cases := []c{
		{"T=0", 100, 100, 0, 0.05, 0, 0.20, feed.SideCall},
		{"T<0", 100, 100, -0.1, 0.05, 0, 0.20, feed.SideCall},
		{"sigma=0", 100, 100, 1, 0.05, 0, 0, feed.SideCall},
		{"sigma<0", 100, 100, 1, 0.05, 0, -0.1, feed.SideCall},
		{"spot=0", 0, 100, 1, 0.05, 0, 0.20, feed.SideCall},
		{"spot<0", -100, 100, 1, 0.05, 0, 0.20, feed.SideCall},
		{"strike=0", 100, 0, 1, 0.05, 0, 0.20, feed.SideCall},
		{"strike<0", 100, -100, 1, 0.05, 0, 0.20, feed.SideCall},
		{"side=unknown", 100, 100, 1, 0.05, 0, 0.20, feed.SideUnknown},
	}
	for _, tc := range cases {
		got := BS(tc.spot, tc.strike, tc.tt, tc.r, tc.q, tc.sigma, tc.side)
		if got != 0 {
			t.Errorf("%s: BS = %v, want 0", tc.name, got)
		}
	}
}

// TestBS_Monotonicity checks that price is monotonically increasing in σ
// (vega > 0). Required for Brent IV solver to be well-defined.
func TestBS_Monotonicity(t *testing.T) {
	prev := BS(5810, 5810, 0.0027, 0.045, 0.013, 0.05, feed.SideCall)
	for sigma := 0.10; sigma <= 1.5; sigma += 0.05 {
		cur := BS(5810, 5810, 0.0027, 0.045, 0.013, sigma, feed.SideCall)
		if cur <= prev {
			t.Errorf("non-monotonic at σ=%.2f: %.6f → %.6f", sigma, prev, cur)
		}
		prev = cur
	}
}

func BenchmarkBS(b *testing.B) {
	for i := 0; i < b.N; i++ {
		_ = BS(5810, 5810, 0.0027, 0.045, 0.013, 0.18, feed.SideCall)
	}
}
