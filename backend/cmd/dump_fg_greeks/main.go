// Package main is a one-shot dumper that reads the input CSV produced by
// scripts/validation/iv_parity.py and emits FlowGreeks's IV + analytical
// Greeks per row, so the Python harness can compute parity diffs.
//
// This binary is NOT part of the production pipeline. It exists only to
// give the offline math validation harness an apples-to-apples comparison
// between scipy.brentq (reference) and FlowGreeks's own solver.
//
// Input CSV (header row required, written by iv_parity.py):
//
//	instrument_id,...,strike_price,mid,t_years,instrument_class,...
//
// Output CSV columns:
//
//	instrument_id,strike,t_years,side,mid,fg_iv,fg_converged,fg_iters,
//	fg_reason,fg_delta,fg_gamma,fg_theta,fg_vega,fg_charm,fg_vanna
//
// Usage:
//
//	go run ./cmd/dump_fg_greeks \
//	  -in   scripts/validation/outputs/2026-02-02/iv_ref_SPX_160000Z.csv \
//	  -out  scripts/validation/outputs/2026-02-02/iv_fg_SPX_160000Z.csv \
//	  -spot 6812.50 -r 0.045 -q 0.013
package main

import (
	"encoding/csv"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"os"
	"strconv"
	"strings"

	"flowgreeks/internal/feed"
	"flowgreeks/internal/greeks"
)

func main() {
	var (
		inPath  = flag.String("in", "", "input CSV from iv_parity.py (required)")
		outPath = flag.String("out", "", "output CSV path (required)")
		spot    = flag.Float64("spot", 0, "underlying spot used in the Python script (required)")
		rfr     = flag.Float64("r", 0.045, "continuously-compounded risk-free rate")
		div     = flag.Float64("q", 0.013, "continuous dividend yield (SPX≈0.013, NDX≈0.008)")
	)
	flag.Parse()

	if *inPath == "" || *outPath == "" || *spot <= 0 {
		flag.Usage()
		os.Exit(2)
	}

	inFile, err := os.Open(*inPath)
	if err != nil {
		log.Fatalf("open input: %v", err)
	}
	defer inFile.Close()

	outFile, err := os.Create(*outPath)
	if err != nil {
		log.Fatalf("create output: %v", err)
	}
	defer outFile.Close()

	r := csv.NewReader(inFile)
	r.FieldsPerRecord = -1 // tolerate variable column counts (pandas adds index)

	header, err := r.Read()
	if err != nil {
		log.Fatalf("read header: %v", err)
	}
	col := func(name string) int {
		for i, h := range header {
			if strings.EqualFold(strings.TrimSpace(h), name) {
				return i
			}
		}
		return -1
	}
	idIdx := col("instrument_id")
	strikeIdx := col("strike_price")
	midIdx := col("mid")
	tIdx := col("t_years")
	classIdx := col("instrument_class")
	for name, idx := range map[string]int{
		"instrument_id": idIdx, "strike_price": strikeIdx,
		"mid": midIdx, "t_years": tIdx, "instrument_class": classIdx,
	} {
		if idx < 0 {
			log.Fatalf("input CSV missing required column %q", name)
		}
	}

	w := csv.NewWriter(outFile)
	defer w.Flush()
	if err := w.Write([]string{
		"instrument_id", "strike", "t_years", "side", "mid",
		"fg_iv", "fg_converged", "fg_iters", "fg_reason",
		"fg_delta", "fg_gamma", "fg_theta", "fg_vega", "fg_charm", "fg_vanna",
	}); err != nil {
		log.Fatalf("write header: %v", err)
	}

	cfg := greeks.DefaultSolverConfig

	var rows, solved, failed int
	for {
		rec, err := r.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			log.Fatalf("read row %d: %v", rows+1, err)
		}
		rows++

		instrID := rec[idIdx]
		strike, err1 := strconv.ParseFloat(rec[strikeIdx], 64)
		mid, err2 := strconv.ParseFloat(rec[midIdx], 64)
		tYears, err3 := strconv.ParseFloat(rec[tIdx], 64)
		if err1 != nil || err2 != nil || err3 != nil ||
			strike <= 0 || mid <= 0 || tYears <= 0 {
			continue
		}
		var side feed.Side
		switch strings.ToUpper(strings.TrimSpace(rec[classIdx])) {
		case "C", "CALL":
			side = feed.SideCall
		case "P", "PUT":
			side = feed.SidePut
		default:
			continue
		}

		res := greeks.ImpliedVol(mid, *spot, strike, tYears, *rfr, *div, side, cfg)
		var g greeks.Greeks
		if res.Converged {
			g = greeks.All(*spot, strike, tYears, *rfr, *div, res.IV, side)
			solved++
		} else {
			failed++
		}

		sideStr := "C"
		if side == feed.SidePut {
			sideStr = "P"
		}

		row := []string{
			instrID,
			strconv.FormatFloat(strike, 'f', 6, 64),
			strconv.FormatFloat(tYears, 'f', 9, 64),
			sideStr,
			strconv.FormatFloat(mid, 'f', 6, 64),
			fmtFloat(res.IV),
			strconv.FormatBool(res.Converged),
			strconv.Itoa(res.Iterations),
			res.Reason,
			fmtFloat(g.Delta),
			fmtFloat(g.Gamma),
			fmtFloat(g.Theta),
			fmtFloat(g.Vega),
			fmtFloat(g.Charm),
			fmtFloat(g.Vanna),
		}
		if err := w.Write(row); err != nil {
			log.Fatalf("write row %d: %v", rows, err)
		}
	}

	w.Flush()
	if err := w.Error(); err != nil {
		log.Fatalf("flush: %v", err)
	}

	fmt.Printf("rows=%d solved=%d failed=%d  spot=%.2f r=%.4f q=%.4f\n",
		rows, solved, failed, *spot, *rfr, *div)
	fmt.Printf("→ %s\n", *outPath)
}

func fmtFloat(v float64) string {
	if math.IsNaN(v) || math.IsInf(v, 0) {
		return ""
	}
	return strconv.FormatFloat(v, 'f', 9, 64)
}
