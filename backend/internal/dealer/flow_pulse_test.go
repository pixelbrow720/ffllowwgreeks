package dealer

import (
	"math"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

func pulseTradeTick(side feed.Side, size uint32, ag feed.Aggressor) feed.Tick {
	return feed.Tick{
		TickType:   feed.TickTypeTrade,
		AssetClass: feed.AssetClassOption,
		Symbol:     feed.SymbolSPX,
		Side:       side,
		Expiry:     20260620,
		Strike:     5810000,
		Size:       size,
		Aggressor:  ag,
		Price:      2.45,
		Bid:        2.40,
		Ask:        2.50,
	}
}

func TestFlowPulse_EmptyBucket(t *testing.T) {
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	now := time.Unix(1_700_000_000, 0)
	got := tr.Snapshot(now)
	if got.GammaPulse != 0 || got.CharmPulse != 0 || got.VannaPulse != 0 || got.TotalPulse != 0 {
		t.Fatalf("empty bucket: want zero pulse, got %+v", got)
	}
	if got.NormFactor != DefaultFlowPulseNormalizer {
		t.Fatalf("norm factor: want %v, got %v", DefaultFlowPulseNormalizer, got.NormFactor)
	}
	if got.TsNs != uint64(now.UnixNano()) {
		t.Fatalf("ts: want %d, got %d", uint64(now.UnixNano()), got.TsNs)
	}
}

func TestFlowPulse_BuyAggressorPositiveDelta_NegativeGamma(t *testing.T) {
	// Customer LIFTED ASK on a positive-delta call → dealer SOLD the call →
	// dealer is short delta → must SELL index to hedge → negative pulse.
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	tr.OnTrade(pulseTradeTick(feed.SideCall, 10, feed.AggressorBuy), 0, 0.5, 0, 0, 0)
	got := tr.Snapshot(time.Unix(0, 0))
	if !(got.GammaPulse < 0) {
		t.Fatalf("buy aggressor + positive delta: GammaPulse must be negative, got %v", got.GammaPulse)
	}
}

func TestFlowPulse_SellAggressorPositiveDelta_PositiveGamma(t *testing.T) {
	// Customer HIT BID on positive-delta call → dealer BOUGHT call → long
	// delta → must BUY index to flatten? No — long delta means dealer is
	// already long, so to maintain delta-neutral they SELL. Wait, the spec
	// says: hedge_delta = -aggressor_sign × size × multiplier × delta.
	// AggressorSell → sign=-1 → hedge_delta = +size·mult·delta > 0.
	// Sign convention "+ve = dealer must BUY index". Reconcile:
	// If dealer bought a +Δ call, they are long Δ. To hedge, they sell
	// futures (sell index). Yet the formula gives +ve. The rationale: §10
	// flips the sign so that the indicator points at the FORCED dealer
	// hedging that the MARKET MAKER has yet to do — i.e. the hedge they
	// just put on equals -dealer_gamma_exposure_change, and the next
	// re-hedge as spot moves keeps that direction. The spec is normative;
	// we follow it. Customer-sells-call dealer-positions-bullish-flow.
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	tr.OnTrade(pulseTradeTick(feed.SideCall, 10, feed.AggressorSell), 0, 0.5, 0, 0, 0)
	got := tr.Snapshot(time.Unix(0, 0))
	if !(got.GammaPulse > 0) {
		t.Fatalf("sell aggressor + positive delta: GammaPulse must be positive, got %v", got.GammaPulse)
	}
}

func TestFlowPulse_CharmContribution(t *testing.T) {
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	// Non-zero charm only — gamma and vanna must remain zero.
	tr.OnTrade(pulseTradeTick(feed.SideCall, 5, feed.AggressorBuy), 0, 0, -1e-4, 0, 0)
	got := tr.Snapshot(time.Unix(0, 0))
	if got.CharmPulse == 0 {
		t.Fatalf("non-zero charm: CharmPulse must be non-zero")
	}
	if got.GammaPulse != 0 {
		t.Fatalf("zero delta: GammaPulse must be zero, got %v", got.GammaPulse)
	}
	if got.VannaPulse != 0 {
		t.Fatalf("zero ivChange: VannaPulse must be zero, got %v", got.VannaPulse)
	}
}

func TestFlowPulse_VannaRequiresIvChange(t *testing.T) {
	// Non-zero vanna but zero ivChange → vanna contribution drops out.
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	tr.OnTrade(pulseTradeTick(feed.SideCall, 5, feed.AggressorBuy), 0, 0, 0, 0.05, 0)
	got := tr.Snapshot(time.Unix(0, 0))
	if got.VannaPulse != 0 {
		t.Fatalf("zero ivChange: VannaPulse must be zero, got %v", got.VannaPulse)
	}

	// Now with non-zero ivChange — vanna pulse must appear.
	tr2 := NewFlowPulseTracker(FlowPulseConfig{})
	tr2.OnTrade(pulseTradeTick(feed.SideCall, 5, feed.AggressorBuy), 0, 0, 0, 0.05, 0.01)
	got2 := tr2.Snapshot(time.Unix(0, 0))
	if got2.VannaPulse == 0 {
		t.Fatalf("non-zero vanna+ivChange: VannaPulse must be non-zero")
	}
}

func TestFlowPulse_AggressorUnknownSkipped(t *testing.T) {
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	tr.OnTrade(pulseTradeTick(feed.SideCall, 10, feed.AggressorUnknown), 0, 0.5, -1e-4, 0.05, 0.01)
	got := tr.Snapshot(time.Unix(0, 0))
	if got.GammaPulse != 0 || got.CharmPulse != 0 || got.VannaPulse != 0 {
		t.Fatalf("aggressor unknown must be skipped, got %+v", got)
	}
}

func TestFlowPulse_NonTradeOrZeroSizeIgnored(t *testing.T) {
	tr := NewFlowPulseTracker(FlowPulseConfig{})

	tt := pulseTradeTick(feed.SideCall, 10, feed.AggressorBuy)
	tt.TickType = feed.TickTypeQuote
	tr.OnTrade(tt, 0, 0.5, -1e-4, 0.05, 0.01)

	tt = pulseTradeTick(feed.SideCall, 10, feed.AggressorBuy)
	tt.AssetClass = feed.AssetClassFuture
	tr.OnTrade(tt, 0, 0.5, -1e-4, 0.05, 0.01)

	tr.OnTrade(pulseTradeTick(feed.SideCall, 0, feed.AggressorBuy), 0, 0.5, -1e-4, 0.05, 0.01)

	got := tr.Snapshot(time.Unix(0, 0))
	if got.GammaPulse != 0 || got.CharmPulse != 0 || got.VannaPulse != 0 {
		t.Fatalf("non-trade/zero-size must be ignored, got %+v", got)
	}
}

func TestFlowPulse_EWMAConvergesToSteadyState(t *testing.T) {
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	// EWMA with constant input X converges to X. With α=0.4, after 6
	// samples the residual is (1-α)^6 ≈ 0.0467 of the gap from start (0).
	delta := 0.5
	var final FlowPulse
	for i := 0; i < 6; i++ {
		tr.OnTrade(pulseTradeTick(feed.SideCall, 10, feed.AggressorSell), 0, delta, 0, 0, 0)
		final = tr.Snapshot(time.Unix(int64(i), 0))
	}
	// Per-bucket gamma contribution = +1·10·100·0.5 = 500.
	// Steady-state smooth → 500. Normalized: 500 / 5e6 = 1e-4.
	expected := 500.0 / DefaultFlowPulseNormalizer
	residual := math.Pow(1-DefaultFlowPulseAlpha, 6)
	tol := expected*residual + 1e-9
	if math.Abs(final.GammaPulse-expected) > tol {
		t.Fatalf("EWMA convergence: got %v, want ~%v (tol %v)", final.GammaPulse, expected, tol)
	}
}

func TestFlowPulse_TotalIsSumOfComponents(t *testing.T) {
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	for i := 0; i < 4; i++ {
		tr.OnTrade(pulseTradeTick(feed.SideCall, 7, feed.AggressorBuy), 0, 0.55, -2e-4, 0.04, 0.015)
		tr.OnTrade(pulseTradeTick(feed.SidePut, 3, feed.AggressorSell), 0, -0.45, -1e-4, 0.03, 0.012)
		got := tr.Snapshot(time.Unix(int64(i), 0))
		want := got.GammaPulse + got.CharmPulse + got.VannaPulse
		if math.Abs(got.TotalPulse-want) > 1e-12 {
			t.Fatalf("bucket %d: TotalPulse %v != sum %v", i, got.TotalPulse, want)
		}
	}
}

func TestFlowPulse_Normalization(t *testing.T) {
	// One trade producing gamma_contrib = -1·100·100·1 = -10000 raw.
	// Normalizer = 1000 → expected normalized first sample (smooth = α·raw):
	// 0.4·(-10000)/1000 = -4.0.
	cfg := FlowPulseConfig{
		BucketDuration:     time.Second,
		EWMAAlpha:          0.4,
		Normalizer:         1000,
		ContractMultiplier: 100,
	}
	tr := NewFlowPulseTracker(cfg)
	tr.OnTrade(pulseTradeTick(feed.SideCall, 100, feed.AggressorBuy), 0, 1.0, 0, 0, 0)
	got := tr.Snapshot(time.Unix(0, 0))
	want := 0.4 * (-10000.0) / 1000.0
	if math.Abs(got.GammaPulse-want) > 1e-9 {
		t.Fatalf("normalization: got %v, want %v", got.GammaPulse, want)
	}
	if got.NormFactor != 1000 {
		t.Fatalf("NormFactor: got %v, want 1000", got.NormFactor)
	}
}

func TestFlowPulse_BucketResetsAfterSnapshot(t *testing.T) {
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	tr.OnTrade(pulseTradeTick(feed.SideCall, 10, feed.AggressorBuy), 0, 0.5, 0, 0, 0)
	first := tr.Snapshot(time.Unix(0, 0))
	// No trades in second bucket — pulse must decay by (1-α) factor, not
	// stay flat (which would imply the bucket wasn't reset).
	second := tr.Snapshot(time.Unix(1, 0))
	want := first.GammaPulse * (1 - DefaultFlowPulseAlpha)
	if math.Abs(second.GammaPulse-want) > 1e-12 {
		t.Fatalf("bucket reset: second snapshot = %v, want %v (decay)", second.GammaPulse, want)
	}
}

func TestFlowPulse_DefaultsApplied(t *testing.T) {
	tr := NewFlowPulseTracker(FlowPulseConfig{
		BucketDuration:     0,
		EWMAAlpha:          0,
		Normalizer:         0,
		ContractMultiplier: 0,
	})
	if tr.bucketDuration != DefaultFlowPulseBucketDuration {
		t.Errorf("bucketDuration: got %v, want %v", tr.bucketDuration, DefaultFlowPulseBucketDuration)
	}
	if tr.alpha != DefaultFlowPulseAlpha {
		t.Errorf("alpha: got %v, want %v", tr.alpha, DefaultFlowPulseAlpha)
	}
	if tr.normalizer != DefaultFlowPulseNormalizer {
		t.Errorf("normalizer: got %v, want %v", tr.normalizer, DefaultFlowPulseNormalizer)
	}
	if tr.contractMultiplier != DefaultFlowPulseMultiplier {
		t.Errorf("multiplier: got %v, want %v", tr.contractMultiplier, DefaultFlowPulseMultiplier)
	}
}

func BenchmarkOnTrade(b *testing.B) {
	tr := NewFlowPulseTracker(FlowPulseConfig{})
	tick := pulseTradeTick(feed.SideCall, 10, feed.AggressorBuy)
	b.ReportAllocs()
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		tr.OnTrade(tick, 0, 0.5, -1e-4, 0.05, 0.01)
	}
}
