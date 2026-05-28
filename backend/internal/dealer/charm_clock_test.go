package dealer

import (
	"math"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

// sessionWindow returns a representative US equity session bounded at
// 09:30 - 16:00 ET (UTC-4 on the date used here).
func sessionWindow() (start, end time.Time) {
	loc := time.FixedZone("ET", -4*3600)
	start = time.Date(2026, 5, 26, 9, 30, 0, 0, loc)
	end = time.Date(2026, 5, 26, 16, 0, 0, 0, loc)
	return
}

func TestCharmClockWeakAtSessionOpen(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)

	now := start.Add(15 * time.Minute) // first hour
	zone := c.Classify(feed.SymbolSPX, 500_000, now)
	if zone != CharmZoneWeak {
		t.Fatalf("zone = %v, want WEAK", zone)
	}
}

func TestCharmClockRising(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)

	// Walk velocity from 1.5M to 4M over ~75 minutes after the WEAK window.
	base := start.Add(70 * time.Minute)
	step := 100_000.0
	v := 1_500_000.0
	var last CharmZone
	for i := 0; i < 25; i++ {
		ts := base.Add(time.Duration(i) * time.Minute)
		last = c.Classify(feed.SymbolSPX, v, ts)
		v += step
	}
	if last != CharmZoneRising {
		t.Fatalf("zone = %v, want RISING (final v=%.0f)", last, v-step)
	}
}

func TestCharmClockPeak(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)

	base := start.Add(90 * time.Minute)
	// Establish a session max of 7.5M, then sample 7.0M (within 25%).
	c.Classify(feed.SymbolSPX, 7_500_000, base)
	zone := c.Classify(feed.SymbolSPX, 7_000_000, base.Add(30*time.Second))
	if zone != CharmZonePeak {
		t.Fatalf("zone = %v, want PEAK", zone)
	}
}

func TestCharmClockFading(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)

	base := start.Add(120 * time.Minute)
	// Build up to 8M peak.
	v := 6_000_000.0
	for i := 0; i < 10; i++ {
		c.Classify(feed.SymbolSPX, v, base.Add(time.Duration(i)*time.Minute))
		v += 250_000
	}
	// v ended at 8.5M; force one explicit peak at 8M.
	c.Classify(feed.SymbolSPX, 8_000_000, base.Add(10*time.Minute))

	// Now decline to 5M over 8 minutes.
	v = 7_500_000.0
	var last CharmZone
	for i := 0; i < 8; i++ {
		ts := base.Add(time.Duration(11+i) * time.Minute)
		last = c.Classify(feed.SymbolSPX, v, ts)
		v -= 350_000
	}
	if last != CharmZoneFading {
		t.Fatalf("zone = %v, want FADING (final v=%.0f)", last, v+350_000)
	}
}

func TestCharmClockPin(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)

	now := end.Add(-15 * time.Minute) // inside the 30-min PIN window
	zone := c.Classify(feed.SymbolSPX, 4_000_000, now)
	if zone != CharmZonePin {
		t.Fatalf("zone = %v, want PIN", zone)
	}

	// PIN dominates regardless of velocity.
	if z := c.Classify(feed.SymbolSPX, 50_000, end.Add(-1*time.Minute)); z != CharmZonePin {
		t.Errorf("low-velocity end-of-day zone = %v, want PIN", z)
	}
}

func TestCharmClockStatePersistenceFadingTriggers(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)

	base := start.Add(2 * time.Hour) // well past WEAK window, well outside PIN

	// 30 successive samples ramping up — should land in PEAK with sessionMax
	// well above the peak floor.
	v := 2_000_000.0
	for i := 0; i < 30; i++ {
		ts := base.Add(time.Duration(i) * time.Minute)
		c.Classify(feed.SymbolSPX, v, ts)
		v += 200_000
	}

	// Confirm we hit PEAK at the top of the ramp.
	peakTs := base.Add(30 * time.Minute)
	if z := c.Classify(feed.SymbolSPX, 8_000_000, peakTs); z != CharmZonePeak {
		t.Fatalf("expected PEAK at ramp top, got %v", z)
	}

	// Drop the velocity well below the peak band to trigger FADING. We
	// check at i==1 (second drop sample) — by then the lookback (5
	// positions back) still points into the ramp, so the trend is
	// negative. Once the buffer is fully populated with drop samples
	// the trend goes flat and FADING gives way to the magnitude
	// fallback.
	dropTs := base.Add(35 * time.Minute)
	for i := 0; i < charmTrendLookback+2; i++ {
		ts := dropTs.Add(time.Duration(i) * time.Second)
		zone := c.Classify(feed.SymbolSPX, 4_000_000, ts)
		if i == 1 && zone != CharmZoneFading {
			t.Errorf("expected FADING right after decline started, got %v", zone)
		}
	}
}

func TestCharmClockBiasFor(t *testing.T) {
	c := NewCharmClockClassifier(time.Time{}, time.Time{})
	cases := []struct {
		zone   CharmZone
		regime Regime
		want   string
	}{
		{CharmZonePeak, RegimeShortGamma, "sell-into-rallies / buy-into-dips (mean-reverting forced flow)"},
		{CharmZonePeak, RegimeLongGamma, "volatility compression — favor mean-reversion"},
		{CharmZonePeak, RegimeNeutral, "neutral"},
		{CharmZonePeak, RegimeUnknown, "neutral"},
		{CharmZoneRising, RegimeShortGamma, "neutral"},
		{CharmZoneFading, RegimeLongGamma, "neutral"},
		{CharmZoneWeak, RegimeShortGamma, "neutral"},
		{CharmZonePin, RegimeShortGamma, "neutral"},
		{CharmZoneUnknown, RegimeUnknown, "neutral"},
	}
	for _, tc := range cases {
		if got := c.BiasFor(tc.zone, tc.regime); got != tc.want {
			t.Errorf("BiasFor(%v, %v) = %q, want %q", tc.zone, tc.regime, got, tc.want)
		}
	}
}

func TestCharmClockWindowSummary(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)

	base := start.Add(2 * time.Hour)
	for i := 0; i < 12; i++ {
		c.Classify(feed.SymbolSPX, float64(2_000_000+i*250_000), base.Add(time.Duration(i)*time.Minute))
	}

	sum := c.WindowSummary(feed.SymbolSPX)
	if sum.SessionMaxAbsVel < 4_000_000 {
		t.Errorf("SessionMaxAbsVel = %v, want >= 4M", sum.SessionMaxAbsVel)
	}
	if sum.CurrentZone == CharmZoneUnknown {
		t.Errorf("CurrentZone = UNKNOWN, want a real zone")
	}
}

func TestCharmClockWindowSummaryUnknownSymbol(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)
	sum := c.WindowSummary(feed.SymbolSPX)
	if sum.CurrentZone != CharmZoneUnknown || sum.SessionMaxAbsVel != 0 {
		t.Errorf("unknown-symbol summary not zeroed: %+v", sum)
	}
}

func TestCharmClockHandlesNaNVelocity(t *testing.T) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)
	now := start.Add(45 * time.Minute)
	// NaN should be treated as zero — caller cannot crash us.
	nan := math.NaN()
	zone := c.Classify(feed.SymbolSPX, nan, now)
	if zone != CharmZoneWeak {
		t.Errorf("NaN velocity zone = %v, want WEAK", zone)
	}
}

func BenchmarkCharmClockClassify(b *testing.B) {
	start, end := sessionWindow()
	c := NewCharmClockClassifier(start, end)
	base := start.Add(2 * time.Hour)
	// Prime with some history so the trend path runs.
	for i := 0; i < charmHistorySize; i++ {
		c.Classify(feed.SymbolSPX, float64(2_000_000+i*100_000), base.Add(time.Duration(i)*time.Second))
	}
	now := base.Add(time.Duration(charmHistorySize) * time.Second)
	v := 6_000_000.0

	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		c.Classify(feed.SymbolSPX, v, now)
	}
}
