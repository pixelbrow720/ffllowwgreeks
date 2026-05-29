// calibration.go is the runtime-loadable side of the offline
// `cmd/calibrate` tool. Walks dealer_state_1s in batch, fits empirical
// normalizers, writes JSON to disk; cmd/compute reads that JSON at
// startup and applies the values to DPIScorer + CharmClockClassifier.
//
// Schema MUST stay in lock-step with cmd/calibrate/main.go::CalibrationResult.
// PinMinProbability is parsed but currently unused by the engine — pin.go
// has no clean trigger-probability gate; wiring it is a separate design
// call. Loaded values are validated > 0 (the three norms) or pair
// monotonic (charm zone boundaries); malformed entries fall back to the
// engine's hard-coded defaults so a half-baked calibration JSON never
// silently zeroes out the production engine.
package dealer

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
)

// CalibrationConfig is one symbol's fit. Mirrors the JSON shape emitted
// by cmd/calibrate. Pointers (`*float64`) for the optional pin field
// distinguish "engine never triggered in window" (omit) from "fitted
// to zero" (present).
type CalibrationConfig struct {
	GEXNorm             float64    `json:"gex_norm"`
	CharmFlowRateNorm   float64    `json:"charm_flow_rate_norm"`
	VannaPressureNorm   float64    `json:"vanna_pressure_norm"`
	CharmZoneBoundaries [2]float64 `json:"charm_zone_boundaries"`
	PinMinProbability   *float64   `json:"pin_min_probability,omitempty"`
	SampleCount         int        `json:"sample_count"`
	FitWindow           string     `json:"fit_window"`
}

// LoadCalibration reads + parses the JSON written by cmd/calibrate.
// Returns the per-symbol map keyed by lowercase symbol ("spx", "ndx").
// An empty path returns (nil, nil) so callers can pass through the
// "no calibration" case without branching.
func LoadCalibration(path string) (map[string]CalibrationConfig, error) {
	if strings.TrimSpace(path) == "" {
		return nil, nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	var raw map[string]CalibrationConfig
	if err := json.Unmarshal(b, &raw); err != nil {
		return nil, fmt.Errorf("parse %s: %w", path, err)
	}
	out := make(map[string]CalibrationConfig, len(raw))
	for sym, cfg := range raw {
		out[strings.ToLower(sym)] = cfg
	}
	return out, nil
}

// PreferredSymbol picks the calibration entry to use for global
// (cross-symbol) thresholds. SPX wins when present because the
// historical archive has full coverage there; NDX OI is partial on
// some session days and would under-normalize if applied as the
// global default. Falls back to NDX when only NDX exists. Returns
// (CalibrationConfig{}, "", false) when the map is empty or no
// entry has a positive sample count.
//
// This is a deliberately simple policy. Once cmd/compute supports
// per-symbol scorers (post-M9 follow-up), drop this helper and feed
// each pipeline its own CalibrationConfig.
func PreferredSymbol(m map[string]CalibrationConfig) (CalibrationConfig, string, bool) {
	if cfg, ok := m["spx"]; ok && cfg.SampleCount > 0 {
		return cfg, "spx", true
	}
	if cfg, ok := m["ndx"]; ok && cfg.SampleCount > 0 {
		return cfg, "ndx", true
	}
	return CalibrationConfig{}, "", false
}
