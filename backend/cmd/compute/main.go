// Package main runs the FlowGreeks compute binary.
//
// Responsibilities:
//   - Subscribe to NATS `ticks.<symbol>.>` for live tick stream
//   - Per-symbol pipeline: Lee-Ready classifier → dealer position update;
//     for futures, basis tracker.
//   - Every 1s aggregator tick: snapshot position, compute Greeks per
//     active strike, run dealer.Aggregate to get NetGEX/walls/regime,
//     publish state to NATS state.<symbol>.gex.
//
// M2 scope: classifier + position tracker + Greeks engine + basis +
// aggregator wiring. Full DPI / charm zone / Flow Pulse oscillator come
// in M3. This binary already publishes the inputs they'll consume.
package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"net/http"
	"os"
	"os/signal"
	"sort"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"flowgreeks/internal/bus"
	"flowgreeks/internal/config"
	"flowgreeks/internal/dealer"
	"flowgreeks/internal/feed"
	"flowgreeks/internal/greeks"
	"flowgreeks/internal/logger"
	"flowgreeks/internal/narrative"
	"flowgreeks/internal/store"

	"github.com/nats-io/nats.go"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

const (
	serviceName    = "compute"
	metricsAddr    = ":9092"
	aggregatorTick = 1 * time.Second

	// stateMaxStrikes caps how many strikes we ship in `state.<sym>.gex`.
	// Real SPX/NDX 0DTE chains run ~2,000 active strikes by mid-session
	// and the per-second JSON snapshot exceeds NATS' 1 MiB max payload
	// once strike count passes ~600 — silently drops the WS broadcast.
	// 64 strikes by |dealer_pos| keeps the payload < 32 KiB and still
	// covers every meaningful concentration on a typical session (the
	// dashboard chain panel never plots more than ~50 anyway). Adjust
	// in M5 if a downstream panel actually needs the long tail.
	stateMaxStrikes = 64

	// Risk / yield placeholders. Refresh daily from a config service in M4+.
	defaultRate  = 0.045
	defaultYield = 0.013
)

// Pipeline holds per-symbol mutable state.
type Pipeline struct {
	symbol     feed.Symbol
	classifier *dealer.Classifier
	positions  *dealer.PositionTracker
	pulse      *dealer.FlowPulseTracker
	quotes     *dealer.QuoteCache

	// per-strike IV cache for solver warm-start
	ivMu  sync.RWMutex
	ivCache map[ivKey]float64
	ivLast  map[ivKey]float64 // ivCache one snapshot ago — for ivChange in pulse

	// trailing 5-min signed flow per strike, for DPI flow concentration
	flowMu      sync.Mutex
	flowWindow  []flowSample
	flow5min    map[uint32]int64

	// trailing 5-min strike-test count for Pin Probability Engine. A
	// "test" is a trade printed at a given strike; flow persistence
	// rewards strikes that have been touched repeatedly near close.
	pinFlow map[uint32]int

	// per-strike Greeks (rebuilt on each aggregate tick)
	// last spot estimate (from futures basis when available)
	spot float64

	// lastEventNs is the high-water mark of TsEvent across every tick the
	// pipeline has consumed. Drives the "virtual now" for replay so TTE,
	// TTC, charm-clock zone selection, and persisted state ts all track
	// historical event time instead of wall-clock. Atomic so the
	// aggregator goroutine can read without holding any pipeline mutex.
	lastEventNs atomic.Uint64
}

type ivKey struct {
	expiry uint32
	strike uint32
	side   feed.Side
}

// flowSample tracks one trade for the rolling 5-min flow window.
type flowSample struct {
	tsNs   int64
	strike uint32
	signed int64 // +size if customer bought, -size if customer sold
	count  int   // 1 per trade — used for pin engine's strike-test counter
}

func newPipeline(sym feed.Symbol) *Pipeline {
	return &Pipeline{
		symbol:     sym,
		classifier: dealer.NewClassifier(),
		positions:  dealer.NewPositionTracker(),
		pulse:      dealer.NewFlowPulseTracker(dealer.FlowPulseConfig{}),
		quotes:     dealer.NewQuoteCache(),
		ivCache:    make(map[ivKey]float64),
		ivLast:     make(map[ivKey]float64),
		flow5min:   make(map[uint32]int64),
		pinFlow:    make(map[uint32]int),
	}
}

func main() {
	calibrationPath := flag.String("calibration-config", "",
		"path to JSON file emitted by `cmd/calibrate` to override DPI / "+
			"Charm Clock thresholds; empty = engine defaults")
	flag.Parse()

	cfg, err := config.Load()
	if err != nil {
		_, _ = os.Stderr.WriteString("config load failed: " + err.Error() + "\n")
		os.Exit(1)
	}

	log := logger.New(cfg.Log.Format, cfg.Log.Level).With(
		"service", serviceName,
		"env", cfg.AppEnv,
	)

	rootCtx, cancel := context.WithCancel(context.Background())
	defer cancel()

	// ─── NATS connection (subscribe + publish on same conn) ───
	nc, err := nats.Connect(cfg.NATS.URL,
		nats.Name("flowgreeks-compute"),
		nats.MaxReconnects(-1),
		nats.ReconnectWait(time.Second),
	)
	if err != nil {
		log.Error("nats connect failed", "err", err)
		os.Exit(1)
	}
	defer nc.Close()
	log.Info("nats connected", "url", cfg.NATS.URL)

	// Per-symbol pipelines.
	pipelines := map[feed.Symbol]*Pipeline{
		feed.SymbolSPX: newPipeline(feed.SymbolSPX),
		feed.SymbolNDX: newPipeline(feed.SymbolNDX),
	}
	basis := dealer.NewBasisTracker(dealer.DefaultBasisAlpha)

	// Session bounds for DPI / Charm Clock components. Use today's
	// 09:30-16:00 ET window in UTC. M4+ should pull from a calendar
	// service; this hard-coded window is fine for development.
	now := time.Now().UTC()
	easternOffsetH := 4 // EDT; flip to 5 in winter (M4 calendar service handles DST)
	sessionStart := time.Date(now.Year(), now.Month(), now.Day(),
		9+easternOffsetH, 30, 0, 0, time.UTC)
	sessionEnd := time.Date(now.Year(), now.Month(), now.Day(),
		16+easternOffsetH, 0, 0, 0, time.UTC)

	dpiScorer := dealer.NewDPIScorer(dealer.DPIConfig{
		SessionStart: sessionStart,
		SessionEnd:   sessionEnd,
	})
	charmClock := dealer.NewCharmClockClassifier(sessionStart, sessionEnd)

	// Apply offline calibration if requested. Failures are non-fatal —
	// a missing or malformed file logs a warning and the engine falls
	// back to the spec defaults so an operator typo can never silently
	// zero out a production normalizer.
	if path := *calibrationPath; path != "" {
		cal, err := dealer.LoadCalibration(path)
		if err != nil {
			log.Warn("calibration load failed; using engine defaults",
				"path", path, "err", err)
		} else if pref, sym, ok := dealer.PreferredSymbol(cal); ok {
			dpiScorer.SetThresholds(pref.GEXNorm, pref.CharmFlowRateNorm, pref.VannaPressureNorm)
			if pref.CharmZoneBoundaries[0] > 0 && pref.CharmZoneBoundaries[1] > 0 {
				charmClock.SetVelocityThresholds(
					pref.CharmZoneBoundaries[0],
					pref.CharmZoneBoundaries[1],
				)
			}
			log.Info("calibration applied",
				"path", path,
				"basis_symbol", sym,
				"sample_count", pref.SampleCount,
				"fit_window", pref.FitWindow,
				"gex_norm", pref.GEXNorm,
				"charm_flow_rate_norm", pref.CharmFlowRateNorm,
				"vanna_pressure_norm", pref.VannaPressureNorm,
				"charm_weak_ceiling", pref.CharmZoneBoundaries[0],
				"charm_peak_floor", pref.CharmZoneBoundaries[1],
			)
		} else {
			log.Warn("calibration loaded but no usable symbol entry; using defaults",
				"path", path)
		}
	}
	narrators := map[feed.Symbol]*narrative.Engine{
		feed.SymbolSPX: narrative.NewEngine(feed.SymbolSPX),
		feed.SymbolNDX: narrative.NewEngine(feed.SymbolNDX),
	}

	// State archive writer — best-effort. Failure to construct (postgres
	// down, migration not applied) logs a warning and the rest of the
	// compute pipeline keeps running without persistence.
	stateWriter, err := store.NewStateWriter(rootCtx, cfg.Postgres.DSN(), log, store.StateWriterOpts{})
	if err != nil {
		log.Warn("state archive disabled", "err", err)
		stateWriter = nil
	} else {
		go func() {
			if err := stateWriter.Run(rootCtx); err != nil {
				log.Warn("state writer Run exited", "err", err)
			}
		}()
		defer func() { _ = stateWriter.Close() }()
		log.Info("state archive writer running")
	}

	// ─── Subscribe to all ticks ───
	for _, sym := range []feed.Symbol{feed.SymbolSPX, feed.SymbolNDX} {
		s := sym
		subj := bus.SubjectTicks(s)
		sub, err := nc.Subscribe(subj, func(m *nats.Msg) {
			t, err := bus.Decode(m.Data)
			if err != nil {
				log.Warn("decode tick", "err", err, "subj", m.Subject)
				return
			}
			handleTick(pipelines[s], basis, t, log)
		})
		if err != nil {
			log.Error("nats subscribe", "subj", subj, "err", err)
			os.Exit(1)
		}
		// Bump pending message + byte limits well past defaults (65k msgs
		// / 64 MiB). Unpaced replay can publish ~3M ticks in seconds; the
		// default slow-consumer ceiling drops most of them on the floor.
		// 8M msgs × 1 GiB is comfortable for a 30-min SPX window and
		// keeps the cmd/compute resident set bounded on a dev box.
		if err := sub.SetPendingLimits(8_000_000, 1024*1024*1024); err != nil {
			log.Warn("nats set pending limits", "subj", subj, "err", err)
		}
		log.Info("subscribed", "subject", subj)
	}

	// ─── Metrics endpoint ───
	metricsSrv := &http.Server{
		Addr:              metricsAddr,
		Handler:           promhttp.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}
	go func() {
		log.Info("metrics listening", "addr", metricsAddr)
		if err := metricsSrv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Error("metrics server crashed", "err", err)
		}
	}()

	// ─── Aggregator loop ───
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		runAggregator(rootCtx, log, nc, pipelines, basis, dpiScorer, charmClock, narrators, stateWriter, sessionStart, sessionEnd)
	}()

	// ─── Wait for signal ───
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	sig := <-stop
	log.Info("shutdown signal received", "signal", sig.String())

	cancel()
	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	_ = metricsSrv.Shutdown(shutdownCtx)

	wg.Wait()
	log.Info("compute stopped")
}

// handleTick is invoked per incoming Tick. Hot path — keep allocations
// minimal.
func handleTick(p *Pipeline, basis *dealer.BasisTracker, t feed.Tick,
	log interface{ Warn(string, ...any) }) {

	ticksProcessedTotal.WithLabelValues(
		computeSymLabel(uint8(p.symbol)),
		tickTypeLabel(t.IsFuture(), uint8(t.TickType)),
	).Inc()

	// Maintain the pipeline's "virtual now" by tracking the high-water
	// mark of TsEvent. Out-of-order ticks (rare under JetStream order
	// guarantees but possible across symbols) are ignored. Drives TTE
	// + TTC during replay where wall-clock is months ahead of event time.
	if t.TsEvent > 0 {
		for {
			cur := p.lastEventNs.Load()
			if t.TsEvent <= cur {
				break
			}
			if p.lastEventNs.CompareAndSwap(cur, t.TsEvent) {
				break
			}
		}
	}

	// Futures path: feed basis tracker only.
	if t.IsFuture() {
		basis.UpdateFuture(t)
		// Eagerly seed pipeline spot from basis on the first futures
		// tick so the option-quote handler downstream can solve IV
		// against a realistic spot. Without this the per-second
		// aggregator is the only thing that copies basis → p.spot,
		// which leaves the IV solver running against the hard-coded
		// fallback for the first ~600ms of a fast replay flood —
		// long enough for thousands of options quotes to be discarded
		// because the implied vol produced from (fallback spot, real
		// mid) doesn't bracket.
		if p.spot == 0 {
			snap := basis.Snapshot(t.Symbol)
			if snap.Spot > 0 {
				p.spot = snap.Spot
			} else if snap.FutFrontMid > 0 {
				p.spot = snap.FutFrontMid - snap.BasisSmooth
			}
		}
		return
	}

	if !t.IsOption() {
		return
	}

	switch t.TickType {
	case feed.TickTypeOI:
		p.positions.SeedFromOI(t)

	case feed.TickTypeQuote:
		// Mirror the NBBO so trade ticks (which don't carry bid/ask) can
		// be classified at trade time.
		p.quotes.Update(t)

		// Solve IV opportunistically. Warm-start from cache if present.
		mid := (t.Bid + t.Ask) * 0.5
		if mid <= 0 {
			return
		}
		years := greeks.TimeToExpiryYears(t.TsEvent, t.Expiry)
		if years <= 0 {
			return
		}
		spot := pipelineSpot(p)
		strike := feed.DecodeStrike(t.Strike)

		cfg := greeks.DefaultSolverConfig
		k := ivKey{expiry: t.Expiry, strike: t.Strike, side: t.Side}
		p.ivMu.RLock()
		if last, ok := p.ivCache[k]; ok && last > 0 {
			cfg.InitGuess = last
		}
		p.ivMu.RUnlock()

		res := greeks.ImpliedVol(mid, spot, strike, years, defaultRate, defaultYield, t.Side, cfg)
		ivSolverAttemptsTotal.Inc()
		if res.Converged {
			p.ivMu.Lock()
			p.ivCache[k] = res.IV
			p.ivMu.Unlock()
		} else {
			ivSolverFailuresTotal.Inc()
		}

	case feed.TickTypeTrade:
		// Fill bid/ask from cached NBBO so Lee-Ready can classify.
		// Trade ticks from the live wire don't carry top-of-book.
		p.quotes.Apply(&t)
		t.Aggressor = p.classifier.Classify(t)
		p.positions.Apply(t)

		// Append to rolling flow window for DPI Flow Concentration.
		var signed int64
		switch t.Aggressor {
		case feed.AggressorBuy:
			signed = int64(t.Size)
		case feed.AggressorSell:
			signed = -int64(t.Size)
		}
		if signed != 0 {
			p.flowMu.Lock()
			p.flowWindow = append(p.flowWindow, flowSample{
				tsNs:   int64(t.TsEvent),
				strike: t.Strike,
				signed: signed,
				count:  1,
			})
			p.flowMu.Unlock()
		}

		// Forward to Flow Pulse — lookup current Greeks (best-effort:
		// cached IV → analytical Greeks) and fold the trade in.
		k := ivKey{expiry: t.Expiry, strike: t.Strike, side: t.Side}
		p.ivMu.RLock()
		iv := p.ivCache[k]
		ivPrev := p.ivLast[k]
		p.ivMu.RUnlock()
		if iv > 0 {
			years := greeks.TimeToExpiryYears(t.TsEvent, t.Expiry)
			if years > 0 {
				strike := feed.DecodeStrike(t.Strike)
				spot := pipelineSpot(p)
				g := greeks.All(spot, strike, years, defaultRate, defaultYield, iv, t.Side)
				dealerPos := p.positions.Get(p.symbol, t.Expiry, t.Strike, t.Side)
				ivChange := iv - ivPrev
				p.pulse.OnTrade(t, dealerPos, g.Delta, g.Charm, g.Vanna, ivChange)
			}
		}
	}
}

// flow5MinPurge drops samples older than 5 minutes from the rolling window
// and rebuilds the per-strike net signed map. Called once per aggregator
// tick (1 Hz) so the window cost stays bounded.
func flow5MinPurge(p *Pipeline, nowNs int64) {
	cutoff := nowNs - int64(5*time.Minute)
	p.flowMu.Lock()
	defer p.flowMu.Unlock()
	keep := p.flowWindow[:0]
	for _, s := range p.flowWindow {
		if s.tsNs >= cutoff {
			keep = append(keep, s)
		}
	}
	p.flowWindow = keep

	for k := range p.flow5min {
		delete(p.flow5min, k)
	}
	for k := range p.pinFlow {
		delete(p.pinFlow, k)
	}
	for _, s := range p.flowWindow {
		p.flow5min[s.strike] += s.signed
		p.pinFlow[s.strike] += s.count
	}
}

// pipelineSpot returns a spot estimate. SPX → derived from ES front-mid
// minus basis. NDX → NQ front-mid minus basis. M3 will replace this with
// a proper IV-implied spot or index-direct feed if available.
func pipelineSpot(p *Pipeline) float64 {
	if p.spot > 0 {
		return p.spot
	}
	// Conservative fallback. Will be overwritten by aggregator pulling
	// fresh basis snapshot every tick.
	switch p.symbol {
	case feed.SymbolSPX:
		return 5800
	case feed.SymbolNDX:
		return 20000
	}
	return 0
}

// runAggregator runs the per-symbol per-second aggregation loop. Pulls
// position snapshots, computes Greeks per strike using cached IVs, runs
// dealer.Aggregate, publishes state.
//
// All time-sensitive computations (TTE, TTC, charm-clock zone, persisted
// row ts) are driven by per-pipeline event time (TsEvent high-water mark),
// not wall clock. Live ingest sets event-time ≈ wall-clock so behaviour is
// unchanged; replay sets event-time to the historical session being
// replayed so the math is computed against the actual session-of-record.
func runAggregator(ctx context.Context,
	log interface {
		Info(string, ...any)
		Warn(string, ...any)
	},
	nc *nats.Conn, pipes map[feed.Symbol]*Pipeline, basis *dealer.BasisTracker,
	dpiScorer *dealer.DPIScorer, charmClock *dealer.CharmClockClassifier,
	narrators map[feed.Symbol]*narrative.Engine,
	stateWriter *store.StateWriter,
	sessionStart, sessionEnd time.Time) {

	ticker := time.NewTicker(aggregatorTick)
	defer ticker.Stop()

	var iter uint64
	for {
		select {
		case <-ctx.Done():
			log.Info("aggregator stopping", "iterations", iter)
			return
		case <-ticker.C:
			iter++
			aggregatorIterations.Inc()
			passStart := time.Now()
			for sym, p := range pipes {
				// Per-pipeline event-time "now". When no tick has been
				// observed yet (cold start) fall back to wall-clock so the
				// aggregator does not stall before data arrives.
				eventNs := p.lastEventNs.Load()
				var eventNow time.Time
				if eventNs > 0 {
					eventNow = time.Unix(0, int64(eventNs)).UTC()
				} else {
					eventNow = time.Now().UTC()
				}

				// Session window for this iteration, anchored on event-time
				// day. M4+ calendar service can refine for non-DST and
				// half-days; for now an EDT 09:30→16:00 ET window in UTC
				// is good enough.
				const easternOffsetH = 4 // EDT; flip to 5 in winter
				sStart := time.Date(eventNow.Year(), eventNow.Month(), eventNow.Day(),
					9+easternOffsetH, 30, 0, 0, time.UTC)
				sEnd := time.Date(eventNow.Year(), eventNow.Month(), eventNow.Day(),
					16+easternOffsetH, 0, 0, 0, time.UTC)
				dpiScorer.SetSessionBounds(sStart, sEnd)
				charmClock.SetSessionBounds(sStart, sEnd)

				snap := basis.Snapshot(sym)
				if snap.Spot > 0 {
					p.spot = snap.Spot
				} else if snap.FutFrontMid > 0 && snap.BasisSmooth != 0 {
					p.spot = snap.FutFrontMid - snap.BasisSmooth
				} else if snap.FutFrontMid > 0 {
					// First-iteration fallback: before the basis EWMA has
					// initialised (needs spot to seed), use front-mid as a
					// crude spot proxy so downstream math has a non-zero S
					// from the very first aggregator pass during replay.
					p.spot = snap.FutFrontMid
				}
				rows := p.positions.Snapshot(sym)
				if len(rows) == 0 {
					continue
				}
				fillGreeks(p, rows, eventNs)
				view := dealer.Aggregate(rows, p.spot)

				flow5MinPurge(p, eventNow.UnixNano())

				p.flowMu.Lock()
				flowCopy := make(map[uint32]int64, len(p.flow5min))
				for k, v := range p.flow5min {
					flowCopy[k] = v
				}
				pinFlowCopy := make(dealer.PinFlow, len(p.pinFlow))
				for k, v := range p.pinFlow {
					pinFlowCopy[k] = v
				}
				p.flowMu.Unlock()

				breakdown := dpiScorer.Score(sym, view, rows, flowCopy, eventNow)
				pulse := p.pulse.Snapshot(eventNow)

				charmVel := aggregateCharmVelocity(rows, p.spot)
				zone := charmClock.Classify(sym, charmVel, eventNow)

				pinResult := dealer.EvaluatePin(rows, p.spot, pinFlowCopy, eventNow,
					sStart, sEnd, dealer.DefaultPinConfig())

				// Narrative: feed condensed snapshot, fan resulting events
				// to NATS for the dashboard "Live Dealer Narrative" panel.
				if eng := narrators[sym]; eng != nil {
					evs := eng.Step(narrative.Snapshot{
						TsNs:         uint64(eventNow.UnixNano()),
						Spot:         p.spot,
						NetGEX:       view.NetGEX,
						ZeroGamma:    view.ZeroGamma,
						Regime:       view.Regime,
						DPI:          breakdown.Composite(),
						CharmZone:    zone,
						PinActive:    pinResult.Active,
						PinTopStrike: pinResult.TopStrike,
						PinTopProb:   pinResult.TopProbability,
					})
					publishNarrative(nc, log, sym, evs)
				}

				// Roll over IV cache snapshot for next bucket's ivChange.
				p.ivMu.Lock()
				for k, v := range p.ivCache {
					p.ivLast[k] = v
				}
				p.ivMu.Unlock()

				publishState(nc, log, sym, p.spot, snap, view, rows,
					breakdown, pulse, zone, charmVel, pinResult, sStart, sEnd, eventNow)

				if stateWriter != nil {
					row := store.StateRow{
						Ts:               eventNow,
						Symbol:           sym,
						Spot:             p.spot,
						BasisSmooth:      snap.BasisSmooth,
						NetGEX:           view.NetGEX,
						ZeroGamma:        view.ZeroGamma,
						CallWall:         view.CallWall,
						PutWall:          view.PutWall,
						ExpectedMv:       view.ExpectedMv,
						Regime:           view.Regime,
						CharmZone:        zone,
						CharmVelocity:    charmVel,
						DPIComposite:     float32(breakdown.Composite()),
						DPINetGamma:      float32(breakdown.NetGammaSign),
						DPICharmVelocity: float32(breakdown.CharmVelocity),
						DPIVanna:         float32(breakdown.VannaSensitivity),
						DPITTC:           float32(breakdown.TimeToCloseDecay),
						DPIFlow:          float32(breakdown.FlowConcentration),
						PulseGamma:       float32(pulse.GammaPulse),
						PulseCharm:       float32(pulse.CharmPulse),
						PulseVanna:       float32(pulse.VannaPulse),
						PulseTotal:       float32(pulse.TotalPulse),
						PinActive:        pinResult.Active,
						PinTopStrike:     pinResult.TopStrike,
						PinTopProb:       float32(pinResult.TopProbability),
					}
					if err := stateWriter.Write(row); err != nil {
						log.Warn("state writer drop", "err", err, "sym", sym)
					}
				}
			}
			if iter%30 == 0 {
				log.Info("aggregator heartbeat",
					"iterations", iter,
					"spx_strikes", len(pipes[feed.SymbolSPX].ivCache),
					"ndx_strikes", len(pipes[feed.SymbolNDX].ivCache))
			}
			aggregatorDuration.Observe(time.Since(passStart).Seconds())
			for sym, p := range pipes {
				p.ivMu.RLock()
				aggregatorActiveStrikes.WithLabelValues(computeSymLabel(uint8(sym))).Set(float64(len(p.ivCache)))
				p.ivMu.RUnlock()
			}
		}
	}
}

// aggregateCharmVelocity sums |charm × dealerPos × multiplier × spot| /
// minutesPerYear across rows for the Charm Clock classifier input. Same
// formula as DPI's CV component but pre-normalization.
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

// topStrikesByDealerPos returns the n strikes with the largest |DealerPos|.
// Stable secondary sort on (Expiry, Strike, Side) so the trimmed payload
// is deterministic and diffable across consecutive aggregator iterations.
// Returns rows itself when len(rows) ≤ n; otherwise allocates a new
// slice (caller cannot mutate the source).
func topStrikesByDealerPos(rows []dealer.StrikeRow, n int) []dealer.StrikeRow {
	if n <= 0 || len(rows) <= n {
		return rows
	}
	out := make([]dealer.StrikeRow, len(rows))
	copy(out, rows)
	sort.Slice(out, func(i, j int) bool {
		ai := out[i].DealerPos
		if ai < 0 {
			ai = -ai
		}
		aj := out[j].DealerPos
		if aj < 0 {
			aj = -aj
		}
		if ai != aj {
			return ai > aj
		}
		if out[i].Expiry != out[j].Expiry {
			return out[i].Expiry < out[j].Expiry
		}
		if out[i].Strike != out[j].Strike {
			return out[i].Strike < out[j].Strike
		}
		return out[i].Side < out[j].Side
	})
	return out[:n]
}

// fillGreeks populates Greeks fields on each row using cached IVs.
// nowNs is the pipeline's event-time "now" in ns since epoch; falls back
// to wall-clock when zero (cold start). Driving TTE off event time is
// what makes replay produce non-zero Greeks — wall-clock would be
// months past the historical expiries on disk.
func fillGreeks(p *Pipeline, rows []dealer.StrikeRow, nowNs uint64) {
	if nowNs == 0 {
		nowNs = uint64(time.Now().UnixNano())
	}
	for i := range rows {
		r := &rows[i]
		k := ivKey{expiry: r.Expiry, strike: r.Strike, side: r.Side}
		p.ivMu.RLock()
		iv := p.ivCache[k]
		p.ivMu.RUnlock()
		if iv <= 0 {
			continue
		}
		years := greeks.TimeToExpiryYears(nowNs, r.Expiry)
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
}

// publishState fans out the computed state. For M3+ we publish JSON for
// observability (downstream consumers don't exist yet). M4+ replaces with
// a binary protocol if hot-path bandwidth becomes an issue.
//
// sessionStart / sessionEnd are reserved for future state-output fields
// (calendar metadata, time-to-close as a duration, etc). Currently
// unused — pin already carries its own time-to-close minutes.
//
// eventNow is the pipeline's event-time "now" — drives the published
// ts_ns so replay frames carry the historical session timestamp instead
// of wall-clock. Live ingest passes eventNow ≈ time.Now().
func publishState(nc *nats.Conn,
	log interface{ Warn(string, ...any) },
	sym feed.Symbol, spot float64,
	bs dealer.BasisSnapshot,
	view dealer.AggregateView,
	rows []dealer.StrikeRow,
	breakdown dealer.DPIBreakdown,
	pulse dealer.FlowPulse,
	zone dealer.CharmZone,
	charmVel float64,
	pin dealer.PinResult,
	sessionStart, sessionEnd time.Time,
	eventNow time.Time) {
	_ = sessionStart
	_ = sessionEnd

	type strikeOut struct {
		Expiry      uint32  `json:"expiry"`
		Strike      uint32  `json:"strike"`
		Side        uint8   `json:"side"`
		DealerPos   int64   `json:"dealer_pos"`
		IV          float64 `json:"iv"`
		Gamma       float64 `json:"gamma"`
		Charm       float64 `json:"charm"`
		Vanna       float64 `json:"vanna"`
		GEXNotional float64 `json:"gex_notional"`
	}
	type pinCandOut struct {
		Strike          float64 `json:"strike"`
		Probability     float64 `json:"probability"`
		GammaStrength   float64 `json:"gamma_strength"`
		DistanceFactor  float64 `json:"distance_factor"`
		FlowPersistence float64 `json:"flow_persistence"`
		TimeFactor      float64 `json:"time_factor"`
	}
	out := struct {
		TsNs        uint64      `json:"ts_ns"`
		Symbol      uint8       `json:"symbol"`
		Spot        float64     `json:"spot"`
		BasisSmooth float64     `json:"basis_smooth"`
		FutFrontSym string      `json:"fut_front_sym"`
		NetGEX      float64     `json:"net_gex"`
		ZeroGamma   float64     `json:"zero_gamma"`
		CallWall    float64     `json:"call_wall"`
		PutWall     float64     `json:"put_wall"`
		ExpectedMv  float64     `json:"expected_mv"`
		Regime      uint8       `json:"regime"`

		// M3 components
		DPI struct {
			Composite        float64 `json:"composite"`
			NetGammaSign     float64 `json:"net_gamma_sign"`
			CharmVelocity    float64 `json:"charm_velocity"`
			VannaSensitivity float64 `json:"vanna_sensitivity"`
			TimeToCloseDecay float64 `json:"time_to_close_decay"`
			FlowConcentration float64 `json:"flow_concentration"`
		} `json:"dpi"`
		FlowPulse struct {
			Gamma float64 `json:"gamma"`
			Charm float64 `json:"charm"`
			Vanna float64 `json:"vanna"`
			Total float64 `json:"total"`
		} `json:"flow_pulse"`
		CharmZone     uint8   `json:"charm_zone"`
		CharmVelocity float64 `json:"charm_velocity_raw"`

		Pin struct {
			Active         bool        `json:"active"`
			WindowMins     float64     `json:"window_mins"`
			TopStrike      float64     `json:"top_strike"`
			TopProbability float64     `json:"top_probability"`
			Candidates     []pinCandOut `json:"candidates"`
		} `json:"pin"`

		Strikes     []strikeOut `json:"strikes"`
		// StrikeCountTotal is the unfiltered strike count maintained by
		// the aggregator. StrikeCountReturned is len(Strikes) after the
		// top-N trim — clients use the diff to know they are looking at
		// a concentration view, not the full chain.
		StrikeCountTotal    int `json:"strike_count_total"`
		StrikeCountReturned int `json:"strike_count_returned"`
	}{
		TsNs:        uint64(eventNow.UnixNano()),
		Symbol:      uint8(sym),
		Spot:        spot,
		BasisSmooth: bs.BasisSmooth,
		FutFrontSym: bs.FutFrontSym,
		NetGEX:      view.NetGEX,
		ZeroGamma:   view.ZeroGamma,
		CallWall:    view.CallWall,
		PutWall:     view.PutWall,
		ExpectedMv:  view.ExpectedMv,
		Regime:      uint8(view.Regime),
		CharmZone:   uint8(zone),
		CharmVelocity: charmVel,
		Strikes:     make([]strikeOut, 0, len(rows)),
	}
	out.DPI.Composite = breakdown.Composite()
	out.DPI.NetGammaSign = breakdown.NetGammaSign
	out.DPI.CharmVelocity = breakdown.CharmVelocity
	out.DPI.VannaSensitivity = breakdown.VannaSensitivity
	out.DPI.TimeToCloseDecay = breakdown.TimeToCloseDecay
	out.DPI.FlowConcentration = breakdown.FlowConcentration
	out.FlowPulse.Gamma = pulse.GammaPulse
	out.FlowPulse.Charm = pulse.CharmPulse
	out.FlowPulse.Vanna = pulse.VannaPulse
	out.FlowPulse.Total = pulse.TotalPulse

	out.Pin.Active = pin.Active
	out.Pin.WindowMins = pin.WindowMins
	out.Pin.TopStrike = pin.TopStrike
	out.Pin.TopProbability = pin.TopProbability
	out.Pin.Candidates = make([]pinCandOut, 0, len(pin.Candidates))
	for _, c := range pin.Candidates {
		side := "C"
		if c.GammaStrength == 0 && c.DistanceFactor == 0 {
			// purely a defensive check; UI uses StrikePrice anyway
		}
		_ = side
		out.Pin.Candidates = append(out.Pin.Candidates, pinCandOut{
			Strike:          c.StrikePrice,
			Probability:     c.Probability,
			GammaStrength:   c.GammaStrength,
			DistanceFactor:  c.DistanceFactor,
			FlowPersistence: c.FlowPersistence,
			TimeFactor:      c.TimeFactor,
		})
	}
	for _, r := range topStrikesByDealerPos(rows, stateMaxStrikes) {
		out.Strikes = append(out.Strikes, strikeOut{
			Expiry: r.Expiry, Strike: r.Strike, Side: uint8(r.Side),
			DealerPos: r.DealerPos, IV: r.IV, Gamma: r.Gamma,
			Charm: r.Charm, Vanna: r.Vanna, GEXNotional: r.GEXNotional,
		})
	}
	out.StrikeCountTotal = len(rows)
	out.StrikeCountReturned = len(out.Strikes)
	data, err := json.Marshal(out)
	if err != nil {
		log.Warn("state marshal", "err", err)
		return
	}
	subj := bus.SubjectState(sym, bus.StateKindGEX)
	if err := nc.Publish(subj, data); err != nil {
		log.Warn("state publish", "err", err, "subj", subj)
	}
}

// publishNarrative emits each narrative event onto narrative.<sym>.
// One NATS message per event so the api fan-out can route them
// straight to dashboard subscribers without re-batching.
func publishNarrative(nc *nats.Conn,
	log interface{ Warn(string, ...any) },
	sym feed.Symbol, evs []narrative.Event) {
	if len(evs) == 0 {
		return
	}
	subj := bus.SubjectNarrative(sym)
	type wireEvent struct {
		TsNs uint64         `json:"ts_ns"`
		Tag  string         `json:"tag"`
		Text string         `json:"text"`
		Refs map[string]any `json:"refs,omitempty"`
	}
	for _, e := range evs {
		data, err := json.Marshal(wireEvent{
			TsNs: e.TsNs, Tag: string(e.Tag), Text: e.Text, Refs: e.Refs,
		})
		if err != nil {
			log.Warn("narrative marshal", "err", err, "tag", e.Tag)
			continue
		}
		if err := nc.Publish(subj, data); err != nil {
			log.Warn("narrative publish", "err", err, "subj", subj)
		}
	}
}
var _ = binary.LittleEndian
