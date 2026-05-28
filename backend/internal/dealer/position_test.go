package dealer

import (
	"testing"

	"flowgreeks/internal/feed"
)

func oiTick(side feed.Side, strike, oi uint32) feed.Tick {
	return feed.Tick{
		TickType:     feed.TickTypeOI,
		AssetClass:   feed.AssetClassOption,
		Symbol:       feed.SymbolSPX,
		Side:         side,
		Expiry:       20260620,
		Strike:       strike,
		OpenInterest: oi,
	}
}

func tradeTick(side feed.Side, strike, size uint32, ag feed.Aggressor) feed.Tick {
	return feed.Tick{
		TickType:   feed.TickTypeTrade,
		AssetClass: feed.AssetClassOption,
		Symbol:     feed.SymbolSPX,
		Side:       side,
		Expiry:     20260620,
		Strike:     strike,
		Size:       size,
		Aggressor:  ag,
		Price:      2.45,
		Bid:        2.40,
		Ask:        2.50,
	}
}

func TestPosition_SeedFromOI_Call(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000))
	got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SideCall)
	want := int64(-700) // -0.7 * 1000
	if got != want {
		t.Fatalf("call seed: want %d, got %d", want, got)
	}
}

func TestPosition_SeedFromOI_Put(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SidePut, 5810000, 1000))
	got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SidePut)
	want := int64(500) // +0.5 * 1000
	if got != want {
		t.Fatalf("put seed: want %d, got %d", want, got)
	}
}

func TestPosition_SeedFromOI_IgnoresNonOI(t *testing.T) {
	p := NewPositionTracker()

	tt := oiTick(feed.SideCall, 5810000, 1000)
	tt.TickType = feed.TickTypeTrade
	p.SeedFromOI(tt)

	tt = oiTick(feed.SideCall, 5810000, 1000)
	tt.AssetClass = feed.AssetClassFuture
	p.SeedFromOI(tt)

	tt = oiTick(feed.SideUnknown, 5810000, 1000)
	p.SeedFromOI(tt)

	if got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SideCall); got != 0 {
		t.Fatalf("non-OI must not seed: got %d", got)
	}
}

func TestPosition_Apply_BuyDecreasesPosition(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000)) // start at -700
	p.Apply(tradeTick(feed.SideCall, 5810000, 10, feed.AggressorBuy))
	got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SideCall)
	if got != -710 {
		t.Fatalf("buy(10): want -710, got %d", got)
	}
}

func TestPosition_Apply_SellIncreasesPosition(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000)) // -700
	p.Apply(tradeTick(feed.SideCall, 5810000, 25, feed.AggressorSell))
	got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SideCall)
	if got != -675 {
		t.Fatalf("sell(25): want -675, got %d", got)
	}
}

func TestPosition_Apply_UnknownIgnored(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000)) // -700
	p.Apply(tradeTick(feed.SideCall, 5810000, 100, feed.AggressorUnknown))
	got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SideCall)
	if got != -700 {
		t.Fatalf("unknown aggressor: want -700, got %d", got)
	}
}

func TestPosition_Apply_ZeroSize(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000))
	p.Apply(tradeTick(feed.SideCall, 5810000, 0, feed.AggressorBuy))
	got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SideCall)
	if got != -700 {
		t.Fatalf("zero size: want -700, got %d", got)
	}
}

func TestPosition_Apply_NonTradeNonOption(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000))

	tt := tradeTick(feed.SideCall, 5810000, 10, feed.AggressorBuy)
	tt.TickType = feed.TickTypeQuote
	p.Apply(tt)

	tt = tradeTick(feed.SideCall, 5810000, 10, feed.AggressorBuy)
	tt.AssetClass = feed.AssetClassFuture
	p.Apply(tt)

	got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SideCall)
	if got != -700 {
		t.Fatalf("non-trade/non-option must not apply: got %d", got)
	}
}

func TestPosition_Apply_MixedSequence(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000)) // -700
	p.Apply(tradeTick(feed.SideCall, 5810000, 50, feed.AggressorBuy))     // -750
	p.Apply(tradeTick(feed.SideCall, 5810000, 30, feed.AggressorSell))    // -720
	p.Apply(tradeTick(feed.SideCall, 5810000, 5, feed.AggressorUnknown))  // -720
	p.Apply(tradeTick(feed.SideCall, 5810000, 0, feed.AggressorBuy))      // -720
	p.Apply(tradeTick(feed.SideCall, 5810000, 20, feed.AggressorSell))    // -700

	got := p.Get(feed.SymbolSPX, 20260620, 5810000, feed.SideCall)
	if got != -700 {
		t.Fatalf("mixed sequence: want -700, got %d", got)
	}
}

func TestPosition_Get_UnknownStrike(t *testing.T) {
	p := NewPositionTracker()
	got := p.Get(feed.SymbolSPX, 20260620, 9999000, feed.SideCall)
	if got != 0 {
		t.Fatalf("unknown strike: want 0, got %d", got)
	}
}

func TestPosition_Snapshot(t *testing.T) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000)) // -700
	p.SeedFromOI(oiTick(feed.SidePut, 5800000, 600))   // +300
	p.SeedFromOI(oiTick(feed.SideCall, 5820000, 200))  // -140

	// One NDX strike must NOT appear in SPX snapshot.
	ndx := oiTick(feed.SideCall, 21000000, 500)
	ndx.Symbol = feed.SymbolNDX
	p.SeedFromOI(ndx)

	rows := p.Snapshot(feed.SymbolSPX)
	if len(rows) != 3 {
		t.Fatalf("snapshot len: want 3, got %d", len(rows))
	}
	seen := make(map[strikeKey]int64, len(rows))
	for _, r := range rows {
		// Greeks must be zero — filled by another component.
		if r.Delta != 0 || r.Gamma != 0 || r.IV != 0 || r.Theta != 0 || r.Vega != 0 {
			t.Fatalf("snapshot row Greeks must be zero, got %+v", r)
		}
		seen[strikeKey{Symbol: feed.SymbolSPX, Side: r.Side, Expiry: r.Expiry, Strike: r.Strike}] = r.DealerPos
	}

	check := func(side feed.Side, strike uint32, want int64) {
		t.Helper()
		k := strikeKey{Symbol: feed.SymbolSPX, Side: side, Expiry: 20260620, Strike: strike}
		if got, ok := seen[k]; !ok || got != want {
			t.Fatalf("snapshot[%v %d]: want %d, got %d (present=%v)", side, strike, want, got, ok)
		}
	}
	check(feed.SideCall, 5810000, -700)
	check(feed.SidePut, 5800000, 300)
	check(feed.SideCall, 5820000, -140)

	// NDX symbol returns only its single row.
	ndxRows := p.Snapshot(feed.SymbolNDX)
	if len(ndxRows) != 1 {
		t.Fatalf("ndx snapshot len: want 1, got %d", len(ndxRows))
	}
	if ndxRows[0].DealerPos != -350 { // -0.7 * 500
		t.Fatalf("ndx pos: want -350, got %d", ndxRows[0].DealerPos)
	}
}

func BenchmarkApply(b *testing.B) {
	p := NewPositionTracker()
	p.SeedFromOI(oiTick(feed.SideCall, 5810000, 1000))
	tt := tradeTick(feed.SideCall, 5810000, 1, feed.AggressorBuy)
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		p.Apply(tt)
	}
}
