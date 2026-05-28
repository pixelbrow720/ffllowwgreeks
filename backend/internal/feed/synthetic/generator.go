// Package synthetic generates deterministic synthetic Tick streams for
// testing FlowGreeks compute components without depending on a live
// vendor feed.
//
// The generator produces a realistic-shaped SPX 0DTE chain:
//   - 80 strikes around spot (40 below, 40 above, 5pt spacing)
//   - Both call and put per strike
//   - Quote updates throttled per strike
//   - Trade prints with classifiable aggressor side (price vs mid)
//   - OI snapshots at session open
//   - Futures ticks for ES/NQ basis tracking
//
// Output goes to a channel of feed.Tick — drop-in for any consumer that
// would normally read from the Databento adapter.
package synthetic

import (
	"context"
	"math"
	"math/rand"
	"sync"
	"time"

	"flowgreeks/internal/feed"
)

// Config tunes the synthetic generator.
type Config struct {
	Symbol      feed.Symbol // SPX or NDX
	Spot        float64     // initial spot (e.g. 5810 for SPX, 20000 for NDX)
	IV          float64     // baseline IV (e.g. 0.18 for 18%)
	Drift       float64     // spot drift per second (e.g. 0.0001 = +0.01%/s)
	Vol         float64     // spot Brownian-noise stddev per second
	StrikeSteps int         // strikes each side of ATM (default 40)
	StrikeStep  float64     // spacing between strikes (default 5)
	ExpiryDate  uint32      // YYYYMMDD; 0 = today
	QuotesPerSec int         // total quote events/sec across chain (default 200)
	TradesPerSec int         // total trade events/sec across chain (default 30)
	BasisPerSec  int         // futures tick events/sec (default 20)
	Seed        int64       // RNG seed (0 = time.Now().UnixNano())
}

// Defaults sets reasonable values for any zero fields.
func (c *Config) Defaults() {
	if c.Symbol == feed.SymbolUnknown {
		c.Symbol = feed.SymbolSPX
	}
	if c.Spot <= 0 {
		c.Spot = 5810
	}
	if c.IV <= 0 {
		c.IV = 0.18
	}
	if c.StrikeSteps <= 0 {
		c.StrikeSteps = 40
	}
	if c.StrikeStep <= 0 {
		c.StrikeStep = 5
	}
	if c.QuotesPerSec <= 0 {
		c.QuotesPerSec = 200
	}
	if c.TradesPerSec <= 0 {
		c.TradesPerSec = 30
	}
	if c.BasisPerSec <= 0 {
		c.BasisPerSec = 20
	}
	if c.ExpiryDate == 0 {
		t := time.Now().UTC()
		c.ExpiryDate = uint32(t.Year()*10000 + int(t.Month())*100 + t.Day())
	}
	if c.Seed == 0 {
		c.Seed = time.Now().UnixNano()
	}
}

// Generator emits feed.Tick values from goroutines launched by Start.
type Generator struct {
	cfg     Config
	rngMu   sync.Mutex // guards rng (math/rand.Rand is not goroutine-safe)
	rng     *rand.Rand
	mu      sync.Mutex
	spot    float64
	out     chan feed.Tick
	stop    chan struct{}
	stopped bool
	wg      sync.WaitGroup // tracks producer goroutines
}

// New constructs a Generator. Call Start() to begin emission.
func New(cfg Config) *Generator {
	cfg.Defaults()
	return &Generator{
		cfg:  cfg,
		rng:  rand.New(rand.NewSource(cfg.Seed)),
		spot: cfg.Spot,
		out:  make(chan feed.Tick, 4096),
		stop: make(chan struct{}),
	}
}

// Ticks returns the receive-only output channel. Closed when Stop returns.
func (g *Generator) Ticks() <-chan feed.Tick { return g.out }

// Start begins generating ticks at the configured rates. Returns when
// ctx is cancelled or Stop is called.
func (g *Generator) Start(ctx context.Context) {
	g.wg.Add(5)
	go g.runSpot(ctx)
	go g.runQuotes(ctx)
	go g.runTrades(ctx)
	go g.runBasis(ctx)
	go g.emitOpenOI(ctx)
}

// Stop ends generation cleanly. Idempotent. Closes the stop channel
// first so producer goroutines exit, waits for them via wg, THEN
// closes the output channel — closing first would panic the producers
// mid-send.
func (g *Generator) Stop() {
	g.mu.Lock()
	if g.stopped {
		g.mu.Unlock()
		return
	}
	g.stopped = true
	close(g.stop)
	g.mu.Unlock()
	g.wg.Wait()
	close(g.out)
}

func (g *Generator) Spot() float64 {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.spot
}

// rng helpers — math/rand.Rand is not goroutine-safe, so every call
// goes through these locked accessors. Keeps the producer goroutines
// race-free without scattering rngMu lock/unlock across the file.
func (g *Generator) randIntn(n int) int {
	g.rngMu.Lock()
	defer g.rngMu.Unlock()
	return g.rng.Intn(n)
}
func (g *Generator) randNorm() float64 {
	g.rngMu.Lock()
	defer g.rngMu.Unlock()
	return g.rng.NormFloat64()
}
func (g *Generator) randFloat() float64 {
	g.rngMu.Lock()
	defer g.rngMu.Unlock()
	return g.rng.Float64()
}

// runSpot updates the spot price ~10x/sec via geometric Brownian motion.
func (g *Generator) runSpot(ctx context.Context) {
	defer g.wg.Done()
	t := time.NewTicker(100 * time.Millisecond)
	defer t.Stop()
	dt := 0.1
	for {
		select {
		case <-ctx.Done():
			return
		case <-g.stop:
			return
		case <-t.C:
			noise := g.cfg.Vol * math.Sqrt(dt) * g.randNorm()
			g.mu.Lock()
			drift := g.cfg.Drift * dt
			g.spot *= math.Exp(drift + noise)
			g.mu.Unlock()
		}
	}
}

// strikeFor returns the k-th strike encoded as feed.Strike (price * 1000).
// k ranges from -StrikeSteps to +StrikeSteps. Anchors the grid on the
// CURRENT spot rather than cfg.Spot so a long-running generator that
// drifts substantially still emits strikes around live ATM. Without
// this, after a few percent drift the entire chain becomes deep-OTM /
// deep-ITM and downstream consumers stop seeing realistic flow.
func (g *Generator) strikeFor(k int) (uint32, float64) {
	atm := math.Round(g.Spot()/g.cfg.StrikeStep) * g.cfg.StrikeStep
	price := atm + float64(k)*g.cfg.StrikeStep
	return feed.EncodeStrike(price), price
}

// runQuotes emits cmbp-1-style quote updates across the chain.
func (g *Generator) runQuotes(ctx context.Context) {
	defer g.wg.Done()
	if g.cfg.QuotesPerSec <= 0 {
		return
	}
	interval := time.Second / time.Duration(g.cfg.QuotesPerSec)
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-g.stop:
			return
		case <-t.C:
			k := g.randIntn(2*g.cfg.StrikeSteps+1) - g.cfg.StrikeSteps
			side := feed.SideCall
			if g.randIntn(2) == 0 {
				side = feed.SidePut
			}
			strike, kPrice := g.strikeFor(k)
			spot := g.Spot()
			theoMid := blackScholesMid(spot, kPrice, g.cfg.IV, side)
			if theoMid < 0.05 {
				continue
			}
			spread := math.Max(0.05, theoMid*0.005) // 0.5% spread cap min 5c
			bid := math.Max(0.01, theoMid-spread/2)
			ask := theoMid + spread/2
			tick := feed.Tick{
				TsEvent:    uint64(time.Now().UnixNano()),
				TsRecv:     uint64(time.Now().UnixNano()),
				Symbol:     g.cfg.Symbol,
				AssetClass: feed.AssetClassOption,
				TickType:   feed.TickTypeQuote,
				Expiry:     g.cfg.ExpiryDate,
				Strike:     strike,
				Side:       side,
				Bid:        bid,
				Ask:        ask,
				BidSize:    uint32(50 + g.randIntn(500)),
				AskSize:    uint32(50 + g.randIntn(500)),
			}
			g.send(tick)
		}
	}
}

// runTrades emits trade prints. Aggressor side is implied by price vs
// theoretical mid (downstream Lee-Ready will rediscover it from bid/ask).
func (g *Generator) runTrades(ctx context.Context) {
	defer g.wg.Done()
	if g.cfg.TradesPerSec <= 0 {
		return
	}
	interval := time.Second / time.Duration(g.cfg.TradesPerSec)
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-g.stop:
			return
		case <-t.C:
			// Bias trades toward near-the-money strikes.
			k := int(math.Round(g.randNorm() * 8))
			if k > g.cfg.StrikeSteps {
				k = g.cfg.StrikeSteps
			}
			if k < -g.cfg.StrikeSteps {
				k = -g.cfg.StrikeSteps
			}
			side := feed.SideCall
			if g.randIntn(2) == 0 {
				side = feed.SidePut
			}
			strike, kPrice := g.strikeFor(k)
			spot := g.Spot()
			theoMid := blackScholesMid(spot, kPrice, g.cfg.IV, side)
			if theoMid < 0.05 {
				continue
			}
			spread := math.Max(0.05, theoMid*0.005)
			// 60% of trades aggressed at ask (buy), 40% at bid (sell).
			price := theoMid + spread/2 // default lift ask
			if g.randFloat() < 0.4 {
				price = theoMid - spread/2 // hit bid
			}
			tick := feed.Tick{
				TsEvent:    uint64(time.Now().UnixNano()),
				TsRecv:     uint64(time.Now().UnixNano()),
				Symbol:     g.cfg.Symbol,
				AssetClass: feed.AssetClassOption,
				TickType:   feed.TickTypeTrade,
				Expiry:     g.cfg.ExpiryDate,
				Strike:     strike,
				Side:       side,
				Price:      price,
				Size:       uint32(1 + g.randIntn(50)),
			}
			g.send(tick)
		}
	}
}

// runBasis emits ES/NQ futures ticks for basis tracking. Front-month
// contract code derived from current month + 6mo (typical 3rd Friday
// quarterly).
func (g *Generator) runBasis(ctx context.Context) {
	defer g.wg.Done()
	if g.cfg.BasisPerSec <= 0 {
		return
	}
	contract := frontMonthContract(g.cfg.Symbol, time.Now())
	carry := 6.0 // dollars typical SPX -> ES basis
	if g.cfg.Symbol == feed.SymbolNDX {
		carry = 25.0
	}
	interval := time.Second / time.Duration(g.cfg.BasisPerSec)
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-g.stop:
			return
		case <-t.C:
			spot := g.Spot()
			fut := spot + carry + g.randNorm()*0.3
			tick := feed.Tick{
				TsEvent:         uint64(time.Now().UnixNano()),
				TsRecv:          uint64(time.Now().UnixNano()),
				Symbol:          g.cfg.Symbol,
				AssetClass:      feed.AssetClassFuture,
				TickType:        feed.TickTypeQuote,
				FuturesContract: feed.EncodeFuturesContract(contract),
				Bid:             fut - 0.25,
				Ask:             fut + 0.25,
				BidSize:         uint32(50 + g.randIntn(200)),
				AskSize:         uint32(50 + g.randIntn(200)),
			}
			g.send(tick)
		}
	}
}

// emitOpenOI fires once after a 200ms delay with a snapshot of OI per
// strike, mimicking Databento's stat_type=9 (open_interest) message at
// session open.
func (g *Generator) emitOpenOI(ctx context.Context) {
	defer g.wg.Done()
	timer := time.NewTimer(200 * time.Millisecond)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return
	case <-g.stop:
		return
	case <-timer.C:
	}
	for k := -g.cfg.StrikeSteps; k <= g.cfg.StrikeSteps; k++ {
		strike, _ := g.strikeFor(k)
		for _, side := range []feed.Side{feed.SideCall, feed.SidePut} {
			oi := uint32(500 + g.randIntn(5000))
			tick := feed.Tick{
				TsEvent:      uint64(time.Now().UnixNano()),
				TsRecv:       uint64(time.Now().UnixNano()),
				Symbol:       g.cfg.Symbol,
				AssetClass:   feed.AssetClassOption,
				TickType:     feed.TickTypeOI,
				Expiry:       g.cfg.ExpiryDate,
				Strike:       strike,
				Side:         side,
				OpenInterest: oi,
			}
			g.send(tick)
		}
	}
}

func (g *Generator) send(t feed.Tick) {
	g.mu.Lock()
	stopped := g.stopped
	g.mu.Unlock()
	if stopped {
		return
	}
	select {
	case g.out <- t:
	case <-g.stop:
	}
}

// blackScholesMid returns a quick mid price approximation. Not full BS —
// just intrinsic + a vol-scaled time premium so synthetic prices are
// in the right ballpark for downstream IV solver to converge.
func blackScholesMid(spot, strike, iv float64, side feed.Side) float64 {
	intrinsic := 0.0
	if side == feed.SideCall {
		intrinsic = math.Max(0, spot-strike)
	} else {
		intrinsic = math.Max(0, strike-spot)
	}
	// Simple time premium: ~iv * spot * sqrt(1day/year) for ATM, decaying with moneyness.
	atmPremium := iv * spot * math.Sqrt(1.0/365.25)
	moneyness := math.Abs(spot-strike) / spot
	timePremium := atmPremium * math.Exp(-moneyness*15)
	return intrinsic + timePremium
}

// frontMonthContract returns the typical CME quarterly front-month code.
// Approximation: pick the next Mar/Jun/Sep/Dec contract.
func frontMonthContract(sym feed.Symbol, now time.Time) string {
	root := "ES"
	if sym == feed.SymbolNDX {
		root = "NQ"
	}
	monthCodes := map[time.Month]byte{
		time.March: 'H', time.June: 'M', time.September: 'U', time.December: 'Z',
	}
	// find next quarterly month
	candidates := []time.Month{time.March, time.June, time.September, time.December}
	target := candidates[0]
	for _, m := range candidates {
		if int(m) >= int(now.Month()) {
			target = m
			break
		}
	}
	year := now.Year() % 10
	return string(root) + string(monthCodes[target]) + string(byte('0'+year))
}
