package dealer

import (
	"math"
	"testing"

	"flowgreeks/internal/feed"
)

func approxEqual(a, b, tol float64) bool {
	if math.IsNaN(a) || math.IsNaN(b) {
		return false
	}
	return math.Abs(a-b) <= tol
}

// TestAggregateThreeStrike: hand-calculated reference scenario.
//
//	Spot = 5800
//	5790 Call: DealerPos=-100, Γ=0.001  → DealerGamma=-10  → GEX=-3,364,000
//	5800 Call: DealerPos=+50,  Γ=0.005  → DealerGamma=+25  → GEX=+8,410,000  IV=0.20
//	5810 Put : DealerPos=+100, Γ=0.002  → DealerGamma=+20  → GEX=+6,728,000
//
// Net = +11,774,000 (NEUTRAL — well under $500M threshold)
// Walk: -3.364M → +5.046M  ⇒ ZeroGamma between 5790 and 5800
//
//	zg = 5790 + (3.364 / 8.410) * 10 = 5794.0
//
// CallWall = 5790 (most-negative call dealer gamma)
// PutWall  = 5810 (most-positive put  dealer gamma)
// ExpectedMv = 0.20 * sqrt(1/365.25) * 100 ≈ 1.0464
func TestAggregateThreeStrike(t *testing.T) {
	spot := 5800.0
	rows := []StrikeRow{
		{Expiry: 20260620, Strike: feed.EncodeStrike(5790), Side: feed.SideCall, DealerPos: -100, Gamma: 0.001},
		{Expiry: 20260620, Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: 50, Gamma: 0.005, IV: 0.20},
		{Expiry: 20260620, Strike: feed.EncodeStrike(5810), Side: feed.SidePut, DealerPos: 100, Gamma: 0.002},
	}

	view := Aggregate(rows, spot)

	wantNet := -3_364_000.0 + 8_410_000.0 + 6_728_000.0
	if !approxEqual(view.NetGEX, wantNet, 1.0) {
		t.Errorf("NetGEX=%g want %g", view.NetGEX, wantNet)
	}
	if view.Regime != RegimeNeutral {
		t.Errorf("Regime=%s want NEUTRAL", view.Regime)
	}
	if !approxEqual(view.ZeroGamma, 5794.0, 0.01) {
		t.Errorf("ZeroGamma=%g want 5794.0", view.ZeroGamma)
	}
	if !approxEqual(view.CallWall, 5790.0, 0.01) {
		t.Errorf("CallWall=%g want 5790", view.CallWall)
	}
	if !approxEqual(view.PutWall, 5810.0, 0.01) {
		t.Errorf("PutWall=%g want 5810", view.PutWall)
	}
	wantEM := 0.20 * math.Sqrt(1.0/365.25) * 100
	if !approxEqual(view.ExpectedMv, wantEM, 1e-6) {
		t.Errorf("ExpectedMv=%g want %g", view.ExpectedMv, wantEM)
	}

	// Verify GEXNotional populated in-place. Rows were sorted by strike,
	// so build a strike→GEX map and check.
	got := map[uint32]float64{}
	for _, r := range rows {
		got[r.Strike] = r.GEXNotional
	}
	cases := []struct {
		strike float64
		want   float64
	}{
		{5790, -3_364_000},
		{5800, 8_410_000},
		{5810, 6_728_000},
	}
	for _, c := range cases {
		k := feed.EncodeStrike(c.strike)
		if !approxEqual(got[k], c.want, 1.0) {
			t.Errorf("GEXNotional[%g]=%g want %g", c.strike, got[k], c.want)
		}
	}
}

func TestAggregateLongGamma(t *testing.T) {
	spot := 5800.0
	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5790), Side: feed.SideCall, DealerPos: 10000, Gamma: 0.005},
		{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: 10000, Gamma: 0.005},
		{Strike: feed.EncodeStrike(5810), Side: feed.SidePut, DealerPos: 10000, Gamma: 0.005},
	}
	view := Aggregate(rows, spot)

	if view.Regime != RegimeLongGamma {
		t.Errorf("Regime=%s want LONG_GAMMA (NetGEX=%g)", view.Regime, view.NetGEX)
	}
	if view.NetGEX <= 0 {
		t.Errorf("NetGEX=%g want positive", view.NetGEX)
	}
	if view.ZeroGamma != 0 {
		t.Errorf("ZeroGamma=%g want 0 (no sign flip)", view.ZeroGamma)
	}
	// All-positive call gamma → no call wall.
	if view.CallWall != 0 {
		t.Errorf("CallWall=%g want 0 (no negative call gamma)", view.CallWall)
	}
}

func TestAggregateShortGamma(t *testing.T) {
	spot := 5800.0
	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5790), Side: feed.SideCall, DealerPos: -10000, Gamma: 0.005},
		{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: -10000, Gamma: 0.005},
		{Strike: feed.EncodeStrike(5810), Side: feed.SidePut, DealerPos: -10000, Gamma: 0.005},
	}
	view := Aggregate(rows, spot)

	if view.Regime != RegimeShortGamma {
		t.Errorf("Regime=%s want SHORT_GAMMA (NetGEX=%g)", view.Regime, view.NetGEX)
	}
	if view.NetGEX >= 0 {
		t.Errorf("NetGEX=%g want negative", view.NetGEX)
	}
	if view.ZeroGamma != 0 {
		t.Errorf("ZeroGamma=%g want 0 (no sign flip)", view.ZeroGamma)
	}
	// All-negative put gamma → no put wall.
	if view.PutWall != 0 {
		t.Errorf("PutWall=%g want 0 (no positive put gamma)", view.PutWall)
	}
}

func TestAggregateEmpty(t *testing.T) {
	view := Aggregate(nil, 5800.0)
	if view != (AggregateView{}) {
		t.Errorf("empty input: got %+v want zero AggregateView", view)
	}

	view = Aggregate([]StrikeRow{}, 5800.0)
	if view != (AggregateView{}) {
		t.Errorf("zero-len input: got %+v want zero AggregateView", view)
	}
}

func TestAggregateGEXNotionalInPlace(t *testing.T) {
	spot := 5800.0
	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: 10, Gamma: 0.01},
	}
	if rows[0].GEXNotional != 0 {
		t.Fatalf("precondition: GEXNotional should be 0")
	}
	Aggregate(rows, spot)
	want := 10.0 * 0.01 * 100 * spot * spot * 0.01
	if !approxEqual(rows[0].GEXNotional, want, 1e-6) {
		t.Errorf("GEXNotional=%g want %g (in-place mutation failed)", rows[0].GEXNotional, want)
	}
}

// TestAggregateZeroGammaInterpolation: two-strike crossing with asymmetric
// magnitudes — verifies linear interpolation, not just midpoint.
//
//	5790 Call DealerPos=-100 Γ=0.001 → GEX=-3,364,000
//	5800 Call DealerPos=+25  Γ=0.001 → GEX=  +841,000
//
// Total cumulative goes -3.364M → -2.523M (no cross). Add a third strike:
//
//	5810 Call DealerPos=+200 Γ=0.001 → GEX=+6,728,000
//
// Cumulative: -3.364M, -2.523M, +4.205M → cross between 5800 and 5810.
//
//	zg = 5800 + 2.523 / (2.523 + 4.205) * 10
//	   = 5800 + 2.523 / 6.728 * 10
//	   ≈ 5803.75
func TestAggregateZeroGammaInterpolation(t *testing.T) {
	spot := 5800.0
	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5790), Side: feed.SideCall, DealerPos: -100, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: 25, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: 200, Gamma: 0.001},
	}
	view := Aggregate(rows, spot)

	want := 5800.0 + 2_523_000.0/6_728_000.0*10.0
	if !approxEqual(view.ZeroGamma, want, 0.01) {
		t.Errorf("ZeroGamma=%g want %g", view.ZeroGamma, want)
	}
}

// TestAggregateNoExpectedMove: no strike with valid IV → ExpectedMv=0.
func TestAggregateNoExpectedMove(t *testing.T) {
	spot := 5800.0
	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: 10, Gamma: 0.01},
	}
	view := Aggregate(rows, spot)
	if view.ExpectedMv != 0 {
		t.Errorf("ExpectedMv=%g want 0 (no IV)", view.ExpectedMv)
	}
}

// build200Strikes produces a realistic SPX 0DTE chain centered on spot.
// 100 call strikes + 100 put strikes, $5 increments around spot.
func build200Strikes(spot float64) []StrikeRow {
	rows := make([]StrikeRow, 0, 200)
	for i := -50; i < 50; i++ {
		k := spot + float64(i)*5.0
		// rough peaked gamma profile around ATM
		gamma := 0.005 * math.Exp(-float64(i*i)/200.0)
		rows = append(rows, StrikeRow{
			Expiry:    20260620,
			Strike:    feed.EncodeStrike(k),
			Side:      feed.SideCall,
			DealerPos: int64(-100 + i),
			Gamma:     gamma,
			IV:        0.18,
		})
		rows = append(rows, StrikeRow{
			Expiry:    20260620,
			Strike:    feed.EncodeStrike(k),
			Side:      feed.SidePut,
			DealerPos: int64(80 - i),
			Gamma:     gamma,
			IV:        0.19,
		})
	}
	return rows
}

func BenchmarkAggregate(b *testing.B) {
	spot := 5800.0
	template := build200Strikes(spot)
	rows := make([]StrikeRow, len(template))

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		copy(rows, template)
		_ = Aggregate(rows, spot)
	}
}
