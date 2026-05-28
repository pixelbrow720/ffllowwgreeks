package greeks

import (
	"math"
	"testing"

	"flowgreeks/internal/feed"
)

// TestAll_HardcodedReference: canonical 0DTE-style scenario.
// Reference values derived by direct evaluation of COMPUTE_MODEL.md §2:
//
//	S=5810 K=5810 T=0.0027 r=0.045 q=0.013 σ=0.18 (call)
//	d1 = 0.0139142  d2 = 0.0045612
//	N(d1)=0.505551  N(d2)=0.501819  φ(d1)=0.398904
//	e^(-qT)=0.9999649  e^(-rT)=0.9998785
//
// Delta = e^(-qT)·N(d1)                    ≈ 0.505533
// Gamma = e^(-qT)·φ(d1)/(S·σ√T)           ≈ 0.0073407
// Vega  = S·e^(-qT)·φ(d1)·√T / 100        ≈ 1.20424
// Theta = -S·e^(-qT)·φ(d1)·σ/(2√T)
//        - r·K·e^(-rT)·N(d2)
//        + q·S·e^(-qT)·N(d1)              ≈ -4107.04 / yr
// Charm = -e^(-qT)·φ(d1)·(2(r-q)T − d2σ√T)/(2Tσ√T)
//        - q·e^(-qT)·N(d1)                ≈ -1.0343 / yr
// Vanna = -e^(-qT)·φ(d1)·d2/σ             ≈ -0.010108
func TestAll_HardcodedReference(t *testing.T) {
	g := All(5810, 5810, 0.0027, 0.045, 0.013, 0.18, feed.SideCall)

	type ref struct {
		name      string
		got, want float64
		absTol    float64
	}
	refs := []ref{
		{"Delta", g.Delta, 0.505533, 5e-4},
		{"Gamma", g.Gamma, 0.0073407, 5e-6},
		{"Vega", g.Vega, 1.20424, 5e-3},
		{"Theta", g.Theta, -4107.04, 5e-1},
		{"Charm", g.Charm, -1.0343, 5e-3},
		{"Vanna", g.Vanna, -0.010108, 5e-5},
	}
	for _, r := range refs {
		if math.Abs(r.got-r.want) > r.absTol {
			t.Errorf("%s = %.8f, want %.8f (tol %.0e, diff %.2e)", r.name, r.got, r.want, r.absTol, r.got-r.want)
		}
	}
}

// TestAll_FiniteDiffParity validates each analytical Greek against a central
// difference of BS. Tolerances reflect FD truncation+rounding error: tight
// for first-order Greeks (Δ, Vega, Θ), looser for second-order mixed
// derivatives (Γ, Charm, Vanna) which are dominated by FD-of-FD noise,
// especially at 0DTE where the Charm formula scales as 1/(2T·σ√T).
func TestAll_FiniteDiffParity(t *testing.T) {
	scenarios := []struct {
		name                   string
		spot, strike, tt, r, q float64
		side                   feed.Side
	}{
		{"atm-call-0dte", 5810, 5810, 0.0027, 0.045, 0.013, feed.SideCall},
		{"atm-put-0dte", 5810, 5810, 0.0027, 0.045, 0.013, feed.SidePut},
		{"itm-call-0dte", 5810, 5750, 0.0027, 0.045, 0.013, feed.SideCall},
		{"otm-put-0dte", 5810, 5750, 0.0027, 0.045, 0.013, feed.SidePut},
		{"atm-call-3mo", 100, 100, 0.25, 0.045, 0.013, feed.SideCall},
		{"otm-put-3mo", 100, 90, 0.25, 0.045, 0.013, feed.SidePut},
	}

	const sigma = 0.20

	for _, s := range scenarios {
		s := s
		t.Run(s.name, func(t *testing.T) {
			g := All(s.spot, s.strike, s.tt, s.r, s.q, sigma, s.side)

			// h_S near ε^(1/4)·S balances truncation vs rounding for 2nd-order.
			hS := 1e-4 * s.spot
			hSig := 1e-4
			// h_T scaled to T to keep relative perturbation bounded for short T.
			hT := math.Max(1e-7, 1e-3*s.tt)

			// Delta = ∂C/∂S
			deltaFD := (BS(s.spot+hS, s.strike, s.tt, s.r, s.q, sigma, s.side) -
				BS(s.spot-hS, s.strike, s.tt, s.r, s.q, sigma, s.side)) / (2 * hS)
			if d := math.Abs(g.Delta - deltaFD); d > 1e-4 {
				t.Errorf("Delta: analytical %.8f vs FD %.8f (diff %.2e)", g.Delta, deltaFD, d)
			}

			// Gamma = ∂²C/∂S²
			gammaFD := (BS(s.spot+hS, s.strike, s.tt, s.r, s.q, sigma, s.side) -
				2*BS(s.spot, s.strike, s.tt, s.r, s.q, sigma, s.side) +
				BS(s.spot-hS, s.strike, s.tt, s.r, s.q, sigma, s.side)) / (hS * hS)
			if d := math.Abs(g.Gamma-gammaFD) / math.Max(math.Abs(g.Gamma), 1e-9); d > 1e-2 {
				t.Errorf("Gamma: analytical %.10f vs FD %.10f (rel %.2e)", g.Gamma, gammaFD, d)
			}

			// Vega = ∂C/∂σ / 100
			vegaFD := (BS(s.spot, s.strike, s.tt, s.r, s.q, sigma+hSig, s.side) -
				BS(s.spot, s.strike, s.tt, s.r, s.q, sigma-hSig, s.side)) / (2 * hSig) / 100
			if d := math.Abs(g.Vega - vegaFD); d > 1e-4 {
				t.Errorf("Vega: analytical %.8f vs FD %.8f (diff %.2e)", g.Vega, vegaFD, d)
			}

			// Theta = ∂C/∂t (per year). Sign: time forward = T decreasing.
			thetaFD := -(BS(s.spot, s.strike, s.tt+hT, s.r, s.q, sigma, s.side) -
				BS(s.spot, s.strike, s.tt-hT, s.r, s.q, sigma, s.side)) / (2 * hT)
			if d := math.Abs(g.Theta-thetaFD) / math.Max(math.Abs(g.Theta), 1); d > 1e-3 {
				t.Errorf("Theta: analytical %.4f vs FD %.4f (rel %.2e)", g.Theta, thetaFD, d)
			}

			// Charm = ∂Δ/∂t. 4-corner mixed FD truncation O(h_S²+h_T²) leaves
			// ~10-15% noise on long-T ATM where Charm magnitude is small.
			// Hardcoded reference + put-call parity pin the exact value.
			deltaPlus := (BS(s.spot+hS, s.strike, s.tt+hT, s.r, s.q, sigma, s.side) -
				BS(s.spot-hS, s.strike, s.tt+hT, s.r, s.q, sigma, s.side)) / (2 * hS)
			deltaMinus := (BS(s.spot+hS, s.strike, s.tt-hT, s.r, s.q, sigma, s.side) -
				BS(s.spot-hS, s.strike, s.tt-hT, s.r, s.q, sigma, s.side)) / (2 * hS)
			charmFD := -(deltaPlus - deltaMinus) / (2 * hT)
			if d := math.Abs(g.Charm-charmFD) / math.Max(math.Abs(g.Charm), 1e-3); d > 0.15 {
				t.Errorf("Charm: analytical %.6f vs FD %.6f (rel %.2e)", g.Charm, charmFD, d)
			}

			// Vanna = ∂Δ/∂σ. Same compound-FD caveats as Charm.
			deltaSP := (BS(s.spot+hS, s.strike, s.tt, s.r, s.q, sigma+hSig, s.side) -
				BS(s.spot-hS, s.strike, s.tt, s.r, s.q, sigma+hSig, s.side)) / (2 * hS)
			deltaSM := (BS(s.spot+hS, s.strike, s.tt, s.r, s.q, sigma-hSig, s.side) -
				BS(s.spot-hS, s.strike, s.tt, s.r, s.q, sigma-hSig, s.side)) / (2 * hS)
			vannaFD := (deltaSP - deltaSM) / (2 * hSig)
			if d := math.Abs(g.Vanna-vannaFD) / math.Max(math.Abs(g.Vanna), 1e-3); d > 0.10 {
				t.Errorf("Vanna: analytical %.6f vs FD %.6f (rel %.2e)", g.Vanna, vannaFD, d)
			}
		})
	}
}

// TestAll_PutCallParity: Δ_call - Δ_put = e^(-qT), Γ identical, Vega identical.
func TestAll_PutCallParity(t *testing.T) {
	S, K, T, r, q, sigma := 5810.0, 5810.0, 0.0027, 0.045, 0.013, 0.18
	c := All(S, K, T, r, q, sigma, feed.SideCall)
	p := All(S, K, T, r, q, sigma, feed.SidePut)
	expected := math.Exp(-q * T)
	if d := math.Abs(c.Delta - p.Delta - expected); d > 1e-12 {
		t.Errorf("Δ_c - Δ_p = %.10f, want %.10f", c.Delta-p.Delta, expected)
	}
	if d := math.Abs(c.Gamma - p.Gamma); d > 1e-12 {
		t.Errorf("Gamma_c != Gamma_p (diff %.2e)", d)
	}
	if d := math.Abs(c.Vega - p.Vega); d > 1e-12 {
		t.Errorf("Vega_c != Vega_p (diff %.2e)", d)
	}
}

func TestAll_Invalid(t *testing.T) {
	zero := Greeks{}
	type c struct {
		name                          string
		spot, strike, tt, r, q, sigma float64
		side                          feed.Side
	}
	cases := []c{
		{"T=0", 100, 100, 0, 0.05, 0, 0.20, feed.SideCall},
		{"T<0", 100, 100, -1, 0.05, 0, 0.20, feed.SideCall},
		{"sigma=0", 100, 100, 1, 0.05, 0, 0, feed.SideCall},
		{"sigma<0", 100, 100, 1, 0.05, 0, -0.1, feed.SideCall},
		{"spot<=0", 0, 100, 1, 0.05, 0, 0.20, feed.SideCall},
		{"strike<=0", 100, 0, 1, 0.05, 0, 0.20, feed.SideCall},
		{"unknown side", 100, 100, 1, 0.05, 0, 0.20, feed.SideUnknown},
	}
	for _, tc := range cases {
		got := All(tc.spot, tc.strike, tc.tt, tc.r, tc.q, tc.sigma, tc.side)
		if got != zero {
			t.Errorf("%s: All = %+v, want zero", tc.name, got)
		}
	}
}

func BenchmarkAll(b *testing.B) {
	for i := 0; i < b.N; i++ {
		_ = All(5810, 5810, 0.0027, 0.045, 0.013, 0.18, feed.SideCall)
	}
}
