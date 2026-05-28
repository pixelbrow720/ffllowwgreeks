package dealer

import (
	"math"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

func TestNGSPressureSign(t *testing.T) {
	const norm = 5e9
	cases := []struct {
		name   string
		netGEX float64
		want   float64
		tol    float64
	}{
		{"zero -> 50", 0, 50, 1e-9},
		{"strong long -> 0", +norm, 0, 1e-9},
		{"strong short -> 100", -norm, 100, 1e-9},
		{"saturated long -> 0", +2 * norm, 0, 1e-9},
		{"saturated short -> 100", -2 * norm, 100, 1e-9},
		{"mid long -> below 50", +norm * 0.2, 40, 1e-9},
		{"mid short -> above 50", -norm * 0.2, 60, 1e-9},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := ngsPressure(c.netGEX, norm)
			if !approxEqual(got, c.want, c.tol) {
				t.Errorf("ngsPressure(%g)=%g want %g", c.netGEX, got, c.want)
			}
		})
	}
}

func TestNGSDirectionViaScore(t *testing.T) {
	s := NewDPIScorer(DPIConfig{GEXNorm: 5e9})

	// positive NetGEX (long γ) → low NGS
	rowsLong := []StrikeRow{{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: 1, Gamma: 0.001, GEXNotional: +5e9}}
	bLong := s.Score(feed.SymbolSPX, AggregateView{NetGEX: +5e9}, rowsLong, nil, time.Time{})
	if bLong.NetGammaSign > 1 {
		t.Errorf("long gamma NGS=%g want ~0", bLong.NetGammaSign)
	}

	// negative NetGEX (short γ) → high NGS
	s2 := NewDPIScorer(DPIConfig{GEXNorm: 5e9})
	rowsShort := []StrikeRow{{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: -1, Gamma: 0.001, GEXNotional: -5e9}}
	bShort := s2.Score(feed.SymbolSPX, AggregateView{NetGEX: -5e9}, rowsShort, nil, time.Time{})
	if bShort.NetGammaSign < 99 {
		t.Errorf("short gamma NGS=%g want ~100", bShort.NetGammaSign)
	}

	// zero NetGEX → 50
	s3 := NewDPIScorer(DPIConfig{GEXNorm: 5e9})
	bZero := s3.Score(feed.SymbolSPX, AggregateView{NetGEX: 0}, nil, nil, time.Time{})
	if !approxEqual(bZero.NetGammaSign, 50, 1e-9) {
		t.Errorf("zero NetGEX NGS=%g want 50", bZero.NetGammaSign)
	}
}

func TestCharmVelocityEmpty(t *testing.T) {
	got := charmVelocity(nil, 5800, 5e6)
	if got != 0 {
		t.Errorf("empty rows: CV=%g want 0", got)
	}
	got = charmVelocity([]StrikeRow{}, 5800, 5e6)
	if got != 0 {
		t.Errorf("zero-len rows: CV=%g want 0", got)
	}
}

func TestCharmVelocityLargeFlow(t *testing.T) {
	// Push CV above norm so output saturates near 100.
	// per-strike contribution = |0.5 · 1e8 · 100 · 5800| = 2.9e13
	// /525600 ≈ 5.5e7 ≫ norm=5e6 → saturate to 100.
	rows := []StrikeRow{{
		Strike: feed.EncodeStrike(5800), Side: feed.SideCall,
		DealerPos: 100_000_000, Charm: 0.5, Gamma: 0.001,
		GEXNotional: 100_000_000 * 0.001 * 5800 * 5800,
	}}
	got := charmVelocity(rows, 5800, 5e6)
	if got < 99.9 {
		t.Errorf("large charm: CV=%g want ~100", got)
	}

	// Small input → low score.
	rowsSmall := []StrikeRow{{Strike: feed.EncodeStrike(5800), Side: feed.SideCall,
		DealerPos: 1, Charm: 0.001, Gamma: 0.001}}
	got = charmVelocity(rowsSmall, 5800, 5e6)
	if got > 1 {
		t.Errorf("small charm: CV=%g want ~0", got)
	}
}

func TestVannaSensitivity(t *testing.T) {
	// Saturate against norm=1e6.
	rows := []StrikeRow{{
		Strike: feed.EncodeStrike(5800), Side: feed.SideCall,
		DealerPos: 100, Vanna: 0.5, Gamma: 0.001,
		GEXNotional: 100 * 0.001 * 5800 * 5800,
	}}
	got := vannaSensitivity(rows, 5800, 1e6)
	if got < 99.9 {
		t.Errorf("large vanna: VS=%g want ~100 (sum=%g)", got, 100*0.5*100*5800)
	}

	got = vannaSensitivity(nil, 5800, 1e6)
	if got != 0 {
		t.Errorf("empty rows: VS=%g want 0", got)
	}
}

func TestTTCDecayBoundaries(t *testing.T) {
	start := time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC) // 9:30 ET in UTC
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)    // 16:00 ET in UTC

	if got := ttcDecay(start, start, end); !approxEqual(got, 0, 1e-9) {
		t.Errorf("ttc(start)=%g want 0", got)
	}
	if got := ttcDecay(end, start, end); !approxEqual(got, 100, 1e-9) {
		t.Errorf("ttc(end)=%g want 100", got)
	}

	mid := start.Add(end.Sub(start) / 2)
	want := 100 * math.Pow(0.5, 1.5)
	if got := ttcDecay(mid, start, end); !approxEqual(got, want, 1e-6) {
		t.Errorf("ttc(mid)=%g want %g", got, want)
	}

	// Before session start clamps to 0 (remaining > 1).
	before := start.Add(-1 * time.Hour)
	if got := ttcDecay(before, start, end); !approxEqual(got, 0, 1e-9) {
		t.Errorf("ttc(before)=%g want 0", got)
	}
	// After close clamps to 100 (remaining < 0).
	after := end.Add(1 * time.Hour)
	if got := ttcDecay(after, start, end); !approxEqual(got, 100, 1e-9) {
		t.Errorf("ttc(after)=%g want 100", got)
	}

	// Zero session length → 0.
	if got := ttcDecay(start, start, start); got != 0 {
		t.Errorf("ttc(zero session)=%g want 0", got)
	}
}

func TestFlowConcentrationUniform(t *testing.T) {
	// 100 strikes each with equal magnitude → HHI = 0.01 → FC = 5.
	flow := make(map[uint32]int64, 100)
	for i := 0; i < 100; i++ {
		flow[feed.EncodeStrike(5700+float64(i))] = 100
	}
	got := flowConcentration(flow)
	if got > 6 {
		t.Errorf("uniform FC=%g want low (<6)", got)
	}
}

func TestFlowConcentrationConcentrated(t *testing.T) {
	// All flow on one strike → HHI = 1 → FC = 500 → clamped to 100.
	flow := map[uint32]int64{feed.EncodeStrike(5800): 10_000}
	got := flowConcentration(flow)
	if !approxEqual(got, 100, 1e-9) {
		t.Errorf("single-strike FC=%g want 100", got)
	}

	// Empty → 0.
	if got := flowConcentration(nil); got != 0 {
		t.Errorf("nil FC=%g want 0", got)
	}
	if got := flowConcentration(map[uint32]int64{}); got != 0 {
		t.Errorf("empty FC=%g want 0", got)
	}
	// All-zero values → 0.
	if got := flowConcentration(map[uint32]int64{1: 0, 2: 0}); got != 0 {
		t.Errorf("zero-total FC=%g want 0", got)
	}
}

// TestScoreCompositeEndToEnd exercises Score() against a synthetic chain
// + flow map and verifies every component lands in [0,100] and the
// composite stays in range.
func TestScoreCompositeEndToEnd(t *testing.T) {
	spot := 5800.0
	rows := build200Strikes(spot)
	for i := range rows {
		rows[i].Charm = 0.05 * math.Exp(-float64((i/2)*(i/2))/200.0)
		rows[i].Vanna = 0.10 * math.Exp(-float64((i/2)*(i/2))/200.0)
	}
	view := Aggregate(rows, spot)

	flow := map[uint32]int64{}
	for i, r := range rows {
		if i%4 == 0 {
			flow[r.Strike] = int64(100 + i)
		}
	}

	start := time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC)
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)
	mid := start.Add(2 * time.Hour)

	s := NewDPIScorer(DPIConfig{
		GEXNorm:           5e9,
		CharmFlowRateNorm: 5e6,
		VannaPressureNorm: 1e6,
		EWMAAlpha:         0.3,
		SessionStart:      start,
		SessionEnd:        end,
	})

	b := s.Score(feed.SymbolSPX, view, rows, flow, mid)

	check := func(name string, x float64) {
		if math.IsNaN(x) || math.IsInf(x, 0) {
			t.Errorf("%s=%g want finite", name, x)
		}
		if x < 0 || x > 100 {
			t.Errorf("%s=%g want in [0,100]", name, x)
		}
	}
	check("NGS", b.NetGammaSign)
	check("CV", b.CharmVelocity)
	check("VS", b.VannaSensitivity)
	check("TTC", b.TimeToCloseDecay)
	check("FC", b.FlowConcentration)
	check("Composite", b.Composite())

	if b.TimeToCloseDecay <= 0 {
		t.Errorf("TTC=%g want > 0 at midsession", b.TimeToCloseDecay)
	}
}

// TestScoreEWMAStationary: identical inputs across two calls produce
// identical smoothed output.
func TestScoreEWMAStationary(t *testing.T) {
	spot := 5800.0
	rows := []StrikeRow{{
		Strike: feed.EncodeStrike(5800), Side: feed.SideCall,
		DealerPos: -1000, Gamma: 0.005, Charm: 0.02, Vanna: 0.05,
		GEXNotional: -1000 * 0.005 * spot * spot,
	}}
	view := Aggregate(rows, spot)
	flow := map[uint32]int64{feed.EncodeStrike(5800): 100, feed.EncodeStrike(5810): 100}
	now := time.Date(2026, 5, 26, 17, 0, 0, 0, time.UTC)

	s := NewDPIScorer(DPIConfig{
		EWMAAlpha:    0.3,
		SessionStart: time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC),
		SessionEnd:   time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC),
	})

	b1 := s.Score(feed.SymbolSPX, view, rows, flow, now)
	b2 := s.Score(feed.SymbolSPX, view, rows, flow, now)
	if b1 != b2 {
		t.Errorf("stationary EWMA drifted: b1=%+v b2=%+v", b1, b2)
	}
}

// TestScoreEWMABetweenRaws: between-raw output for changing inputs.
func TestScoreEWMABetweenRaws(t *testing.T) {
	spot := 5800.0
	view0 := AggregateView{NetGEX: +5e9} // → NGS=0
	view1 := AggregateView{NetGEX: -5e9} // → NGS=100
	rows := []StrikeRow{{Strike: feed.EncodeStrike(5800), Side: feed.SideCall,
		DealerPos: 1, Gamma: 0.001, GEXNotional: 1 * 0.001 * spot * spot}}

	s := NewDPIScorer(DPIConfig{GEXNorm: 5e9, EWMAAlpha: 0.3})

	b1 := s.Score(feed.SymbolSPX, view0, rows, nil, time.Time{})
	if !approxEqual(b1.NetGammaSign, 0, 1e-9) {
		t.Fatalf("first call NGS=%g want 0", b1.NetGammaSign)
	}
	b2 := s.Score(feed.SymbolSPX, view1, rows, nil, time.Time{})
	// b2 = 0.3·100 + 0.7·0 = 30
	want := 30.0
	if !approxEqual(b2.NetGammaSign, want, 1e-6) {
		t.Errorf("second call NGS=%g want %g (between 0 and 100)", b2.NetGammaSign, want)
	}
	if b2.NetGammaSign <= 0 || b2.NetGammaSign >= 100 {
		t.Errorf("second call NGS=%g not strictly between raws", b2.NetGammaSign)
	}
}

// TestScoreResetClearsState: Reset wipes EWMA so next call returns raw.
func TestScoreResetClearsState(t *testing.T) {
	s := NewDPIScorer(DPIConfig{GEXNorm: 5e9, EWMAAlpha: 0.3})

	_ = s.Score(feed.SymbolSPX, AggregateView{NetGEX: +5e9}, nil, nil, time.Time{})
	s.Reset(feed.SymbolSPX)
	b := s.Score(feed.SymbolSPX, AggregateView{NetGEX: -5e9}, nil, nil, time.Time{})
	if !approxEqual(b.NetGammaSign, 100, 1e-9) {
		t.Errorf("post-reset NGS=%g want 100 (no EWMA blend)", b.NetGammaSign)
	}
}

func TestNewDPIScorerDefaults(t *testing.T) {
	s := NewDPIScorer(DPIConfig{})
	if s.cfg.GEXNorm != DefaultDPIGEXNorm {
		t.Errorf("GEXNorm=%g want default %g", s.cfg.GEXNorm, DefaultDPIGEXNorm)
	}
	if s.cfg.CharmFlowRateNorm != DefaultDPICharmFlowRateNorm {
		t.Errorf("CharmFlowRateNorm default mismatch")
	}
	if s.cfg.VannaPressureNorm != DefaultDPIVannaPressureNorm {
		t.Errorf("VannaPressureNorm default mismatch")
	}
	if s.cfg.EWMAAlpha != DefaultDPIEWMAAlpha {
		t.Errorf("EWMAAlpha default mismatch")
	}
}

// build200StrikesWithGreeks extends gex_test.go's build200Strikes with
// charm/vanna profiles for the dpi benchmark.
func build200StrikesWithGreeks(spot float64) []StrikeRow {
	rows := build200Strikes(spot)
	for i := range rows {
		j := (i / 2) - 50
		damp := math.Exp(-float64(j*j) / 200.0)
		rows[i].Charm = 0.05 * damp
		rows[i].Vanna = 0.10 * damp
	}
	return rows
}

func BenchmarkScore(b *testing.B) {
	spot := 5800.0
	rows := build200StrikesWithGreeks(spot)
	view := Aggregate(rows, spot)

	flow := make(map[uint32]int64, 50)
	for i := 0; i < 50; i++ {
		flow[feed.EncodeStrike(5700+float64(i)*4)] = int64(50 + i)
	}

	start := time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC)
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)
	now := start.Add(2 * time.Hour)

	s := NewDPIScorer(DPIConfig{
		GEXNorm: 5e9, CharmFlowRateNorm: 5e6, VannaPressureNorm: 1e6,
		EWMAAlpha: 0.3, SessionStart: start, SessionEnd: end,
	})

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = s.Score(feed.SymbolSPX, view, rows, flow, now)
	}
}
