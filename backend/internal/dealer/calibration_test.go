package dealer

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestLoadCalibration_EmptyPath(t *testing.T) {
	t.Parallel()
	got, err := LoadCalibration("")
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	if got != nil {
		t.Errorf("got %v, want nil", got)
	}
}

func TestLoadCalibration_RoundTrip(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	path := filepath.Join(dir, "cal.json")
	body := []byte(`{
		"spx": {
			"gex_norm": 5.5e10,
			"charm_flow_rate_norm": 1.25e3,
			"vanna_pressure_norm": 8.5e2,
			"charm_zone_boundaries": [1.5e6, 6.0e6],
			"pin_min_probability": 0.62,
			"sample_count": 1515,
			"fit_window": "2026-02-12T11:30:00Z/2026-02-12T14:39:59Z"
		},
		"NDX": {
			"gex_norm": 2.0e10,
			"charm_flow_rate_norm": 6e2,
			"vanna_pressure_norm": 4e2,
			"charm_zone_boundaries": [1.0e6, 4.0e6],
			"sample_count": 0,
			"fit_window": ""
		}
	}`)
	if err := os.WriteFile(path, body, 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	got, err := LoadCalibration(path)
	if err != nil {
		t.Fatalf("err = %v", err)
	}
	// Lower-cased keys.
	spx, ok := got["spx"]
	if !ok {
		t.Fatal("spx missing")
	}
	if spx.GEXNorm != 5.5e10 || spx.SampleCount != 1515 {
		t.Errorf("spx = %+v, want gex 5.5e10 + 1515 samples", spx)
	}
	if spx.PinMinProbability == nil || *spx.PinMinProbability != 0.62 {
		t.Errorf("pin = %v, want 0.62 ptr", spx.PinMinProbability)
	}
	ndx, ok := got["ndx"]
	if !ok {
		t.Fatal("ndx missing (should be lower-cased from NDX)")
	}
	if ndx.PinMinProbability != nil {
		t.Errorf("ndx pin = %v, want nil (omitted in JSON)", *ndx.PinMinProbability)
	}
}

func TestLoadCalibration_MissingFile(t *testing.T) {
	t.Parallel()
	if _, err := LoadCalibration(filepath.Join(t.TempDir(), "absent.json")); err == nil {
		t.Error("expected error for missing file")
	}
}

func TestLoadCalibration_MalformedJSON(t *testing.T) {
	t.Parallel()
	dir := t.TempDir()
	path := filepath.Join(dir, "bad.json")
	if err := os.WriteFile(path, []byte("not json"), 0o644); err != nil {
		t.Fatalf("write: %v", err)
	}
	if _, err := LoadCalibration(path); err == nil {
		t.Error("expected parse error")
	}
}

func TestPreferredSymbol_PrefersSPX(t *testing.T) {
	t.Parallel()
	m := map[string]CalibrationConfig{
		"spx": {GEXNorm: 1, SampleCount: 100},
		"ndx": {GEXNorm: 2, SampleCount: 200},
	}
	cfg, sym, ok := PreferredSymbol(m)
	if !ok || sym != "spx" || cfg.GEXNorm != 1 {
		t.Errorf("got (%+v, %q, %v), want spx wins", cfg, sym, ok)
	}
}

func TestPreferredSymbol_FallsBackToNDX(t *testing.T) {
	t.Parallel()
	m := map[string]CalibrationConfig{
		"ndx": {GEXNorm: 2, SampleCount: 200},
	}
	cfg, sym, ok := PreferredSymbol(m)
	if !ok || sym != "ndx" || cfg.GEXNorm != 2 {
		t.Errorf("got (%+v, %q, %v), want ndx", cfg, sym, ok)
	}
}

func TestPreferredSymbol_SkipsZeroSampleEntries(t *testing.T) {
	t.Parallel()
	m := map[string]CalibrationConfig{
		"spx": {GEXNorm: 1, SampleCount: 0},
		"ndx": {GEXNorm: 2, SampleCount: 50},
	}
	cfg, sym, ok := PreferredSymbol(m)
	if !ok || sym != "ndx" || cfg.GEXNorm != 2 {
		t.Errorf("got (%+v, %q, %v), want ndx (spx had 0 samples)", cfg, sym, ok)
	}
}

func TestPreferredSymbol_EmptyMap(t *testing.T) {
	t.Parallel()
	if _, _, ok := PreferredSymbol(nil); ok {
		t.Error("expected no preferred entry for nil map")
	}
}

func TestDPIScorer_SetThresholds_AppliesPositive(t *testing.T) {
	t.Parallel()
	s := NewDPIScorer(DPIConfig{
		GEXNorm:           5e9,
		CharmFlowRateNorm: 5e6,
		VannaPressureNorm: 1e6,
	})
	s.SetThresholds(7.7e10, 1.1e3, 4.4e2)
	if s.cfg.GEXNorm != 7.7e10 {
		t.Errorf("GEXNorm = %g, want 7.7e10", s.cfg.GEXNorm)
	}
	if s.cfg.CharmFlowRateNorm != 1.1e3 {
		t.Errorf("CharmFlowRateNorm = %g, want 1.1e3", s.cfg.CharmFlowRateNorm)
	}
	if s.cfg.VannaPressureNorm != 4.4e2 {
		t.Errorf("VannaPressureNorm = %g, want 4.4e2", s.cfg.VannaPressureNorm)
	}
}

func TestDPIScorer_SetThresholds_IgnoresNonPositive(t *testing.T) {
	t.Parallel()
	s := NewDPIScorer(DPIConfig{
		GEXNorm:           5e9,
		CharmFlowRateNorm: 5e6,
		VannaPressureNorm: 1e6,
	})
	prev := s.cfg
	s.SetThresholds(0, -1, -0.0)
	if s.cfg != prev {
		t.Errorf("non-positive overrides applied: got %+v, want %+v", s.cfg, prev)
	}
}

func TestCharmClock_SetVelocityThresholds_AppliesMonotonic(t *testing.T) {
	t.Parallel()
	c := NewCharmClockClassifier(time.Time{}, time.Time{})
	c.SetVelocityThresholds(1.5e6, 6.0e6)
	if c.weakVelocityCeiling != 1.5e6 || c.peakVelocityFloor != 6.0e6 {
		t.Errorf("got (%g, %g), want (1.5e6, 6.0e6)",
			c.weakVelocityCeiling, c.peakVelocityFloor)
	}
}

func TestCharmClock_SetVelocityThresholds_RejectsBadInputs(t *testing.T) {
	t.Parallel()
	c := NewCharmClockClassifier(time.Time{}, time.Time{})
	prevWeak := c.weakVelocityCeiling
	prevPeak := c.peakVelocityFloor

	// Non-positive
	c.SetVelocityThresholds(0, 5e6)
	c.SetVelocityThresholds(-1, 5e6)
	c.SetVelocityThresholds(1e6, 0)
	// Non-monotonic (weak >= peak)
	c.SetVelocityThresholds(5e6, 1e6)
	c.SetVelocityThresholds(5e6, 5e6)

	if c.weakVelocityCeiling != prevWeak || c.peakVelocityFloor != prevPeak {
		t.Errorf("bad inputs mutated thresholds: got (%g, %g), want defaults (%g, %g)",
			c.weakVelocityCeiling, c.peakVelocityFloor, prevWeak, prevPeak)
	}
}
