// Package main runs the FlowGreeks offline calibration binary.
//
// Walks `dealer_state_1s` over a historical window and emits empirical
// constants used by the live DPI / Charm Clock / Pin engines:
//
//	gex_norm              95th percentile of |net_gex|
//	charm_flow_rate_norm  95th percentile of |charm_velocity|
//	vanna_pressure_norm   95th percentile of |dpi_vanna| (proxy)
//	charm_zone_boundaries [33rd, 66th] percentiles of charm_velocity
//	pin_min_probability   median of pin_top_prob where pin_active=true
//
// The binary is offline / batch only — allocations are not on the hot
// path. cmd/compute is the consumer of the JSON it writes; wiring that
// load is a separate follow-up.
//
// Usage:
//
//	calibrate \
//	  --from 2026-02-02T00:00:00Z \
//	  --to   2026-02-13T00:00:00Z \
//	  --symbol spx,ndx \
//	  --output data/calibration/2026-02-13.json
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"flowgreeks/internal/config"
	"flowgreeks/internal/feed"

	"github.com/jackc/pgx/v5/pgxpool"
)

// CalibrationResult is the per-symbol output. Fields use omitempty
// where the metric may be skipped (no qualifying rows).
type CalibrationResult struct {
	GEXNorm             float64    `json:"gex_norm"`
	CharmFlowRateNorm   float64    `json:"charm_flow_rate_norm"`
	VannaPressureNorm   float64    `json:"vanna_pressure_norm"`
	CharmZoneBoundaries [2]float64 `json:"charm_zone_boundaries"`
	PinMinProbability   *float64   `json:"pin_min_probability,omitempty"`
	SampleCount         int        `json:"sample_count"`
	FitWindow           string     `json:"fit_window"`
}

// stateSamples holds one column slice per metric we calibrate on.
// Populated by the DB walker; consumed by Calibrate.
type stateSamples struct {
	netGEX        []float64 // raw signed; we take |.| for the p95
	charmVelocity []float64 // raw signed; we take |.| for p95 and raw for zone boundaries
	dpiVanna      []float64 // raw; we take |.| for the p95
	pinTopProb    []float64 // only rows where pin_active = true
}

func main() {
	var (
		fromStr     = flag.String("from", "", "window start, RFC3339 (e.g. 2026-02-02T00:00:00Z)")
		toStr       = flag.String("to", "", "window end, RFC3339 (exclusive)")
		symbolFlag  = flag.String("symbol", "spx", "symbol(s) to calibrate, comma-separated: spx | ndx | spx,ndx")
		outPath     = flag.String("output", "", "output JSON path (e.g. data/calibration/2026-02-13.json)")
		postgresURL = flag.String("postgres-url", "", "Postgres DSN (overrides POSTGRES_* env vars)")
	)
	flag.Parse()

	from, err := time.Parse(time.RFC3339, strings.TrimSpace(*fromStr))
	if err != nil {
		fatalf("invalid --from: %v", err)
	}
	to, err := time.Parse(time.RFC3339, strings.TrimSpace(*toStr))
	if err != nil {
		fatalf("invalid --to: %v", err)
	}
	if !to.After(from) {
		fatalf("--to (%s) must be after --from (%s)", to.Format(time.RFC3339), from.Format(time.RFC3339))
	}
	if strings.TrimSpace(*outPath) == "" {
		fatalf("--output is required")
	}

	symbols, err := parseSymbolList(*symbolFlag)
	if err != nil {
		fatalf("%v", err)
	}

	dsn, err := resolveDSN(*postgresURL)
	if err != nil {
		fatalf("%v", err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()

	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		fatalf("pgx pool: %v", err)
	}
	defer pool.Close()

	pingCtx, pingCancel := context.WithTimeout(ctx, 5*time.Second)
	defer pingCancel()
	if err := pool.Ping(pingCtx); err != nil {
		fatalf("postgres ping: %v", err)
	}

	out := make(map[string]CalibrationResult, len(symbols))
	for _, sym := range symbols {
		samples, err := loadSamples(ctx, pool, sym, from, to)
		if err != nil {
			fatalf("load samples for %s: %v", sym, err)
		}
		res, err := Calibrate(samples, from, to)
		if err != nil {
			fatalf("calibrate %s: %v", sym, err)
		}
		out[strings.ToLower(sym.String())] = res
		fmt.Fprintf(os.Stderr, "calibrate %s: %d rows in window %s\n",
			sym, res.SampleCount, res.FitWindow)
	}

	if err := writeJSON(*outPath, out); err != nil {
		fatalf("write output: %v", err)
	}
	fmt.Fprintf(os.Stderr, "wrote %s (%d symbol(s))\n", *outPath, len(out))
}

// ─── DB layer ────────────────────────────────────────────────────────

// loadSamples streams the columns we need for calibration. We read raw
// signed values for net_gex / charm_velocity / dpi_vanna and let
// Calibrate decide where to take absolute value vs keep sign.
func loadSamples(ctx context.Context, pool *pgxpool.Pool, sym feed.Symbol, from, to time.Time) (stateSamples, error) {
	const q = `
		SELECT net_gex, charm_velocity, dpi_vanna, pin_active, pin_top_prob
		FROM dealer_state_1s
		WHERE symbol = $1 AND ts >= $2 AND ts < $3
		ORDER BY ts ASC
	`
	rows, err := pool.Query(ctx, q, int16(sym), from, to)
	if err != nil {
		return stateSamples{}, fmt.Errorf("query: %w", err)
	}
	defer rows.Close()

	out := stateSamples{
		netGEX:        make([]float64, 0, 8192),
		charmVelocity: make([]float64, 0, 8192),
		dpiVanna:      make([]float64, 0, 8192),
		pinTopProb:    make([]float64, 0, 1024),
	}
	for rows.Next() {
		var (
			netGEX        *float64
			charmVelocity *float64
			dpiVanna      float32
			pinActive     bool
			pinTopProb    float32
		)
		if err := rows.Scan(&netGEX, &charmVelocity, &dpiVanna, &pinActive, &pinTopProb); err != nil {
			return stateSamples{}, fmt.Errorf("scan: %w", err)
		}
		if netGEX != nil {
			out.netGEX = append(out.netGEX, *netGEX)
		}
		if charmVelocity != nil {
			out.charmVelocity = append(out.charmVelocity, *charmVelocity)
		}
		out.dpiVanna = append(out.dpiVanna, float64(dpiVanna))
		if pinActive {
			out.pinTopProb = append(out.pinTopProb, float64(pinTopProb))
		}
	}
	if err := rows.Err(); err != nil {
		return stateSamples{}, fmt.Errorf("rows: %w", err)
	}
	return out, nil
}

// resolveDSN prefers an explicit --postgres-url override; otherwise it
// builds one from POSTGRES_* env vars via the canonical config loader.
// The override path skips the production-only validation in
// config.Load() because calibration is an offline batch tool.
func resolveDSN(override string) (string, error) {
	if strings.TrimSpace(override) != "" {
		return override, nil
	}
	cfg, err := config.Load()
	if err != nil {
		return "", fmt.Errorf("config load: %w", err)
	}
	return cfg.Postgres.DSN(), nil
}

// ─── Pure math layer (testable without a DB) ─────────────────────────

// Calibrate consumes raw column slices and returns the calibration
// constants. The window arguments only flavour the FitWindow string;
// they don't filter samples (caller is expected to have queried the
// matching range already).
//
// Behaviour:
//   - Empty net_gex / charm_velocity / dpi_vanna columns → corresponding
//     norm is 0 and SampleCount reflects whatever rows were observed.
//   - PinMinProbability is nil when no rows had pin_active=true (we
//     don't fabricate a number when the engine never triggered).
func Calibrate(s stateSamples, from, to time.Time) (CalibrationResult, error) {
	if to.Before(from) {
		return CalibrationResult{}, errors.New("to before from")
	}

	// SampleCount tracks the dominant column. We use len(charmVelocity)
	// because the charm-zone boundaries depend on it and it is the
	// strictest of the three "row-aligned" columns.
	sampleCount := len(s.charmVelocity)
	if n := len(s.netGEX); n > sampleCount {
		sampleCount = n
	}
	if n := len(s.dpiVanna); n > sampleCount {
		sampleCount = n
	}

	res := CalibrationResult{
		SampleCount: sampleCount,
		FitWindow:   from.UTC().Format(time.RFC3339) + "/" + to.UTC().Format(time.RFC3339),
	}

	res.GEXNorm = percentileAbs(s.netGEX, 0.95)
	res.CharmFlowRateNorm = percentileAbs(s.charmVelocity, 0.95)
	res.VannaPressureNorm = percentileAbs(s.dpiVanna, 0.95)

	// Charm-zone boundaries are signed — the live classifier already
	// takes |velocity| inside, but the boundary fit is on the raw
	// distribution so a heavily one-sided session does not collapse to
	// zero.
	res.CharmZoneBoundaries = [2]float64{
		percentile(s.charmVelocity, 0.33),
		percentile(s.charmVelocity, 0.66),
	}

	if len(s.pinTopProb) > 0 {
		p := percentile(s.pinTopProb, 0.50)
		res.PinMinProbability = &p
	}

	return res, nil
}

// percentile returns the q-th quantile (q ∈ [0,1]) of the given slice
// using linear interpolation between adjacent ranks (the R-7 / NumPy
// default). Empty slice returns 0; clamps q to [0,1] silently.
//
// Mutates a working copy, never the caller's slice.
func percentile(values []float64, q float64) float64 {
	n := len(values)
	if n == 0 {
		return 0
	}
	if math.IsNaN(q) {
		return 0
	}
	switch {
	case q < 0:
		q = 0
	case q > 1:
		q = 1
	}

	work := make([]float64, n)
	copy(work, values)
	sort.Float64s(work)

	if n == 1 {
		return work[0]
	}
	pos := q * float64(n-1)
	lo := int(math.Floor(pos))
	hi := int(math.Ceil(pos))
	if lo == hi {
		return work[lo]
	}
	frac := pos - float64(lo)
	return work[lo] + frac*(work[hi]-work[lo])
}

// percentileAbs is percentile() applied to |values|. Convenience for
// the magnitude-based norms (GEX, charm flow rate, vanna pressure)
// where sign carries no calibration signal.
func percentileAbs(values []float64, q float64) float64 {
	if len(values) == 0 {
		return 0
	}
	work := make([]float64, len(values))
	for i, v := range values {
		work[i] = math.Abs(v)
	}
	return percentile(work, q)
}

// ─── CLI helpers ─────────────────────────────────────────────────────

// parseSymbolList accepts "spx", "ndx", "spx,ndx", or "ndx,spx".
// Duplicates are collapsed, order preserved.
func parseSymbolList(raw string) ([]feed.Symbol, error) {
	parts := strings.Split(strings.ToUpper(strings.TrimSpace(raw)), ",")
	if len(parts) == 0 || (len(parts) == 1 && parts[0] == "") {
		return nil, errors.New("--symbol is required")
	}
	seen := make(map[feed.Symbol]bool, 2)
	out := make([]feed.Symbol, 0, 2)
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		sym := feed.ParseSymbol(p)
		if sym == feed.SymbolUnknown {
			return nil, fmt.Errorf("unknown symbol: %q", p)
		}
		if seen[sym] {
			continue
		}
		seen[sym] = true
		out = append(out, sym)
	}
	if len(out) == 0 {
		return nil, errors.New("--symbol parsed to empty list")
	}
	return out, nil
}

// writeJSON pretty-prints the result map and writes it to outPath,
// creating any missing parent directories.
func writeJSON(outPath string, payload map[string]CalibrationResult) error {
	if dir := filepath.Dir(outPath); dir != "" && dir != "." {
		if err := os.MkdirAll(dir, 0o755); err != nil {
			return fmt.Errorf("mkdir %s: %w", dir, err)
		}
	}
	buf, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return fmt.Errorf("marshal: %w", err)
	}
	buf = append(buf, '\n')
	if err := os.WriteFile(outPath, buf, 0o644); err != nil {
		return fmt.Errorf("write %s: %w", outPath, err)
	}
	return nil
}

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "calibrate: "+format+"\n", args...)
	os.Exit(1)
}
