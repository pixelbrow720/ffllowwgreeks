package dealer

import (
	"math"
	"sort"

	"flowgreeks/internal/feed"
)

const (
	contractMultiplier  = 100.0
	regimeGEXThreshold  = 5e8 // $500M for SPX; tunable later
	daysPerYear         = 365.25
)

// AggregateView is the computed GEX / regime slice produced by Aggregate.
// The caller folds these scalar fields into AggregateState; the strike
// matrix mutations (GEXNotional populated, slice reordered by strike) are
// applied in-place to the rows passed in.
type AggregateView struct {
	NetGEX     float64
	ZeroGamma  float64
	CallWall   float64
	PutWall    float64
	ExpectedMv float64
	Regime     Regime
}

// Aggregate populates rows[i].GEXNotional in-place and returns the
// derived aggregate view. See docs/COMPUTE_MODEL.md §4.
//
// Mutations on rows:
//   - GEXNotional set on every row
//   - slice reordered by Strike ascending (used for the cumulative walk)
func Aggregate(rows []StrikeRow, spot float64) AggregateView {
	var view AggregateView
	if len(rows) == 0 {
		return view
	}

	spotSq := spot * spot

	var netGEX float64
	for i := range rows {
		dGamma := float64(rows[i].DealerPos) * rows[i].Gamma * contractMultiplier
		rows[i].GEXNotional = dGamma * spotSq * 0.01
		netGEX += rows[i].GEXNotional
	}
	view.NetGEX = netGEX

	switch {
	case netGEX > regimeGEXThreshold:
		view.Regime = RegimeLongGamma
	case netGEX < -regimeGEXThreshold:
		view.Regime = RegimeShortGamma
	default:
		view.Regime = RegimeNeutral
	}

	// Expected 1-day move from the call strike closest to spot with a
	// usable IV. ATM call is the conventional pick (delta ≈ 0.5).
	spotEnc := feed.EncodeStrike(spot)
	var atmDist uint32 = math.MaxUint32
	var atmIV float64
	for i := range rows {
		if rows[i].Side != feed.SideCall || rows[i].IV <= 0 {
			continue
		}
		var d uint32
		if rows[i].Strike >= spotEnc {
			d = rows[i].Strike - spotEnc
		} else {
			d = spotEnc - rows[i].Strike
		}
		if d < atmDist {
			atmDist = d
			atmIV = rows[i].IV
		}
	}
	if atmIV > 0 {
		view.ExpectedMv = atmIV * math.Sqrt(1.0/daysPerYear) * 100
	}

	sort.Slice(rows, func(i, j int) bool { return rows[i].Strike < rows[j].Strike })

	var (
		cumulative     float64
		prevCumulative float64
		prevStrikeEnc  uint32
		firstStrike    = true
		zeroGammaFound bool

		callWallGamma  float64
		callWallStrike uint32
		putWallGamma   float64
		putWallStrike  uint32
	)

	for i := 0; i < len(rows); {
		strikeEnc := rows[i].Strike
		var strikeGEX, callGamma, putGamma float64
		for i < len(rows) && rows[i].Strike == strikeEnc {
			strikeGEX += rows[i].GEXNotional
			dGamma := float64(rows[i].DealerPos) * rows[i].Gamma * contractMultiplier
			switch rows[i].Side {
			case feed.SideCall:
				callGamma += dGamma
			case feed.SidePut:
				putGamma += dGamma
			}
			i++
		}

		if callGamma < callWallGamma {
			callWallGamma = callGamma
			callWallStrike = strikeEnc
		}
		if putGamma > putWallGamma {
			putWallGamma = putGamma
			putWallStrike = strikeEnc
		}

		newCumulative := cumulative + strikeGEX
		if !zeroGammaFound && !firstStrike {
			switch {
			case prevCumulative > 0 && newCumulative < 0,
				prevCumulative < 0 && newCumulative > 0:
				ratio := -prevCumulative / (newCumulative - prevCumulative)
				prevPrice := feed.DecodeStrike(prevStrikeEnc)
				strikePrice := feed.DecodeStrike(strikeEnc)
				view.ZeroGamma = prevPrice + ratio*(strikePrice-prevPrice)
				zeroGammaFound = true
			case newCumulative == 0:
				view.ZeroGamma = feed.DecodeStrike(strikeEnc)
				zeroGammaFound = true
			}
		}
		prevStrikeEnc = strikeEnc
		prevCumulative = newCumulative
		cumulative = newCumulative
		firstStrike = false
	}

	if callWallGamma < 0 {
		view.CallWall = feed.DecodeStrike(callWallStrike)
	}
	if putWallGamma > 0 {
		view.PutWall = feed.DecodeStrike(putWallStrike)
	}
	return view
}
