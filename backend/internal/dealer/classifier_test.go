package dealer

import (
	"testing"

	"flowgreeks/internal/feed"
)

func optTrade(price float64, bid, ask float64) feed.Tick {
	return feed.Tick{
		TickType:   feed.TickTypeTrade,
		AssetClass: feed.AssetClassOption,
		Symbol:     feed.SymbolSPX,
		Side:       feed.SideCall,
		Expiry:     20260620,
		Strike:     5810000,
		Price:      price,
		Size:       1,
		Bid:        bid,
		Ask:        ask,
	}
}

func TestClassifier_LiftAsk(t *testing.T) {
	c := NewClassifier()
	got := c.Classify(optTrade(2.50, 2.40, 2.50))
	if got != feed.AggressorBuy {
		t.Fatalf("price>=ask: want BUY, got %v", got)
	}
	// strictly above ask too
	got = c.Classify(optTrade(2.55, 2.40, 2.50))
	if got != feed.AggressorBuy {
		t.Fatalf("price>ask: want BUY, got %v", got)
	}
}

func TestClassifier_HitBid(t *testing.T) {
	c := NewClassifier()
	got := c.Classify(optTrade(2.40, 2.40, 2.50))
	if got != feed.AggressorSell {
		t.Fatalf("price<=bid: want SELL, got %v", got)
	}
	got = c.Classify(optTrade(2.30, 2.40, 2.50))
	if got != feed.AggressorSell {
		t.Fatalf("price<bid: want SELL, got %v", got)
	}
}

func TestClassifier_AboveMid(t *testing.T) {
	c := NewClassifier()
	got := c.Classify(optTrade(2.46, 2.40, 2.50))
	if got != feed.AggressorBuy {
		t.Fatalf("price>mid: want BUY, got %v", got)
	}
}

func TestClassifier_BelowMid(t *testing.T) {
	c := NewClassifier()
	got := c.Classify(optTrade(2.44, 2.40, 2.50))
	if got != feed.AggressorSell {
		t.Fatalf("price<mid: want SELL, got %v", got)
	}
}

func TestClassifier_TickTest(t *testing.T) {
	c := NewClassifier()

	// Without prior state: at-mid → UNKNOWN.
	got := c.Classify(optTrade(2.45, 2.40, 2.50))
	if got != feed.AggressorUnknown {
		t.Fatalf("at-mid w/o history: want UNKNOWN, got %v", got)
	}

	// Seed: lower last trade price → next mid trade is BUY.
	c.Classify(optTrade(2.30, 2.40, 2.50)) // last=2.30
	got = c.Classify(optTrade(2.45, 2.40, 2.50))
	if got != feed.AggressorBuy {
		t.Fatalf("tick-test rising: want BUY, got %v", got)
	}

	// Now last=2.45; next at-mid is equal → UNKNOWN.
	got = c.Classify(optTrade(2.45, 2.40, 2.50))
	if got != feed.AggressorUnknown {
		t.Fatalf("tick-test equal: want UNKNOWN, got %v", got)
	}

	// Seed higher, then mid → SELL.
	c.Classify(optTrade(2.60, 2.40, 2.70)) // last=2.60, ask=2.70 mid=2.55
	// reset window; do an at-mid where last>price
	got = c.Classify(optTrade(2.55, 2.40, 2.70))
	if got != feed.AggressorSell {
		t.Fatalf("tick-test falling: want SELL, got %v", got)
	}
}

func TestClassifier_PerStrikeIsolation(t *testing.T) {
	c := NewClassifier()

	a := optTrade(2.30, 2.40, 2.50) // call 5810
	c.Classify(a)

	b := optTrade(2.45, 2.40, 2.50)
	b.Strike = 5820000
	got := c.Classify(b)
	if got != feed.AggressorUnknown {
		t.Fatalf("different strike must not inherit history: want UNKNOWN, got %v", got)
	}

	// Same strike as a, at mid → uses last=2.30 → BUY.
	c2 := optTrade(2.45, 2.40, 2.50)
	got = c.Classify(c2)
	if got != feed.AggressorBuy {
		t.Fatalf("same-strike history lost: want BUY, got %v", got)
	}
}

func TestClassifier_NonTradeAndNonOption(t *testing.T) {
	c := NewClassifier()

	q := optTrade(2.45, 2.40, 2.50)
	q.TickType = feed.TickTypeQuote
	if got := c.Classify(q); got != feed.AggressorUnknown {
		t.Fatalf("quote: want UNKNOWN, got %v", got)
	}

	fut := optTrade(2.45, 2.40, 2.50)
	fut.AssetClass = feed.AssetClassFuture
	if got := c.Classify(fut); got != feed.AggressorUnknown {
		t.Fatalf("future: want UNKNOWN, got %v", got)
	}
}

func TestClassifier_MissingOrCrossedQuotes(t *testing.T) {
	c := NewClassifier()

	miss := optTrade(2.45, 0, 2.50)
	if got := c.Classify(miss); got != feed.AggressorUnknown {
		t.Fatalf("missing bid: want UNKNOWN, got %v", got)
	}

	miss = optTrade(2.45, 2.40, 0)
	if got := c.Classify(miss); got != feed.AggressorUnknown {
		t.Fatalf("missing ask: want UNKNOWN, got %v", got)
	}

	crossed := optTrade(2.45, 2.60, 2.50)
	if got := c.Classify(crossed); got != feed.AggressorUnknown {
		t.Fatalf("crossed quote: want UNKNOWN, got %v", got)
	}
}

func BenchmarkClassify(b *testing.B) {
	c := NewClassifier()
	// Warm up history so tick-test path isn't always cold.
	c.Classify(optTrade(2.40, 2.40, 2.50))

	tick := optTrade(2.46, 2.40, 2.50)
	b.ResetTimer()
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = c.Classify(tick)
	}
}
