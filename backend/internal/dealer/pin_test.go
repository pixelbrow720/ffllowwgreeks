package dealer

import (
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

func TestPin_InactiveOutsideWindow(t *testing.T) {
	now := time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC) // 09:30 ET equiv
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)  // 16:00 ET equiv

	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: -1000, Gamma: 0.001},
	}
	res := EvaluatePin(rows, 5810, nil, now, now, end, DefaultPinConfig())
	if res.Active {
		t.Errorf("engine should be inactive >90 min before close, got Active=true")
	}
}

func TestPin_InactiveAfterClose(t *testing.T) {
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)
	// now is past close
	now := end.Add(10 * time.Minute)

	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: -1000, Gamma: 0.001},
	}
	res := EvaluatePin(rows, 5810, nil, now, end.Add(-7*time.Hour), end, DefaultPinConfig())
	if res.Active {
		t.Error("engine should be inactive after close")
	}
}

func TestPin_TopStrikeMatchesHighestGamma(t *testing.T) {
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)
	now := end.Add(-30 * time.Minute) // inside the 90-min window

	// 3 strikes near spot 5810. Strike 5815 has the largest |gamma×pos|.
	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: -500, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: -1000, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5815), Side: feed.SideCall, DealerPos: -3000, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5820), Side: feed.SideCall, DealerPos: -200, Gamma: 0.001},
	}
	res := EvaluatePin(rows, 5810, nil, now, end.Add(-7*time.Hour), end, DefaultPinConfig())
	if !res.Active {
		t.Fatal("expected active inside 90-min window")
	}
	if res.TopStrike != 5815 {
		t.Errorf("expected top strike = 5815 (largest gamma), got %v", res.TopStrike)
	}
	if res.TopProbability <= 0 || res.TopProbability > 1 {
		t.Errorf("TopProbability out of [0,1]: %v", res.TopProbability)
	}
}

func TestPin_DistanceDecayPicksClosestStrike(t *testing.T) {
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)
	now := end.Add(-30 * time.Minute)

	// All strikes have identical gamma. Distance should be the
	// tie-breaker — strike closest to spot wins.
	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: -1000, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: -1000, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5820), Side: feed.SideCall, DealerPos: -1000, Gamma: 0.001},
	}
	res := EvaluatePin(rows, 5810, nil, now, end.Add(-7*time.Hour), end, DefaultPinConfig())
	if res.TopStrike != 5810 {
		t.Errorf("equal gamma: expected ATM (5810) to win on distance, got %v", res.TopStrike)
	}
}

func TestPin_ProbabilitiesSumToOne(t *testing.T) {
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)
	now := end.Add(-30 * time.Minute)

	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5800), Side: feed.SideCall, DealerPos: -500, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: -1000, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5820), Side: feed.SideCall, DealerPos: -300, Gamma: 0.001},
	}
	res := EvaluatePin(rows, 5810, nil, now, end.Add(-7*time.Hour), end, DefaultPinConfig())
	var sum float64
	for _, c := range res.Candidates {
		if c.Probability < 0 || c.Probability > 1 {
			t.Errorf("candidate prob out of [0,1]: %v", c.Probability)
		}
		sum += c.Probability
	}
	if sum < 0.999 || sum > 1.001 {
		t.Errorf("probabilities should sum to ~1, got %.6f", sum)
	}
}

func TestPin_FlowPersistenceBoostsScore(t *testing.T) {
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)
	now := end.Add(-30 * time.Minute)

	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: -500, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5820), Side: feed.SideCall, DealerPos: -500, Gamma: 0.001},
	}
	// 5820 has way more recent tests than 5810.
	flow := PinFlow{
		feed.EncodeStrike(5810): 1,
		feed.EncodeStrike(5820): 50,
	}
	res := EvaluatePin(rows, 5815, flow, now, end.Add(-7*time.Hour), end, DefaultPinConfig())
	if !res.Active {
		t.Fatal("expected active")
	}
	if res.TopStrike != 5820 {
		t.Errorf("flow persistence should push 5820 to top, got %v", res.TopStrike)
	}
}

func TestPin_OutOfRangeStrikeFiltered(t *testing.T) {
	end := time.Date(2026, 5, 26, 20, 0, 0, 0, time.UTC)
	now := end.Add(-30 * time.Minute)

	// 5900 is 90pt from spot — beyond the default 20pt MaxDistance.
	rows := []StrikeRow{
		{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: -500, Gamma: 0.001},
		{Strike: feed.EncodeStrike(5900), Side: feed.SideCall, DealerPos: -10000, Gamma: 0.001},
	}
	res := EvaluatePin(rows, 5810, nil, now, end.Add(-7*time.Hour), end, DefaultPinConfig())
	for _, c := range res.Candidates {
		if c.StrikePrice == 5900 {
			t.Errorf("5900 should be filtered out by MaxDistance, found in candidates")
		}
	}
}
