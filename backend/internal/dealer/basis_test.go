package dealer

import (
	"math"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

func makeContract(s string) [12]byte {
	var out [12]byte
	copy(out[:], s)
	return out
}

func futQuote(symbol feed.Symbol, contract string, bid, ask float64, ts uint64) feed.Tick {
	return feed.Tick{
		TsEvent:         ts,
		Symbol:          symbol,
		AssetClass:      feed.AssetClassFuture,
		TickType:        feed.TickTypeQuote,
		FuturesContract: makeContract(contract),
		Bid:             bid,
		Ask:             ask,
	}
}

func futTrade(symbol feed.Symbol, contract string, price float64, ts uint64) feed.Tick {
	return feed.Tick{
		TsEvent:         ts,
		Symbol:          symbol,
		AssetClass:      feed.AssetClassFuture,
		TickType:        feed.TickTypeTrade,
		FuturesContract: makeContract(contract),
		Price:           price,
	}
}

func TestParseFuturesContract(t *testing.T) {
	currentYear := time.Now().Year()
	decade := currentYear - (currentYear % 10)

	cases := []struct {
		sym       string
		wantYear  int
		wantMonth int
		wantOK    bool
	}{
		{"ESM6", decade + 6, 6, true},
		{"ESU6", decade + 6, 9, true},
		{"NQH7", func() int {
			y := decade + 7
			if y < currentYear {
				y += 10
			}
			return y
		}(), 3, true},
		{"ESZ6", decade + 6, 12, true},
		{"NQ", 0, 0, false},     // too short
		{"ESM6X", 0, 0, false},  // too long
		{"ESA6", 0, 0, false},   // bad month code
		{"ESMX", 0, 0, false},   // bad year digit
	}

	// ESM6/ESU6/ESZ6 with digit=6: y = decade+6. If decade+6 < currentYear,
	// add 10. Make the expected match this rule.
	for i := range cases {
		c := &cases[i]
		if c.sym == "ESM6" || c.sym == "ESU6" || c.sym == "ESZ6" {
			y := decade + 6
			if y < currentYear {
				y += 10
			}
			c.wantYear = y
		}
	}

	for _, c := range cases {
		gotY, gotM, gotOK := parseFuturesContract(c.sym)
		if gotOK != c.wantOK {
			t.Errorf("parseFuturesContract(%q) ok = %v, want %v", c.sym, gotOK, c.wantOK)
			continue
		}
		if !c.wantOK {
			continue
		}
		if gotY != c.wantYear || gotM != c.wantMonth {
			t.Errorf("parseFuturesContract(%q) = (%d, %d), want (%d, %d)",
				c.sym, gotY, gotM, c.wantYear, c.wantMonth)
		}
	}
}

func TestThirdFriday(t *testing.T) {
	cases := []struct {
		year, month int
		want        time.Time
	}{
		{2026, 6, time.Date(2026, 6, 19, 0, 0, 0, 0, time.UTC)},
		{2026, 9, time.Date(2026, 9, 18, 0, 0, 0, 0, time.UTC)},
		{2026, 3, time.Date(2026, 3, 20, 0, 0, 0, 0, time.UTC)},
		{2026, 12, time.Date(2026, 12, 18, 0, 0, 0, 0, time.UTC)},
	}
	for _, c := range cases {
		got := thirdFriday(c.year, c.month)
		if !got.Equal(c.want) {
			t.Errorf("thirdFriday(%d, %d) = %v, want %v", c.year, c.month, got, c.want)
		}
		if got.Weekday() != time.Friday {
			t.Errorf("thirdFriday(%d, %d) is %v, want Friday", c.year, c.month, got.Weekday())
		}
	}
}

func TestNewBasisTrackerInvalidAlpha(t *testing.T) {
	cases := []float64{0, -0.1, 1.5, math.NaN()}
	for _, alpha := range cases {
		func() {
			defer func() {
				if r := recover(); r == nil {
					t.Errorf("NewBasisTracker(%v) did not panic", alpha)
				}
			}()
			_ = NewBasisTracker(alpha)
		}()
	}
}

func TestUpdateSpotFutureBasis(t *testing.T) {
	tr := NewBasisTracker(DefaultBasisAlpha)
	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5817.0, 5818.0, 1_000_000))

	snap := tr.Snapshot(feed.SymbolSPX)
	if snap.Symbol != feed.SymbolSPX {
		t.Fatalf("snapshot symbol = %v, want SPX", snap.Symbol)
	}
	if snap.Spot != 5810.0 {
		t.Errorf("Spot = %v, want 5810", snap.Spot)
	}
	if snap.FutFrontSym != "ESM6" {
		t.Errorf("FutFrontSym = %q, want ESM6", snap.FutFrontSym)
	}
	wantMid := 5817.5
	if snap.FutFrontMid != wantMid {
		t.Errorf("FutFrontMid = %v, want %v", snap.FutFrontMid, wantMid)
	}
	wantBasis := wantMid - 5810.0
	if math.Abs(snap.Basis-wantBasis) > 1e-9 {
		t.Errorf("Basis = %v, want %v", snap.Basis, wantBasis)
	}
	// First sample initializes EWMA to raw.
	if math.Abs(snap.BasisSmooth-wantBasis) > 1e-9 {
		t.Errorf("BasisSmooth (first sample) = %v, want %v", snap.BasisSmooth, wantBasis)
	}
}

func TestUpdateFutureTradeMid(t *testing.T) {
	tr := NewBasisTracker(DefaultBasisAlpha)
	tr.UpdateSpot(feed.SymbolNDX, 20100.0)
	tr.UpdateFuture(futTrade(feed.SymbolNDX, "NQM6", 20120.5, 1))
	snap := tr.Snapshot(feed.SymbolNDX)
	if snap.FutFrontMid != 20120.5 {
		t.Errorf("trade mid = %v, want 20120.5", snap.FutFrontMid)
	}
	if math.Abs(snap.Basis-20.5) > 1e-9 {
		t.Errorf("trade basis = %v, want 20.5", snap.Basis)
	}
}

func TestUpdateFutureIgnoresOneSidedQuote(t *testing.T) {
	tr := NewBasisTracker(DefaultBasisAlpha)
	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5817.0, 0, 1)) // ask missing
	snap := tr.Snapshot(feed.SymbolSPX)
	if snap.FutFrontSym != "" || snap.FutFrontMid != 0 {
		t.Errorf("one-sided quote should be ignored, got front=%q mid=%v",
			snap.FutFrontSym, snap.FutFrontMid)
	}
}

func TestEWMASmoothing(t *testing.T) {
	tr := NewBasisTracker(0.1)
	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	// Steady-state target: front mid = 5816 → raw basis = 6.0
	for i := 0; i < 200; i++ {
		tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5815.5, 5816.5, uint64(i)))
	}
	snap := tr.Snapshot(feed.SymbolSPX)
	if math.Abs(snap.BasisSmooth-6.0) > 1e-6 {
		t.Errorf("EWMA steady state = %v, want ~6.0", snap.BasisSmooth)
	}

	// Step input: shock new raw to 10.0, single update; smooth should move
	// alpha of the way from 6 toward 10 = 6 + 0.1*(10-6) = 6.4.
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5819.5, 5820.5, 1000))
	snap = tr.Snapshot(feed.SymbolSPX)
	if math.Abs(snap.BasisSmooth-6.4) > 1e-6 {
		t.Errorf("EWMA single step = %v, want 6.4", snap.BasisSmooth)
	}
}

func TestRolloverActivatesNearExpiry(t *testing.T) {
	tr := NewBasisTracker(DefaultBasisAlpha)

	// Pin "now" to 5 days before ESM6 expiry (third Friday of June 2026 = Jun 19).
	frontExpiry := thirdFriday(2026, 6)
	fakeNow := frontExpiry.AddDate(0, 0, -5)
	tr.nowFn = func() time.Time { return fakeNow }

	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5817.0, 5818.0, 1))

	snap := tr.Snapshot(feed.SymbolSPX)
	if !snap.InRollover {
		t.Fatalf("expected InRollover=true at 5 days from front expiry, got false")
	}
	if snap.FutFrontSym != "ESM6" {
		t.Errorf("front = %q, want ESM6", snap.FutFrontSym)
	}
	if snap.FutBackSym != "" {
		t.Errorf("back populated before back-month tick: %q", snap.FutBackSym)
	}

	// Back-month tick (next quarterly = ESU6) arrives.
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESU6", 5824.0, 5826.0, 2))

	snap = tr.Snapshot(feed.SymbolSPX)
	if snap.FutFrontSym != "ESM6" {
		t.Errorf("front changed unexpectedly: %q", snap.FutFrontSym)
	}
	if snap.FutBackSym != "ESU6" {
		t.Errorf("back = %q, want ESU6", snap.FutBackSym)
	}
	if math.Abs(snap.FutBackMid-5825.0) > 1e-9 {
		t.Errorf("FutBackMid = %v, want 5825.0", snap.FutBackMid)
	}
	if math.Abs(snap.BasisBack-15.0) > 1e-9 {
		t.Errorf("BasisBack = %v, want 15.0", snap.BasisBack)
	}
	if !snap.InRollover {
		t.Errorf("InRollover should remain true with both contracts tracked")
	}
}

func TestNotInRolloverFarFromExpiry(t *testing.T) {
	tr := NewBasisTracker(DefaultBasisAlpha)
	frontExpiry := thirdFriday(2026, 6)
	tr.nowFn = func() time.Time { return frontExpiry.AddDate(0, 0, -30) }

	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5817.0, 5818.0, 1))
	snap := tr.Snapshot(feed.SymbolSPX)
	if snap.InRollover {
		t.Errorf("InRollover=true at 30 days from expiry, want false")
	}
}

func TestEarlierExpiryPromotesToFront(t *testing.T) {
	// If the back-month arrives first (e.g. ESU6) and then a closer-dated
	// contract arrives (ESM6), the closer one should become front.
	tr := NewBasisTracker(DefaultBasisAlpha)
	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESU6", 5825.0, 5827.0, 1))
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5817.0, 5818.0, 2))
	snap := tr.Snapshot(feed.SymbolSPX)
	if snap.FutFrontSym != "ESM6" {
		t.Errorf("front = %q, want ESM6 (earlier expiry)", snap.FutFrontSym)
	}
	if snap.FutBackSym != "ESU6" {
		t.Errorf("back = %q, want ESU6", snap.FutBackSym)
	}
}

func TestPerSymbolIsolation(t *testing.T) {
	tr := NewBasisTracker(DefaultBasisAlpha)
	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	tr.UpdateSpot(feed.SymbolNDX, 20100.0)
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5816.0, 5818.0, 1))
	tr.UpdateFuture(futQuote(feed.SymbolNDX, "NQM6", 20119.0, 20121.0, 2))

	spx := tr.Snapshot(feed.SymbolSPX)
	ndx := tr.Snapshot(feed.SymbolNDX)
	if spx.FutFrontSym != "ESM6" {
		t.Errorf("SPX front = %q, want ESM6", spx.FutFrontSym)
	}
	if ndx.FutFrontSym != "NQM6" {
		t.Errorf("NDX front = %q, want NQM6", ndx.FutFrontSym)
	}
	if math.Abs(spx.Basis-7.0) > 1e-9 {
		t.Errorf("SPX basis = %v, want 7.0", spx.Basis)
	}
	if math.Abs(ndx.Basis-20.0) > 1e-9 {
		t.Errorf("NDX basis = %v, want 20.0", ndx.Basis)
	}
}

func TestSnapshotEmptyForUnknownSymbol(t *testing.T) {
	tr := NewBasisTracker(DefaultBasisAlpha)
	snap := tr.Snapshot(feed.SymbolSPX)
	if snap.FutFrontSym != "" || snap.Spot != 0 || snap.BasisSmooth != 0 {
		t.Errorf("empty snapshot expected, got %+v", snap)
	}
}

func TestNonFutureTickIgnored(t *testing.T) {
	tr := NewBasisTracker(DefaultBasisAlpha)
	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	opt := feed.Tick{
		Symbol:     feed.SymbolSPX,
		AssetClass: feed.AssetClassOption,
		TickType:   feed.TickTypeQuote,
		Bid:        12.0,
		Ask:        12.5,
	}
	tr.UpdateFuture(opt)
	snap := tr.Snapshot(feed.SymbolSPX)
	if snap.FutFrontSym != "" {
		t.Errorf("option tick should be ignored, got front=%q", snap.FutFrontSym)
	}
}

func BenchmarkUpdateFuture(b *testing.B) {
	tr := NewBasisTracker(DefaultBasisAlpha)
	tr.UpdateSpot(feed.SymbolSPX, 5810.0)
	// Prime the front-month slot so the benchmark exercises the hot path.
	tr.UpdateFuture(futQuote(feed.SymbolSPX, "ESM6", 5817.0, 5818.0, 1))

	tick := futQuote(feed.SymbolSPX, "ESM6", 5817.0, 5818.0, 0)

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		tick.TsEvent = uint64(i)
		tr.UpdateFuture(tick)
	}
}
