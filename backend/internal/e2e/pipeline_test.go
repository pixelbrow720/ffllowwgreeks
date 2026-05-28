// Package e2e holds black-box pipeline integration tests that exercise
// multiple internal packages together: synthetic ticks → dealer compute
// → alerts engine → backtest engine. Lives in its own package so it
// can import everything without creating cycles inside dealer.
//
// No networking. No goroutines beyond what the synthetic generator
// owns. Runs as part of `go test ./...`.
package e2e

import (
	"context"
	"encoding/json"
	"math"
	"testing"
	"time"

	"flowgreeks/internal/alerts"
	"flowgreeks/internal/backtest"
	"flowgreeks/internal/dealer"
	"flowgreeks/internal/feed"
	"flowgreeks/internal/feed/synthetic"
	"flowgreeks/internal/greeks"
)

const (
	defaultRate  = 0.045
	defaultYield = 0.013
)

// pipeState carries the running per-symbol pipeline used to drive the
// e2e tests. Mirrors cmd/compute's Pipeline shape but with no
// concurrency overhead — tests own a single goroutine.
type pipeState struct {
	symbol      feed.Symbol
	classifier  *dealer.Classifier
	positions   *dealer.PositionTracker
	pulse       *dealer.FlowPulseTracker
	quotes      *dealer.QuoteCache
	basis       *dealer.BasisTracker
	dpiScorer   *dealer.DPIScorer
	charmClock  *dealer.CharmClockClassifier
	ivCache     map[ivKey]float64
	flow5min    map[uint32]int64
	spot        float64
	sessionStart time.Time
	sessionEnd   time.Time
}

type ivKey struct {
	expiry uint32
	strike uint32
	side   feed.Side
}

func newPipeState(sym feed.Symbol, sessionStart, sessionEnd time.Time, spot float64) *pipeState {
	return &pipeState{
		symbol:     sym,
		classifier: dealer.NewClassifier(),
		positions:  dealer.NewPositionTracker(),
		pulse:      dealer.NewFlowPulseTracker(dealer.FlowPulseConfig{}),
		quotes:     dealer.NewQuoteCache(),
		basis:      dealer.NewBasisTracker(dealer.DefaultBasisAlpha),
		dpiScorer:  dealer.NewDPIScorer(dealer.DPIConfig{SessionStart: sessionStart, SessionEnd: sessionEnd}),
		charmClock: dealer.NewCharmClockClassifier(sessionStart, sessionEnd),
		ivCache:    make(map[ivKey]float64),
		flow5min:   make(map[uint32]int64),
		spot:       spot,
		sessionStart: sessionStart,
		sessionEnd: sessionEnd,
	}
}

func (p *pipeState) handleTick(t feed.Tick) {
	if t.IsFuture() {
		p.basis.UpdateFuture(t)
		return
	}
	if !t.IsOption() {
		return
	}
	switch t.TickType {
	case feed.TickTypeOI:
		p.positions.SeedFromOI(t)
	case feed.TickTypeQuote:
		p.quotes.Update(t)
		mid := (t.Bid + t.Ask) * 0.5
		if mid <= 0 {
			return
		}
		years := greeks.TimeToExpiryYears(t.TsEvent, t.Expiry)
		if years <= 0 {
			return
		}
		strike := feed.DecodeStrike(t.Strike)
		cfg := greeks.DefaultSolverConfig
		k := ivKey{t.Expiry, t.Strike, t.Side}
		if last, ok := p.ivCache[k]; ok && last > 0 {
			cfg.InitGuess = last
		}
		res := greeks.ImpliedVol(mid, p.spot, strike, years,
			defaultRate, defaultYield, t.Side, cfg)
		if res.Converged {
			p.ivCache[k] = res.IV
		}
	case feed.TickTypeTrade:
		p.quotes.Apply(&t)
		t.Aggressor = p.classifier.Classify(t)
		p.positions.Apply(t)
		switch t.Aggressor {
		case feed.AggressorBuy:
			p.flow5min[t.Strike] += int64(t.Size)
		case feed.AggressorSell:
			p.flow5min[t.Strike] -= int64(t.Size)
		}
		k := ivKey{t.Expiry, t.Strike, t.Side}
		if iv := p.ivCache[k]; iv > 0 {
			years := greeks.TimeToExpiryYears(t.TsEvent, t.Expiry)
			if years > 0 {
				strike := feed.DecodeStrike(t.Strike)
				g := greeks.All(p.spot, strike, years, defaultRate, defaultYield, iv, t.Side)
				dealerPos := p.positions.Get(p.symbol, t.Expiry, t.Strike, t.Side)
				p.pulse.OnTrade(t, dealerPos, g.Delta, g.Charm, g.Vanna, 0)
			}
		}
	}
}

// snapshot computes the per-second aggregator output: walls, regime,
// DPI, charm zone, flow pulse. Returns the wire JSON shape (alerts.
// Snapshot) so it feeds directly into both alerts and backtest.
func (p *pipeState) snapshot(now time.Time) (alerts.Snapshot, dealer.AggregateView) {
	bs := p.basis.Snapshot(p.symbol)
	if bs.FutFrontMid > 0 && bs.BasisSmooth != 0 {
		p.spot = bs.FutFrontMid - bs.BasisSmooth
	}
	rows := p.positions.Snapshot(p.symbol)
	if len(rows) == 0 {
		return alerts.Snapshot{Symbol: p.symbol, TsNs: uint64(now.UnixNano())}, dealer.AggregateView{}
	}
	for i := range rows {
		r := &rows[i]
		k := ivKey{r.Expiry, r.Strike, r.Side}
		iv := p.ivCache[k]
		if iv <= 0 {
			continue
		}
		years := greeks.TimeToExpiryYears(uint64(now.UnixNano()), r.Expiry)
		if years <= 0 {
			continue
		}
		strike := feed.DecodeStrike(r.Strike)
		g := greeks.All(p.spot, strike, years, defaultRate, defaultYield, iv, r.Side)
		r.IV = iv
		r.Delta = g.Delta
		r.Gamma = g.Gamma
		r.Theta = g.Theta
		r.Vega = g.Vega
		r.Charm = g.Charm
		r.Vanna = g.Vanna
	}
	view := dealer.Aggregate(rows, p.spot)
	breakdown := p.dpiScorer.Score(p.symbol, view, rows, p.flow5min, now)

	zone := p.charmClock.Classify(p.symbol, aggregateCharmVelocity(rows, p.spot), now)

	pin := dealer.EvaluatePin(rows, p.spot, nil, now,
		p.sessionStart, p.sessionEnd, dealer.DefaultPinConfig())

	out := alerts.Snapshot{
		Symbol:    p.symbol,
		TsNs:      uint64(now.UnixNano()),
		NetGEX:    view.NetGEX,
		Regime:    uint8(view.Regime),
		CharmZone: uint8(zone),
	}
	out.DPI.Composite = breakdown.Composite()
	out.Pin.Active = pin.Active
	out.Pin.TopProbability = pin.TopProbability
	out.Pin.TopStrike = pin.TopStrike
	return out, view
}

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

// TestPipeline_Synthetic_FullStack drives synthetic ticks through the
// full M2/M3 compute pipeline and runs alerts + backtest on the
// resulting state stream. Asserts every layer wakes up with non-zero
// outputs. Mirrors what cmd/compute does in production but in-memory.
func TestPipeline_Synthetic_FullStack(t *testing.T) {
	const (
		runDur = 4 * time.Second
		spot   = 5810.0
		iv     = 0.18
	)
	now := time.Now().UTC()
	sessionStart := now.Add(-2 * time.Hour)
	sessionEnd := now.Add(4 * time.Hour)

	// Expiry must be in the FUTURE for the IV solver to converge.
	// Default synthetic uses today's date which fails if the test
	// happens to run past 16:00 ET.
	tomorrow := now.Add(24 * time.Hour)
	expiry := uint32(tomorrow.Year()*10000 + int(tomorrow.Month())*100 + tomorrow.Day())

	gen := synthetic.New(synthetic.Config{
		Symbol:       feed.SymbolSPX,
		Spot:         spot,
		IV:           iv,
		QuotesPerSec: 800,
		TradesPerSec: 100,
		BasisPerSec:  20,
		StrikeSteps:  20,
		StrikeStep:   5,
		ExpiryDate:   expiry,
		Seed:         99,
	})
	ctx, cancel := context.WithTimeout(context.Background(), runDur)
	defer cancel()
	gen.Start(ctx)

	p := newPipeState(feed.SymbolSPX, sessionStart, sessionEnd, spot)

	// Alerts wired with a low-threshold rule so we see fires from synthetic data.
	alertEng := alerts.NewEngine()
	captured := make([]alerts.Trigger, 0, 16)
	alertEng.AddSink("capture", &captureSink{collect: &captured})
	alertEng.AddRule(alerts.Rule{
		ID: "dpi_above_30", UserID: "u1", Symbol: feed.SymbolSPX,
		Kind: alerts.RuleDPIAbove, Threshold: 30, Enabled: true,
		Cooldown: 100 * time.Millisecond,
	})

	// Backtest stream: collect snapshots as we tick, then replay through
	// Run() once at the end.
	btSnaps := make([]backtest.Snapshot, 0, 16)
	btTimer := time.NewTicker(100 * time.Millisecond)
	defer btTimer.Stop()

drain:
	for {
		select {
		case <-ctx.Done():
			break drain
		case tick, ok := <-gen.Ticks():
			if !ok {
				break drain
			}
			p.handleTick(tick)
		case ts := <-btTimer.C:
			snap, _ := p.snapshot(ts)
			alertEng.OnSnapshot(snap)
			btSnaps = append(btSnaps, backtest.Snapshot{
				Ts: ts, Spot: p.spot, State: snap,
			})
		}
	}
	gen.Stop()

	// ── Pipeline produced state ──
	if len(btSnaps) < 5 {
		t.Fatalf("expected at least 5 snapshots, got %d", len(btSnaps))
	}
	if p.quotes.Len() < 10 {
		t.Errorf("quote cache too small (%d) — quote update path broken", p.quotes.Len())
	}

	// ── Alerts fired ──
	if len(captured) == 0 {
		t.Error("no alerts fired — engine not wired to snapshots")
	} else {
		t.Logf("alerts fired: %d (first: %s)", len(captured), captured[0].Text)
	}

	// ── Backtest runs without panic + emits a summary ──
	ch := make(chan backtest.Snapshot, len(btSnaps))
	for _, s := range btSnaps {
		ch <- s
	}
	close(ch)
	res, err := backtest.Run(context.Background(), backtest.Strategy{
		Name: "dpi_above_30",
		Entry: func(s alerts.Snapshot) bool { return s.DPI.Composite > 30 },
		Exit: func(s alerts.Snapshot) bool { return s.DPI.Composite < 20 },
		Direction:   backtest.Long,
		MaxHoldMin:  60,
		CooldownMin: 0,
	}, ch)
	if err != nil {
		t.Fatalf("backtest run: %v", err)
	}
	t.Logf("backtest result: %s", res)

	// Sanity: per-snapshot fields aren't garbage.
	for _, s := range btSnaps[:min(3, len(btSnaps))] {
		if math.IsNaN(s.State.DPI.Composite) || math.IsInf(s.State.DPI.Composite, 0) {
			t.Errorf("DPI is NaN/Inf at %v: %v", s.Ts, s.State.DPI.Composite)
		}
	}

	// Round-trip alerts.Snapshot through JSON to confirm wire-shape parity.
	if len(captured) > 0 {
		b, err := json.Marshal(captured[0])
		if err != nil {
			t.Errorf("trigger marshal: %v", err)
		}
		if len(b) == 0 {
			t.Error("empty trigger JSON")
		}
	}
}

type captureSink struct {
	collect *[]alerts.Trigger
}

func (c *captureSink) Deliver(t alerts.Trigger) error {
	*c.collect = append(*c.collect, t)
	return nil
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
