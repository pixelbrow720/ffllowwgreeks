package backtest

import (
	"math"
	"strings"

	"flowgreeks/internal/alerts"
)

// alertsRuleMatches mirrors alerts.evaluate without the cooldown +
// trigger-construction overhead. Kept here so the backtest package can
// reuse a saved Rule's logic without depending on the engine's stateful
// firing semantics.
//
// NaN inputs short-circuit to false: every comparison against NaN is
// false, so a rule firing rate computed against a corrupted state
// would silently drop to 0 instead of erroring. Caller decides whether
// to treat NaN as a missing data condition or a hard failure.
func alertsRuleMatches(r alerts.Rule, s alerts.Snapshot) bool {
	if r.Symbol != s.Symbol {
		return false
	}
	switch r.Kind {
	case alerts.RuleDPIAbove:
		if math.IsNaN(s.DPI.Composite) {
			return false
		}
		return s.DPI.Composite > r.Threshold
	case alerts.RuleDPIBelow:
		if math.IsNaN(s.DPI.Composite) {
			return false
		}
		return s.DPI.Composite < r.Threshold
	case alerts.RuleNetGEXAbove:
		if math.IsNaN(s.NetGEX) {
			return false
		}
		return s.NetGEX > r.Threshold
	case alerts.RuleNetGEXBelow:
		if math.IsNaN(s.NetGEX) {
			return false
		}
		return s.NetGEX < r.Threshold
	case alerts.RuleCharmZone:
		return zoneCode(r.StringArg) == s.CharmZone
	case alerts.RuleRegime:
		return regimeCode(r.StringArg) == s.Regime
	case alerts.RulePinProb:
		if math.IsNaN(s.Pin.TopProbability) {
			return false
		}
		return s.Pin.Active && s.Pin.TopProbability > r.Threshold
	}
	return false
}

func zoneCode(label string) uint8 {
	switch strings.ToUpper(strings.TrimSpace(label)) {
	case "WEAK", "1":
		return 1
	case "RISING", "2":
		return 2
	case "PEAK", "3":
		return 3
	case "FADING", "4":
		return 4
	case "PIN", "5":
		return 5
	}
	return 0
}

func regimeCode(label string) uint8 {
	switch strings.ToUpper(strings.TrimSpace(label)) {
	case "SHORT", "SHORT_GAMMA", "1":
		return 1
	case "LONG", "LONG_GAMMA", "2":
		return 2
	case "NEUTRAL", "3":
		return 3
	}
	return 0
}
