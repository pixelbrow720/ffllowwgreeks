package main

import (
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"flowgreeks/internal/feed"
)

func TestPercentile_Empty(t *testing.T) {
	t.Parallel()
	if got := percentile(nil, 0.95); got != 0 {
		t.Fatalf("percentile(nil) = %v, want 0", got)
	}
	if got := percentileAbs([]float64{}, 0.95); got != 0 {
		t.Fatalf("percentileAbs([]) = %v, want 0", got)
	}
}

func TestPercentile_Single(t *testing.T) {
	t.Parallel()
	if got := percentile([]float64{42}, 0.0); got != 42 {
		t.Fatalf("p0(single) = %v, want 42", got)
	}
	if got := percentile([]float64{42}, 1.0); got != 42 {
		t.Fatalf("p100(single) = %v, want 42", got)
	}
}

func TestPercentile_KnownDataset(t *testing.T) {
	t.Parallel()
	// 1..10 — R-7 percentiles match NumPy / Excel defaults.
	xs := []float64{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
	cases := []struct {
		q    float64
		want float64
	}{
		{0.00, 1},
		{0.50, 5.5},
		{0.95, 9.55},
		{1.00, 10},
		{0.33, 3.97},
		{0.66, 6.94},
	}
	for _, c := range cases {
		got := percentile(xs, c.q)
		if math.Abs(got-c.want) > 1e-9 {
			t.Errorf("p%.2f = %v, want %v", c.q, got, c.want)
		}
	}
}

func TestPercentile_DoesNotMutate(t *testing.T) {
	t.Parallel()
	xs := []float64{5, 1, 4, 2, 3}
	orig := make([]float64, len(xs))
	copy(orig, xs)
	_ = percentile(xs, 0.5)
	for i := range xs {
		if xs[i] != orig[i] {
			t.Fatalf("input mutated at %d: got %v, want %v", i, xs[i], orig[i])
		}
	}
}

func TestPercentile_ClampsQ(t *testing.T) {
	t.Parallel()
	xs := []float64{10, 20, 30}
	if got := percentile(xs, -0.5); got != 10 {
		t.Errorf("q=-0.5 → %v, want 10 (clamped to 0)", got)
	}
	if got := percentile(xs, 1.5); got != 30 {
		t.Errorf("q=1.5 → %v, want 30 (clamped to 1)", got)
	}
	if got := percentile(xs, math.NaN()); got != 0 {
		t.Errorf("q=NaN → %v, want 0", got)
	}
}

func TestPercentileAbs_TakesMagnitude(t *testing.T) {
	t.Parallel()
	// Mixed signs, |.| sequence is 1..10 → p95 = 9.55.
	xs := []float64{-1, 2, -3, 4, -5, 6, -7, 8, -9, 10}
	got := percentileAbs(xs, 0.95)
	if math.Abs(got-9.55) > 1e-9 {
		t.Fatalf("p95(|.|) = %v, want 9.55", got)
	}
}

func TestCalibrate_FullSyntheticDataset(t *testing.T) {
	t.Parallel()
	// netGEX magnitudes 1e9..10e9; signs alternate so the abs p95 is
	// the same as percentile(1e9..10e9, 0.95) = 9.55e9.
	netGEX := make([]float64, 10)
	for i := range netGEX {
		v := float64(i+1) * 1e9
		if i%2 == 0 {
			v = -v
		}
		netGEX[i] = v
	}
	// charm velocity 1..10 raw — used for both p95(|.|) and the
	// 33/66 zone boundaries on the raw distribution.
	charm := []float64{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
	// dpi vanna magnitudes 100..1000 with mixed signs.
	vanna := []float64{-100, 200, -300, 400, -500, 600, -700, 800, -900, 1000}
	// pin: half active; values 0.40..0.49.
	pin := []float64{0.40, 0.42, 0.44, 0.46, 0.48}

	from := time.Date(2026, 2, 2, 0, 0, 0, 0, time.UTC)
	to := time.Date(2026, 2, 13, 0, 0, 0, 0, time.UTC)

	res, err := Calibrate(stateSamples{
		netGEX:        netGEX,
		charmVelocity: charm,
		dpiVanna:      vanna,
		pinTopProb:    pin,
	}, from, to)
	if err != nil {
		t.Fatalf("Calibrate err: %v", err)
	}
	if math.Abs(res.GEXNorm-9.55e9) > 1e-3 {
		t.Errorf("GEXNorm = %v, want 9.55e9", res.GEXNorm)
	}
	if math.Abs(res.CharmFlowRateNorm-9.55) > 1e-9 {
		t.Errorf("CharmFlowRateNorm = %v, want 9.55", res.CharmFlowRateNorm)
	}
	if math.Abs(res.VannaPressureNorm-955) > 1e-6 {
		t.Errorf("VannaPressureNorm = %v, want 955", res.VannaPressureNorm)
	}
	if math.Abs(res.CharmZoneBoundaries[0]-3.97) > 1e-9 {
		t.Errorf("zone[0] = %v, want 3.97", res.CharmZoneBoundaries[0])
	}
	if math.Abs(res.CharmZoneBoundaries[1]-6.94) > 1e-9 {
		t.Errorf("zone[1] = %v, want 6.94", res.CharmZoneBoundaries[1])
	}
	if res.PinMinProbability == nil {
		t.Fatalf("PinMinProbability = nil, want median(0.40..0.48) = 0.44")
	}
	if math.Abs(*res.PinMinProbability-0.44) > 1e-9 {
		t.Errorf("PinMinProbability = %v, want 0.44", *res.PinMinProbability)
	}
	if res.SampleCount != 10 {
		t.Errorf("SampleCount = %d, want 10", res.SampleCount)
	}
	want := "2026-02-02T00:00:00Z/2026-02-13T00:00:00Z"
	if res.FitWindow != want {
		t.Errorf("FitWindow = %q, want %q", res.FitWindow, want)
	}
}

func TestCalibrate_PinInactiveSkipped(t *testing.T) {
	t.Parallel()
	// No pin_active rows accumulated → engine never triggered → no
	// median to fit; PinMinProbability must be nil so cmd/compute's
	// loader can fall back to the engine default.
	from := time.Date(2026, 2, 2, 0, 0, 0, 0, time.UTC)
	to := from.Add(24 * time.Hour)
	res, err := Calibrate(stateSamples{
		netGEX:        []float64{1, 2, 3},
		charmVelocity: []float64{1, 2, 3},
		dpiVanna:      []float64{1, 2, 3},
		// pinTopProb intentionally empty — caller filters on pin_active=true
	}, from, to)
	if err != nil {
		t.Fatalf("Calibrate err: %v", err)
	}
	if res.PinMinProbability != nil {
		t.Fatalf("PinMinProbability = %v, want nil", *res.PinMinProbability)
	}
	// JSON round-trip must omit the field entirely.
	buf, err := json.Marshal(res)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	if strings.Contains(string(buf), "pin_min_probability") {
		t.Errorf("expected pin_min_probability to be omitted; got %s", buf)
	}
}

func TestCalibrate_AllEmpty(t *testing.T) {
	t.Parallel()
	from := time.Date(2026, 2, 2, 0, 0, 0, 0, time.UTC)
	to := from.Add(time.Hour)
	res, err := Calibrate(stateSamples{}, from, to)
	if err != nil {
		t.Fatalf("Calibrate err: %v", err)
	}
	if res.SampleCount != 0 {
		t.Errorf("SampleCount = %d, want 0", res.SampleCount)
	}
	if res.GEXNorm != 0 || res.CharmFlowRateNorm != 0 || res.VannaPressureNorm != 0 {
		t.Errorf("norms not zero: %+v", res)
	}
	if res.CharmZoneBoundaries != [2]float64{0, 0} {
		t.Errorf("zone boundaries = %v, want [0 0]", res.CharmZoneBoundaries)
	}
	if res.PinMinProbability != nil {
		t.Errorf("PinMinProbability = %v, want nil", *res.PinMinProbability)
	}
}

func TestCalibrate_RejectsInvertedWindow(t *testing.T) {
	t.Parallel()
	from := time.Date(2026, 2, 13, 0, 0, 0, 0, time.UTC)
	to := time.Date(2026, 2, 2, 0, 0, 0, 0, time.UTC)
	if _, err := Calibrate(stateSamples{}, from, to); err == nil {
		t.Fatal("expected error for inverted window, got nil")
	}
}

func TestParseSymbolList(t *testing.T) {
	t.Parallel()
	cases := []struct {
		in   string
		want []feed.Symbol
		err  bool
	}{
		{"spx", []feed.Symbol{feed.SymbolSPX}, false},
		{"NDX", []feed.Symbol{feed.SymbolNDX}, false},
		{"spx,ndx", []feed.Symbol{feed.SymbolSPX, feed.SymbolNDX}, false},
		{"ndx,spx", []feed.Symbol{feed.SymbolNDX, feed.SymbolSPX}, false},
		{"spx,spx", []feed.Symbol{feed.SymbolSPX}, false},
		{"spx, ndx ", []feed.Symbol{feed.SymbolSPX, feed.SymbolNDX}, false},
		{"", nil, true},
		{"foo", nil, true},
		{"spx,xyz", nil, true},
	}
	for _, c := range cases {
		got, err := parseSymbolList(c.in)
		if c.err {
			if err == nil {
				t.Errorf("parseSymbolList(%q) err = nil, want error", c.in)
			}
			continue
		}
		if err != nil {
			t.Errorf("parseSymbolList(%q) err = %v", c.in, err)
			continue
		}
		if len(got) != len(c.want) {
			t.Errorf("parseSymbolList(%q) len = %d, want %d", c.in, len(got), len(c.want))
			continue
		}
		for i := range got {
			if got[i] != c.want[i] {
				t.Errorf("parseSymbolList(%q)[%d] = %v, want %v", c.in, i, got[i], c.want[i])
			}
		}
	}
}

func TestWriteJSON_CreatesParents(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	out := filepath.Join(dir, "nested", "deeper", "calibration.json")

	pin := 0.62
	payload := map[string]CalibrationResult{
		"spx": {
			GEXNorm:             5e10,
			CharmFlowRateNorm:   1.2e3,
			VannaPressureNorm:   8.5e2,
			CharmZoneBoundaries: [2]float64{50, 150},
			PinMinProbability:   &pin,
			SampleCount:         1515,
			FitWindow:           "2026-02-02T00:00:00Z/2026-02-13T00:00:00Z",
		},
	}
	if err := writeJSON(out, payload); err != nil {
		t.Fatalf("writeJSON: %v", err)
	}
	data, err := os.ReadFile(out)
	if err != nil {
		t.Fatalf("read back: %v", err)
	}
	var got map[string]CalibrationResult
	if err := json.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if len(got) != 1 || got["spx"].SampleCount != 1515 {
		t.Errorf("round-trip lost data: %+v", got)
	}
	if got["spx"].PinMinProbability == nil || math.Abs(*got["spx"].PinMinProbability-0.62) > 1e-9 {
		t.Errorf("PinMinProbability round-trip wrong: %+v", got["spx"].PinMinProbability)
	}
}
