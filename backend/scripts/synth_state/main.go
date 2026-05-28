// Long-running synthetic state publisher.
//
// Emits realistic-looking state.<sym>.gex + narrative.<sym> messages
// at 1 Hz on the same NATS subjects compute publishes to in
// production, so frontend devs can iterate against /api/snapshot,
// /ws/live, /api/simulate, /api/backtest/run without needing
// Databento or a live ingest path.
//
// Behaviour:
//   - Spot drifts via geometric Brownian motion
//   - DPI / NetGEX / Charm zone evolve smoothly with regime cycles
//   - Strike matrix rebuilt each tick around spot (40 strikes, 5pt step)
//   - Narrative events fire on regime/zone transitions only
//
// Usage:
//   NATS_URL=nats://localhost:4222 go run ./scripts/synth_state
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"math"
	"math/rand"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/nats-io/nats.go"
)

type symbolCfg struct {
	id         uint8
	name       string
	spot       float64
	frontSym   string
	carry      float64
	strikeStep float64
}

func main() {
	url := flag.String("nats", envOr("NATS_URL", nats.DefaultURL), "NATS URL")
	rate := flag.Float64("rate", 1.0, "publish rate per second")
	seed := flag.Int64("seed", time.Now().UnixNano(), "RNG seed")
	flag.Parse()

	nc, err := nats.Connect(*url, nats.Name("flowgreeks-synth-state"))
	if err != nil {
		log.Fatalf("nats connect: %v", err)
	}
	defer nc.Close()
	log.Printf("connected: %s", *url)

	rng := rand.New(rand.NewSource(*seed))
	syms := []symbolCfg{
		{id: 1, name: "spx", spot: 5810, frontSym: "ESM6", carry: 6, strikeStep: 5},
		{id: 2, name: "ndx", spot: 20000, frontSym: "NQM6", carry: 25, strikeStep: 25},
	}

	prevRegime := map[uint8]uint8{}
	prevZone := map[uint8]uint8{}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
	go func() { <-stop; cancel() }()

	period := time.Duration(float64(time.Second) / *rate)
	t := time.NewTicker(period)
	defer t.Stop()

	tick := 0
	for {
		select {
		case <-ctx.Done():
			log.Printf("stopping after %d ticks", tick)
			return
		case <-t.C:
			tick++
			now := time.Now().UTC()
			for i := range syms {
				s := &syms[i]
				stepSpot(s, rng)
				snap := buildSnapshot(s, now, rng, tick)
				data, _ := json.Marshal(snap)
				if err := nc.Publish(fmt.Sprintf("state.%s.gex", s.name), data); err != nil {
					log.Printf("publish state: %v", err)
					continue
				}
				if pr, ok := prevRegime[s.id]; ok && pr != snap.Regime {
					emitNarrative(nc, s, "REGIME", regimeText(snap.Regime, snap.NetGEX), now)
				}
				if pz, ok := prevZone[s.id]; ok && pz != snap.CharmZone {
					emitNarrative(nc, s, "CHARM", charmText(snap.CharmZone), now)
				}
				prevRegime[s.id] = snap.Regime
				prevZone[s.id] = snap.CharmZone
			}
			if tick%30 == 0 {
				log.Printf("heartbeat tick=%d spx_spot=%.2f ndx_spot=%.2f", tick, syms[0].spot, syms[1].spot)
			}
		}
	}
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

// stepSpot evolves spot via tiny GBM step + occasional regime jolts.
func stepSpot(s *symbolCfg, rng *rand.Rand) {
	const dt = 1.0
	drift := 0.0
	vol := 0.0008 * s.spot // ~0.08% per second
	s.spot += drift*dt + rng.NormFloat64()*vol*math.Sqrt(dt)
}

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

type pinCandidate struct {
	Strike          float64 `json:"strike"`
	Probability     float64 `json:"probability"`
	GammaStrength   float64 `json:"gamma_strength"`
	DistanceFactor  float64 `json:"distance_factor"`
	FlowPersistence float64 `json:"flow_persistence"`
	TimeFactor      float64 `json:"time_factor"`
}

type stateSnapshot struct {
	TsNs        uint64  `json:"ts_ns"`
	Symbol      uint8   `json:"symbol"`
	Spot        float64 `json:"spot"`
	BasisSmooth float64 `json:"basis_smooth"`
	FutFrontSym string  `json:"fut_front_sym"`
	NetGEX      float64 `json:"net_gex"`
	ZeroGamma   float64 `json:"zero_gamma"`
	CallWall    float64 `json:"call_wall"`
	PutWall     float64 `json:"put_wall"`
	ExpectedMv  float64 `json:"expected_mv"`
	Regime      uint8   `json:"regime"`

	DPI struct {
		Composite         float64 `json:"composite"`
		NetGammaSign      float64 `json:"net_gamma_sign"`
		CharmVelocity     float64 `json:"charm_velocity"`
		VannaSensitivity  float64 `json:"vanna_sensitivity"`
		TimeToCloseDecay  float64 `json:"time_to_close_decay"`
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
		Active         bool           `json:"active"`
		WindowMins     float64        `json:"window_mins"`
		TopStrike      float64        `json:"top_strike"`
		TopProbability float64        `json:"top_probability"`
		Candidates     []pinCandidate `json:"candidates"`
	} `json:"pin"`

	Strikes []strikeOut `json:"strikes"`
}

func buildSnapshot(s *symbolCfg, now time.Time, rng *rand.Rand, tick int) stateSnapshot {
	atm := math.Round(s.spot/s.strikeStep) * s.strikeStep
	const half = 20

	// Walls + flip drift slowly so they don't snap with every spot tick.
	cycle := math.Sin(float64(tick) / 60.0)
	netGEX := -2.5e9 + cycle*1.5e9
	zeroGamma := atm - 8 + rng.NormFloat64()*1.5
	callWall := atm + 20 + rng.NormFloat64()*2
	putWall := atm - 25 + rng.NormFloat64()*2
	regime := uint8(1) // SHORT
	if netGEX > 0 {
		regime = 2 // LONG
	}

	dpiComposite := 50 + 40*math.Sin(float64(tick)/45.0)
	if dpiComposite < 0 {
		dpiComposite = 0
	}
	if dpiComposite > 100 {
		dpiComposite = 100
	}

	zone := classifyZone(now, dpiComposite)

	// Strike matrix.
	strikes := make([]strikeOut, 0, half*4)
	for k := -half; k <= half; k++ {
		px := atm + float64(k)*s.strikeStep
		dist := math.Abs(px-s.spot) / s.spot
		gammaCore := 0.18 * math.Exp(-dist*40)
		for _, side := range []uint8{1, 2} {
			pos := int64(-(800 + rng.Intn(2000)))
			if side == 2 && k < 0 {
				pos = int64(-(1500 + rng.Intn(3000)))
			}
			if side == 1 && k > 0 {
				pos = int64(-(1500 + rng.Intn(3000)))
			}
			strikes = append(strikes, strikeOut{
				Expiry:      todayYYYYMMDD(now),
				Strike:      uint32(px * 1000),
				Side:        side,
				DealerPos:   pos,
				IV:          0.18 + rng.Float64()*0.05,
				Gamma:       gammaCore,
				Charm:       -gammaCore * 0.05,
				Vanna:       gammaCore * 0.1,
				GEXNotional: float64(pos) * gammaCore * 100 * s.spot,
			})
		}
	}

	out := stateSnapshot{
		TsNs:          uint64(now.UnixNano()),
		Symbol:        s.id,
		Spot:          s.spot,
		BasisSmooth:   s.carry,
		FutFrontSym:   s.frontSym,
		NetGEX:        netGEX,
		ZeroGamma:     zeroGamma,
		CallWall:      callWall,
		PutWall:       putWall,
		ExpectedMv:    0.4 + rng.Float64()*0.3,
		Regime:        regime,
		CharmZone:     zone,
		CharmVelocity: 2.5e6 + rng.NormFloat64()*0.5e6,
		Strikes:       strikes,
	}
	out.DPI.Composite = dpiComposite
	out.DPI.NetGammaSign = clip(50+40*math.Sin(float64(tick)/40), 0, 100)
	out.DPI.CharmVelocity = clip(40+30*math.Sin(float64(tick)/55), 0, 100)
	out.DPI.VannaSensitivity = clip(60+25*math.Cos(float64(tick)/70), 0, 100)
	out.DPI.TimeToCloseDecay = ttc(now)
	out.DPI.FlowConcentration = clip(45+35*math.Cos(float64(tick)/50), 0, 100)
	out.FlowPulse.Gamma = math.Sin(float64(tick) / 30)
	out.FlowPulse.Charm = math.Cos(float64(tick) / 35)
	out.FlowPulse.Vanna = 0.5 * math.Sin(float64(tick)/45+1)
	out.FlowPulse.Total = out.FlowPulse.Gamma + out.FlowPulse.Charm + out.FlowPulse.Vanna

	out.Pin.Active = zone == 5
	out.Pin.WindowMins = ttcMins(now)
	if out.Pin.Active {
		out.Pin.TopStrike = atm
		out.Pin.TopProbability = 0.55 + rng.Float64()*0.2
		out.Pin.Candidates = []pinCandidate{
			{Strike: atm, Probability: out.Pin.TopProbability, GammaStrength: 0.9, DistanceFactor: 1.0, FlowPersistence: 0.6, TimeFactor: 0.8},
			{Strike: atm + s.strikeStep, Probability: out.Pin.TopProbability * 0.7, GammaStrength: 0.6, DistanceFactor: 0.7, FlowPersistence: 0.5, TimeFactor: 0.8},
			{Strike: atm - s.strikeStep, Probability: out.Pin.TopProbability * 0.65, GammaStrength: 0.55, DistanceFactor: 0.7, FlowPersistence: 0.45, TimeFactor: 0.8},
		}
	}
	return out
}

func emitNarrative(nc *nats.Conn, s *symbolCfg, tag, text string, now time.Time) {
	subj := fmt.Sprintf("narrative.%s", s.name)
	body := struct {
		TsNs uint64 `json:"ts_ns"`
		Tag  string `json:"tag"`
		Text string `json:"text"`
	}{
		TsNs: uint64(now.UnixNano()), Tag: tag, Text: text,
	}
	data, _ := json.Marshal(body)
	_ = nc.Publish(subj, data)
}

func regimeText(r uint8, netGEX float64) string {
	switch r {
	case 1:
		return fmt.Sprintf("Dealers flipped to SHORT GAMMA (net GEX %.1fB)", netGEX/1e9)
	case 2:
		return fmt.Sprintf("Dealers flipped to LONG GAMMA (net GEX %.1fB)", netGEX/1e9)
	}
	return "Regime change"
}

func charmText(z uint8) string {
	switch z {
	case 1:
		return "Charm velocity weak — early session, low pin pressure"
	case 2:
		return "Charm velocity rising — pin pressure building"
	case 3:
		return "Charm velocity at PEAK — maximum pin pressure window"
	case 4:
		return "Charm velocity fading — pin pressure releasing"
	case 5:
		return "PIN regime active — spot likely magnetised to top strike"
	}
	return "Charm zone change"
}

// classifyZone returns 1..5 based on time-to-close + DPI level. Zones
// loosely match dealer.CharmZone — early=WEAK, mid=PEAK, late=PIN.
func classifyZone(now time.Time, dpi float64) uint8 {
	mins := ttcMins(now)
	switch {
	case mins > 240 || mins < 0:
		return 1 // WEAK / off-session
	case mins > 120:
		return 2 // RISING
	case mins > 45:
		if dpi > 70 {
			return 3 // PEAK
		}
		return 2
	case mins > 10:
		return 4 // FADING
	default:
		return 5 // PIN
	}
}

// ttc returns 0..100 score: late session = high.
func ttc(now time.Time) float64 {
	mins := ttcMins(now)
	if mins <= 0 {
		return 100
	}
	if mins > 390 {
		return 0
	}
	return 100 - (mins/390)*100
}

// ttcMins returns minutes-to-4pm-ET, with negative = past close.
// Uses fixed UTC offset 4 (EDT). Good enough for synthetic.
func ttcMins(now time.Time) float64 {
	close := time.Date(now.Year(), now.Month(), now.Day(), 16+4, 0, 0, 0, time.UTC)
	return close.Sub(now).Minutes()
}

func todayYYYYMMDD(now time.Time) uint32 {
	return uint32(now.Year()*10000 + int(now.Month())*100 + now.Day())
}

func clip(x, lo, hi float64) float64 {
	if x < lo {
		return lo
	}
	if x > hi {
		return hi
	}
	return x
}
