// Flow Pulse oscillator. Implements docs/COMPUTE_MODEL.md §10.
//
// Decomposes dealer-side forced-flow into three components driven by
// distinct Greek sources: Gamma (spot-driven), Charm (time-driven),
// Vanna (vol-driven). Per-trade contributions accumulate into a fixed
// bucket window; Snapshot closes the bucket, applies an EWMA across the
// per-component sums, normalizes by a typical-bucket notional, and emits
// the 3+1 line snapshot. Sign convention: +ve = dealer must BUY index
// (bullish), -ve = dealer must SELL (bearish).
package dealer

import (
	"time"

	"flowgreeks/internal/feed"
)

// Defaults per COMPUTE_MODEL.md §10.2.
const (
	DefaultFlowPulseBucketDuration = time.Second
	DefaultFlowPulseAlpha          = 0.4
	DefaultFlowPulseNormalizer     = 5e6 // typical 1s notional; calibrated empirically
	DefaultFlowPulseMultiplier     = 100.0
)

// charmPerMinScale converts the per-year charm parameter used in the
// formula to a per-minute hedge accumulation (×60 per spec §10.2).
const charmPerMinScale = 60.0

// FlowPulse is one bucket-snapshot of the oscillator. All pulse fields
// are normalized: 1.0 = one typical bucket of bullish flow.
type FlowPulse struct {
	TsNs       uint64
	GammaPulse float64
	CharmPulse float64
	VannaPulse float64
	TotalPulse float64
	NormFactor float64
}

// FlowPulseConfig parameterizes the tracker. Zero/invalid fields fall
// back to the defaults defined in this file.
type FlowPulseConfig struct {
	BucketDuration     time.Duration
	EWMAAlpha          float64
	Normalizer         float64
	ContractMultiplier float64
}

// FlowPulseTracker accumulates per-trade flow contributions and emits a
// smoothed, normalized 3-line pulse per bucket.
//
// Concurrency: single-threaded by design — the compute service drives it
// from one event loop, same invariant as Classifier and PositionTracker.
type FlowPulseTracker struct {
	bucketDuration     time.Duration
	alpha              float64
	normalizer         float64
	contractMultiplier float64

	// Current bucket raw sums (reset each Snapshot).
	gammaBucket float64
	charmBucket float64
	vannaBucket float64

	// EWMA state over post-bucket sums (carry across Snapshots).
	gammaSmooth float64
	charmSmooth float64
	vannaSmooth float64
}

// NewFlowPulseTracker constructs a tracker. Invalid or zero config fields
// fall back to docs/COMPUTE_MODEL.md §10 defaults.
func NewFlowPulseTracker(cfg FlowPulseConfig) *FlowPulseTracker {
	t := &FlowPulseTracker{
		bucketDuration:     cfg.BucketDuration,
		alpha:              cfg.EWMAAlpha,
		normalizer:         cfg.Normalizer,
		contractMultiplier: cfg.ContractMultiplier,
	}
	if t.bucketDuration <= 0 {
		t.bucketDuration = DefaultFlowPulseBucketDuration
	}
	if !(t.alpha > 0 && t.alpha <= 1) {
		t.alpha = DefaultFlowPulseAlpha
	}
	if !(t.normalizer > 0) {
		t.normalizer = DefaultFlowPulseNormalizer
	}
	if !(t.contractMultiplier > 0) {
		t.contractMultiplier = DefaultFlowPulseMultiplier
	}
	return t
}

// OnTrade folds one classified trade into the current bucket. Greeks are
// per-contract; ivChange is the recent IV move at the strike (zero is
// fine — vanna contribution drops out). Ticks with unknown aggressor,
// zero size, or wrong type are silent no-ops.
//
// dealerPos is part of the public API for future per-strike weighting
// extensions; the §10.2 formula does not consume it.
func (t *FlowPulseTracker) OnTrade(tick feed.Tick, dealerPos int64, delta, charm, vanna, ivChange float64) {
	_ = dealerPos

	if tick.TickType != feed.TickTypeTrade || tick.AssetClass != feed.AssetClassOption {
		return
	}
	if tick.Size == 0 {
		return
	}

	var sign float64
	switch tick.Aggressor {
	case feed.AggressorBuy:
		sign = 1
	case feed.AggressorSell:
		sign = -1
	default:
		return
	}

	base := -sign * float64(tick.Size) * t.contractMultiplier
	t.gammaBucket += base * delta
	t.charmBucket += base * charm * charmPerMinScale
	t.vannaBucket += base * vanna * ivChange
}

// Snapshot closes the current bucket: EWMA-smooths each component,
// normalizes by the configured Normalizer, returns the pulse, and resets
// the bucket accumulators. now stamps the returned FlowPulse.
func (t *FlowPulseTracker) Snapshot(now time.Time) FlowPulse {
	t.gammaSmooth = t.alpha*t.gammaBucket + (1-t.alpha)*t.gammaSmooth
	t.charmSmooth = t.alpha*t.charmBucket + (1-t.alpha)*t.charmSmooth
	t.vannaSmooth = t.alpha*t.vannaBucket + (1-t.alpha)*t.vannaSmooth

	t.gammaBucket = 0
	t.charmBucket = 0
	t.vannaBucket = 0

	gp := t.gammaSmooth / t.normalizer
	cp := t.charmSmooth / t.normalizer
	vp := t.vannaSmooth / t.normalizer

	return FlowPulse{
		TsNs:       uint64(now.UnixNano()),
		GammaPulse: gp,
		CharmPulse: cp,
		VannaPulse: vp,
		TotalPulse: gp + cp + vp,
		NormFactor: t.normalizer,
	}
}
