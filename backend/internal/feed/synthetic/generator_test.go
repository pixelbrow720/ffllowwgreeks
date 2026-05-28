package synthetic

import (
	"context"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

func TestGeneratorEmitsAllTickTypes(t *testing.T) {
	gen := New(Config{
		Symbol:       feed.SymbolSPX,
		Spot:         5810,
		IV:           0.18,
		QuotesPerSec: 100,
		TradesPerSec: 50,
		BasisPerSec:  10,
		Seed:         42,
	})
	ctx, cancel := context.WithTimeout(context.Background(), 1500*time.Millisecond)
	defer cancel()
	gen.Start(ctx)

	got := map[feed.TickType]int{}
	gotAsset := map[feed.AssetClass]int{}
	for tk := range gen.Ticks() {
		got[tk.TickType]++
		gotAsset[tk.AssetClass]++
		if got[feed.TickTypeQuote] > 50 && got[feed.TickTypeTrade] > 20 && got[feed.TickTypeOI] > 10 {
			gen.Stop()
		}
	}

	if got[feed.TickTypeQuote] == 0 {
		t.Errorf("expected some quote ticks, got 0")
	}
	if got[feed.TickTypeTrade] == 0 {
		t.Errorf("expected some trade ticks, got 0")
	}
	if got[feed.TickTypeOI] == 0 {
		t.Errorf("expected some OI ticks, got 0")
	}
	if gotAsset[feed.AssetClassOption] == 0 {
		t.Errorf("expected option ticks, got 0")
	}
	if gotAsset[feed.AssetClassFuture] == 0 {
		t.Errorf("expected future ticks, got 0")
	}
}

func TestFrontMonthContractFormat(t *testing.T) {
	cases := []struct {
		sym  feed.Symbol
		when time.Time
		want string
	}{
		{feed.SymbolSPX, time.Date(2026, time.May, 25, 0, 0, 0, 0, time.UTC), "ESM6"},
		{feed.SymbolNDX, time.Date(2026, time.July, 1, 0, 0, 0, 0, time.UTC), "NQU6"},
		{feed.SymbolSPX, time.Date(2026, time.December, 15, 0, 0, 0, 0, time.UTC), "ESZ6"},
	}
	for _, c := range cases {
		got := frontMonthContract(c.sym, c.when)
		if got != c.want {
			t.Errorf("frontMonthContract(%v, %v) = %q, want %q", c.sym, c.when, got, c.want)
		}
	}
}
