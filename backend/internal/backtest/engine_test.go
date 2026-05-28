package backtest

import (
	"context"
	"math"
	"testing"
	"time"

	"flowgreeks/internal/alerts"
	"flowgreeks/internal/feed"
)

func mkBTSnapshot(ts time.Time, spot, dpi float64, zone, regime uint8) Snapshot {
	st := alerts.Snapshot{Symbol: feed.SymbolSPX, TsNs: uint64(ts.UnixNano()),
		CharmZone: zone, Regime: regime}
	st.DPI.Composite = dpi
	return Snapshot{Ts: ts, Spot: spot, State: st}
}

func feedAll(t *testing.T, ch chan Snapshot, snaps []Snapshot) {
	t.Helper()
	go func() {
		defer close(ch)
		for _, s := range snaps {
			ch <- s
		}
	}()
}

func TestRun_LongStrategy_HappyPath(t *testing.T) {
	now := time.Now().UTC().Truncate(time.Minute)
	mk := func(min int, spot, dpi float64) Snapshot {
		return mkBTSnapshot(now.Add(time.Duration(min)*time.Minute), spot, dpi, 3, 1)
	}
	snaps := []Snapshot{
		mk(0, 5800, 50),
		mk(1, 5800, 90), // entry
		mk(2, 5810, 90),
		mk(3, 5820, 90),
		mk(4, 5820, 50), // exit (DPI fell)
	}
	ch := make(chan Snapshot, len(snaps))
	feedAll(t, ch, snaps)
	strat := Strategy{
		Name:      "long_dpi80",
		Entry:     func(s alerts.Snapshot) bool { return s.DPI.Composite > 80 },
		Exit:      func(s alerts.Snapshot) bool { return s.DPI.Composite < 60 },
		Direction: Long,
		CooldownMin: 0,
	}
	res, err := Run(context.Background(), strat, ch)
	if err != nil {
		t.Fatalf("Run: %v", err)
	}
	if res.Count != 1 {
		t.Fatalf("expected 1 trade, got %d", res.Count)
	}
	tr := res.Trades[0]
	if tr.EntrySpot != 5800 || tr.ExitSpot != 5820 {
		t.Errorf("trade prices wrong: %+v", tr)
	}
	want := (5820.0 - 5800) / 5800
	if math.Abs(tr.ReturnPct-want) > 1e-9 {
		t.Errorf("ReturnPct: got %v want %v", tr.ReturnPct, want)
	}
	if res.WinRate != 1.0 {
		t.Errorf("expected WinRate=1, got %v", res.WinRate)
	}
}

func TestRun_ShortStrategy(t *testing.T) {
	now := time.Now().UTC()
	mk := func(min int, spot, dpi float64) Snapshot {
		return mkBTSnapshot(now.Add(time.Duration(min)*time.Minute), spot, dpi, 3, 1)
	}
	snaps := []Snapshot{
		mk(0, 5800, 50),
		mk(1, 5800, 90),
		mk(2, 5780, 90),
		mk(3, 5780, 30), // exit
	}
	ch := make(chan Snapshot, len(snaps))
	feedAll(t, ch, snaps)
	strat := Strategy{
		Name:      "short_dpi80",
		Entry:     func(s alerts.Snapshot) bool { return s.DPI.Composite > 80 },
		Exit:      func(s alerts.Snapshot) bool { return s.DPI.Composite < 60 },
		Direction: Short,
		CooldownMin: 0,
	}
	res, _ := Run(context.Background(), strat, ch)
	if res.Count != 1 {
		t.Fatalf("expected 1 trade")
	}
	if res.Trades[0].ReturnPct <= 0 {
		t.Errorf("short trade with falling spot should be profitable, got %v", res.Trades[0].ReturnPct)
	}
}

func TestRun_MaxHoldExit(t *testing.T) {
	now := time.Now().UTC()
	mk := func(min int, spot, dpi float64) Snapshot {
		return mkBTSnapshot(now.Add(time.Duration(min)*time.Minute), spot, dpi, 3, 1)
	}
	snaps := []Snapshot{
		mk(0, 5800, 50),
		mk(1, 5800, 90), // entry
		mk(2, 5805, 90),
		mk(3, 5810, 90),
		mk(4, 5815, 90),
		mk(5, 5820, 90),
		mk(6, 5825, 90), // hits MaxHold=5min
	}
	ch := make(chan Snapshot, len(snaps))
	feedAll(t, ch, snaps)
	strat := Strategy{
		Name:        "long_5m",
		Entry:       func(s alerts.Snapshot) bool { return s.DPI.Composite > 80 },
		Direction:   Long,
		MaxHoldMin:  5,
		CooldownMin: 0,
	}
	res, _ := Run(context.Background(), strat, ch)
	if res.Count != 1 {
		t.Fatalf("expected 1 trade, got %d", res.Count)
	}
	if res.Trades[0].ExitReason != "max_hold" {
		t.Errorf("expected max_hold exit, got %q", res.Trades[0].ExitReason)
	}
}

func TestRun_Cooldown(t *testing.T) {
	now := time.Now().UTC()
	mk := func(min int, spot, dpi float64) Snapshot {
		return mkBTSnapshot(now.Add(time.Duration(min)*time.Minute), spot, dpi, 3, 1)
	}
	snaps := []Snapshot{
		mk(0, 5800, 90), // entry
		mk(1, 5810, 30), // exit
		mk(2, 5810, 90), // would be entry but cooldown
		mk(3, 5810, 30),
		mk(11, 5810, 90), // past 10min cooldown — entry
		mk(12, 5820, 30),
	}
	ch := make(chan Snapshot, len(snaps))
	feedAll(t, ch, snaps)
	strat := Strategy{
		Name:        "cd_test",
		Entry:       func(s alerts.Snapshot) bool { return s.DPI.Composite > 80 },
		Exit:        func(s alerts.Snapshot) bool { return s.DPI.Composite < 60 },
		Direction:   Long,
		CooldownMin: 10,
	}
	res, _ := Run(context.Background(), strat, ch)
	if res.Count != 2 {
		t.Errorf("expected 2 trades (cooldown enforced), got %d", res.Count)
	}
}

func TestRun_StreamEndClosesOpen(t *testing.T) {
	now := time.Now().UTC()
	mk := func(min int, spot, dpi float64) Snapshot {
		return mkBTSnapshot(now.Add(time.Duration(min)*time.Minute), spot, dpi, 3, 1)
	}
	snaps := []Snapshot{
		mk(0, 5800, 90),
		mk(1, 5810, 90), // still in trade at end
	}
	ch := make(chan Snapshot, len(snaps))
	feedAll(t, ch, snaps)
	strat := Strategy{
		Name:      "open_at_end",
		Entry:     func(s alerts.Snapshot) bool { return s.DPI.Composite > 80 },
		Direction: Long,
		CooldownMin: 0,
	}
	res, _ := Run(context.Background(), strat, ch)
	if res.Count != 1 {
		t.Fatalf("stream-end should close open trade")
	}
	if res.Trades[0].ExitReason != "stream_end" {
		t.Errorf("expected stream_end exit, got %q", res.Trades[0].ExitReason)
	}
}

func TestSummaryMetrics(t *testing.T) {
	r := summarize("t", []Trade{
		{ReturnPct: 0.01}, {ReturnPct: 0.02}, {ReturnPct: -0.005}, {ReturnPct: 0.015},
	}, time.Time{}, time.Time{})
	if r.WinRate != 0.75 {
		t.Errorf("win rate: got %v want 0.75", r.WinRate)
	}
	if r.MeanReturn <= 0 {
		t.Error("mean should be positive")
	}
	if r.Sharpe == 0 {
		t.Error("sharpe should be non-zero with positive mean and variance")
	}
	if r.TotalRet <= 0 {
		t.Error("compound return should be positive")
	}
}

func TestPredicateFromAlertRule(t *testing.T) {
	rule := alerts.Rule{Symbol: feed.SymbolSPX, Kind: alerts.RuleDPIAbove, Threshold: 75}
	pred := PredicateFromAlertRule(rule, feed.SymbolSPX)
	hi := alerts.Snapshot{Symbol: feed.SymbolSPX}
	hi.DPI.Composite = 80
	lo := alerts.Snapshot{Symbol: feed.SymbolSPX}
	lo.DPI.Composite = 70
	if !pred(hi) {
		t.Error("should fire on DPI=80 with threshold 75")
	}
	if pred(lo) {
		t.Error("should not fire on DPI=70 with threshold 75")
	}
}

func TestSortTradesByReturn(t *testing.T) {
	trs := []Trade{
		{ReturnPct: -0.01}, {ReturnPct: 0.05}, {ReturnPct: 0.02},
	}
	SortTradesByReturn(trs)
	if trs[0].ReturnPct != 0.05 || trs[2].ReturnPct != -0.01 {
		t.Errorf("sort order wrong: %+v", trs)
	}
}
