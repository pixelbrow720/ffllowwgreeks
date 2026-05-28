package dealer

import (
	"math"
	"testing"
	"time"

	"flowgreeks/internal/feed"
	"flowgreeks/internal/greeks"
)

// buildPopulatedChain builds a small SPX 0DTE-ish chain with Greeks
// already computed at the baseline (spot, iv, T). Mimics what the M2
// aggregator hands to Simulate in production.
func buildPopulatedChain(spot, iv float64, expiryYYYYMMDD uint32, now time.Time, dealerPosCall, dealerPosPut int64) []StrikeRow {
	const r, q = 0.045, 0.013
	tsEvent := uint64(now.UnixNano())
	years := greeks.TimeToExpiryYears(tsEvent, expiryYYYYMMDD)
	if years <= 0 {
		return nil
	}
	rows := make([]StrikeRow, 0, 8)
	for k := -2; k <= 2; k++ {
		strikePrice := math.Round(spot/5)*5 + float64(k)*5
		for _, side := range []feed.Side{feed.SideCall, feed.SidePut} {
			g := greeks.All(spot, strikePrice, years, r, q, iv, side)
			pos := dealerPosCall
			if side == feed.SidePut {
				pos = dealerPosPut
			}
			rows = append(rows, StrikeRow{
				Expiry:    expiryYYYYMMDD,
				Strike:    feed.EncodeStrike(strikePrice),
				Side:      side,
				DealerPos: pos,
				IV:        iv,
				Delta:     g.Delta,
				Gamma:     g.Gamma,
				Theta:     g.Theta,
				Vega:      g.Vega,
				Charm:     g.Charm,
				Vanna:     g.Vanna,
			})
		}
	}
	return rows
}

func TestSimulator_EmptyAndZeroSpot(t *testing.T) {
	if got := Simulate(nil, 5810, time.Now(), 0.045, 0.013, ScenarioInput{}); got.ForcedNotional != 0 {
		t.Errorf("nil rows: expected zero result, got %+v", got)
	}
	rows := []StrikeRow{{Strike: feed.EncodeStrike(5810), Side: feed.SideCall, DealerPos: 100, IV: 0.18, Delta: 0.5}}
	if got := Simulate(rows, 0, time.Now(), 0.045, 0.013, ScenarioInput{}); got.ForcedNotional != 0 {
		t.Errorf("zero spot: expected zero result, got %+v", got)
	}
}

// TestSimulator_ShortGammaSpotUp_ForcesBuy: short-gamma dealer + spot
// rises → dealer must BUY futures (chases the rally — this is what
// amplifies the move). ForcedNotional > 0.
func TestSimulator_ShortGammaSpotUp_ForcesBuy(t *testing.T) {
	now := time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC)
	expiry := uint32(now.Year()*10000 + int(now.Month())*100 + now.Day() + 1)
	// Negative call positions = dealer sold calls = short calls = short gamma.
	rows := buildPopulatedChain(5810, 0.18, expiry, now, -1000, 0)

	in := ScenarioInput{SpotPctChange: 0.005, DurationMinutes: 30, VolPtChange: 0}
	res := Simulate(rows, 5810, now, 0.045, 0.013, in)

	if res.ForcedNotional <= 0 {
		t.Errorf("short γ + spot up should force BUY (positive ForcedNotional), got %.0f", res.ForcedNotional)
	}
	if res.NewSpot <= 5810 {
		t.Errorf("NewSpot must be > 5810 after +0.5%% move, got %v", res.NewSpot)
	}
}

// TestSimulator_LongGammaSpotUp_ForcesSell: inverse — long-gamma dealer
// + spot rises → dealer SELLS to lock in delta gain (long γ stabilizes).
// ForcedNotional < 0.
func TestSimulator_LongGammaSpotUp_ForcesSell(t *testing.T) {
	now := time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC)
	expiry := uint32(now.Year()*10000 + int(now.Month())*100 + now.Day() + 1)
	// Positive call positions = dealer bought calls = long calls = long gamma.
	rows := buildPopulatedChain(5810, 0.18, expiry, now, +1000, 0)

	in := ScenarioInput{SpotPctChange: 0.005, DurationMinutes: 30, VolPtChange: 0}
	res := Simulate(rows, 5810, now, 0.045, 0.013, in)

	if res.ForcedNotional >= 0 {
		t.Errorf("long γ + spot up should force SELL (negative ForcedNotional), got %.0f", res.ForcedNotional)
	}
}

// TestSimulator_TopContributionsSorted verifies the per-strike list is
// sorted by |ForcedNotional| descending and capped.
func TestSimulator_TopContributionsSorted(t *testing.T) {
	now := time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC)
	expiry := uint32(now.Year()*10000 + int(now.Month())*100 + now.Day() + 1)
	rows := buildPopulatedChain(5810, 0.18, expiry, now, -500, 500)

	in := ScenarioInput{SpotPctChange: 0.01, DurationMinutes: 30, VolPtChange: 0}
	res := Simulate(rows, 5810, now, 0.045, 0.013, in)

	if len(res.TopContributions) == 0 {
		t.Fatal("expected non-empty TopContributions")
	}
	for i := 1; i < len(res.TopContributions); i++ {
		if absF(res.TopContributions[i-1].ForcedNotional) < absF(res.TopContributions[i].ForcedNotional) {
			t.Errorf("contributions not sorted desc at index %d: %v < %v",
				i, absF(res.TopContributions[i-1].ForcedNotional), absF(res.TopContributions[i].ForcedNotional))
		}
	}
	if len(res.TopContributions) > maxTopContributions {
		t.Errorf("contribution list exceeds cap: %d", len(res.TopContributions))
	}
}

// TestSimulator_VolShockProducesNonZero verifies a vol-only shock (no
// spot move, no time elapse) still produces a non-zero hedge demand
// via vanna sensitivity.
func TestSimulator_VolShockProducesNonZero(t *testing.T) {
	now := time.Date(2026, 5, 26, 13, 30, 0, 0, time.UTC)
	expiry := uint32(now.Year()*10000 + int(now.Month())*100 + now.Day() + 1)
	rows := buildPopulatedChain(5810, 0.18, expiry, now, -1000, 0)

	in := ScenarioInput{SpotPctChange: 0, DurationMinutes: 0.001, VolPtChange: 0.05}
	res := Simulate(rows, 5810, now, 0.045, 0.013, in)

	if res.ForcedNotional == 0 {
		t.Error("pure vol shock should still produce non-zero hedge demand via vanna")
	}
}

func TestSimulator_DurationYearsCalculation(t *testing.T) {
	rows := []StrikeRow{}
	res := Simulate(rows, 5810, time.Now(), 0.045, 0.013, ScenarioInput{DurationMinutes: 30})
	expected := 30.0 / minutesPerYearForCharm
	if math.Abs(res.DurationYears-expected) > 1e-12 {
		t.Errorf("DurationYears mismatch: got %v want %v", res.DurationYears, expected)
	}
}
