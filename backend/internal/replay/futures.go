// futures.go reconstructs front-month CME equity-index futures contract
// symbols from (FlowGreeks symbol, event timestamp) for replay reads
// against the ticks hypertable, which doesn't carry the contract column.
//
// Convention: the front contract is the earliest quarterly (H/M/U/Z =
// Mar/Jun/Sep/Dec) whose third-Friday expiry is strictly after `ts.Date()`.
// On the expiry day itself we already advance, mirroring CME equity-index
// futures' 09:30 ET final-settlement convention.
//
// FlowGreeks symbol → CME root mapping:
//   SPX → ES (E-mini S&P 500)
//   NDX → NQ (E-mini Nasdaq 100)
//
// Returns "" for unknown symbols. Year digit follows CME's single-digit
// convention; 2026 → '6', 2027 → '7', etc.
package replay

import (
	"time"

	"flowgreeks/internal/feed"
)

var quarterlyMonths = [4]time.Month{time.March, time.June, time.September, time.December}
var quarterlyCodes = [4]byte{'H', 'M', 'U', 'Z'}

// FrontMonthContract returns the CME front-month contract symbol active
// on `ts` for the given FlowGreeks symbol. Empty string for unknown
// symbols.
func FrontMonthContract(sym feed.Symbol, ts time.Time) string {
	var root string
	switch sym {
	case feed.SymbolSPX:
		root = "ES"
	case feed.SymbolNDX:
		root = "NQ"
	default:
		return ""
	}

	tsUTC := ts.UTC()
	year := tsUTC.Year()
	// Bounded loop: 4 quarterlies × <=2 years is enough headroom.
	for cycle := 0; cycle < 8; cycle++ {
		for i, m := range quarterlyMonths {
			expiry := thirdFridayUTC(year, m)
			if expiry.After(tsUTC) {
				yearDigit := byte('0' + (year % 10))
				return string([]byte{root[0], root[1], quarterlyCodes[i], yearDigit})
			}
		}
		year++
	}
	return ""
}

// thirdFridayUTC returns the third Friday of (year, month) at 00:00 UTC.
// CME equity-index futures cash-settle on this date.
func thirdFridayUTC(year int, month time.Month) time.Time {
	first := time.Date(year, month, 1, 0, 0, 0, 0, time.UTC)
	weekday := int(first.Weekday()) // Sun=0, ..., Fri=5, Sat=6
	offset := (5 - weekday + 7) % 7
	day := 1 + offset + 14
	return time.Date(year, month, day, 0, 0, 0, 0, time.UTC)
}
