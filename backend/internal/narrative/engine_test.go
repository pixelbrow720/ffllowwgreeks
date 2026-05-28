package narrative

import (
	"strings"
	"testing"

	"flowgreeks/internal/dealer"
	"flowgreeks/internal/feed"
)

func TestEngine_FirstStepIsSilent(t *testing.T) {
	e := NewEngine(feed.SymbolSPX)
	out := e.Step(Snapshot{Regime: dealer.RegimeShortGamma, DPI: 50})
	if len(out) != 0 {
		t.Errorf("first step should be silent (seeding prior), got %v", out)
	}
}

func TestEngine_RegimeFlip(t *testing.T) {
	e := NewEngine(feed.SymbolSPX)
	_ = e.Step(Snapshot{Regime: dealer.RegimeLongGamma, DPI: 30, ZeroGamma: 5800})
	out := e.Step(Snapshot{Regime: dealer.RegimeShortGamma, DPI: 30, ZeroGamma: 5810})
	if len(out) == 0 {
		t.Fatal("regime flip should emit an event")
	}
	got := out[0]
	if got.Tag != TagRegime {
		t.Errorf("expected TagRegime, got %v", got.Tag)
	}
	if !strings.Contains(got.Text, "SHORT_GAMMA") {
		t.Errorf("text missing regime label: %q", got.Text)
	}
	if !strings.Contains(got.Text, "5810") {
		t.Errorf("text missing zero gamma: %q", got.Text)
	}
}

func TestEngine_DPIThresholdCrossOnceOnly(t *testing.T) {
	e := NewEngine(feed.SymbolSPX)
	// Seed at DPI=50 (below 60, below 80).
	_ = e.Step(Snapshot{DPI: 50})
	// Cross 60 rising.
	out1 := e.Step(Snapshot{DPI: 65})
	tagCount := 0
	for _, ev := range out1 {
		if ev.Tag == TagDPI {
			tagCount++
		}
	}
	if tagCount != 1 {
		t.Errorf("expected exactly one DPI cross at 60, got %d", tagCount)
	}
	// Stay at 65 — should NOT re-emit.
	out2 := e.Step(Snapshot{DPI: 65})
	for _, ev := range out2 {
		if ev.Tag == TagDPI {
			t.Errorf("steady-state DPI should not re-emit threshold: %v", ev.Text)
		}
	}
	// Cross 80 rising.
	out3 := e.Step(Snapshot{DPI: 85})
	tagCount = 0
	for _, ev := range out3 {
		if ev.Tag == TagDPI {
			tagCount++
		}
	}
	if tagCount != 1 {
		t.Errorf("expected one DPI cross at 80, got %d", tagCount)
	}
	// Drop back to 50 — should emit two falling crosses (80 then 60).
	out4 := e.Step(Snapshot{DPI: 50})
	tagCount = 0
	for _, ev := range out4 {
		if ev.Tag == TagDPI && strings.Contains(ev.Text, "falling") {
			tagCount++
		}
	}
	if tagCount != 2 {
		t.Errorf("expected 2 falling crosses, got %d", tagCount)
	}
}

func TestEngine_CharmZoneEnter(t *testing.T) {
	e := NewEngine(feed.SymbolSPX)
	_ = e.Step(Snapshot{CharmZone: dealer.CharmZoneRising})
	out := e.Step(Snapshot{CharmZone: dealer.CharmZonePeak, Regime: dealer.RegimeShortGamma})
	found := false
	for _, ev := range out {
		if ev.Tag == TagCharm && strings.Contains(ev.Text, "PEAK") {
			found = true
			if !strings.Contains(ev.Text, "momentum") {
				t.Errorf("expected SHORT γ commentary, got %q", ev.Text)
			}
		}
	}
	if !found {
		t.Errorf("expected charm-zone-enter event, got %v", out)
	}
}

func TestEngine_PinCandidate(t *testing.T) {
	e := NewEngine(feed.SymbolSPX)
	_ = e.Step(Snapshot{PinActive: false})
	out := e.Step(Snapshot{PinActive: true, PinTopStrike: 5825, PinTopProb: 0.45})
	found := false
	for _, ev := range out {
		if ev.Tag == TagPin {
			found = true
			if !strings.Contains(ev.Text, "5825") || !strings.Contains(ev.Text, "45") {
				t.Errorf("pin text malformed: %q", ev.Text)
			}
		}
	}
	if !found {
		t.Error("expected pin event")
	}
	// Same strike again — should not re-emit.
	out2 := e.Step(Snapshot{PinActive: true, PinTopStrike: 5825, PinTopProb: 0.45})
	for _, ev := range out2 {
		if ev.Tag == TagPin {
			t.Errorf("steady-state pin should not re-emit: %v", ev.Text)
		}
	}
	// Strike change → re-emit.
	out3 := e.Step(Snapshot{PinActive: true, PinTopStrike: 5830, PinTopProb: 0.5})
	pinCount := 0
	for _, ev := range out3 {
		if ev.Tag == TagPin {
			pinCount++
		}
	}
	if pinCount != 1 {
		t.Errorf("expected pin re-emit on strike change, got %d", pinCount)
	}
}

func TestEngine_SweepFlowEvent(t *testing.T) {
	e := NewEngine(feed.SymbolSPX)
	_ = e.Step(Snapshot{})
	out := e.Step(Snapshot{
		SweepStrike: 5810, SweepSize: 38000, SweepSide: feed.SideCall, SweepBuy: true,
	})
	found := false
	for _, ev := range out {
		if ev.Tag == TagFlow {
			found = true
			if !strings.Contains(ev.Text, "5810") || !strings.Contains(ev.Text, "38.0k") {
				t.Errorf("sweep text malformed: %q", ev.Text)
			}
			if !strings.Contains(ev.Text, "BUY spot") {
				t.Errorf("dealer action wrong for customer BUY CALL: %q", ev.Text)
			}
		}
	}
	if !found {
		t.Error("expected sweep flow event")
	}
}

func TestEngine_DealerActionAllFour(t *testing.T) {
	cases := []struct {
		buy     bool
		side    feed.Side
		wantSub string
	}{
		{true, feed.SideCall, "BUY spot"},
		{false, feed.SideCall, "SELL spot"},
		{true, feed.SidePut, "SELL spot"},
		{false, feed.SidePut, "BUY spot"},
	}
	for _, c := range cases {
		got := dealerAction(c.buy, c.side)
		if !strings.Contains(got, c.wantSub) {
			t.Errorf("dealerAction(%v,%v) = %q, want substring %q", c.buy, c.side, got, c.wantSub)
		}
	}
}
