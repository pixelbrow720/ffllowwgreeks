// Charm Clock zone classifier — COMPUTE_MODEL.md §6.
//
// Maps an aggregated charm velocity (delta/min) into one of five intraday
// regime zones (WEAK, RISING, PEAK, FADING, PIN). Stateful: tracks a rolling
// 30-sample velocity history per symbol plus the session-running max so that
// PEAK / FADING decisions can reference where we have already been.
package dealer

import (
	"math"
	"sync"
	"time"

	"flowgreeks/internal/feed"
)

// Zone thresholds per COMPUTE_MODEL.md §6 table. Velocities are in
// delta/minute units; caller is responsible for the aggregation. The
// `Default*` values are the spec defaults — the live thresholds are
// per-classifier and overridable via SetVelocityThresholds so the
// offline calibrate tool can fit them against the historical archive.
const (
	DefaultCharmWeakVelocityCeiling = 1_000_000.0
	DefaultCharmPeakVelocityFloor   = 5_000_000.0

	charmPeakBandFraction = 0.75 // ±25% of session max
	charmPinWindow        = 30 * time.Minute
	charmWeakWindow       = time.Hour
	charmHistorySize      = 30
	charmTrendLookback    = 5
	charmTrendThreshold   = 0.02 // 2% relative move to register a trend
)

// WindowSummary is the per-symbol view returned by WindowSummary.
// SessionMaxAbsVel is the running max of |charmVelocity| observed within the
// session window. TimeInZone is the duration since CurrentZone was entered.
// NextZoneETA is a slope-extrapolated estimate of when the next zone is
// likely to activate; zero when there is no usable trend.
type WindowSummary struct {
	CurrentZone      CharmZone
	SessionMaxAbsVel float64
	TimeInZone       time.Duration
	NextZoneETA      time.Duration
}

// charmSample is one entry in the per-symbol ring buffer.
type charmSample struct {
	ts  time.Time
	vel float64 // |charm velocity|
}

// charmSymbolState is the per-symbol rolling state.
type charmSymbolState struct {
	samples     [charmHistorySize]charmSample
	idx         int // next write position
	count       int // populated entries (capped at charmHistorySize)
	sessionMax  float64
	currentZone CharmZone
	zoneStart   time.Time
	lastTs      time.Time
}

// CharmClockClassifier maps aggregated charm velocity to a zone, holding
// rolling history per symbol. Safe for concurrent use across symbols.
//
// weakVelocityCeiling and peakVelocityFloor are per-instance so the
// offline calibrate tool can fit them against the historical archive
// without touching the package-level constants. Both are guarded by
// `mu` and read inside classifyLocked under the same lock.
type CharmClockClassifier struct {
	mu                  sync.RWMutex
	sessionStart        time.Time
	sessionEnd          time.Time
	weakVelocityCeiling float64
	peakVelocityFloor   float64
	states              map[feed.Symbol]*charmSymbolState
}

// NewCharmClockClassifier constructs a classifier bound to the given session
// window. Both bounds are inclusive in the sense that session-max updates
// occur for now in [sessionStart, sessionEnd]. Velocity thresholds are
// seeded from the spec defaults and may be overridden via
// SetVelocityThresholds after construction.
func NewCharmClockClassifier(sessionStart, sessionEnd time.Time) *CharmClockClassifier {
	return &CharmClockClassifier{
		sessionStart:        sessionStart,
		sessionEnd:          sessionEnd,
		weakVelocityCeiling: DefaultCharmWeakVelocityCeiling,
		peakVelocityFloor:   DefaultCharmPeakVelocityFloor,
		states:              make(map[feed.Symbol]*charmSymbolState),
	}
}

// SetSessionBounds updates the session window. Idempotent and safe to
// call every aggregator iteration; replay drives these from event time
// so the window tracks the historical day instead of "today" baked in
// at construction.
func (c *CharmClockClassifier) SetSessionBounds(sessionStart, sessionEnd time.Time) {
	c.mu.Lock()
	c.sessionStart = sessionStart
	c.sessionEnd = sessionEnd
	c.mu.Unlock()
}

// SetVelocityThresholds replaces the WEAK / RISING / PEAK velocity
// boundaries. Both values are applied only when strictly positive AND
// weakCeiling < peakFloor (a non-monotonic pair would silently swap
// zones). Existing rolling state is preserved so a SIGHUP reload
// doesn't reset session max.
func (c *CharmClockClassifier) SetVelocityThresholds(weakCeiling, peakFloor float64) {
	if weakCeiling <= 0 || peakFloor <= 0 || !(weakCeiling < peakFloor) {
		return
	}
	c.mu.Lock()
	c.weakVelocityCeiling = weakCeiling
	c.peakVelocityFloor = peakFloor
	c.mu.Unlock()
}

// Classify returns the current charm zone for symbol given charmVelocity
// (signed delta/min) and the current wall-clock time. The classifier
// tracks rolling history and session max as a side effect; repeated calls
// are intended.
func (c *CharmClockClassifier) Classify(symbol feed.Symbol, charmVelocity float64, now time.Time) CharmZone {
	abs := math.Abs(charmVelocity)
	if math.IsNaN(abs) || math.IsInf(abs, 0) {
		abs = 0
	}

	c.mu.Lock()
	s := c.states[symbol]
	if s == nil {
		s = &charmSymbolState{currentZone: CharmZoneUnknown, zoneStart: now}
		c.states[symbol] = s
	}

	if !now.Before(c.sessionStart) && !now.After(c.sessionEnd) {
		if abs > s.sessionMax {
			s.sessionMax = abs
		}
	}

	s.samples[s.idx] = charmSample{ts: now, vel: abs}
	s.idx++
	if s.idx == charmHistorySize {
		s.idx = 0
	}
	if s.count < charmHistorySize {
		s.count++
	}
	s.lastTs = now

	zone := c.classifyLocked(s, abs, now)
	if zone != s.currentZone {
		s.currentZone = zone
		s.zoneStart = now
	}
	c.mu.Unlock()
	return zone
}

// classifyLocked picks the zone given fully updated rolling state. Caller
// must hold c.mu.
func (c *CharmClockClassifier) classifyLocked(s *charmSymbolState, abs float64, now time.Time) CharmZone {
	weakCeiling := c.weakVelocityCeiling
	peakFloor := c.peakVelocityFloor

	// PIN dominates: any velocity in the last 30 min before close.
	timeToClose := c.sessionEnd.Sub(now)
	if timeToClose > 0 && timeToClose < charmPinWindow {
		return CharmZonePin
	}

	inPeakBand := abs > peakFloor &&
		s.sessionMax > 0 &&
		abs >= charmPeakBandFraction*s.sessionMax
	if inPeakBand {
		return CharmZonePeak
	}

	trend := trendOf(s)

	// FADING: was at peak earlier in session, now declining and below peak band.
	if trend < 0 &&
		s.sessionMax >= peakFloor &&
		abs < charmPeakBandFraction*s.sessionMax {
		return CharmZoneFading
	}

	sinceOpen := now.Sub(c.sessionStart)

	// WEAK: first hour AND velocity < weakCeiling.
	if sinceOpen >= 0 && sinceOpen < charmWeakWindow && abs < weakCeiling {
		return CharmZoneWeak
	}

	// RISING: velocity rising AND in [weakCeiling, peakFloor).
	if trend > 0 && abs >= weakCeiling && abs < peakFloor {
		return CharmZoneRising
	}

	// Magnitude fallbacks so the classifier always returns one of the 5 zones.
	if abs < weakCeiling {
		return CharmZoneWeak
	}
	if abs < peakFloor {
		return CharmZoneRising
	}
	return CharmZonePeak
}

// trendOf returns +1 for rising, -1 for declining, 0 for flat / insufficient
// history. Compares the last sample to the one charmTrendLookback positions
// back, requiring a relative move of at least charmTrendThreshold.
func trendOf(s *charmSymbolState) int {
	if s.count < charmTrendLookback+1 {
		return 0
	}
	lastIdx := s.idx - 1
	if lastIdx < 0 {
		lastIdx += charmHistorySize
	}
	prevIdx := lastIdx - charmTrendLookback
	if prevIdx < 0 {
		prevIdx += charmHistorySize
	}
	last := s.samples[lastIdx].vel
	prev := s.samples[prevIdx].vel
	delta := last - prev
	thresh := charmTrendThreshold * prev
	if thresh < 1 {
		thresh = 1 // tiny floor: avoid noise on near-zero baselines
	}
	if delta > thresh {
		return 1
	}
	if delta < -thresh {
		return -1
	}
	return 0
}

// BiasFor returns the directional-bias text for the given zone × regime
// combination per COMPUTE_MODEL.md §6 "Direction bias".
func (c *CharmClockClassifier) BiasFor(zone CharmZone, regime Regime) string {
	if zone == CharmZonePeak {
		switch regime {
		case RegimeShortGamma:
			return "sell-into-rallies / buy-into-dips (mean-reverting forced flow)"
		case RegimeLongGamma:
			return "volatility compression — favor mean-reversion"
		}
	}
	return "neutral"
}

// WindowSummary returns a snapshot of the rolling state for symbol. Zero
// values are returned when the symbol has not yet been classified.
func (c *CharmClockClassifier) WindowSummary(symbol feed.Symbol) WindowSummary {
	c.mu.RLock()
	defer c.mu.RUnlock()

	s, ok := c.states[symbol]
	if !ok {
		return WindowSummary{}
	}
	out := WindowSummary{
		CurrentZone:      s.currentZone,
		SessionMaxAbsVel: s.sessionMax,
	}
	if !s.zoneStart.IsZero() && !s.lastTs.IsZero() {
		if d := s.lastTs.Sub(s.zoneStart); d > 0 {
			out.TimeInZone = d
		}
	}
	out.NextZoneETA = nextZoneETA(c, s)
	return out
}

// nextZoneETA does a linear extrapolation against the relevant threshold for
// the current zone and returns the projected duration until the next zone
// would activate. Returns 0 when the projection is not meaningful (no slope,
// wrong sign, or no obvious target).
func nextZoneETA(c *CharmClockClassifier, s *charmSymbolState) time.Duration {
	if s.count < charmTrendLookback+1 {
		return 0
	}
	lastIdx := s.idx - 1
	if lastIdx < 0 {
		lastIdx += charmHistorySize
	}
	prevIdx := lastIdx - charmTrendLookback
	if prevIdx < 0 {
		prevIdx += charmHistorySize
	}
	last := s.samples[lastIdx]
	prev := s.samples[prevIdx]
	dt := last.ts.Sub(prev.ts).Seconds()
	if dt <= 0 {
		return 0
	}
	slope := (last.vel - prev.vel) / dt // per-second velocity change

	switch s.currentZone {
	case CharmZoneWeak, CharmZoneRising:
		if slope <= 0 {
			return 0
		}
		target := c.peakVelocityFloor
		if last.vel >= target {
			return 0
		}
		secs := (target - last.vel) / slope
		return time.Duration(secs * float64(time.Second))
	case CharmZonePeak:
		if slope >= 0 {
			return 0
		}
		target := charmPeakBandFraction * s.sessionMax
		if last.vel <= target {
			return 0
		}
		secs := (target - last.vel) / slope
		if secs <= 0 {
			return 0
		}
		return time.Duration(secs * float64(time.Second))
	case CharmZoneFading:
		// Fading flows toward PIN by wall-clock, not velocity.
		eta := c.sessionEnd.Add(-charmPinWindow).Sub(last.ts)
		if eta <= 0 {
			return 0
		}
		return eta
	default:
		return 0
	}
}
