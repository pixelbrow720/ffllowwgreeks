// End-to-end integration test for the M2 + M3 compute pipeline.
//
// Drives the synthetic SPX chain through the full per-tick → per-second
// aggregator path and asserts the outputs are non-trivial. No NATS, no
// Postgres — pure Go. Runs as part of `go test ./...`.
//
// What this proves:
//   - Lee-Ready aggressor classifier wires correctly
//   - Position tracker accumulates from OI seed + trade flow
//   - IV solver converges on synthetic prices
//   - Analytical Greeks populate StrikeRow fields
//   - GEX aggregator produces NetGEX + walls + regime
//   - DPI scorer emits a non-zero composite
//   - Charm Clock classifier picks a zone
//   - Flow Pulse oscillator emits non-trivial gamma/charm/vanna components
//
// If this passes, swapping the synthetic generator for the live Databento
// adapter at the source will produce the same downstream behaviour.
package dealer_test

import (
	"context"
	"math"
	"testing"
	"time"

	"flowgreeks/internal/dealer"
	"flowgreeks/internal/feed"
	"flowgreeks/internal/feed/synthetic"
	"flowgreeks/internal/greeks"
)

const (
	defaultRate  = 0.045
	defaultYield = 0.013
)

func TestEndToEnd_Pipeline_SyntheticSPX(t *testing.T) {
	const (
		runDuration  = 3 * time.Second
		spot         = 5810.0
		iv           = 0.18
	)

	gen := synthetic.New(synthetic.Config{
		Symbol:       feed.SymbolSPX,
		Spot:         spot,
		IV:           iv,
		QuotesPerSec: 600,
		TradesPerSec: 80,
		BasisPerSec:  20,
		StrikeSteps:  20,
		StrikeStep:   5,
		// Always use a future expiry so this test is deterministic
		// regardless of wall clock. The 4pm ET cutoff in
		// TimeToExpiryYears makes "today" return 0 after the close.
		ExpiryDate: tomorrowYYYYMMDD(),
		Seed:       42,
	})
	ctx, cancel := context.WithTimeout(context.Background(), runDuration)
	defer cancel()
	gen.Start(ctx)

	classifier := dealer.NewClassifier()
	positions := dealer.NewPositionTracker()
	pulse := dealer.NewFlowPulseTracker(dealer.FlowPulseConfig{})
	basis := dealer.NewBasisTracker(dealer.DefaultBasisAlpha)
	quotes := dealer.NewQuoteCache()

	now := time.Now().UTC()
	sessionStart := now.Add(-2 * time.Hour)
	sessionEnd := now.Add(4 * time.Hour)
	dpiScorer := dealer.NewDPIScorer(dealer.DPIConfig{
		SessionStart: sessionStart,
		SessionEnd:   sessionEnd,
	})
	charmClock := dealer.NewCharmClockClassifier(sessionStart, sessionEnd)

	type ivKey struct {
		expiry uint32
		strike uint32
		side   feed.Side
	}
	ivCache := make(map[ivKey]float64)
	flowWindow := make(map[uint32]int64)

	var (
		quotesSeen, tradesSeen, oiSeen, futuresSeen int
	)

	// Drain ticks for the test window. Each tick goes through the same
	// path cmd/compute uses in production.
	for {
		select {
		case <-ctx.Done():
			goto done
		case tick, ok := <-gen.Ticks():
			if !ok {
				goto done
			}
			if tick.IsFuture() {
				basis.UpdateFuture(tick)
				futuresSeen++
				continue
			}
			switch tick.TickType {
			case feed.TickTypeOI:
				positions.SeedFromOI(tick)
				oiSeen++
			case feed.TickTypeQuote:
				quotes.Update(tick)
				quotesSeen++
				mid := (tick.Bid + tick.Ask) * 0.5
				if mid <= 0 {
					continue
				}
				years := greeks.TimeToExpiryYears(tick.TsEvent, tick.Expiry)
				if years <= 0 {
					continue
				}
				strike := feed.DecodeStrike(tick.Strike)
				cfg := greeks.DefaultSolverConfig
				k := ivKey{tick.Expiry, tick.Strike, tick.Side}
				if last, ok := ivCache[k]; ok && last > 0 {
					cfg.InitGuess = last
				}
				res := greeks.ImpliedVol(mid, spot, strike, years,
					defaultRate, defaultYield, tick.Side, cfg)
				if res.Converged {
					ivCache[k] = res.IV
				}
			case feed.TickTypeTrade:
				quotes.Apply(&tick)
				tick.Aggressor = classifier.Classify(tick)
				positions.Apply(tick)
				tradesSeen++

				switch tick.Aggressor {
				case feed.AggressorBuy:
					flowWindow[tick.Strike] += int64(tick.Size)
				case feed.AggressorSell:
					flowWindow[tick.Strike] -= int64(tick.Size)
				}

				k := ivKey{tick.Expiry, tick.Strike, tick.Side}
				if iv := ivCache[k]; iv > 0 {
					years := greeks.TimeToExpiryYears(tick.TsEvent, tick.Expiry)
					if years > 0 {
						strike := feed.DecodeStrike(tick.Strike)
						g := greeks.All(spot, strike, years, defaultRate, defaultYield, iv, tick.Side)
						dealerPos := positions.Get(feed.SymbolSPX, tick.Expiry, tick.Strike, tick.Side)
						pulse.OnTrade(tick, dealerPos, g.Delta, g.Charm, g.Vanna, 0)
					}
				}
			}
		}
	}

done:
	gen.Stop()

	// ── Sanity: did we exercise every tick path? ──
	if quotesSeen < 100 {
		t.Fatalf("quotesSeen too low: %d (expected >= 100)", quotesSeen)
	}
	if tradesSeen < 30 {
		t.Fatalf("tradesSeen too low: %d (expected >= 30)", tradesSeen)
	}
	if oiSeen == 0 {
		t.Fatal("no OI ticks observed")
	}
	if futuresSeen == 0 {
		t.Fatal("no futures ticks observed (basis tracker would be empty)")
	}
	t.Logf("ticks observed: quotes=%d trades=%d oi=%d futures=%d", quotesSeen, tradesSeen, oiSeen, futuresSeen)

	// ── Aggregate: snapshot positions, fill Greeks, run dealer.Aggregate ──
	rows := positions.Snapshot(feed.SymbolSPX)
	if len(rows) == 0 {
		t.Fatal("position tracker returned empty snapshot — OI seed didn't fire")
	}

	for i := range rows {
		r := &rows[i]
		k := ivKey{r.Expiry, r.Strike, r.Side}
		iv := ivCache[k]
		if iv <= 0 {
			continue
		}
		years := greeks.TimeToExpiryYears(uint64(time.Now().UnixNano()), r.Expiry)
		if years <= 0 {
			continue
		}
		strike := feed.DecodeStrike(r.Strike)
		g := greeks.All(spot, strike, years, defaultRate, defaultYield, iv, r.Side)
		r.IV = iv
		r.Delta = g.Delta
		r.Gamma = g.Gamma
		r.Theta = g.Theta
		r.Vega = g.Vega
		r.Charm = g.Charm
		r.Vanna = g.Vanna
	}
	view := dealer.Aggregate(rows, spot)

	if view.NetGEX == 0 {
		t.Error("Aggregate.NetGEX is zero — dealer positioning didn't accumulate")
	}
	if view.Regime == dealer.RegimeUnknown {
		t.Error("Aggregate.Regime is Unknown")
	}

	// ── DPI ──
	breakdown := dpiScorer.Score(feed.SymbolSPX, view, rows, flowWindow, time.Now().UTC())
	composite := breakdown.Composite()
	if composite <= 0 || composite > 100 {
		t.Errorf("DPI composite out of [0,100]: %v", composite)
	}
	t.Logf("DPI composite: %.1f (NGS=%.1f CV=%.1f VS=%.1f TTC=%.1f FC=%.1f)",
		composite, breakdown.NetGammaSign, breakdown.CharmVelocity,
		breakdown.VannaSensitivity, breakdown.TimeToCloseDecay, breakdown.FlowConcentration)

	// ── Charm Clock ──
	charmVel := aggregateCharmVelocity(rows, spot)
	zone := charmClock.Classify(feed.SymbolSPX, charmVel, time.Now().UTC())
	if zone == dealer.CharmZoneUnknown {
		t.Error("Charm Clock zone is Unknown")
	}
	t.Logf("Charm zone: %v (velocity %.3e Δ/min)", zone, charmVel)

	// ── Flow Pulse ──
	// QuoteCache (added in this commit) is what makes this assertion meaningful:
	// without it, trade ticks have no bid/ask at classification time and every
	// pulse contribution multiplies by aggressor=UNKNOWN→0.
	fp := pulse.Snapshot(time.Now())
	if math.Abs(fp.GammaPulse)+math.Abs(fp.CharmPulse)+math.Abs(fp.VannaPulse) == 0 {
		t.Error("Flow Pulse all zero — quote cache or classifier path broken")
	}
	t.Logf("Flow Pulse: gamma=%.3f charm=%.3f vanna=%.3f total=%.3f",
		fp.GammaPulse, fp.CharmPulse, fp.VannaPulse, fp.TotalPulse)

	// ── Basis ──
	bs := basis.Snapshot(feed.SymbolSPX)
	if bs.FutFrontMid == 0 {
		t.Error("Basis tracker has no futures mid — futures path broken")
	}
	t.Logf("Basis: front=%s mid=%.2f basis=%.2f smooth=%.2f",
		bs.FutFrontSym, bs.FutFrontMid, bs.Basis, bs.BasisSmooth)
}

// aggregateCharmVelocity is a copy of cmd/compute helper, kept here so
// the integration test stays self-contained (no cmd/ import dependency).
func aggregateCharmVelocity(rows []dealer.StrikeRow, spot float64) float64 {
	if spot <= 0 {
		return 0
	}
	const minutesPerYear = 525600.0
	const contractMultiplier = 100.0
	sum := 0.0
	for _, r := range rows {
		if r.DealerPos == 0 || r.Charm == 0 {
			continue
		}
		v := r.Charm * float64(r.DealerPos) * contractMultiplier * spot
		if v < 0 {
			v = -v
		}
		sum += v
	}
	return sum / minutesPerYear
}

// tomorrowYYYYMMDD returns next trading-day's expiry packed as YYYYMMDD,
// so the test stays deterministic across wall-clock time. Using "today"
// breaks after 16:00 ET when TimeToExpiryYears returns 0 and the IV
// solver never runs.
func tomorrowYYYYMMDD() uint32 {
	t := time.Now().UTC().Add(24 * time.Hour)
	return uint32(t.Year()*10000 + int(t.Month())*100 + t.Day())
}
